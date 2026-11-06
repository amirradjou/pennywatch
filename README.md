# pennywatch

An independent performance monitor for PennyLane, Lightning and Catalyst.

## Getting started

```sh
uv sync
uv run python -c "import pennywatch"
```

## Development

```sh
uv run pytest     # tests
uv run ruff check . && uv run ruff format --check .     # lint + format
```
