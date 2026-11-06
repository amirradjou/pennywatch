"""Results as Markdown: the table the README's plan was built on, regenerated from a run."""

from statistics import median

from pennywatch import analysis


def _pct(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}%"


def _billions(value: int) -> str:
    return f"{value / 1e9:.2f}B"


def _measured(result: dict) -> list[dict]:
    return [cell for cell in result["cells"] if "instructions" in cell]


def stability_row(result: dict) -> str:
    cells = [c for c in _measured(result) if c["instructions"]["spread_pct"] is not None]
    if not cells:
        return "Not measured: this run had one repeat of each cell"
    worst = max(cells, key=lambda c: c["instructions"]["spread_pct"])
    typical = median(c["instructions"]["spread_pct"] for c in cells)
    spread = worst["instructions"]["spread_pct"]
    return (
        f"{result['repeats']} runs of each of {len(cells)} cells: typically "
        f"{_pct(typical, 4)} apart, at worst **{_pct(spread, 4)}** ({worst['name']}, of "
        f"{_billions(worst['instructions']['net'])}), counted from after the imports"
    )


def canary_row(result: dict) -> str:
    cells = [c for c in _measured(result) if "canary" in c]
    if not cells:
        return "Not measured"
    detected = sum(c["canary"]["detected"] for c in cells)
    weakest = min(cells, key=lambda c: c["canary"]["noise_ratio"])
    canary = weakest["canary"]
    return (
        f"Detected on **{detected} of {len(cells)}** cells. Weakest: {weakest['name']}, where "
        f"{canary['extra_gates']} extra gates added {canary['delta']:,} instructions "
        f"({_pct(canary['delta_pct'], 2)}), {canary['noise_ratio']:.0f} times the noise"
    )


def busy_row(result: dict) -> str:
    busy = result.get("busy_check")
    if not busy:
        return "Not measured in this run"
    cell = next(c for c in result["cells"] if c["name"] == busy["cell"])
    moved = abs(busy["busy_count"] - median(busy["quiet_runs"]))
    return (
        f"With {busy['spinners']} spinning processes on {busy['cell']}, counts moved "
        f"**{_pct(moved / cell['instructions']['net'] * 100, 4)}** while wall-clock time was "
        f"**{busy['wall_ratio']:.1f} times slower**"
    )


def wall_row(result: dict) -> str:
    cell = next((c for c in result["cells"] if c["name"] == "qft/lightning"), None)
    if cell is None or not cell.get("wall_s"):
        return "Not measured"
    times = cell["wall_s"][1:]  # the first call also does one-off setup
    spread = analysis.relative_range_pct(times)
    verdict = ", so a 1% regression is invisible in it" if spread > 1 else ""
    return (
        f"The same circuit ({cell['name']}, {len(times)} repeats in one process) took "
        f"{min(times):.3f} to {max(times):.3f} s, a range of {spread:.0f}%{verdict}"
    )


def gates_row(result: dict) -> str:
    parts = []
    for cell in result["cells"]:
        gates = cell.get("gates")
        if cell["backend"] == "lightning" and gates:
            top, device = gates["top"], gates["device"]
            parts.append(
                f"{cell['circuit']} {top['gates']} → {device['gates']} gates "
                f"(depth {top['depth']} → {device['depth']})"
            )
    return "Top level → device level: " + "; ".join(parts) if parts else "Not measured"


def compiler_row(result: dict) -> str:
    cells = [c for c in _measured(result) if "compile" in c]
    if not cells:
        return "Not measured"
    parts = []
    for cell in cells:
        compiler = cell["compile"]["compiler_process"]
        exact = "the same in every run" if len(set(compiler)) == 1 else "varying between runs"
        parts.append(
            f"{cell['circuit']} {_billions(cell['compile']['net'])}, of which the compiler "
            f"process {_billions(compiler[0])} ({exact})"
        )
    return "Net compile cost under qjit: " + "; ".join(parts)


