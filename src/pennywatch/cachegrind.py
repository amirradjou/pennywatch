"""Reading Cachegrind's output files.

With --trace-children=yes each process writes its own file. A qjit run has five: Python, Catalyst's
compiler, then gcc, collect2 and ld. The compiler is real compile cost that a plain Cachegrind run
never sees, while the link step measures gcc and binutils, which this project doesn't watch and the
image pins. So the linker is recorded but kept out of the total.
"""

from dataclasses import dataclass
from pathlib import Path

from pennywatch import untrusted

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
        text = untrusted.read_file(path, untrusted.CACHEGRIND_LIMIT).decode(errors="replace")
        command, summary = read_out_file(text)
        totals[process_kind(command)] += summary
    return Counts(**totals)


def read_functions(text: str) -> dict[str, int]:
    """Instructions per function in one Cachegrind output file. Its lines are `fn=<name>`, then one
    `<line number> <count>` per source line, and a function can appear under several files."""
    functions: dict[str, int] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("fn="):
            current = line[3:]
            functions.setdefault(current, 0)
        elif current is not None and line[:1].isdigit():
            functions[current] += int(line.split()[1])
    return functions


def collect_functions(directory: Path) -> dict[str, int]:
    """Instructions per function, summed over the processes of one run. The link step is left out,
    as it is from the total."""
    totals: dict[str, int] = {}
    for path in sorted(directory.glob("cachegrind.*")):
        text = untrusted.read_file(path, untrusted.CACHEGRIND_LIMIT).decode(errors="replace")
        command, _ = read_out_file(text)
        if process_kind(command) == "linker":
            continue
        for name, count in read_functions(text).items():
            totals[name] = totals.get(name, 0) + count
    return totals


def function_changes(
    before: dict[str, int], after: dict[str, int], top: int = 20
) -> list[tuple[str, int, int]]:
    """The functions that moved most between two runs, largest movement first."""
    moved = [
        (name, before.get(name, 0), after.get(name, 0))
        for name in before.keys() | after.keys()
        if before.get(name, 0) != after.get(name, 0)
    ]
    moved.sort(key=lambda row: (-abs(row[2] - row[1]), row[0]))
    return moved[:top]
