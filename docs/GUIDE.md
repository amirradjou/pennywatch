# Guide

Everything runs in Docker, one container per measured process, with no network and a read-only stack.
You need Docker and [uv](https://docs.astral.sh/uv/). The first run builds the image and installs a
stack, which takes a few minutes and about 2 GiB.

## Measuring

```sh
uv run pennywatch run --suite full --cells qft/lightning --repeats 1 --no-busy-check   # one real circuit
uv run pennywatch run --suite smoke --repeats 1 --no-busy-check   # all the plumbing; its numbers mean nothing
uv run pennywatch run                                             # three circuits, one stack
uv run pennywatch run --suite full --stack 0.14.1                 # all six, on any release
uv run pennywatch run --stack 0.16.0.dev134                       # a dev build from TestPyPI
uv run pennywatch run --cells qft/qjit,trotter/default            # part of a suite
```

| Option | Default | Meaning |
|---|---|---|
| `--stack` | `0.15.0` | A Catalyst version. It names the whole stack, because it pins or goes with one PennyLane and one Lightning |
| `--suite` | `m0` | `smoke` (its numbers mean nothing), `m0` (three circuits), `full` (six, plus peak memory) |
| `--repeats` | `3` | Runs per cell, each under a different memory layout. The figure is their median |
| `--cpus` | `0,1` | CPUs to use, one process on each at a time |
| `--no-busy-check` | off | Skip the run against four spinning processes |
| `--keep-raw DIR` | off | Keep Cachegrind's output files |
| `--out FILE` | `results/` | Where the JSON goes. A rejected run is saved as `*.rejected.json` |

A run is rejected, and never published, if any circuit gives the wrong answer, any canary goes
undetected, or any package isn't the locked version.

## Reading results

```sh
uv run pennywatch report results/stable/catalyst-0.15.0.json     # one run, as Markdown
uv run pennywatch compare a.json b.json                          # how far two runs of one stack agree
uv run pennywatch steps results/stable/*.json                    # what changed from build to build
uv run pennywatch steps results/stable/*.json --series           # with each metric's whole history
uv run pennywatch steps results/stable/*.json --second-set results/stable-repeat/*.json
```

`compare` fails if any metric ranges wider than `--limit` percent, and warns if the runs weren't like
for like. `--second-set` marks each step as confirmed or not against separate results.

## Explaining a change

```sh
uv run pennywatch explain 0.12.0 0.13.0 --cell qft/default
uv run pennywatch explain 0.15.0 0.16.0.dev135 --cell qpe/default --bisect
```

This re-measures the cell on both builds, then lists what merged between them:

- Each pull request scores the Python calls that moved in the files it touched, and the list starts with
  the highest. That order is a weak pointer: on real changes it hasn't yet put the cause first. See
  [explanations](explanations.md).
- For a step in the imports, the score comes from Python's import timer instead, with each outside
  package charged to the file of PennyLane that imported it.
- `--bisect` runs PennyLane alone at each commit on top of the older stack. If the newest PennyLane
  alone doesn't give most of the step, it says the cause is elsewhere. Commits that can't be measured
  are stepped around, and if the step is among them, the answer is the range.
- If PennyLane needs something the older stack lacks, `--with tomlkit` adds it. If it needs a different
  version of something the stack has, the bisect can't run across that change, and it says so.
- Lightning needs a C++ build per commit and Catalyst is too heavy to build per commit, so for those
  it stops at the candidates and says so.

## Adding a stack or a circuit

- `uv run pennywatch lock 0.16.0` writes `stacks/catalyst-0.16.0.pylock.toml`, with the URL and hash of
  every wheel. Third-party packages are resolved as they stood when the set was complete.
- A new circuit goes in `harness/circuits.py`, with its answer in `harness/expected.py` and its size in
  `suite.py`. Its answer has to come from something other than PennyLane: a closed form, or a separate
  simulation like `tests/trotter_numpy.py`.
- Anything in `harness/` is part of the instrument. Editing it can move the counts, so results record a
  hash of that directory and comparisons warn when it differs.
- API differences between PennyLane versions go in `harness/adapter.py`.
- `docker/pin-packages.sh` regenerates the image's pins. A new Valgrind or gcc can move every count, so
  only do this deliberately.

## Automation

| Workflow | Runs | Does |
|---|---|---|
| CI | every pull request | Lint and tests |
| Measure | by hand, or a push to `measure/spread-*` | The suite on several runners at once, to see how far they agree |
| History | by hand, or a push to `measure/history` | The last eight stable releases |
| Nightly | 08:23 UTC, or a push to `measure/nightly` | The newest unmeasured dev build, published to the `data` branch |
| Follow-up | after Nightly, or by hand with a `step` | Up to three new steps measured again on fresh runners and explained, recorded in `followups/` on the `data` branch, and an issue here for those that held |
| Pages | after Nightly and Follow-up, or a push to `main` that changes results or the page | The public page |

`Nightly` with a `limit` above 1 works through older builds, newest first, and that's how the backfill
runs. Only its publish job can write, it runs nothing from TestPyPI, and it treats everything the
measuring job uploaded as hostile.

GitHub turns a schedule off after 60 days with no activity in a public repo. If the page stops gaining
builds, turn `Nightly` back on in the Actions tab.

`Follow-up` picks the newest night's steps first, then the largest older ones, so quiet nights work
through the backlog. One follow-up covers the imports of every backend, and a `qjit` cell's instructions
cover its compile cost. Peak memory is measured again but not explained. To follow up one step by hand,
followed up before or not, run it with `step` set to the name the page gives it, such as
`catalyst-0.16.0.dev139..catalyst-0.16.0.dev140/qpe/lightning/instructions`. Locally:

```sh
uv run pennywatch followup plan --data ../data --step catalyst-0.12.0..catalyst-0.13.0/qft/default/imports
uv run pennywatch followup run --step '<one planned step>' --out followed/one
```

Its issues name pull requests by number in code spans and never link them: on a public repo a link would
add a cross-reference to the pull request's page.

Nothing in this repo posts to any PennyLane repository.

## Tests

```sh
uv run pytest                                         # fast, no Docker
uv run pytest -m docker                               # against the real image and stacks, in minutes
uv run ruff check . && uv run ruff format --check .
```
