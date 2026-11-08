"""lightning.kokkos, the fourth backend, run through its own entry point.

The workload's table of devices names the other three. Adding a fourth there would change the code
those three run, and a few bytes can move a count by half a percent (docs/PLAN.md, "Why the plan
changed"), so kokkos registers itself here instead, and only its own runs import this module. The
package itself comes from a separate directory on the path, so the other backends' environment
doesn't change either.
"""

from pennywatch.harness import instrument

# As in the workload: first, so what gets counted before it is Python's own startup.
instrument.stop()

from pennywatch.harness import adapter, workload  # noqa: E402

workload.DEVICES["kokkos"] = "lightning.kokkos"
_make_qnode = adapter.make_qnode


def make_qnode(qml, device_name: str, wires: int, circuit, seed: int):
    """Older lightning.kokkos takes no seed, and its sampler then seeds itself. Kept here, not in
    the adapter, so the other backends run exactly the code they did."""
    try:
        return _make_qnode(qml, device_name, wires, circuit, seed)
    except TypeError as error:
        if "seed" not in str(error):
            raise
    options = {"diff_method": "adjoint"} if circuit.gradient_of else {}
    if hasattr(qml, "set_shots"):
        device = qml.device(device_name, wires=wires)
        return qml.set_shots(qml.QNode(circuit.body, device, **options), shots=circuit.shots)
    device = qml.device(device_name, wires=wires, shots=circuit.shots)
    return qml.QNode(circuit.body, device, **options)


adapter.make_qnode = make_qnode

if __name__ == "__main__":
    workload.main()
