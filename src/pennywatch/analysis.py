"""The arithmetic: turning raw counts into a cost, a noise figure and a canary verdict."""

from statistics import median

# 1% extra gates has to stand clear of the noise, otherwise the instrument couldn't tell a real 1%
# from jitter.
CANARY_MIN_NOISE_RATIO = 5.0
# The widest range across three runs of any cell under one memory layout, per backend, measured in
# M0. A canary is compared within one layout, so this is the noise it has to clear, and it has only
# one run to compare with, so it can't measure its own.
# kokkos was measured the same way on 2026-11-07: 381,836 at most, on trotter/kokkos at 0.15.0.
NOISE_FLOOR = {"default": 100_000, "lightning": 230_000, "qjit": 710_000, "kokkos": 400_000}


def spread_pct(values: list[int]) -> float | None:
    """Range of repeated runs as a percentage of their median. None if there is only one run."""
    if len(values) < 2 or median(values) == 0:
        return None
    return (max(values) - min(values)) / median(values) * 100


def noise(values: list[int]) -> int:
    return max(values) - min(values)


def canary_verdict(pairs: list[tuple[int, int]], extra_gates: int, backend: str) -> dict:
    """Did the circuit with 1% extra gates show up? If not, the build's numbers can't be trusted.

    Each pair is (canary, full run on the same layout). One pair is usually enough. A canary's own
    allocations can nudge the layout, though, so on a cell where it adds only 0.1% one pair can hide
    it. The median of several shows the signal if the instrument works, and not if it doesn't."""
    delta = int(median(canary - full for canary, full in pairs))
    cost = int(median(full for _, full in pairs))
    jitter = NOISE_FLOOR[backend]
    ratio = delta / jitter
    return {
        "extra_gates": extra_gates,
        "pairs": [list(pair) for pair in pairs],
        "delta": delta,
        "delta_pct": delta / cost * 100 if cost else None,
        "noise": jitter,
        "noise_ratio": ratio,
        "detected": ratio >= CANARY_MIN_NOISE_RATIO,
    }


def relative_range_pct(values: list[float]) -> float:
    """How far a set of values disagrees, as a percentage of their mean."""
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / mean * 100 if mean else 0.0
