"""A stack is one Catalyst build and the exact versions it fits with.

Dev builds pin PennyLane and Lightning exactly, so naming Catalyst names the whole stack. Each stack
gets a lock file with the URL and hash of every wheel, committed to the repo, and that is what gets
installed.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pennywatch import container

STACKS_DIR = container.REPO_ROOT / "stacks"
CATALYST = "pennylane-catalyst"
TESTPYPI_PACKAGES = ("pennylane", "pennylane-lightning", CATALYST)
PINNED_BY_CATALYST = ("pennylane", "pennylane-lightning")

# Dev Catalyst also pins this, and it exists only on TestPyPI. Measuring lightning.qubit never loads
# it, and leaving it out keeps TestPyPI limited to the three packages this project watches. The
# marker is never true, which is how uv removes a dependency.
DROPPED_DEPENDENCY = "pennylane-lightning-kokkos; python_version < '3'"

_PIN = re.compile(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;,]+)$")


@dataclass(frozen=True)
class Stack:
    catalyst: str

    @property
    def id(self) -> str:
        return f"catalyst-{self.catalyst}"

    @property
    def is_dev(self) -> bool:
        return ".dev" in self.catalyst

    @property
    def lock_path(self) -> Path:
        return STACKS_DIR / f"{self.id}.pylock.toml"


def cache_dir() -> Path:
    return Path(os.environ.get("PENNYWATCH_CACHE", Path.home() / ".cache" / "pennywatch"))


def json_api_url(stack: Stack) -> str:
    host = "test.pypi.org" if stack.is_dev else "pypi.org"
    return f"https://{host}/pypi/{CATALYST}/{stack.catalyst}/json"


def exact_pins(requires_dist: list[str]) -> dict[str, str]:
    """The exact versions a Catalyst build asks for, spelled the PEP 440 way (`.dev142`)."""
    pins = {}
    for requirement in requires_dist:
        if match := _PIN.match(requirement):
            pins[match[1].lower()] = match[2].replace("-dev", ".dev")
    return pins


def project_file(stack: Stack, pins: dict[str, str]) -> str:
    """The input to uv's resolver. Only the three named packages may come from TestPyPI."""
    dependencies = [f"{CATALYST}=={stack.catalyst}"]
    index = ""
    if stack.is_dev:
        # uv accepts a pre-release only when asked for it by name, so the pins go in directly.
        dependencies += [f"{package}=={pins[package]}" for package in PINNED_BY_CATALYST]
        sources = "\n".join(
            f'{package} = {{ index = "testpypi" }}' for package in TESTPYPI_PACKAGES
        )
        index = (
            '\n[[tool.uv.index]]\nname = "testpypi"\n'
            'url = "https://test.pypi.org/simple/"\nexplicit = true\n'
            f"\n[tool.uv.sources]\n{sources}\n"
            f'\n[tool.uv]\noverride-dependencies = ["{DROPPED_DEPENDENCY}"]\n'
        )
    return (
        '[project]\nname = "pennywatch-stack"\nversion = "0"\nrequires-python = "==3.12.*"\n'
        f"dependencies = {json.dumps(dependencies)}\n{index}"
    )


def write_lock(stack: Stack, image: str) -> Path:
    """Resolve a stack and save its lock file. Needs the network; touches only this repo's file."""
    with urllib.request.urlopen(json_api_url(stack), timeout=60) as response:
        requires_dist = json.load(response)["info"].get("requires_dist") or []
    with tempfile.TemporaryDirectory(prefix="pennywatch-lock-") as temporary:
        work = Path(temporary)
        (work / "pyproject.toml").write_text(project_file(stack, exact_pins(requires_dist)))
        _uv(
            image,
            work,
            "pip compile pyproject.toml --quiet --python-version 3.12 "
            "--python-platform x86_64-manylinux_2_28 --only-binary :all: -o pylock.toml",
        )
        STACKS_DIR.mkdir(exist_ok=True)
        shutil.copyfile(work / "pylock.toml", stack.lock_path)
    return stack.lock_path


def locked_versions(lock_path: Path) -> dict[str, str]:
    packages = tomllib.loads(lock_path.read_text())["packages"]
    return {package["name"]: package["version"] for package in packages}


def lock_sha256(lock_path: Path) -> str:
    return hashlib.sha256(lock_path.read_bytes()).hexdigest()


def install(stack: Stack, image: str) -> Path:
    """Install the locked wheels once per lock file. Bytecode is compiled here and the directory is
    mounted read-only afterwards, so no measured run ever pays for writing it."""
    target = cache_dir() / "stacks" / f"{stack.id}-{lock_sha256(stack.lock_path)[:12]}"
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Built under a temporary name, so an interrupted install is never taken for a finished one.
    partial = Path(tempfile.mkdtemp(prefix=f"{target.name}.", dir=target.parent))
    partial.chmod(0o755)
    shutil.copyfile(stack.lock_path, partial / "pylock.toml")
    script = (
        "uv venv --quiet --allow-existing --python /usr/local/bin/python /stack && "
        "uv pip sync --quiet --python /stack/bin/python --compile-bytecode /stack/pylock.toml"
    )
    _in_image(image, ["sh", "-c", script], partial, "/stack")
    partial.rename(target)
    return target


def _uv(image: str, work: Path, arguments: str) -> None:
    _in_image(image, ["uv", *arguments.split()], work, "/work", workdir="/work")


def _in_image(image: str, command: list[str], mount: Path, at: str, workdir: str = "/") -> None:
    """A networked container for installing, with no secrets and a limit on its CPUs."""
    uv_cache = cache_dir() / "uv"
    uv_cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--cpus=2",
            container.user_flag(),
            "-e",
            "HOME=/tmp",
            "-e",
            "UV_CACHE_DIR=/uv-cache",
            f"--volume={uv_cache}:/uv-cache",
            f"--volume={mount}:{at}",
            f"--workdir={workdir}",
            image,
            *command,
        ],
        check=True,
    )
