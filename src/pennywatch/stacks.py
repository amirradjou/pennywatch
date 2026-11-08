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
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

COMPILE_BYTECODE = [
    "/stack/bin/python",
    "-I",
    "-S",
    "-m",
    "compileall",
    "-q",
    "-j",
    "2",
    "/stack/lib/python3.12/site-packages",
]

KOKKOS = "pennylane-lightning-kokkos"
KOKKOS_BYTECODE = ["/usr/local/bin/python", "-I", "-S", "-m", "compileall", "-q", "/kokkos"]

# Catalyst 0.15 goes with PennyLane 0.45. Its metadata can't be used for this: the requirement is
# only a lower bound, and Catalyst 0.10's says `pennylane>=0.4`, meaning 0.40.
PENNYLANE_MINOR_OFFSET = 30

_PIN = re.compile(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;,]+)$")
_STABLE_VERSION = re.compile(r"^\d+(\.\d+)+$")


class StackUnavailable(RuntimeError):
    """This build can never be measured as it stands, which is different from a network failure:
    trying again tomorrow won't help, so it gets recorded and not retried."""


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


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _host(stack: Stack) -> str:
    return "test.pypi.org" if stack.is_dev else "pypi.org"


def json_api_url(stack: Stack) -> str:
    return f"https://{_host(stack)}/pypi/{CATALYST}/{stack.catalyst}/json"


def pennylane_series(catalyst: str) -> str:
    """The PennyLane minor version a stable Catalyst was released with: 0.15.0 gives 0.45."""
    major, minor, *_ = catalyst.split(".")
    return f"{major}.{int(minor) + PENNYLANE_MINOR_OFFSET}"


def newest_upload(files: list[dict]) -> datetime:
    return max(datetime.fromisoformat(file["upload_time_iso_8601"]) for file in files)


def newest_of_series(releases: dict[str, list[dict]], series: str) -> list[dict]:
    """The files of the highest stable release in a series, skipping any that was withdrawn."""
    versions = [
        version
        for version, files in releases.items()
        if files and _STABLE_VERSION.match(version) and version.startswith(f"{series}.")
    ]
    if not versions:
        raise ValueError(f"no release of series {series}")
    return releases[max(versions, key=lambda v: [int(part) for part in v.split(".")])]


def as_of(stack: Stack, catalyst_files: list[dict], pins: dict[str, str]) -> str:
    """The moment this set of packages was complete. Third-party packages are resolved as they
    stood then, because an old PennyLane with today's dependencies is a stack nobody ran, and may
    not work at all."""
    uploads = [newest_upload(catalyst_files)]
    for package in PINNED_BY_CATALYST:
        if stack.is_dev:
            url = f"https://{_host(stack)}/pypi/{package}/{pins[package]}/json"
            uploads.append(newest_upload(fetch_json(url)["urls"]))
        else:
            releases = fetch_json(f"https://{_host(stack)}/pypi/{package}/json")["releases"]
            series = pennylane_series(stack.catalyst)
            uploads.append(newest_upload(newest_of_series(releases, series)))
    cutoff = max(uploads).astimezone(UTC) + timedelta(seconds=1)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    if not stack.is_dev:
        series = pennylane_series(stack.catalyst)
        dependencies += [f"{package}=={series}.*" for package in PINNED_BY_CATALYST]
    else:
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
    try:
        release = fetch_json(json_api_url(stack))
        pins = exact_pins(release["info"].get("requires_dist") or [])
        cutoff = as_of(stack, release["urls"], pins)
        project = project_file(stack, pins)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise StackUnavailable(f"not on the index: {error.url}") from error
        raise
    except KeyError as error:
        raise StackUnavailable(f"Catalyst {stack.catalyst} doesn't pin {error}") from error
    with tempfile.TemporaryDirectory(prefix="pennywatch-lock-") as temporary:
        work = Path(temporary)
        (work / "pyproject.toml").write_text(project)
        try:
            _uv(
                image,
                work,
                "pip compile pyproject.toml --quiet --python-version 3.12 "
                "--python-platform x86_64-manylinux_2_28 --only-binary :all: "
                f"--exclude-newer {cutoff} -o pylock.toml",
            )
        except subprocess.CalledProcessError as error:
            if "No solution found" in (error.stderr or ""):
                reason = error.stderr.strip()[-400:]
                raise StackUnavailable(
                    f"its packages can't be installed together: {reason}"
                ) from error
            raise
        STACKS_DIR.mkdir(exist_ok=True)
        shutil.copyfile(work / "pylock.toml", stack.lock_path)
    return stack.lock_path


