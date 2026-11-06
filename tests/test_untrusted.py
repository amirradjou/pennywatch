import json
import os

import pytest

from pennywatch import cachegrind, untrusted


def test_reads_a_plain_file(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"data")
    assert untrusted.read_file(path, 100) == b"data"


def test_refuses_to_follow_a_symlink_to_a_host_file(tmp_path):
    secret = tmp_path / "credentials"
    secret.write_text('{"token": "secret"}')
    link = tmp_path / "sentinel.json"
    link.symlink_to(secret)
    with pytest.raises(untrusted.UnsafeOutput):
        untrusted.read_sentinel(link)


def test_a_fifo_is_refused_rather_than_hanging_the_host(tmp_path):
    fifo = tmp_path / "sentinel.json"
    os.mkfifo(fifo)
    with pytest.raises(untrusted.UnsafeOutput, match="not a plain file"):
        untrusted.read_file(fifo, 100)


def test_a_directory_is_refused(tmp_path):
    directory = tmp_path / "sentinel.json"
    directory.mkdir()
    with pytest.raises(untrusted.UnsafeOutput, match="not a plain file"):
        untrusted.read_file(directory, 100)


def test_a_hard_linked_file_is_refused(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"data")
    os.link(path, tmp_path / "other")
    with pytest.raises(untrusted.UnsafeOutput, match="not a plain file"):
        untrusted.read_file(path, 100)


def test_an_oversized_file_is_refused(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"x" * 101)
    with pytest.raises(untrusted.UnsafeOutput, match="larger than"):
        untrusted.read_file(path, 100)


def test_a_missing_sentinel_is_not_an_error(tmp_path):
    assert untrusted.read_sentinel(tmp_path / "sentinel.json") is None


def test_a_sentinel_must_be_an_object(tmp_path):
    path = tmp_path / "sentinel.json"
    path.write_text("[1, 2]")
    with pytest.raises(untrusted.UnsafeOutput, match="not a JSON object"):
        untrusted.read_sentinel(path)


def test_a_dangling_symlink_sentinel_is_refused_not_treated_as_missing(tmp_path):
    (tmp_path / "sentinel.json").symlink_to(tmp_path / "nowhere")
    with pytest.raises(untrusted.UnsafeOutput):
        untrusted.read_sentinel(tmp_path / "sentinel.json")


def test_cachegrind_refuses_a_symlinked_output_file(tmp_path):
    secret = tmp_path / "credentials"
    secret.write_text("cmd: x\nsummary: 1\n")
    (tmp_path / "cachegrind.1").symlink_to(secret)
    with pytest.raises(untrusted.UnsafeOutput):
        cachegrind.collect(tmp_path)


def test_keeping_raw_output_leaves_links_behind(tmp_path):
    secret = tmp_path / "credentials"
    secret.write_text("secret")
    run = tmp_path / "runs" / "job"
    run.mkdir(parents=True)
    (run / "cachegrind.1").write_text("cmd: x\nsummary: 1\n")
    (run / "stolen").symlink_to(secret)
    (tmp_path / "runs" / "linked-run").symlink_to(tmp_path)

    kept = tmp_path / "kept"
    untrusted.copy_plain_files(tmp_path / "runs", kept)

    assert (kept / "job" / "cachegrind.1").exists()
    assert not (kept / "job" / "stolen").exists()
    assert not (kept / "linked-run").exists()


def test_a_sentinel_round_trip(tmp_path):
    path = tmp_path / "sentinel.json"
    path.write_text(json.dumps({"result": [1.0]}))
    assert untrusted.read_sentinel(path) == {"result": [1.0]}


def test_a_name_cannot_break_out_of_a_markdown_table_or_move_the_terminal():
    assert untrusted.clean_name("a|b`c\x1b[31md\ne") == "a/b'c?[31md?e"


def test_a_name_has_a_sensible_length():
    assert len(untrusted.clean_name("x" * 5000)) == untrusted.NAME_LIMIT
