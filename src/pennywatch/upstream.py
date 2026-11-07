"""Reading upstream's history: which commit a build came from, and what merged between two builds.

Read-only, from local clones with no file contents until they're needed. Nothing in this project
posts anything to these repositories.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pennywatch import stacks


@dataclass(frozen=True)
class Repo:
    package: str
    slug: str
    version_file: str


REPOS = {
    repo.package: repo
    for repo in (
        Repo("pennylane", "PennyLaneAI/pennylane", "pennylane/_version.py"),
        Repo(
            "pennylane-lightning",
            "PennyLaneAI/pennylane-lightning",
            "pennylane_lightning/core/_version.py",
        ),
        Repo("pennylane-catalyst", "PennyLaneAI/catalyst", "frontend/catalyst/_version.py"),
    )
}

_VERSION_LINE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")
_VERSION_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-]?(dev|rc)(\d+))?")
_PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")
_NIGHTLY_BUMP = "bump nightly version"


class NotFound(LookupError):
    """A build this history doesn't contain."""


def _ordering(version: str) -> tuple:
    """Order along a branch: dev and rc builds come before their release."""
    match = _VERSION_PARTS.match(version)
    if match is None:
        raise ValueError(f"not a version: {version}")
    major, minor, patch, kind, number = match.groups()
    stage = {"dev": 0, "rc": 1, None: 2}[kind]
    return (int(major), int(minor), int(patch), stage, int(number or 0))


def clone_path(repo: Repo) -> Path:
    return stacks.cache_dir() / "repos" / f"{repo.slug.split('/')[1]}.git"


def _git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *arguments], capture_output=True, text=True, check=True
    ).stdout


def update_clone(repo: Repo) -> Path:
    """A local clone with history and no file contents, which arrive one at a time when read."""
    path = clone_path(repo)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--bare",
                "--filter=blob:none",
                f"https://github.com/{repo.slug}.git",
                str(path),
            ],
            check=True,
        )
    _git(path, "fetch", "--quiet", "--tags", "origin", "+refs/heads/*:refs/heads/*")
    return path


def version_at(path: Path, repo: Repo, commit: str) -> str | None:
    """What a commit calls itself, or None if its version file can't be read that way."""
    try:
        text = _git(path, "show", f"{commit}:{repo.version_file}")
    except subprocess.CalledProcessError:
        return None
    match = _VERSION_LINE.search(text)
    return match[1] if match else None


def commit_of_dev_build(version: str, commits: list[str], read_version) -> str:
    """The commit that gave a dev build its number. `commits` touch the version file, oldest first,
    and versions only rise along a branch, so a binary search reads about ten of them."""
    wanted = _ordering(version)
    low, high = 0, len(commits)
    while low < high:
        middle = (low + high) // 2
        found = read_version(commits[middle])
        if found is None or _ordering(found) < wanted:
            low = middle + 1
        else:
            high = middle
    if low < len(commits):
        found = read_version(commits[low])
        if found is not None and _ordering(found) == wanted:
            return commits[low]
    raise NotFound(f"no commit calls itself {version}")


def commit_of(repo: Repo, version: str) -> str:
    """The commit a published version was built from."""
    path = update_clone(repo)
    if ".dev" not in version and "-dev" not in version:
        try:
            return _git(path, "rev-parse", f"v{version}^{{commit}}").strip()
        except subprocess.CalledProcessError as error:
            raise NotFound(f"no tag v{version} in {repo.slug}") from error
    # A bare clone's HEAD is the upstream default branch, which upstream has renamed before.
    commits = _git(
        path, "log", "--first-parent", "--reverse", "--format=%H", "HEAD", "--", repo.version_file
    ).split()
    return commit_of_dev_build(version, commits, lambda sha: version_at(path, repo, sha))


@dataclass(frozen=True)
class Merged:
    """One commit on the main line between two builds: usually a squashed pull request."""

    package: str
    commit: str
    subject: str
    pull_request: int | None
    url: str
    files: tuple[str, ...]


def parse_log(repo: Repo, log: str) -> list[Merged]:
    """`git log --format=%x00%H%x09%s --name-only`, skipping the nightly version bumps."""
    merged = []
    for entry in log.split("\0")[1:]:
        first, *rest = entry.strip("\n").split("\n")
        commit, _, subject = first.partition("\t")
        if _NIGHTLY_BUMP in subject:
            continue
        match = _PR_NUMBER.search(subject)
        number = int(match[1]) if match else None
        url = (
            f"https://github.com/{repo.slug}/pull/{number}"
            if number
            else f"https://github.com/{repo.slug}/commit/{commit}"
        )
        files = tuple(line for line in rest if line)
        merged.append(Merged(repo.package, commit, subject, number, url, files))
    return merged


def merged_between(repo: Repo, before: str, after: str) -> list[Merged]:
    """What landed after one build, up to and including the next, oldest first."""
    path = update_clone(repo)
    log = _git(
        path,
        "log",
        "--first-parent",
        "--reverse",
        "--format=%x00%H%x09%s",
        "--name-only",
        f"{before}..{after}",
    )
    return parse_log(repo, log)
