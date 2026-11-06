# Guide

Everything runs in Docker, one container per measured process, with no network and a read-only stack.
You need Docker and [uv](https://docs.astral.sh/uv/). The first run builds the image and installs a
stack, which takes a few minutes and about 2 GiB.

## Measuring

```sh
uv run pennywatch run --suite smoke --repeats 1 --no-busy-check   # check the plumbing, in minutes
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
uv run pennywatch explain 0.14.1 0.15.0 --cell qft/default
uv run pennywatch explain 0.14.1 0.15.0 --cell qft/default --bisect
```

This re-measures the cell on both builds, then lists what merged between them, most likely first:

- Each pull request scores the Python calls that moved in the files it touched. That's a pointer for a
  person to check, and it can be wrong.
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
| Pages | after Nightly | The public page, once the repo is public |

`Nightly` with a `limit` above 1 works through older builds, newest first, and that's how the backfill
runs. Only its publish job can write, it runs nothing from TestPyPI, and it treats everything the
measuring job uploaded as hostile.

Nothing in this repo posts to any PennyLane repository.

## Tests

```sh
uv run pytest                                         # fast, no Docker
uv run pytest -m docker                               # against the real image and stacks, in minutes
uv run ruff check . && uv run ruff format --check .
```
