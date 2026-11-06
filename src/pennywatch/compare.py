"""How far runs of the same suite disagree: across days, across machines."""

from pennywatch.analysis import relative_range_pct


def _metrics(result: dict) -> dict[tuple[str, str], float]:
    metrics = {}
    for cell in result["cells"]:
        if "instructions" in cell:
            metrics[(cell["name"], "net")] = cell["instructions"]["net"]
        if "compile" in cell:
            metrics[(cell["name"], "compile")] = cell["compile"]["net"]
        if "imports" in cell:
            metrics[(cell["name"], "imports")] = cell["imports"]["net"]
    return metrics


def _gate_counts(result: dict) -> dict[str, dict]:
    return {cell["name"]: cell["gates"] for cell in result["cells"] if cell.get("gates")}


def mismatched_setup(results: list[dict]) -> list[str]:
    """Differences that would make disagreement meaningless or expected."""
    first = results[0]
    warnings = []
    checks = [
        ("harness", lambda r: r["pennywatch"]["harness_sha256"]),
        ("image", lambda r: r["image"]["tag"]),
        ("stack", lambda r: r["stack"]["lock_sha256"]),
    ]
    for name, read in checks:
        if len({read(result) for result in results}) > 1:
            warnings.append(f"The runs used different {name}s, so this isn't a like-for-like test")
    for result in results[1:]:
        if _gate_counts(result) != _gate_counts(first):
            warnings.append("Gate counts differ between runs, and they are exact integers")
            break
    return warnings


def compare(results: list[dict]) -> dict:
    """Range across runs of every metric the runs have in common."""
    tables = [_metrics(result) for result in results]
    shared = set(tables[0]).intersection(*tables[1:])
    rows = [
        {
            "cell": cell,
            "metric": metric,
            "values": [table[(cell, metric)] for table in tables],
            "range_pct": relative_range_pct([table[(cell, metric)] for table in tables]),
        }
        for cell, metric in sorted(shared)
    ]
    return {
        "worst_pct": max((row["range_pct"] for row in rows), default=0.0),
        "rows": rows,
        "warnings": mismatched_setup(results),
    }


def render(results: list[dict], comparison: dict) -> str:
    runs = "".join(
        f"- run {index}: {result['started_at']}, {result['host']['cpu']}, "
        f"kernel {result['host']['kernel']}\n"
        for index, result in enumerate(results, start=1)
    )
    header = " | ".join(f"run {index}" for index in range(1, len(results) + 1))
    lines = [f"| Cell | Metric | {header} | Range |", "|---|---|" + "---:|" * (len(results) + 1)]
    for row in comparison["rows"]:
        values = " | ".join(f"{int(value):,}" for value in row["values"])
        lines.append(f"| {row['cell']} | {row['metric']} | {values} | {row['range_pct']:.4f}% |")
    warnings = "".join(f"\n**Warning:** {warning}\n" for warning in comparison["warnings"])
    table = "\n".join(lines)
    return f"{runs}{warnings}\n{table}\n\nWorst range: **{comparison['worst_pct']:.4f}%**\n"
