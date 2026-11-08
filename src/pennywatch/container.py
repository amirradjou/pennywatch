"""Docker: the pinned image, and the sandbox every measured process runs in."""

import hashlib
import os
import subprocess
import time
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

# A job can reach MEMORY_LIMIT, and whatever else is running has to keep going meanwhile. Limiting
# CPUs alone isn't enough: three chains of jobs on two CPUs once ran a laptop out of memory, and the
# kernel killed the editor.
MIN_AVAILABLE_GIB = 4.0
MEMORY_WAIT_LIMIT_S = 3600
MEMORY_POLL_S = 15


def layout_padding(layout: int) -> str:
    """Padding for an environment variable, which shifts the process's memory layout and nothing
    else. Adding 16 bytes here moved one cell by 0.58%, while repeat runs under one layout agreed to
    0.005%. Python's call counts stay exact across layouts, so the same code runs and the extra work
    is inside the interpreter, where caches such as the method cache depend on addresses. So runs of
    a cell differ in layout, or their agreement means little."""
    return "x" * (16 + 24 * layout)


def available_memory_gib(meminfo: Path = Path("/proc/meminfo")) -> float:
    for line in meminfo.read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / (1024 * 1024)
    raise ValueError("no MemAvailable in /proc/meminfo")


def wait_for_memory(read=available_memory_gib, sleep=time.sleep, now=time.monotonic) -> None:
    """Hold a job back until the host can spare it. Fails rather than waiting for ever, since a job
    that never starts looks just like one that is taking a long time."""
    deadline = now() + MEMORY_WAIT_LIMIT_S
    while (available := read()) < MIN_AVAILABLE_GIB:
        if now() >= deadline:
            raise RuntimeError(
                f"only {available:.1f} GiB of memory free after an hour, "
                f"and a job needs {MIN_AVAILABLE_GIB} GiB to start"
            )
        sleep(MEMORY_POLL_S)


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
    cpu: int,
    stack: Path,
    source: Path,
    output: Path,
    layout: int = 0,
    overlay: Path | None = None,
    kokkos: Path | None = None,
) -> list[str]:
    """No network, no secrets, read-only everywhere except scratch, because this runs nightly code
    from TestPyPI. One CPU per process: with --cpuset the process also sees only one, so thread
    pools and CPU-count checks come out the same on every machine."""
    variables = MEASURE_ENV | {LAYOUT_VARIABLE: layout_padding(layout)}
    # Only a kokkos run gets its package on the path, so nothing changes for the other backends.
    paths = [*(["/overlay"] if overlay else []), *(["/kokkos"] if kokkos else [])]
    if paths:
        variables["PYTHONPATH"] = ":".join([*paths, MEASURE_ENV["PYTHONPATH"]])
    environment = [flag for item in variables.items() for flag in ("-e", "=".join(item))]
    mounts = [f"--volume={overlay}:/overlay:ro"] if overlay is not None else []
    mounts += [f"--volume={kokkos}:/kokkos:ro"] if kokkos is not None else []
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
