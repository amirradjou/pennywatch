"""One interface over PennyLane APIs that differ between versions.

On 0.46.0.dev142 `qml.specs` rejects a bare QNode outright, and the fields of its result are renamed
under qjit. Keep each difference here rather than spreading version checks through the workload.
"""


def _first_attribute(obj, *names):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"{type(obj).__name__} has none of {names}")


def make_qnode(qml, device_name: str, wires: int, circuit, seed: int):
    """Shots moved from the device to the QNode in PennyLane 0.43, and `diff_method` isn't needed
    until something differentiates."""
    options = {"diff_method": "adjoint"} if circuit.gradient_of else {}
    if circuit.shots is None:
        return qml.QNode(circuit.body, qml.device(device_name, wires=wires), **options)
    if hasattr(qml, "set_shots"):
        device = qml.device(device_name, wires=wires, seed=seed)
        return qml.set_shots(qml.QNode(circuit.body, device, **options), shots=circuit.shots)
    device = qml.device(device_name, wires=wires, seed=seed, shots=circuit.shots)
    return qml.QNode(circuit.body, device, **options)


def program(qml, qnode, circuit, under_qjit: bool):
    """The function whose cost gets measured, taking no arguments: `qml.qjit` then compiles it when
    applied, not at its first call. Usually that is the QNode itself."""
    if circuit.gradient_of is None:
        return qnode
    if under_qjit:
        import catalyst
        import jax.numpy as jnp

        angles = jnp.array(circuit.gradient_of)
        return lambda: catalyst.grad(qnode)(angles)
    from pennylane import numpy as pnp

    trainable = pnp.array(circuit.gradient_of, requires_grad=True)
    return lambda: qml.grad(qnode)(trainable)


def jit(qml, function, circuit, seed: int):
    return qml.qjit(function, seed=seed) if circuit.shots else qml.qjit(function)


def qnode_arguments(circuit) -> tuple:
    return () if circuit.gradient_of is None else (circuit.gradient_of,)


def tape_counts(qml, qnode, arguments: tuple) -> dict:
    """Gates and depth of a plain QNode at the top level and the device level."""
    counts = {}
    for level in ("top", "device"):
        batch, _ = qml.workflow.construct_batch(qnode, level=level)(*arguments)
        tape = batch[0]
        counts[level] = {"gates": len(tape.operations), "depth": tape.graph.get_depth()}
    return counts


def qjit_counts(qml, qnode, circuit, seed: int) -> dict:
    """Gates and depth after Catalyst's compile, read from specs. Not comparable with a plain
    QNode's numbers: Catalyst counts depth on its own program. Old stacks can't always say, which
    is no reason to lose the rest of their results."""
    try:
        arguments = qnode_arguments(circuit)
        if arguments:
            import jax.numpy as jnp

            arguments = (jnp.array(arguments[0]),)
        specs = qml.specs(jit(qml, qnode, circuit, seed))(*arguments)
        resources = specs["resources"] if isinstance(specs, dict) else specs.resources
        return {
            "gates": _first_attribute(resources, "num_gates", "total_quantum_operations"),
            "depth": _first_attribute(resources, "depth", "circuit_depth"),
        }
    except Exception as error:  # noqa: BLE001 - any failure here is the finding being recorded
        return {"unavailable": _described(error)}


def bare_qnode_specs(qml, qnode, arguments: tuple) -> str:
    """Whether `qml.specs` accepts a plain QNode: this is how API drift shows up in results."""
    try:
        qml.specs(qnode)(*arguments)
    except Exception as error:  # noqa: BLE001 - any failure here is the finding being recorded
        return _described(error)
    return "ok"


def _described(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error).splitlines()[0][:120]}"
