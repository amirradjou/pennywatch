"""One interface over PennyLane APIs that differ between versions.

On 0.46.0.dev142 `qml.specs` rejects a bare QNode outright, and the fields of its result are renamed
under qjit. Keep each difference here rather than spreading version checks through the workload.
"""


def _first_attribute(obj, *names):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"{type(obj).__name__} has none of {names}")


def tape_counts(qml, qnode) -> dict:
    """Gates and depth of a plain QNode at the top level and the device level."""
    counts = {}
    for level in ("top", "device"):
        batch, _ = qml.workflow.construct_batch(qnode, level=level)()
        tape = batch[0]
        counts[level] = {"gates": len(tape.operations), "depth": tape.graph.get_depth()}
    return counts


def qjit_counts(qml, jitted) -> dict:
    """Gates and depth after Catalyst's compile, read from specs. Not comparable with a plain
    QNode's numbers: Catalyst counts depth on its own program."""
    resources = qml.specs(jitted)().resources
    return {
        "gates": _first_attribute(resources, "num_gates", "total_quantum_operations"),
        "depth": _first_attribute(resources, "depth", "circuit_depth"),
    }


def bare_qnode_specs(qml, qnode) -> str:
    """Whether `qml.specs` accepts a plain QNode: this is how API drift shows up in results."""
    try:
        qml.specs(qnode)()
    except Exception as error:  # noqa: BLE001 - any failure here is the finding being recorded
        return f"{type(error).__name__}: {str(error).splitlines()[0][:120]}"
    return "ok"
