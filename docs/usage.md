# Usage Guide

[← Back to README](../README.md)

## CLI Reference

### `lazyline run`

```text
lazyline run [OPTIONS] SCOPE [SCOPE...] -- COMMAND [ARGS...]
```

Profile a command, instrumenting all functions in the given scope(s).
The `--` separator between SCOPE and COMMAND is required.

| Option | Description |
| -------- | ------------- |
| `--top N` / `-n N` | Show only the N slowest functions |
| `--memory` / `--no-memory` | Enable tracemalloc memory tracking |
| `--output FILE` / `-o FILE` | Export results to JSON (`-` for stdout) |
| `--compact/--full` | Collapse un-hit lines (default) or show all |
| `--summary` | Print only the summary table, no per-line detail |
| `--filter PATTERN` / `-f PATTERN` | Only show functions matching fnmatch pattern(s) (comma-separated). Bare patterns are auto-wrapped with `*...*` |
| `--exclude PATTERN` / `-e PATTERN` | Exclude functions matching fnmatch pattern(s) (comma-separated). Bare patterns are auto-wrapped with `*...*` |
| `--sort KEY` | Sort by: `time` (default), `calls`, `time-per-call`, `name` |
| `--quiet` / `-q` | Suppress discovery/registration stderr messages |
| `--unit UNIT` | Time display unit: `auto` (default), `s`, `ms`, `us`, or `ns` |
| `--no-subprocess` | Disable subprocess profiling injection |
| `--no-multiprocessing` | Disable multiprocessing worker profiling |

Options can appear before or after SCOPE:

```bash
lazyline run --top 5 my_package -- pytest tests/    # options before scope
lazyline run my_package --top 5 -- pytest tests/    # options after scope
```

Multiple scopes can be profiled in a single run:

```bash
lazyline run utils.py my_package -- python script.py
```

### `lazyline show`

```text
lazyline show FILE [--top N] [--compact/--full] [--summary] [--filter PATTERN] [--exclude PATTERN] [--sort KEY] [--unit UNIT]
```

Display profiling results from a previously saved JSON file. Accepts the
same display options as `lazyline run` (except `--memory`, `--output`,
`--no-subprocess`, and `--no-multiprocessing`).

### `lazyline --version`

Print the installed version and exit.

## Scope

Unlike tools like cProfile that profile everything, lazyline focuses on
specific code you choose — this keeps output clean and overhead low.

SCOPE tells lazyline which package or module to profile. It
accepts three formats:

| Format | Example | What it does |
| -------- | --------- | -------------- |
| Dotted module path | `my_package` | Imports and walks all submodules |
| Directory path | `my_package/utils` | Converted to dotted path, then walked |
| Single module | `json` | Imports that module only (+ submodules) |
| Single `.py` file | `utils.py` | Imports the file directly (no `sys.path` needed) |

Lazyline discovers modules via `pkgutil.walk_packages()` for
regular packages and filesystem scanning for implicit namespace
packages (directories without `__init__.py`). Every Python
function found is registered. C extension functions are silently
skipped.

## Commands

COMMAND is what lazyline executes under profiling. Supported forms:

| Form | Example |
| ------ | --------- |
| Bare module name | `pytest tests/` |
| Console script (incl. hyphens) | `my-tool run-all` |
| `python -m module` | `python -m pytest -q` |
| Script file | `python script.py` |
| Inline code | `python -c "import json; json.dumps(1)"` |

Hyphenated console scripts (e.g., `my-tool`) are resolved
via `importlib.metadata` entry points automatically.

## Examples

Profile a package while running its test suite:

```bash
lazyline run --top 10 my_package -- pytest tests/
```

Profile a CLI tool (console scripts with hyphens work too):

```bash
lazyline run my_package.cli -- my-tool run-all
```

Profile with memory tracking:

```bash
lazyline run --memory my_package -- pytest tests/ -q
```

Export results for later analysis:

```bash
lazyline run --output results.json my_package -- pytest -q
lazyline show results.json --top 10
```

Filter to specific functions (bare patterns are auto-wrapped with `*...*`):

```bash
lazyline run --filter "extract,match" my_package -- pytest tests/
lazyline run --exclude "encoder" my_package -- pytest tests/
```

Sort results by calls or name:

```bash
lazyline run --sort calls my_package -- pytest tests/
```

Profile a single `.py` file or multiple scopes in one run:

```bash
lazyline run utils.py -- python script.py
lazyline run utils.py my_package -- python evaluate.py
```

Profile any importable package — even stdlib:

```bash
lazyline run json -- python -c "import json; json.dumps([1, 2, 3])"
```

## Output Format

Lazyline prints a results header, a summary table, and per-line detail.

### Results header

Scope, coverage, total time, wall-clock time, and display unit:

```text
==========================================================================================
  🔥 Lazyline results for pkg
  2 of 15 functions called | Total: 1.4690s | Wall time: 1.5000s | Unit: s (auto)
```

### Summary table

All profiled functions ranked by total time:

```text
Function                                        │Total (s)│ % Total│   Calls│Time/Call (s)
------------------------------------------------------------------------------------------
pkg.module.slow_func                            │   1.2345│   84.0%│     100│     0.012345
pkg.module.helper                               │   0.2345│   16.0%│    1000│     0.000234
------------------------------------------------------------------------------------------
Total                                           │   1.4690
```

### Per-line detail

Source code with timing for each function:

```text
pkg.module.slow_func (pkg/module.py:42)
1.2345s total │ 100 calls │ 0.012345s/call

  Line │    Hits │Time (s) │  Time/Hit (s) │  % Func │Source
------------------------------------------------------------------------------------------
    42 │         │         │               │         │    def slow_func(data):
    43 │     100 │0.000100 │      0.000001 │    0.0% │        result = []
    44 │  100000 │1.234000 │      0.000012 │  100.0% │        for item in data:
    45 │  100000 │0.000400 │      0.000000 │    0.0% │            result.append(item)
    46 │     100 │0.000000 │      0.000000 │    0.0% │        return result
```

### Terminal rendering

On terminals, source code is syntax-highlighted (monokai theme) and
faint `│` column separators appear between numeric columns for easier
visual tracking across wide tables. Piped/file output uses plain text
with no ANSI formatting.

### Memory column

With `--memory`, an additional `Net Mem` column shows per-line
net memory allocation delta (bytes allocated minus freed).
