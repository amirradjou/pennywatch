"""The arithmetic: turning raw counts into a net cost, a noise figure and a canary verdict."""

from statistics import median

# A canary must clear both. 1% extra gates has to stand well clear of the noise, otherwise the
# instrument couldn't tell a real 1% from jitter.
CANARY_MIN_NOISE_RATIO = 10.0
# A single run has no spread to read noise from, so assume the worst the spike saw (0.023%), rounded
# up.
ASSUMED_NOISE_PCT = 0.05


def spread_pct(values: list[int], base: float) -> float | None:
    """Range of repeated runs as a percentage of `base`. None if a single run has no range."""
    if len(values) < 2 or base == 0:
        return None
    return (max(values) - min(values)) / base * 100


def net(gross: list[int], imports: list[int]) -> int:
    return int(median(gross)) - int(median(imports))


def noise(gross: list[int], imports: list[int], net_cost: int) -> int:
    """The jitter of a net figure: it inherits the range of the runs and of the baseline."""
    if len(gross) < 2 and len(imports) < 2:
        return int(net_cost * ASSUMED_NOISE_PCT / 100)
    return (max(gross) - min(gross)) + (max(imports) - min(imports))


def canary_verdict(
    canary_gross: int, gross: list[int], imports: list[int], extra_gates: int
) -> dict:
    """Did the circuit with 1% extra gates show up? If not, the night's numbers can't be trusted."""
    net_cost = net(gross, imports)
    delta = canary_gross - int(median(gross))
    jitter = noise(gross, imports, net_cost)
    ratio = delta / jitter if jitter else float("inf")
    return {
        "extra_gates": extra_gates,
        "gross": canary_gross,
        "delta": delta,
        "delta_pct": delta / net_cost * 100 if net_cost else None,
        "noise": jitter,
        "noise_ratio": ratio,
        "detected": delta > 0 and ratio >= CANARY_MIN_NOISE_RATIO,
    }


def relative_range_pct(values: list[float]) -> float:
    """How far a set of values disagrees, as a percentage of their mean."""
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / mean * 100 if mean else 0.0
