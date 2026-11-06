"""The public page: one static file, with its data inside it and no server behind it.

Result files carry strings that came from a measured process, so the page's script only ever puts
data into the page as text, and the JSON is written so that it can't close its own script element.
"""

import json
from importlib import resources
from pathlib import Path

from pennywatch import history
from pennywatch.suite import BACKENDS

MIB_PER_KB = 1 / 1024
CIRCUIT_ORDER = ("qft", "grover", "trotter", "qpe", "variational", "sampling")
BACKEND_LABELS = {
    "default": "default.qubit",
    "lightning": "lightning.qubit",
    "qjit": "lightning.qubit under qjit",
}


def build_info(result: dict) -> dict:
    stack = result["stack"]
    return {
        "id": stack["id"],
        "catalyst": stack["pennylane-catalyst"],
        "pennylane": stack.get("pennylane"),
        "lightning": stack.get("pennylane-lightning"),
        "measured": str(result.get("started_at", ""))[:10],
    }


def _panel(title: str, unit: str, lines: list[tuple[str, str, list]]) -> dict | None:
    series = [
        {"key": key, "name": name, "values": values}
        for key, name, values in lines
        if any(value is not None for value in values)
    ]
    return {"title": title, "unit": unit, "series": series} if series else None


def _circuits(tables: list[dict]) -> list[str]:
    found = {cell.split("/")[0] for table in tables for (cell, _) in table if "/" in cell}
    found.discard("memory")
    ordered = [circuit for circuit in CIRCUIT_ORDER if circuit in found]
    return ordered + sorted(found - set(ordered))


def _column(tables: list[dict], cell: str, metric: str) -> list:
    return [table.get((cell, metric)) for table in tables]


def _backend_lines(tables: list[dict], circuit: str, metric: str) -> list:
    return [
        (backend, BACKEND_LABELS[backend], _column(tables, f"{circuit}/{backend}", metric))
        for backend in BACKENDS
    ]


def panels(results: list[dict]) -> dict[str, list[dict]]:
    """Every metric as a set of small charts, each with one line per backend."""
    tables = [history.metrics_of(result) for result in results]
    circuits = _circuits(tables)
    by_circuit = {
        "instructions": ("Instructions per run", "instructions"),
        "gates": ("Gates at the device level", "gates.device"),
        "depth": ("Depth at the device level", "depth.device"),
    }
    chosen: dict[str, list[dict]] = {}
    for key, (_, metric) in by_circuit.items():
        unit = "instructions" if key == "instructions" else key
        chosen[key] = [
            panel
            for circuit in circuits
            if (panel := _panel(circuit, unit, _backend_lines(tables, circuit, metric)))
        ]
    chosen["compile"] = [
        panel
        for circuit in circuits
        if (
            panel := _panel(
                circuit,
                "instructions",
                [("qjit", BACKEND_LABELS["qjit"], _column(tables, f"{circuit}/qjit", "compile"))],
            )
        )
    ]
    first = circuits[0] if circuits else ""
    imports = _panel("Imports", "instructions", _backend_lines(tables, first, "imports"))
    chosen["imports"] = [imports] if imports else []
    memory = _panel(
        "Peak resident memory",
        "MiB",
        [
            (
                backend,
                BACKEND_LABELS[backend],
                [
                    None if value is None else round(value * MIB_PER_KB, 1)
                    for value in _column(tables, f"memory/{backend}", "peak_kb")
                ],
            )
            for backend in BACKENDS
        ],
    )
    chosen["memory"] = [memory] if memory else []
    return chosen


def _track(results: list[dict]) -> dict:
    ordered = history.in_build_order(results)
    return {"builds": [build_info(result) for result in ordered], "panels": panels(ordered)}


def explanation_entry(saved: dict) -> tuple[str, dict]:
    """A saved explanation, as the key of the step it explains and what the page says about it. The
    page only links to GitHub, and only prints these as text."""
    key = f"{saved['before']['stack']}..{saved['after']['stack']}/{saved['cell']}/{saved['metric']}"
    bisect = saved.get("bisect") or {}
    culprits = [culprit for culprit in bisect.get("culprits", []) if culprit.get("subject")]
    if culprits:
        also = f", and {len(culprits) - 1} more" if len(culprits) > 1 else ""
        return key, {"text": f"{culprits[0]['subject'][:120]}{also}", "url": culprits[0].get("url")}
    if bisect.get("verdict"):
        return key, {"text": str(bisect["verdict"])[:160], "url": None}
    count = sum(len(changes) for changes in (saved.get("candidates") or {}).values())
    return key, {"text": f"{count} candidates, not narrowed further", "url": None}


def load_explanations(directory: Path | None) -> dict:
    if directory is None or not directory.is_dir():
        return {}
    entries = (explanation_entry(json.loads(path.read_text())) for path in directory.glob("*.json"))
    return dict(entries)


def step_rows(steps: list[history.Step], explanations: dict | None = None) -> list[dict]:
    explanations = explanations or {}
    rows = []
    for step in steps:
        key = f"{step.before_stack}..{step.after_stack}/{step.cell}/{step.metric}"
        rows.append(
            {
                "before_stack": step.before_stack.removeprefix("catalyst-"),
                "after_stack": step.after_stack.removeprefix("catalyst-"),
                "cell": step.cell,
                "metric": step.metric,
                "before": step.before,
                "after": step.after,
                "change_pct": step.change_pct,
                "confirmed": step.confirmed,
                "explanation": explanations.get(key),
            }
        )
    return rows


def page_data(
    stable: list[dict],
    nightly: list[dict],
    steps: list[history.Step],
    gaps: list[dict] | None = None,
    explanations: dict | None = None,
) -> dict:
    return {
        "stable": _track(stable),
        "nightly": _track(nightly),
        "steps": step_rows(steps, explanations),
        "gaps": [
            {
                "build": gap["stack"]["pennylane-catalyst"],
                "why": [str(problem)[:300] for problem in gap.get("problems", [])][:3],
            }
            for gap in gaps or []
        ],
    }


def embed(data: dict) -> str:
    """JSON that is safe inside a script element, whatever the strings in it say."""
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(data: dict) -> str:
    template = resources.files("pennywatch").joinpath("site_template.html").read_text()
    return template.replace("/*DATA*/null", embed(data))


def write(data: dict, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    page = out / "index.html"
    page.write_text(render(data))
    return page
