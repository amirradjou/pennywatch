# pennywatch

An independent performance monitor for [PennyLane](https://github.com/PennyLaneAI/pennylane),
[Lightning](https://github.com/PennyLaneAI/pennylane-lightning) and [Catalyst](https://github.com/PennyLaneAI/catalyst),
Xanadu's open-source quantum software stack. For each new build it runs the same circuits, records what they cost, says
when a cost changes, and traces the change to the pull request that caused it.

> **Status (2026-11-05): this is the plan; nothing is built yet.** The numbers under
> [What was tested first](#what-was-tested-first) come from a throwaway script, and M0 turns it into code. This README is
> the spec and the milestone tracker. pennywatch is not affiliated with or endorsed by Xanadu.

## Why

Quantum software lives or dies by its costs: how many instructions a simulation takes, how much memory it holds, how many
gates a circuit compiles to. Those costs can drift one pull request at a time, and nobody notices until they add up.
PennyLane's repositories already have a `!benchmark` command for pull requests, but I found no public history of results
across builds. pennywatch is the outside view: the same circuits on every build, a history anyone can read, and an
explanation of each change.

## What it measures

| Metric | How | Alert |
|---|---|---|
| **Instructions** (the main signal) | Valgrind's Cachegrind, single-threaded, net of the imports | a change of 0.5% or more, confirmed by a re-run |
| **Gates and depth** | `qml.specs` at the top level and the device level; exact integers | any change, reported as a change rather than a fault |
| **Compile cost** | instructions spent in Catalyst's `qjit`, measured the same way | a change of 0.5% or more, confirmed by a re-run |
| **Peak memory** | maximum resident size, on circuits large enough for the state vector to dominate | threshold set in M1 from measured noise |
| Wall-clock time | recorded for context | never alerts: see below |

## What was tested first

Measured on 2026-11-05 before writing this plan, on one laptop, in a `python:3.12-slim` container limited to 2 CPUs,
with PennyLane 0.45.1, Lightning 0.45.0 and Catalyst 0.15.0. The circuit is 18 qubits and 806 gates on `lightning.qubit`,
and Catalyst is 10 qubits under `qjit`.

| Question | Result |
|---|---|
| Are instruction counts stable? | Three runs were 113,270 instructions apart, on 1.80 billion above the imports: **0.006%**. Catalyst was 0.023%, of 3.3 billion |
| Can a small slowdown be seen? | 8 extra gates (1%) added 14.6 million instructions: **about 129 times the noise** |
| Does a busy machine disturb them? | With four spinning processes the counts moved **0.001%**, while wall-clock time was **about 3.8 times slower** |
| Is wall-clock time usable instead? | On a quiet machine the same circuit took 0.12 to 0.29 s, so a 1% regression is invisible in it |
| Do gate counts show the compile stack? | The same circuit is 90 gates at the top level and 806 at the device level (depth 73 and 165) |
| Are dev builds installable? | Yes, in seconds. PennyLane has 639 on TestPyPI since 2024-07, Lightning 110 since 2025-10, Catalyst 61 since 2026-08, nearly one a day |
| Do the versions fit together? | Only as a set: Catalyst `0.16.0.dev134` pins PennyLane `0.46.0.dev142` and Lightning `0.46.0.dev34` exactly |
| Does the API hold still? | No: on that build, `specs` with a `level` rejected a plain QNode. The harness needs an adapter from the start |

A Valgrind run takes about 35 to 60 s, 30 s of it imports. The first run of a build came out a little higher, likely
Python writing bytecode, so runs must warm up first. Catalyst also needs a C compiler in the image to link.

**Not tested yet:** whether counts agree between GitHub's runners and this laptop, or from one runner to the next. That
is the main open risk, and M0 answers it.

## How it works

1. **Detect.** Watch TestPyPI for a new Catalyst dev build. Its exact pins define one coherent stack, so one pin gives
   one set of versions to measure. Stable releases are measured the same way, from PyPI.
2. **Measure.** One fresh process per circuit and stack, in a container pinned by digest (Python, Valgrind, gcc), with
   fixed seeds and one thread. Each run also measures a **canary**: a circuit with 1% extra gates, which must show up.
   If it doesn't, the instrument is broken and the night's results are thrown out, not published.
3. **Compare** against the previous build. If runners turn out to differ, each job re-measures the previous build on the
   same machine, at twice the cost.
4. **Explain.** A change is narrowed to the pull requests merged between the two builds, about a day's worth. Then, as far
   as each package allows:
   - PennyLane is pure Python, so it can be bisected commit by commit with `pip install git+...`.
   - Lightning needs a C++ build per step, so it is bisected only when a change is worth the wait.
   - Catalyst is too heavy to build per commit, so it stops at the list of candidates and says so.

   A function-level diff from Cachegrind shows where the instructions went.
5. **Publish.** Results are JSON files in the repo, rendered as a static page. There is no server to keep running.

**Security.** Running nightly dev builds means running code from TestPyPI, where names aren't as well guarded as on PyPI.
So only the three named packages may come from it (uv's explicit index), everything else comes from PyPI, and the
measuring job has a read-only token and no secrets. A separate job publishes its results.

### Circuits

Chosen so each one exercises something different: QFT (controlled phases, swaps), Grover (multi-controlled gates), Trotter
evolution of a Hamiltonian, quantum phase estimation, and a variational circuit with adjoint gradients. Each runs on
`default.qubit`, `lightning.qubit` and `lightning.qubit` under `qjit`. Six circuits on three stacks at about 45 s is
about 14 minutes per build.

## Milestones

| | Milestone | Done when |
|---|---|---|
| M0 | The instrument | `pennywatch run` reproduces the table above from code in a pinned image. Three circuits on three stacks. The canary is detected every time. Runs on two different days agree within 0.05%. The same suite on GitHub Actions: spread between runners measured and written down, and the comparison method chosen from it |
| M1 | History | All six circuits on the last eight stable releases, with an API adapter. Every step of 0.5% or more re-measured and listed in the README with its numbers |
| M2 | Nightly | A scheduled Action measures each new dev build. Backfilled to 2026-08-08. Fourteen nights in a row unattended, any gap explained by TestPyPI rather than by pennywatch |
| M3 | Explanation | For at least three real changes from M1 or M2, it names the responsible pull request or a candidate list, checked by reading the diff or bisecting. Wrong answers counted and published. If there aren't three real changes, it is tested on seeded ones and the README says so |
| M4 | Public page | The repo is public under MIT, with a static page showing each metric's history and what changed, credits, and the limits below. The Action's cost is under GitHub's free allowance |
| M5 | Upstream | At least one finding reported by hand and judged valid by the maintainers, or a fix merged |

## Limits

- **Instructions aren't time.** They don't see cache misses, branch prediction or memory bandwidth, so a change that only
  affects those is missed. The history complements real timing; it doesn't replace it.
- **Single-threaded, CPU only.** OpenMP scaling and GPU devices are out of scope, and Valgrind 3.24 can't run AVX-512, so
  those kernels aren't measured.
- **Small circuits.** Valgrind runs about 30 times slower, so the suite stays around 18 qubits and can't see effects that
  only appear at scale.
- **It may find little.** The stack is well engineered and already benchmarked inside. "No regressions in N builds, and
  the canary caught every injected one" is a real and acceptable result, and it gets reported as such.
- **TestPyPI can change.** Old dev builds may not stay available, so past results may not be reproducible for ever.

## Reporting upstream

The PennyLane ecosystem's [AI tool policy](https://github.com/PennyLaneAI/pennylane/blob/master/AI_Policy.md) applies to
pull requests, issues and comments, so everything sent upstream is written and understood by a person, and any substantial
tool use is labelled as the policy asks. This repo's automation never posts to any PennyLane repository.

## Development

```sh
uv sync                                               # install
uv run pytest                                         # tests
uv run ruff check . && uv run ruff format --check .   # lint and format
```
