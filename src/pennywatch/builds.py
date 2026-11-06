"""What exists upstream: how many dev builds, since when, and what the newest Catalyst pins."""

import json
import urllib.error
import urllib.request

from pennywatch import stacks

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


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def availability() -> dict | None:
    """TestPyPI's dev builds, or None if it can't be reached: context, not a result."""
    try:
        packages = {
            package: summarise_releases(
                _fetch_json(f"https://test.pypi.org/pypi/{package}/json")["releases"]
            )
            for package in PACKAGES
        }
        newest = packages[stacks.CATALYST]["last"]["version"]
        info = _fetch_json(f"https://test.pypi.org/pypi/{stacks.CATALYST}/{newest}/json")["info"]
    except (urllib.error.URLError, TimeoutError, KeyError, TypeError) as error:
        print(f"could not read TestPyPI: {error}")
        return None
    pins = stacks.exact_pins(info.get("requires_dist") or [])
    pennylane_pins = {name: version for name, version in pins.items() if "pennylane" in name}
    return {"packages": packages, "newest_catalyst_pins": pennylane_pins}
