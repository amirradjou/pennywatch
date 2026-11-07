"""Reading what a measured process left behind.

That process runs nightly code from TestPyPI, with our user ID, in a directory we later read from
the host. So it could leave a symlink called sentinel.json pointing at a credentials file, a FIFO
that blocks the reader for ever, or a file too large for memory. Nothing here follows a link or
believes a size, and only regular files are ever read.
"""

import json
import os
import re
import stat
from pathlib import Path

SENTINEL_LIMIT = 1024 * 1024
CACHEGRIND_LIMIT = 64 * 1024 * 1024  # real ones are about 2.5 MiB
RAW_FILE_LIMIT = CACHEGRIND_LIMIT
OUTPUT_FILES_LIMIT = 64  # a qjit run has five
NAME_LIMIT = 300

_UNSAFE_IN_TEXT = re.compile(r"[^\x20-\x7e]")


class UnsafeOutput(ValueError):
    """A measured process left something that isn't a plain file of a sensible size."""


def read_file(path: Path, limit: int) -> bytes:
    try:
        # O_NOFOLLOW refuses a symlink and O_NONBLOCK stops a FIFO from hanging the open.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        raise UnsafeOutput(f"{path.name}: {error.strerror}") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        raise UnsafeOutput(f"{path.name}: not a plain file")
    with os.fdopen(descriptor, "rb") as file:
        data = file.read(limit + 1)
    if len(data) > limit:
        raise UnsafeOutput(f"{path.name}: larger than {limit} bytes")
    return data


def clean_name(name: object) -> str:
    """A name from the measured process, safe to print or to put in a Markdown table: no control
    characters, nothing that closes a code span or a table cell, and a sensible length."""
    text = _UNSAFE_IN_TEXT.sub("?", str(name)).replace("`", "'").replace("|", "/")
    return text[:NAME_LIMIT]


def read_sentinel(path: Path) -> dict | None:
    """The sentinel's contents, or None if the process never wrote one."""
    if not os.path.lexists(path):
        return None
    sentinel = json.loads(read_file(path, SENTINEL_LIMIT))
    if not isinstance(sentinel, dict):
        raise UnsafeOutput(f"{path.name}: not a JSON object")
    return sentinel


def copy_plain_files(source: Path, target: Path) -> None:
    """Copy each run's plain files and nothing else. Links and oversized files are left behind."""
    for run in os.scandir(source):
        if not run.is_dir(follow_symlinks=False):
            continue
        destination = target / run.name
        destination.mkdir(parents=True, exist_ok=True)
        for entry in os.scandir(run.path):
            try:
                data = read_file(Path(entry.path), RAW_FILE_LIMIT)
            except UnsafeOutput as error:
                print(f"not keeping {run.name}/{entry.name}: {error}")
                continue
            (destination / entry.name).write_bytes(data)
