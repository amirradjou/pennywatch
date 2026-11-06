import pytest

from pennywatch.cachegrind import Counts, collect, process_kind, read_out_file

OUT_FILE = """desc: Trigger: Program termination
cmd: /stack/bin/python -P -s -m pennywatch.harness.workload --mode=full
events: Ir
fn=(1) main
1 5
summary: 12345
"""


def test_reads_command_and_summary():
    assert read_out_file(OUT_FILE) == (
        "/stack/bin/python -P -s -m pennywatch.harness.workload --mode=full",
        12345,
    )


def test_an_incomplete_file_is_an_error():
    with pytest.raises(ValueError):
        read_out_file("cmd: /stack/bin/python\n")


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("/stack/bin/python -P -s -m pennywatch.harness.workload", "python"),
        ("/usr/local/bin/python3.12 x.py", "python"),
        ("/stack/bin/catalyst -o /tmp/f.ll --module-name f", "compiler"),
        ("/stack/lib/python3.12/site-packages/catalyst/utils/../bin/catalyst -o f", "compiler"),
        ("/usr/bin/gcc -shared f.o", "linker"),
        ("/usr/libexec/gcc/x86_64-linux-gnu/14/collect2 -plugin x", "linker"),
        ("/usr/bin/ld -plugin x", "linker"),
        ("/bin/lscpu", "other"),
    ],
)
def test_process_kinds(command, kind):
    assert process_kind(command) == kind


def test_the_link_step_is_recorded_but_not_counted():
    counts = Counts(python=100, compiler=20, linker=7, other=3)
    assert counts.total == 123


def test_collect_sums_every_process_by_kind(tmp_path):
    for pid, (command, summary) in enumerate(
        [
            ("/stack/bin/python -m x", 1000),
            ("/stack/bin/catalyst -o f", 200),
            ("/usr/bin/gcc -shared", 30),
            ("/usr/bin/ld -plugin", 4),
        ]
    ):
        (tmp_path / f"cachegrind.{pid}").write_text(f"cmd: {command}\nsummary: {summary}\n")
    (tmp_path / "valgrind.1.log").write_text("not an output file")
    assert collect(tmp_path) == Counts(python=1000, compiler=200, linker=34)


def test_collect_refuses_an_empty_run(tmp_path):
    with pytest.raises(ValueError, match="no Cachegrind output"):
        collect(tmp_path)
