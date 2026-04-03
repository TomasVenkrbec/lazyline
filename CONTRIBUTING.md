# Contributing to Lazyline

Thank you for considering contributing to lazyline! This guide covers
everything you need to get started.

## Development setup

Lazyline uses [uv](https://docs.astral.sh/uv/) as its package manager.

```bash
# Clone the repository
git clone https://github.com/TomasVenkrbec/lazyline.git
cd lazyline

# Install dependencies (including dev tools)
uv sync
```

## Running tests

```bash
# Full test suite
uv run pytest

# Single test
uv run pytest tests/test_cli.py::test_version_flag

# Benchmarks only
uv run pytest benchmarks/
```

## Code style

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and
formatting. Pre-commit hooks run automatically if you install them:

```bash
uv run pre-commit install
```

To run manually:

```bash
uv run ruff check --fix src/ tests/ benchmarks/
uv run ruff format src/ tests/ benchmarks/
```

## Type checking

```bash
uv run ty check src/
```

## Pull requests

- Keep changes focused — one feature or fix per PR.
- Add tests for new functionality.
- Run `uv run pytest` and `uv run ruff check --fix` before submitting.
- The CI pipeline runs tests across Python 3.10--3.14, lint, and
  type checking. PRs must pass all checks.

## Project structure

- `src/lazyline/` — source code (src layout)
- `tests/` — test suite
- `benchmarks/` — overhead measurement suite
