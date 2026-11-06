# Findings

Behaviour noticed while building and running the suite, with the measurements behind it. Each entry is
data for a person to check. Nothing here has been reported upstream: see
[Reporting upstream](PLAN.md#reporting-upstream).

Measured on 2026-11-05 on PennyLane 0.45.1 and 0.46.0.dev142, in the pinned image, on one CPU of a
laptop that was busy with other work. Treat the times as rough; the ratios are far larger than the
noise, and the gate lists and the error are exact.

## `import pennylane` pays for all of `scipy.stats`, for one function

`pennylane/transforms/split_non_commuting.py` has `from scipy.stats import multinomial` at the top of
the module, added in [#7988](https://github.com/PennyLaneAI/pennylane/pull/7988) (2025-08-27, in 0.43)
and still there on `main` on 2026-11-06. `multinomial` is called once, at line 90, and only for one way
of distributing shots. The import pulls in `scipy.stats`, and with it `scipy.optimize`, `interpolate`,
`integrate` and `spatial`: scipy goes from 188 modules to 488.

Measured on PennyLane 0.43.3 on its own stack, the instructions to import it and create `default.qubit`,
over three memory layouts:

| | Runs | Median |
|---|---:|---:|
| As released | 7,966,276,946 / 7,966,118,229 / 7,967,085,862 | **7.966 billion** |
| With only that line removed | 5,003,483,139 / 5,003,207,345 / 5,003,329,814 | **5.003 billion** |
| PennyLane 0.42.3, the release before | 4,566,874,928 / 4,566,830,407 / 4,566,558,160 | 4.567 billion |

So that line is 37% of the import, and 87% of the 74% rise from 0.42 to 0.43. `python -X importtime` puts
`scipy.stats` at about 1.6 s on one machine, where a clock is a rough guide.

## `default.qubit` got 11% cheaper from numpy 2.4, not from PennyLane

Every `default.qubit` cell fell at Catalyst 0.13 / PennyLane 0.43: `qft/default` by 10.8%, from 8.627 to
7.696 billion. The function that moved is `zgemm_itcopy_HASWELL`, inside OpenBLAS: from 1,212 to 291
million. Putting numpy 2.4.3 on the older stack, with PennyLane 0.42.3 and nothing else changed, gives
7.678 billion: all of it.

## `default.qubit` builds a dense matrix for `adjoint(QFT)`

`QFT` is decomposed from 6 wires up, but `adjoint(QFT)` stays whole at any size.
`_special_operator_support` in `pennylane/devices/default_qubit.py` matches on the name `"QFT"`. An
`Adjoint(QFT)` has another name, so it falls through to `op.has_matrix`, which is true.

| Wires | `QFT`, device level | Time | `adjoint(QFT)`, device level | Time | Peak RSS, 0.45.1 |
|---:|---|---:|---|---:|---:|
| 8 | 40 gates | 0.004 s | 1 operation | 0.003 s | 239 MiB |
| 10 | 60 gates | 0.006 s | 1 operation | 0.014 s | 285 MiB |
| 12 | 84 gates | 0.016 s | 1 operation | 0.266 s | 1021 MiB |
| 13 | 97 gates | 0.017 s | 1 operation | 2.788 s | 3582 MiB |
| 18 | | | 1 operation | `_ArrayMemoryError` for 512 GiB | |

Times are from 0.45.1. On 0.46.0.dev142 the shape is the same (0.954 s and 2795 MiB at 13 wires).

## `default.qubit` builds a dense matrix for large multi-controlled Z

`qml.FlipSign` and `qml.ctrl(qml.PauliZ(...))` reach the device as `C(PauliZ)`, which goes through
`Controlled.matrix()`. `MultiControlledX` on the same wires has its own path and takes about the same
time at every size.

| Wires | `FlipSign`, 0.45.1 | `ctrl(Z)`, 0.45.1 | `MultiControlledX`, 0.45.1 | `FlipSign`, dev | `ctrl(Z)`, dev | `MultiControlledX`, dev |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.033 s | 0.027 s | 0.006 s | 0.033 s | 0.028 s | 0.008 s |
| 12 | 0.131 s | 0.294 s | 0.009 s | 0.125 s | 0.144 s | 0.009 s |
| 13 | 3.07 s | 4.25 s | 0.006 s | 0.428 s | 0.620 s | 0.009 s |
| 14 | 18.8 s | 21.8 s | 0.011 s | 5.13 s | 9.20 s | 0.012 s |

Peak RSS passed 8 GiB at 14 wires. At 18 wires a Grover circuit using `FlipSign` raises
`_ArrayMemoryError` for 228 GiB. `GroverOperator` itself is decomposed from 13 wires up, by the same
table as `QFT`.

## `default.qubit` builds a dense matrix for a Hamiltonian under adjoint differentiation

`qml.grad` of a QNode with `diff_method="adjoint"` on `default.qubit`, measuring `qml.expval` of a
Hamiltonian on 18 wires, raises `_ArrayMemoryError` for 1.00 TiB, in
`scipy/sparse/_base.py:_process_toarray_args`. Seen on PennyLane 0.38.0, 0.42.3 and 0.46.0.dev142.

`lightning.qubit` runs the same gradient on the same 18 wires in about 0.6 s, and on PennyLane 0.38.0
`qml.specs` of that QNode on `lightning.qubit` also raises `MemoryError` for 1.00 TiB, which it doesn't
on 0.42.3. A two-wire observable has neither problem on any of them, so the suite now uses one.