def imports_row(result: dict) -> str:
    cells = {c["backend"]: c["imports"] for c in result["cells"] if "imports" in c}
    if not cells:
        return "Not measured"
    parts = [
        f"{backend} {_billions(imports['net'])}"
        + (
            f" (spread {_pct(imports['spread_pct'], 4)})"
            if imports["spread_pct"] is not None
            else ""
        )
        for backend, imports in cells.items()
    ]
    return "Counted separately, per backend: " + ", ".join(parts)


def builds_row(result: dict) -> str:
    available = result.get("availability")
    if not available:
        return "TestPyPI could not be read in this run"
    parts = [
        f"{package} {summary['builds']} since {summary['first']['date']}"
        for package, summary in available["packages"].items()
        if summary["first"]
    ]
    return "Dev builds on TestPyPI: " + ", ".join(parts)


def pins_row(result: dict) -> str:
    available = result.get("availability")
    if not available:
        return "TestPyPI could not be read in this run"
    newest = available["packages"]["pennylane-catalyst"]["last"]["version"]
    pins = ", ".join(f"{name} `{ver}`" for name, ver in available["newest_catalyst_pins"].items())
    return f"Only as a set: Catalyst `{newest}` pins {pins} exactly"


def api_row(result: dict) -> str:
    statuses = {c["bare_qnode_specs"] for c in result["cells"] if c.get("bare_qnode_specs")}
    if not statuses:
        return "Not measured"
    if statuses == {"ok"}:
        return "On this stack `qml.specs` accepts a plain QNode"
    rejected = next(status for status in statuses if status != "ok")
    return (
        f"No: `qml.specs` on a plain QNode raises `{rejected}`. The adapter reads the tape instead"
    )


def question_table(result: dict) -> str:
    rows = [
        ("Are instruction counts stable?", stability_row(result)),
        ("Can a small slowdown be seen?", canary_row(result)),
        ("Does a busy machine disturb them?", busy_row(result)),
        ("Is wall-clock time usable instead?", wall_row(result)),
        ("Do gate counts show the compile stack?", gates_row(result)),
        ("What does compiling cost?", compiler_row(result)),
        ("What do the imports cost?", imports_row(result)),
        ("Are dev builds installable?", builds_row(result)),
        ("Do the versions fit together?", pins_row(result)),
        ("Does the API hold still?", api_row(result)),
    ]
    body = "\n".join(f"| {question} | {answer} |" for question, answer in rows)
    return f"| Question | Result |\n|---|---|\n{body}\n"


def cell_table(result: dict) -> str:
    lines = [
        "| Cell | Instructions | Spread | Compile | Canary | Device gates | Depth |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for cell in result["cells"]:
        instructions = cell.get("instructions")
        if not instructions:
            lines.append(f"| {cell['name']} | failed | | | | | |")
            continue
        canary = cell.get("canary")
        canary_text = "failed"
        if canary:
            mark = "" if canary["detected"] else " MISSED"
            canary_text = f"{canary['delta_pct']:+.2f}%, {canary['noise_ratio']:.0f}×{mark}"
        device = cell.get("gates", {}).get("qjit") or cell.get("gates", {}).get("device") or {}
        compile_net = _billions(cell["compile"]["net"]) if "compile" in cell else ""
        lines.append(
            f"| {cell['name']} | {instructions['net']:,} | {_pct(instructions['spread_pct'], 4)} "
            f"| {compile_net} | {canary_text} | {device.get('gates', '')} "
            f"| {device.get('depth', '')} |"
        )
    return "\n".join(lines) + "\n"


def render(result: dict) -> str:
    stack, image, host = result["stack"], result["image"], result["host"]
    versions = (
        f"PennyLane {stack['pennylane']}, Lightning {stack['pennylane-lightning']}, "
        f"Catalyst {stack['pennylane-catalyst']}"
    )
    verdict = "valid" if result["valid"] else "REJECTED"
    header = (
        f"**{verdict}.** {result['started_at']}, {versions}. "
        f"{image['tag']}, {image['valgrind']}, on {host['cpu']}, {result['cpus']} CPUs.\n"
    )
    problems = ""
    if result["problems"]:
        problems = "\nProblems:\n" + "".join(f"- {problem}\n" for problem in result["problems"])
    return f"{header}{problems}\n{question_table(result)}\n{cell_table(result)}"