def locked_versions(lock_path: Path) -> dict[str, str]:
    packages = tomllib.loads(lock_path.read_text())["packages"]
    return {package["name"]: package["version"] for package in packages}


def lock_sha256(lock_path: Path) -> str:
    return hashlib.sha256(lock_path.read_bytes()).hexdigest()


def install(stack: Stack, image: str) -> Path:
    """Install the locked wheels once per lock file, in two steps, because only the first may have
    the network and the shared cache.

    Unpacking wheels runs nothing from them. Compiling bytecode starts Python, and Python runs any
    .pth file a wheel installed unless told not to, so that step is offline and passes -S. Bytecode
    is compiled here and the directory is mounted read-only afterwards, so no measured run ever pays
    for writing it.
    """
    target = cache_dir() / "stacks" / f"{stack.id}-{lock_sha256(stack.lock_path)[:12]}"
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    # Built under a temporary name, so an interrupted install is never taken for a finished one.
    partial = Path(tempfile.mkdtemp(prefix=f"{target.name}.", dir=target.parent))
    partial.chmod(0o755)
    shutil.copyfile(stack.lock_path, partial / "pylock.toml")
    unpack = (
        "uv venv --quiet --allow-existing --python /usr/local/bin/python /stack && "
        "uv pip sync --quiet --python /stack/bin/python /stack/pylock.toml"
    )
    _in_image(image, ["sh", "-c", unpack], partial, "/stack")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--cpus=2",
            "--network=none",
            *container.HARDENING,
            container.user_flag(),
            "-e",
            "HOME=/tmp",
            f"--volume={partial}:/stack",
            image,
            *COMPILE_BYTECODE,
        ],
        check=True,
    )
    partial.rename(target)
    return target


def install_kokkos(stack: Stack, image: str) -> tuple[Path, str]:
    """lightning.kokkos for a stack, in a directory of its own, so the other backends' environment
    stays exactly as it was. Its version is always Lightning's, since the two are released together.

    A dev build's comes from TestPyPI, which makes it the fourth package allowed from there: named
    exactly, pinned exactly, and without its dependencies, which the stack already has. Bytecode is
    compiled here, offline, as for the stack, so no measured run pays for compiling it."""
    version = locked_versions(stack.lock_path).get("pennylane-lightning")
    if version is None:
        raise StackUnavailable("the stack has no Lightning, so no kokkos to match it")
    target = cache_dir() / "kokkos" / f"{stack.id}-{version}"
    if target.is_dir():
        return target, version
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(tempfile.mkdtemp(prefix=f"{target.name}.", dir=target.parent))
    partial.chmod(0o755)
    try:
        install_extras(
            image,
            partial,
            (f"{KOKKOS}=={version}",),
            index_url=f"https://{_host(stack)}/simple/" if stack.is_dev else None,
        )
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--cpus=2",
                "--network=none",
                *container.HARDENING,
                container.user_flag(),
                "-e",
                "HOME=/tmp",
                f"--volume={partial}:/kokkos",
                image,
                *KOKKOS_BYTECODE,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as error:
        shutil.rmtree(partial, ignore_errors=True)
        raise StackUnavailable(f"{KOKKOS} {version} can't be installed") from error
    partial.rename(target)
    return target, version


def install_extras(
    image: str, target: Path, packages: tuple[str, ...], index_url: str | None = None
) -> None:
    """Unpack packages from PyPI, or one other index, into a directory. Wheels only, so nothing
    from them runs."""
    _in_image(
        image,
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            *(["--index-url", index_url] if index_url else []),
            "--only-binary",
            ":all:",
            "--python-version",
            "3.12",
            "--python-platform",
            "x86_64-manylinux_2_28",
            "--target",
            "/target",
            *packages,
        ],
        target,
        "/target",
    )


def _uv(image: str, work: Path, arguments: str) -> None:
    _in_image(
        image, ["uv", *arguments.split()], work, "/work", workdir="/work", capture_errors=True
    )


def _in_image(
    image: str,
    command: list[str],
    mount: Path,
    at: str,
    workdir: str = "/",
    capture_errors: bool = False,
) -> None:
    """A networked container for installing, with no secrets and a limit on its CPUs."""
    uv_cache = cache_dir() / "uv"
    uv_cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--cpus=2",
            *container.HARDENING,
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
        stderr=subprocess.PIPE if capture_errors else None,
        text=capture_errors or None,
    )
