"""Reading Cachegrind's output files.

With --trace-children=yes each process writes its own file. A qjit run has five: Python, Catalyst's
compiler, then gcc, collect2 and ld. The compiler is real compile cost that a plain Cachegrind run
never sees, while the link step measures gcc and binutils, which this project doesn't watch and the
image pins. So the linker is recorded but kept out of the total.
"""

from dataclasses import dataclass
from pathlib import Path

LINKER_PROGRAMS = {"gcc", "cc", "collect2", "ld", "as"}


@dataclass(frozen=True)
class Counts:
    python: int = 0
    compiler: int = 0
    linker: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        """What gets compared: everything except the link step."""
        return self.python + self.compiler + self.other


def read_out_file(text: str) -> tuple[str, int]:
    """The command line and the instruction total from one Cachegrind output file."""
    command, summary = None, None
    for line in text.splitlines():
        if line.startswith("cmd:"):
            command = line.removeprefix("cmd:").strip()
        elif line.startswith("summary:"):
            summary = int(line.split()[1])
    if command is None or summary is None:
        raise ValueError("not a complete Cachegrind output file")
    return command, summary


def process_kind(command: str) -> str:
    program = Path(command.split()[0]).name
    if program.startswith("python"):
        return "python"
    if program == "catalyst":
        return "compiler"
    if program in LINKER_PROGRAMS:
        return "linker"
    return "other"


def collect(directory: Path) -> Counts:
    """Sum the output files of one measured run by kind of process."""
    totals = {"python": 0, "compiler": 0, "linker": 0, "other": 0}
    files = sorted(directory.glob("cachegrind.*"))
    if not files:
        raise ValueError(f"no Cachegrind output in {directory}")
    for path in files:
        command, summary = read_out_file(path.read_text(errors="replace"))
        totals[process_kind(command)] += summary
    return Counts(**totals)
