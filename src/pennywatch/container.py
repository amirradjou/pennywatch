"""Docker: the pinned image, and the sandbox every measured process runs in."""

import hashlib
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_DIR = REPO_ROOT / "docker"
IMAGE_INPUTS = ("Dockerfile", "packages.txt", "instrument.c")

MEMORY_LIMIT = "3g"
PIDS_LIMIT = 1024
FILE_SIZE_LIMIT = 2 * 1024 * 1024 * 1024
SPINNERS = 4

# Fixed so that nothing about the host reaches the process. One thread each, because Valgrind
# serialises threads anyway and a pool sized to the machine would differ between machines.
MEASURE_ENV = {
    "HOME": "/tmp",
    "PYTHONHASHSEED": "0",
    "PYTHONPATH": "/src",
    "PYTHONDONTWRITEBYTECODE": "1",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "JAX_PLATFORMS": "cpu",
}


HARDENING = ["--cap-drop=ALL", "--security-opt=no-new-privileges"]

LAYOUT_VARIABLE = "PENNYWATCH_LAYOUT"


def layout_padding(layout: int) -> str:
    """Padding for an environment variable, which shifts the process's memory layout and nothing
    else. Layout changes what the code does: CPython's method cache and the order of sets of objects
    follow addresses. Adding 16 bytes here moved one cell by 0.58%, while repeat runs under one
    layout agreed to 0.005%. So runs of a cell differ in layout, or their agreement means little."""
    return "x" * (16 + 24 * layout)


def image_tag() -> str:
    """Named after its inputs, so an edited Dockerfile can't pass for the image built before."""
    digest = hashlib.sha256()
    for name in IMAGE_INPUTS:
        digest.update((IMAGE_DIR / name).read_bytes())
    return f"pennywatch-image:{digest.hexdigest()[:12]}"


def ensure_image() -> str:
    tag = image_tag()
    present = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, check=False)
    if present.returncode != 0:
        subprocess.run(
            ["docker", "build", "--cpuset-cpus=0,1", "-t", tag, str(IMAGE_DIR)],
            check=True,
        )
    return tag


def tool_versions(image: str) -> dict[str, str]:
    script = "valgrind --version; gcc -dumpfullversion; python -V; ldd --version | head -1"
    lines = subprocess.run(
        ["docker", "run", "--rm", "--cpus=1", "--network=none", image, "sh", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return dict(zip(("valgrind", "gcc", "python", "libc"), lines, strict=True))


def user_flag() -> str:
    return f"--user={os.getuid()}:{os.getgid()}"


def sandbox_args(
    cpu: int, stack: Path, source: Path, output: Path, layout: int = 0, overlay: Path | None = None
) -> list[str]:
    """No network, no secrets, read-only everywhere except scratch, because this runs nightly code
    from TestPyPI. One CPU per process: with --cpuset the process also sees only one, so thread
    pools and CPU-count checks come out the same on every machine."""
    variables = MEASURE_ENV | {LAYOUT_VARIABLE: layout_padding(layout)}
    if overlay is not None:
        variables["PYTHONPATH"] = f"/overlay:{MEASURE_ENV['PYTHONPATH']}"
    environment = [flag for item in variables.items() for flag in ("-e", "=".join(item))]
    mounts = [f"--volume={overlay}:/overlay:ro"] if overlay is not None else []
    return [
        "--rm",
        "--cpus=1",
        f"--cpuset-cpus={cpu}",
        f"--memory={MEMORY_LIMIT}",
        f"--pids-limit={PIDS_LIMIT}",
        f"--ulimit=fsize={FILE_SIZE_LIMIT}",
        "--network=none",
        "--read-only",
        "--tmpfs=/tmp:rw,exec,size=2g",  # exec: Catalyst loads the library it just built from here
        "--hostname=pennywatch",
        *HARDENING,
        user_flag(),
        *environment,
        f"--volume={stack}:/stack:ro",
        f"--volume={source}:/src:ro",
        f"--volume={output}:/out",
        *mounts,
    ]


def with_spinners(command: list[str]) -> list[str]:
    """The same command competing with busy loops inside its own CPU limit. They die with it, since
    the command becomes the container's main process."""
    script = f'for i in $(seq {SPINNERS}); do (while :; do :; done) & done; exec "$@"'
    return ["sh", "-c", script, "sh", *command]
