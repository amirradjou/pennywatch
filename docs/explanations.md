# Explanations

Every change pennywatch has tried to explain, what it answered, how that was checked, and whether it was
right. The raw output of each is in [`results/explanations/`](../results/explanations/).

## Scoreboard

| Part of the tool | Tried | Right | Wrong | Declined to answer |
|---|---:|---:|---:|---:|
| Bisect | 7 | 4 | 0 | 3, when the newer code can't run on the older stack |
| The order of the candidate list: was the cause first? | 7 | 0 | 7 | |
| Function-level diff: did it point at the right place? | 7 | 5 | 0 | 2, where it only shows the interpreter |

The bisect is what gives answers. The candidate list is a weak pointer, because broad refactors touch the
same files as the real cause, and the output says so. It failed so badly on imports, ranking the cause
240th of 414, that a step in the imports now ranks by Python's import timer instead.

## The changes

Figures are instructions, re-measured across three memory layouts on a separate machine from the history.

### 1. `trotter/lightning`: +102% on dev builds

4,844,155,780 → 9,787,648,258, from Catalyst 0.15.0 to 0.16.0.dev135.

**Answer, bisected over 32 commits.** PennyLane alone gives 100% of the step:

| Pull request | Instructions | Share |
|---|---:|---:|
| [#10113](https://github.com/PennyLaneAI/pennylane/pull/10113), `qp.prod` dispatches to `Prod2` | 6,804,262,117 → 9,792,884,933 | 60% |
| [#9844](https://github.com/PennyLaneAI/pennylane/pull/9844), port `X`, `CNOT`, `Toffoli`, `MultiControlledX` to `Operator2` | 5,443,362,914 → 6,041,462,515 | 12% |
| [#9858](https://github.com/PennyLaneAI/pennylane/pull/9858), port `PauliZ`, `CZ`, `CCZ` to `Operator2` | 5,060,072,417 → 5,383,861,277 | 7% |
| [#9984](https://github.com/PennyLaneAI/pennylane/pull/9984), port `RX`, `CRX` to `Operator2` | 6,393,524,508 → 6,464,627,316 | 1% |

**Checked** by reading the diff of #10113 against the profile: the new calls are in `operator2.py` and
`inspect.py` (0 → 238,270 calls to `Parameter.kind`), and `Operator2.__init__` calls `Signature.bind`.
**Right.** The cause was 14th of 591 in the candidate list.

### 2. `qpe/lightning`: +622% on dev builds

501,066,626 → 3,617,882,816, over the same builds.

**Answer, bisected over 31 commits.** PennyLane alone gives 99%:

| Pull request | Instructions | Share |
|---|---:|---:|
| [#9951](https://github.com/PennyLaneAI/pennylane/pull/9951), `PhaseShift` and `ControlledPhaseShift` to `Operator2` | 619,237,205 → 2,646,855,986 | 66% |
| [#10209](https://github.com/PennyLaneAI/pennylane/pull/10209), `QuantumPhaseEstimation` to `Operator2` | 3,289,957,099 → 3,562,524,199 | 9% |
| [#9788](https://github.com/PennyLaneAI/pennylane/pull/9788), `Operator2` partially abstract instantiation | 3,111,222,191 → 3,274,008,281 | 5% |
| [#9990](https://github.com/PennyLaneAI/pennylane/pull/9990), `QubitUnitary` to `Operator2` | 2,866,422,723 → 2,951,291,541 | 3% |

**Checked** by bisecting, and independently by the next change, which reached the same pull request. **Right.**
The cause was 24th of 591 in the candidate list.

### 3. `qft/lightning`: +46% on dev builds

1,553,150,344 → 2,273,851,992, over the same builds.

**Answer, bisected over 17 commits.** PennyLane alone gives 81%, and the other 19% isn't narrowed further:

| Pull request | Instructions | Share of PennyLane's part |
|---|---:|---:|
| [#9951](https://github.com/PennyLaneAI/pennylane/pull/9951), `PhaseShift` and `ControlledPhaseShift` to `Operator2` | 1,621,522,635 → 2,081,057,057 | 79% |
| [#9932](https://github.com/PennyLaneAI/pennylane/pull/9932), `QFT` to `Operator2` | 1,566,056,532 → 1,600,384,046 | 6% |

**Checked** by bisecting. The circuit is nearly all `ControlledPhaseShift`, which is what #9951 moved. **Right.**
The cause was 39th of 591 in the candidate list.

### 4. `qpe/default`: +171% on dev builds

1,163,285,842 → 3,152,657,700, over the same builds.

**Answer, bisected over 19 commits.** PennyLane alone gives 100%:

| Pull request | Instructions | Share |
|---|---:|---:|
| [#9932](https://github.com/PennyLaneAI/pennylane/pull/9932), `QFT` to `Operator2` | 1,160,874,336 → 2,793,486,727 | 82% |
| [#10209](https://github.com/PennyLaneAI/pennylane/pull/10209), `QuantumPhaseEstimation` to `Operator2` | 2,876,474,995 → 3,150,052,337 | 14% |

**Checked** three ways. The native table has `pocketfft` going up exactly 7.0 times, in a circuit that runs phase
estimation 7 times. The diff of #9932 removes `@functools.lru_cache` from `QFT.compute_matrix`. And adding a cache back
to the current build takes the cell from 3,153,068,442 to 1,523,854,478. **Right, and the most useful answer here.**
The cause wasn't in the top 30 of 591 in the candidate list.

### 5. Imports: +74% at PennyLane 0.43

4,566,843,941 → 7,959,705,146, from Catalyst 0.12.0 to 0.13.0.

**Answer.** The bisect couldn't run, because `autoray` changed its API between the two. The candidate list was
**wrong**: it ranked the cause, [#7988](https://github.com/PennyLaneAI/pennylane/pull/7988), 240th of 414,
since its signal came from a profile of the circuit, which can't see imports. So the tool gained an
import timer. Its table now leads with `pennylane/transforms/split_non_commuting.py`, at +415 ms.

**Checked** by removing the one line, `from scipy.stats import multinomial`, and counting again: 7.966 billion
falls to 5.003 billion, 87% of the rise. See [findings](findings.md). **Right after the fix.**

### 6. `qft/default`: -11% at PennyLane 0.43

8,627,277,739 → 7,695,974,707, from Catalyst 0.12.0 to 0.13.0. Every `default.qubit` cell moved.

**Answer.** The bisect couldn't run, for the same reason. The candidate list was **wrong**: it pointed at
PennyLane pull requests. The native table was right: `zgemm_itcopy_HASWELL`, inside OpenBLAS, fell from 1,212 to
291 million, and the list of packages that moved includes numpy 2.3.2 → 2.4.3.

**Checked** by putting numpy 2.4.3 on the older stack with nothing else changed: 7,677,691,857, all of the fall.
**Right, and not PennyLane at all.** It would have been easy to credit the wrong project.

### 7. `qpe/lightning`: -94% at PennyLane 0.40

7,474,826,447 → 437,942,844, from Catalyst 0.9.0 to 0.10.0, with device gates going from 6,760 to 652.

**Answer.** The bisect couldn't run: Lightning 0.39 can't be imported with PennyLane 0.40. The 54 Lightning
candidates tied, and [#988](https://github.com/PennyLaneAI/pennylane-lightning/pull/988) was among them.

**Checked by reading, not by measuring.** On 0.39, Lightning's `stopping_condition` ends in `op.name in
_operations`, a fixed list, so `C(Adjoint(PhaseShift)**N)` gets decomposed into 2^N gates. #988 replaces that
with `capabilities.supports_operation`, which understands nested controlled and power forms, and on 0.40
those operators stay whole. **Probably right, and the only answer here not confirmed by a count.**

### 8. Lightning's peak memory: -44% at PennyLane 0.41

1,232 → 690 MiB at 25 wires on `lightning.qubit`, and 1,759 → 1,217 MiB under `qjit`, from Catalyst 0.10.0 to 0.11.0.

**Explained by hand: the tool doesn't explain memory**, so this isn't on the scoreboard. Swapping one package at a time
showed it's Lightning, both ways round, and stepping through a run put the peak in measuring. In 0.40 an expectation
value copied the whole state; [#1079](https://github.com/PennyLaneAI/pennylane-lightning/pull/1079) computes it in
place. **Checked** by putting Lightning 0.40.0 back on the newer stack, which brings back the 512 MiB on both backends.
See [findings](findings.md).

## What these add up to

Changes 1 to 4 are one story. PennyLane is moving its operators from `Operator` to `Operator2`, still in progress
on 2026-11-06 and not yet in a stable release, and each operator that moves gets more expensive to build.
That's data about a migration under way, not a verdict on it: the same builds also made `qft/qjit` 35% cheaper.
