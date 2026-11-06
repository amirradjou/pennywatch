"""The arithmetic: turning raw counts into a cost, a noise figure and a canary verdict."""

from statistics import median

# 1% extra gates has to stand well clear of the noise, otherwise the instrument couldn't tell a real
# 1% from jitter.
CANARY_MIN_NOISE_RATIO = 10.0
# A single run has no range to read noise from, so assume about twice the worst range measured.
ASSUMED_NOISE = 1_000_000


def spread_pct(values: list[int]) -> float | None:
    """Range of repeated runs as a percentage of their median. None if there is only one run."""
    if len(values) < 2 or median(values) == 0:
        return None
    return (max(values) - min(values)) / median(values) * 100


def noise(values: list[int]) -> int:
    return max(values) - min(values) if len(values) > 1 else ASSUMED_NOISE


def canary_verdict(canary: int, runs: list[int], extra_gates: int) -> dict:
    """Did the circuit with 1% extra gates show up? If not, the night's numbers can't be trusted."""
    cost = int(median(runs))
    delta = canary - cost
    jitter = noise(runs)
    ratio = delta / jitter if jitter else float("inf")
    return {
        "extra_gates": extra_gates,
        "count": canary,
        "delta": delta,
        "delta_pct": delta / cost * 100 if cost else None,
        "noise": jitter,
        "noise_ratio": ratio,
        "detected": delta > 0 and ratio >= CANARY_MIN_NOISE_RATIO,
    }


def relative_range_pct(values: list[float]) -> float:
    """How far a set of values disagrees, as a percentage of their mean."""
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / mean * 100 if mean else 0.0
