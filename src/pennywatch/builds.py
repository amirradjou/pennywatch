"""What exists upstream: how many dev builds, since when, and what the newest Catalyst pins."""

import urllib.error

from pennywatch import history, stacks

PACKAGES = ("pennylane", "pennylane-lightning", stacks.CATALYST)


def summarise_releases(releases: dict[str, list[dict]]) -> dict:
    """Count the builds that still have files, and date the first and the last."""
    dated = sorted(
        (files[0]["upload_time"][:10], version) for version, files in releases.items() if files
    )
    dev = [item for item in dated if ".dev" in item[1]]
    return {
        "builds": len(dev),
        "first": {"version": dev[0][1], "date": dev[0][0]} if dev else None,
        "last": {"version": dev[-1][1], "date": dev[-1][0]} if dev else None,
    }


def availability() -> dict | None:
    """TestPyPI's dev builds, or None if it can't be reached: context, not a result."""
    try:
        packages = {
            package: summarise_releases(
                stacks.fetch_json(f"https://test.pypi.org/pypi/{package}/json")["releases"]
            )
            for package in PACKAGES
        }
        newest = packages[stacks.CATALYST]["last"]["version"]
        info = stacks.fetch_json(f"https://test.pypi.org/pypi/{stacks.CATALYST}/{newest}/json")[
            "info"
        ]
    except (urllib.error.URLError, TimeoutError, KeyError, TypeError) as error:
        print(f"could not read TestPyPI: {error}")
        return None
    pins = stacks.exact_pins(info.get("requires_dist") or [])
    pennylane_pins = {name: version for name, version in pins.items() if "pennylane" in name}
    return {"packages": packages, "newest_catalyst_pins": pennylane_pins}


def dev_builds(releases: dict[str, list[dict]], since: str) -> list[str]:
    """Catalyst dev builds uploaded on or after a date, oldest first. A build with no files left
    can't be measured, so it isn't listed."""
    versions = [
        version
        for version, files in releases.items()
        if files and ".dev" in version and files[0]["upload_time"][:10] >= since
    ]
    return sorted(versions, key=history.version_key)


def pending(done: set[str], since: str, limit: int) -> list[str]:
    """What to measure next: the newest builds first, so that backfilling older ones never makes
    tonight's build wait."""
    releases = stacks.fetch_json(f"https://test.pypi.org/pypi/{stacks.CATALYST}/json")["releases"]
    waiting = [version for version in dev_builds(releases, since) if version not in done]
    return waiting[::-1][:limit]
