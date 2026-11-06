"""Where results cross from the measuring job into the repo.

The measuring job runs nightly code from TestPyPI, so whatever it uploads is treated as hostile: it
could have been written by that code. This runs in the job that can write to the repo, so nothing
here executes anything from an artifact or trusts a file name. It parses, checks the shape, and
writes a fresh copy to a path worked out from the checked contents.
"""

import json
import math
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from pennywatch import history, stacks

MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_LOCK_BYTES = 2 * 1024 * 1024
MAX_STRING = 2000
MAX_DEPTH = 8
# Only the three packages this project watches may come from TestPyPI.
PYPI_FILES = "files.pythonhosted.org"
TESTPYPI_FILES = "test-files.pythonhosted.org"

_CELL_NAME = re.compile(r"^[a-z]+/[a-z]+$")
_PRINTABLE = re.compile(r"^[\x20-\x7e\n→]*$")


class Rejected(ValueError):
    """An artifact that doesn't have the shape of a result."""


def _refuse_constant(name: str) -> None:
    raise Rejected(f"{name} is not a number this project can publish")


def _check_values(value, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise Rejected("nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            _check_values(key, depth + 1)
            _check_values(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_values(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > MAX_STRING or not _PRINTABLE.match(value):
            raise Rejected("a string is too long or holds unexpected characters")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise Rejected("a number is not finite")
    elif not (isinstance(value, int) or value is None):
        raise Rejected(f"unexpected value of type {type(value).__name__}")


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_cell(cell: dict) -> None:
    if not isinstance(cell, dict) or not _CELL_NAME.match(str(cell.get("name"))):
        raise Rejected("a cell has no usable name")
    for metric in ("instructions", "compile", "imports"):
        if metric in cell and not _is_count(cell[metric].get("net")):
            raise Rejected(f"{cell['name']}: {metric} isn't a count")
    for level, counts in (cell.get("gates") or {}).items():
        if not isinstance(counts, dict):
            raise Rejected(f"{cell['name']}: gates at {level} aren't a table")
        for field in ("gates", "depth"):
            if field in counts and not _is_count(counts[field]):
                raise Rejected(f"{cell['name']}: {field} at {level} isn't a count")


def parse_result(data: bytes) -> dict:
    """A result that is safe to store and render, or Rejected."""
    if len(data) > MAX_RESULT_BYTES:
        raise Rejected("too large")
    try:
        result = json.loads(data, parse_constant=_refuse_constant)
    except ValueError as error:
        raise Rejected(f"not JSON: {error}") from error
    if not isinstance(result, dict):
        raise Rejected("not an object")
    _check_values(result)
    stack = result.get("stack")
    if not isinstance(stack, dict):
        raise Rejected("no stack")
    version = stack.get("pennylane-catalyst")
    try:
        history.version_key(str(version))
    except ValueError as error:
        raise Rejected(str(error)) from error
    if stack.get("id") != f"catalyst-{version}":
        raise Rejected("the stack's id doesn't match its version")
    if not isinstance(result.get("valid"), bool):
        raise Rejected("no verdict")
    if not isinstance(result.get("cells"), list):
        raise Rejected("no cells")
    if result["valid"] and not result["cells"]:
        raise Rejected("valid, but with nothing in it")
    for cell in result["cells"]:
        _check_cell(cell)
    return result


def parse_lock(data: bytes) -> dict:
    """A lock file that is safe to commit: wheels only, from the two expected hosts, and TestPyPI
    only for the three named packages."""
    if len(data) > MAX_LOCK_BYTES:
        raise Rejected("lock file too large")
    try:
        lock = tomllib.loads(data.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Rejected(f"not a lock file: {error}") from error
    packages = lock.get("packages")
    if not isinstance(packages, list) or not packages:
        raise Rejected("a lock file with no packages")
    for package in packages:
        wheels = package.get("wheels")
        if not wheels or "sdist" in package:
            raise Rejected(f"{package.get('name')}: only wheels may be locked")
        for wheel in wheels:
            host = urlparse(wheel.get("url", "")).hostname
            allowed = {PYPI_FILES}
            if package.get("name") in stacks.TESTPYPI_PACKAGES:
                allowed.add(TESTPYPI_FILES)
            if host not in allowed:
                raise Rejected(f"{package.get('name')}: a wheel from {host}")
    return lock


def done_builds(data_dir: Path) -> set[str]:
    """Builds that have been dealt with, including those rejected: measuring them again wouldn't
    change the verdict, and costs minutes."""
    prefix = "catalyst-"
    return {
        path.stem.removeprefix(prefix)
        for directory in (data_dir / "nightly", data_dir / "nightly" / "rejected")
        for path in directory.glob(f"{prefix}*.json")
    }


def accept(artifacts: Path, data_dir: Path) -> list[str]:
    """Store every acceptable result under `artifacts` in `data_dir`, and say what happened."""
    outcomes = []
    for path in sorted(artifacts.rglob("catalyst-*.json")):
        if path.is_symlink() or not path.is_file():
            outcomes.append(f"{path.name}: refused, not a plain file")
            continue
        try:
            result = parse_result(path.read_bytes())
        except Rejected as error:
            outcomes.append(f"{path.name}: refused, {error}")
            continue
        version = result["stack"]["pennylane-catalyst"]
        name = f"catalyst-{version}"
        lock_problem = None
        lock_data = None
        locks = list(artifacts.rglob(f"{name}.pylock.toml"))
        if result["valid"]:
            try:
                if len(locks) != 1 or locks[0].is_symlink():
                    raise Rejected("no lock file came with it")
                lock_data = locks[0].read_bytes()
                parse_lock(lock_data)
            except Rejected as error:
                lock_problem = str(error)
        if lock_problem:
            result = result | {"valid": False, "problems": [f"lock file: {lock_problem}"]}
        directory = data_dir / "nightly" if result["valid"] else data_dir / "nightly" / "rejected"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(json.dumps(result, indent=1) + "\n")
        if lock_data is not None and not lock_problem:
            (data_dir / "stacks").mkdir(parents=True, exist_ok=True)
            (data_dir / "stacks" / f"{name}.pylock.toml").write_bytes(lock_data)
        outcomes.append(f"{version}: {'accepted' if result['valid'] else 'recorded as rejected'}")
    return outcomes
