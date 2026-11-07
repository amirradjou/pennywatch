import pytest

from pennywatch import cachegrind, untrusted
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


FUNCTIONS = """cmd: /stack/bin/python -m x
events: Ir
fl=a.c
fn=first
10 5
11 7
fn=second
3 100
fl=b.c
fn=first
1 1
summary: 113
"""


def test_functions_are_summed_across_source_lines_and_files():
    assert cachegrind.read_functions(FUNCTIONS) == {"first": 13, "second": 100}


def test_counts_are_summed_over_processes_but_not_the_link_step(tmp_path):
    (tmp_path / "cachegrind.1").write_text(FUNCTIONS)
    (tmp_path / "cachegrind.2").write_text(FUNCTIONS.replace("/stack/bin/python", "catalyst"))
    (tmp_path / "cachegrind.3").write_text(FUNCTIONS.replace("/stack/bin/python", "ld"))
    assert cachegrind.collect_functions(tmp_path) == {"first": 26, "second": 200}


def test_function_changes_come_largest_first_and_leave_out_what_did_not_move():
    before = {"same": 5, "small": 10, "big": 100, "gone": 7}
    after = {"same": 5, "small": 11, "big": 50, "new": 3}
    assert cachegrind.function_changes(before, after) == [
        ("big", 100, 50),
        ("gone", 7, 0),
        ("new", 0, 3),
        ("small", 10, 11),
    ]


def test_function_changes_stop_at_the_limit():
    assert len(cachegrind.function_changes({"a": 1, "b": 2}, {}, top=1)) == 1


def test_a_run_with_hundreds_of_output_files_is_refused(tmp_path):
    for index in range(untrusted.OUTPUT_FILES_LIMIT + 1):
        (tmp_path / f"cachegrind.{index}").write_text(FUNCTIONS)
    with pytest.raises(untrusted.UnsafeOutput, match="Cachegrind files"):
        cachegrind.collect(tmp_path)
    with pytest.raises(untrusted.UnsafeOutput, match="Cachegrind files"):
        cachegrind.collect_functions(tmp_path)


def test_a_file_with_endless_function_names_is_refused(monkeypatch):
    monkeypatch.setattr(cachegrind, "FUNCTION_NAMES_LIMIT", 2)
    text = "fn=a\n1 1\nfn=b\n1 1\nfn=c\n1 1\n"
    with pytest.raises(untrusted.UnsafeOutput, match="too many"):
        cachegrind.read_functions(text)


def test_function_names_are_cleaned_as_they_are_read():
    assert cachegrind.read_functions("fn=evil|`\x1b\n1 1\n") == {"evil/'?": 1}
