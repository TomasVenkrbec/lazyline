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
formatting, and [markdownlint](https://github.com/DavidAnson/markdownlint)
for Markdown files. Pre-commit hooks run automatically if you install them:

```bash
uv run pre-commit install
```

To run manually:

```bash
uv run ruff check --fix src/ tests/ benchmarks/
uv run ruff format src/ tests/ benchmarks/
npx markdownlint-cli2 '**/*.md'
```

## Type checking

```bash
uv run ty check src/
```

## Pull requests

- Keep changes focused — one feature or fix per PR.
- Add tests for new functionality.
- Run the full check suite before submitting:

  ```bash
  uv run pytest
  uv run ruff check --fix src/ tests/ benchmarks/
  uv run ruff format src/ tests/ benchmarks/
  uv run ty check src/
  npx markdownlint-cli2 '**/*.md'
  ```

- The CI pipeline runs tests across Python 3.10–3.14, lint, and
  type checking. PRs must pass all checks.

## AI-assisted contributions

AI-assisted contributions are welcome. If you use AI tools (LLMs, code
generators, etc.) to help write code, tests, or documentation, that is
perfectly fine — the same quality bar applies to all contributions
regardless of how they were produced.

Guidelines:

- **You own it.** You are responsible for every line you submit. Review,
  understand, and test AI-generated code before opening a PR.
- **Disclose when relevant.** If a substantial portion of your
  contribution was AI-generated, mention it in the PR description
  (e.g., a `Co-Authored-By` trailer or a short note).
- **No bulk dumps.** Large, uncurated AI-generated PRs will be asked to
  be revised. Thoughtful, focused contributions are always preferred.

## Project structure

- `src/lazyline/` — source code (src layout)
- `tests/` — test suite
- `benchmarks/` — overhead measurement suite
