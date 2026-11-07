# Findings

Behaviour noticed while building and running the suite, with the measurements behind it. Each entry is
data for a person to check. Nothing here has been reported upstream: see
[Reporting upstream](PLAN.md#reporting-upstream).

Counts are instructions, and the median of three memory layouts. The sections on dense matrices were
measured on 2026-11-05 on PennyLane 0.45.1 and 0.46.0.dev142, in the pinned image, on one CPU of a laptop
that was busy with other work. Treat their times as rough; the ratios are far larger than the noise, and
the gate lists and the error are exact.

## `QFT.compute_matrix` lost its cache

Until 0.45, `QFT.compute_matrix` had `@functools.lru_cache`, keyed on `num_wires`. The port to `Operator2`
([#9932](https://github.com/PennyLaneAI/pennylane/pull/9932), 2026-07-31) changed its argument to `wires` and removed
the decorator, and it's still gone on `main` on 2026-11-06. So `default.qubit` builds the same dense matrix again each
time it applies a QFT whole.

`qpe/default` runs phase estimation 7 times, and on the commit before and after the port, `pocketfft`'s share goes
from 205,687,808 to 1,439,814,656 instructions, and `general_nd` from 46,259,411 to 323,815,877: exactly 7.0 times.

Measured on Catalyst 0.16.0.dev135 with only a cached helper added, over three memory layouts:

| `qpe/default` | Runs | Median |
|---|---:|---:|
| As released | 3,152,543,519 / 3,154,024,396 / 3,153,068,442 | **3.153 billion** |
| Matrix cached again | 1,523,113,835 / 1,524,686,538 / 1,523,854,478 | **1.524 billion** |
| 0.45.1, for comparison | 1,163,285,842 is the median | 1.163 billion |

That's 52% of the cell's cost today, and 82% of its rise since 0.45.1.

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

## Decomposition rules read and tokenise their own source when created

`DecompositionRule.__init__` in `pennylane/decomposition/decomposition_rule.py` calls
`inspect.getsource(func)` every time, and the result is used by `__str__`. On a circuit that creates rules while it
runs, that tokenises source code during the run: `qpe/lightning` on 0.46.0.dev142 makes 89,708 calls into
`tokenize.py`, up from none on 0.45.1.

Measured on Catalyst 0.16.0.dev135 with only that call replaced by a constant, over three memory layouts:

| | As released | Source not captured | Change |
|---|---:|---:|---:|
| `qpe/lightning` | 3,620,385,819 | 2,445,917,982 | **-32.4%** |
| Imports and creating `default.qubit` | 8,001,419,809 | 7,388,246,275 | **-7.7%** |
| `trotter/lightning` | 9,785,745,619 | 9,771,755,399 | -0.14% |
| `qft/lightning` | 2,275,221,140 | 2,274,824,802 | -0.02% |

The last two are within their layout noise. Replacing the call breaks `__str__`, so this measures the cost and
isn't a proposal: computing the source when it's asked for would keep that behaviour.

## Moving operators to `Operator2` makes them more expensive to build

From 0.45.1 to 0.46.0.dev142, in instructions: `qpe/lightning` +622%, `qpe/default` +171%, `trotter/lightning` +102%,
`qft/lightning` +46%. Bisects put most of it on the pull requests that port each operator: see
[explanations](explanations.md). `Operator2.__init__` calls `Signature.bind`, and the profile shows the new calls in
`inspect.py`. The migration is still in progress and hasn't reached a stable release, so this is a snapshot.

## Lightning's peak memory fell by 44% at PennyLane 0.41: expectation values stopped copying the state

At 25 wires on `lightning.qubit`, from 1,232 MiB at Catalyst 0.10.0 to 690 MiB at 0.11.0, and from 1,759 to 1,217 MiB
under `qjit`, with a spread across layouts of at most 0.08%. It's held since, rising slowly to 772 and 1,337 MiB at
0.15.0.

The difference is 512 MiB, one state vector at 25 qubits (2^25 amplitudes of 16 bytes). Swapping one package at a time,
on the laptop, three runs each:

| Stack | `lightning.qubit` | under `qjit` |
|---|---:|---:|
| Catalyst 0.10.0 as published | 1,228 MiB | |
| Catalyst 0.11.0 as published | 687 MiB | 1,215 MiB |
| 0.10.0 with only PennyLane 0.41.1 | 1,200 MiB | |
| 0.10.0 with only numpy 2.2.5 | 1,229 MiB | |
| 0.10.0 with PennyLane and Lightning 0.41.1 | 688 MiB | |
| 0.11.0 with only Lightning 0.40.0 | 1,200 MiB | 1,726 MiB |

So it's Lightning, both ways round. (Lightning 0.41 alone won't run with PennyLane 0.40.) Stepping through a run
on 0.40 puts the peak in measuring: after the gates the process holds 716 MiB, while the 25 `expval(PauliZ)` take it to
1,229 MiB and back. In 0.40, Lightning's C++ `expval` for a named observable copied the whole state, applied the
observable to the copy and took an inner product.
[pennylane-lightning#1079](https://github.com/PennyLaneAI/pennylane-lightning/pull/1079), "in-place implementation of
expval for LightningQubit", replaced that with kernels that read the state where it is. The other commits to the
measurement code between the two tags are documentation and sparse matrices, which this circuit doesn't use. Under `qjit` the same C++ code runs from Lightning's library for Catalyst,
and swapping Lightning moves it by the same 512 MiB.

The rest of 0.41's change is smaller: the device no longer allocates its state until a circuit runs, and the imports
take about 30 MiB less.

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
