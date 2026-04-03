# Lazyline

[![PyPI version](https://img.shields.io/pypi/v/lazyline)](https://pypi.org/project/lazyline/)
[![Python versions](https://img.shields.io/pypi/pyversions/lazyline)](https://pypi.org/project/lazyline/)
[![Tests](https://github.com/TomasVenkrbec/lazyline/actions/workflows/ci.yml/badge.svg)](https://github.com/TomasVenkrbec/lazyline/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/TomasVenkrbec/lazyline/graph/badge.svg)](https://codecov.io/gh/TomasVenkrbec/lazyline)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Zero-config line-level profiler for Python packages.**
Point it at a package, give it a command, get a ranked line-by-line breakdown.
No `@profile` decorators. No code changes. No guessing.

## Why Lazyline?

### The problem

Finding line-level bottlenecks in a Python package typically means
decorating suspect functions with `@profile`, running `kernprof`,
reading the output, removing the decorators, and repeating until
you find the real culprit. If you guess wrong, you waste a cycle.

```bash
# Without lazyline — manual, iterative workflow:
#  1. Guess which functions might be slow
#  2. Add @profile decorators to each one
#  3. Run: LINE_PROFILE=1 python script.py or kernprof -lv script.py.
#  4. Read output, realize the bottleneck is elsewhere
#  5. Remove decorators, add new ones, go to step 3
#  6. Clean up all decorators when done
```

Lazyline eliminates this loop. Point it at a package, give it a
command, and every function is profiled automatically:

```bash
# With lazyline — one command, done:
lazyline run my_package -- pytest tests/
```

No decorators. No code changes. No guessing.

### What lazyline adds over raw line_profiler

Lazyline wraps `line_profiler` and adds everything needed to go
from "I want to profile this package" to "here are the bottlenecks"
in a single command:

#### Zero-config profiling

- No `@profile` decorators — every function in the target scope
  is discovered and instrumented automatically
- Automatic module and namespace package discovery (point at a
  package name, directory, or `.py` file)
- `lru_cache`, C-extension wrappers, and callable wrapper instances
  auto-unwrapped

#### Subprocess and worker profiling

- Child Python processes (e.g., Celery workers, Airflow tasks)
  profiled via `sitecustomize.py` injection — no configuration needed
- `concurrent.futures.ProcessPoolExecutor` and `multiprocessing.Pool`
  workers profiled with per-worker instances and merged results

#### Rich terminal output

- Syntax-highlighted source code (Pygments, monokai theme)
- Adaptive column widths that fit your terminal
- Compact mode (default) collapses un-hit lines
- Auto-scaling time units (s/ms/us/ns)

#### Analysis workflow

- `--top N`, `--filter`, `--summary` to focus on what matters
- JSON export/import for sharing and later analysis
- Optional `tracemalloc` memory tracking (`--memory`)

### When to use lazyline

Use lazyline when you need **exact, line-level timing** and want to
find bottlenecks without modifying code. It is especially useful
for profiling packages you don't own or can't easily change.

If you need **low-overhead production profiling**, a sampling
profiler like Scalene or py-spy is a better fit — they trade
line-level precision for significantly lower overhead.

## Quick Start

```bash
pip install lazyline

# Profile a package while running a script:
lazyline run my_package -- python evaluate.py --dataset "some_dataset"

# Profile a package while running its CLI tool:
lazyline run my_package -- my_package_cli run --verbose

# Profile a package while running its test suite:
lazyline run my_package -- pytest tests/

# Profile any importable package — no code changes needed:
lazyline run json -- python -c "import json; json.dumps([1, 2, 3])"
```

Lazyline discovers all modules in the given scope, instruments
(attaches timing to) every Python function, runs the command,
and prints a ranked breakdown:

```text
Discovered 12 module(s) in scope 'my_package'.
Registered 89 function(s) for profiling.

=================================================
  Lazyline results for my_package
  3 of 89 functions called | Total: 12.4451s | Wall time: 10.2300s | Unit: s

Summary

Function                                                                Total (s)  % Total    Calls Time/Call (s)
-----------------------------------------------------------------------------------------------------------------
my_package.process.transform                                          8.3172    66.8%      500      0.016634
my_package.io.load_data                                               3.1245    25.1%        1      3.124500
my_package.utils.normalize                                            1.0034     8.1%    50000      0.000020
...
-----------------------------------------------------------------------------------------------------------------
Total                                                                     12.4451

Functions

my_package.process.transform (.../process.py:42)
8.3172s total | 500 calls | 0.016634s/call

Line     Hits   Time (s)  Time/Hit (s)     % Func Source
----------------------------------------------------------------------------------------
  42                                                  def transform(data):
  43      500   0.031200      0.000062       0.4%         result = []
  44   500000   7.982100      0.000016      96.0%         for row in data:
  45   500000   0.301200      0.000001       3.6%             result.append(row)
  46      500   0.002700      0.000005       0.0%         return result
```

## Installation

```bash
pip install lazyline

# With syntax highlighting (recommended):
pip install lazyline[color]
```

Requires Python 3.10+. The target package must be importable
(installed or on `sys.path`) in the same environment. The `[color]`
extra installs Pygments for syntax-highlighted source in terminal
output (falls back to plain text if not installed).

## Comparison with Alternatives

| Tool | Granularity | Method | Code changes? | Subprocess profiling |
|------|-------------|--------|---------------|----------------------|
| **lazyline** | Per-line | Deterministic | None | Automatic |
| `kernprof` / `line_profiler` | Per-line | Deterministic | `@profile` decorators | No |
| `cProfile` | Per-function | Deterministic | None | No |
| `Scalene` | Per-line | Sampling | None | Yes |
| `py-spy` | Per-line (sampled) | Sampling | None (attach) | Yes (follow-children) |

**Unique:** Lazyline is the only line-level profiler that
automatically profiles `ProcessPoolExecutor`, `multiprocessing.Pool`,
and child Python processes (e.g., Celery workers, Airflow tasks)
without any configuration.

**Deterministic vs sampling:** Deterministic tracing (lazyline,
line_profiler, cProfile) fires a callback on every line or function
call, measuring exact execution counts and times. Sampling profilers
(Scalene, py-spy) periodically interrupt the program and record
where it is — much lower overhead, but statistical approximations
rather than exact counts.

Lazyline's deterministic approach means **relative rankings are
reliable** (if function A appears 10x slower than B, that ratio
holds), but **absolute times are inflated** by tracing overhead.
See [Overhead and Limitations](#overhead-and-limitations) for
details.

## Usage

### `lazyline run`

```text
lazyline run [OPTIONS] SCOPE [SCOPE...] [--] COMMAND [ARGS...]
```

Profile a command, instrumenting all functions in the given scope(s).

| Option | Description |
|--------|-------------|
| `--top N` / `-n N` | Show only the N slowest functions |
| `--memory` | Enable tracemalloc memory tracking |
| `--output FILE` / `-o FILE` | Export results to JSON (`-` for stdout) |
| `--compact/--full` | Collapse un-hit lines (default) or show all |
| `--summary` | Print only the summary table, no per-line detail |
| `--filter PATTERN` / `-f PATTERN` | Only show functions matching fnmatch pattern(s) (comma-separated) |
| `--quiet` / `-q` | Suppress discovery/registration stderr messages |
| `--unit UNIT` | Time display unit: `auto` (default), `s`, `ms`, `us`, or `ns` |

Options can appear before or after SCOPE. When placed after SCOPE,
a `--` separator before COMMAND is required:

```bash
lazyline run --top 5 my_package -- pytest tests/    # options before scope
lazyline run my_package --top 5 -- pytest tests/    # options after scope
```

Multiple scopes can be profiled in a single run (requires `--`):

```bash
lazyline run utils.py my_package -- python script.py
```

### `lazyline show`

```text
lazyline show FILE [--top N] [--compact/--full] [--summary] [--filter PATTERN] [--unit UNIT]
```

Display profiling results from a previously saved JSON file.

### `lazyline --version`

Print the installed version and exit.

## Scope

Unlike tools like cProfile that profile everything, lazyline
focuses on specific code you choose — this keeps output clean
and overhead low.

SCOPE tells lazyline which package or module to profile. It
accepts three formats:

| Format | Example | What it does |
|--------|---------|--------------|
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
|------|---------|
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

Profile a CLI tool (console script with hyphens works too):

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

Filter to specific functions (supports comma-separated patterns):

```bash
lazyline run --filter "*extract*,*match*" my_package -- pytest tests/
```

Profile a single `.py` file or multiple scopes in one run:

```bash
lazyline run utils.py -- python script.py
lazyline run utils.py my_package -- python evaluate.py
```

## Output Format

Lazyline prints a results header, a summary table, and per-line detail:

**Results header** — scope, coverage, total time, wall-clock time, and display unit:

```text
=================================================
  Lazyline results for pkg
  2 of 15 functions called | Total: 1.4690s | Wall time: 1.2300s | Unit: s
```

**Summary table** — all profiled functions ranked by total time:

```text
Function                                                                Total (s)  % Total    Calls Time/Call (s)
-----------------------------------------------------------------------------------------------------------------
pkg.module.slow_func                                                       1.2345    82.1%      100      0.012345
pkg.module.helper                                                          0.2345    15.6%     1000      0.000235
...
```

**Per-line detail** — source code with timing for each function:

```text
pkg.module.slow_func (pkg/module.py:42)
1.2345s total | 100 calls | 0.012345s/call

Line     Hits   Time (s)  Time/Hit (s)     % Func Source
----------------------------------------------------------------------------------------
  42                                                  def slow_func(data):
  43      100   0.000100      0.000001       0.0%         result = []
  44   100000   1.234000      0.000012      99.9%         for item in data:
  45   100000   0.000400      0.000000       0.0%             result.append(item)
  46      100   0.000000      0.000000       0.0%         return result
```

On terminals, source code is syntax-highlighted (monokai theme) and
faint `│` column separators appear between numeric columns for easier
visual tracking across wide tables. Piped/file output uses plain text
with no ANSI formatting.

With `--memory`, an additional `Net Mem` column shows per-line
net memory allocation delta (bytes allocated minus freed).

## How It Works

1. **Discovery** — imports the target scope and walks all
   submodules via `pkgutil.walk_packages()`, supplemented with
   filesystem scanning for implicit namespace packages.
2. **Registration** — registers every Python function with
   `line_profiler`'s `LineProfiler.add_module()`. C extensions
   are skipped; `lru_cache` wrappers and callable wrapper instances
   are auto-unwrapped.
3. **Execution** — runs the user's command with profiling
   enabled. `line_profiler` uses `sys.monitoring` (Python 3.12+)
   or `sys.settrace` for deterministic per-line tracing.
4. **Collection** — extracts per-line timing data, filters
   out stdlib wrapper leaks via scope path matching.
5. **Memory** (optional) — `tracemalloc` takes before/after
   snapshots and computes per-line net allocation deltas.
6. **Multiprocessing** — `concurrent.futures.ProcessPoolExecutor`
   and `multiprocessing.Pool` worker processes are automatically
   profiled with per-worker `LineProfiler` instances, stats
   merged after execution.
7. **Subprocesses** — if the command spawns child Python
   processes (e.g., Celery workers, Airflow tasks), lazyline injects
   a `sitecustomize.py` bootstrap via `PYTHONPATH` so child
   interpreters profile the same scope automatically.

## Overhead and Limitations

Lazyline uses **deterministic tracing** (exact measurement of every
line, as opposed to sampling which checks periodically). This fires
a callback on every line execution, which has important implications:

**Overhead inflates both wall-clock runtime and reported times.**
The callback cost is included in each line's measured time. For
functions with meaningful per-call work (>0.1ms), overhead is
negligible (~1.2x). For tight loops calling tiny functions millions
of times, overhead can be ~7x.

**Relative rankings are reliable.** If function A appears 10x
slower than function B, that ratio holds regardless of overhead.
Use lazyline to find *which* functions are slowest, not to measure
*absolute* execution time.

**Memory measurements are not inflated** by line-profiler tracing.
`tracemalloc` hooks the memory allocator separately. However,
allocation-heavy code (JSON serialization, string formatting) may
see significant wall-clock slowdown from tracemalloc's per-alloc
hooks. Use `--memory` only when you need allocation data.

Lazyline warns when a function exceeds 1M total line hits, as
reported times for such functions are unreliable.

Other limitations:

- `concurrent.futures.ProcessPoolExecutor` and `multiprocessing.Pool`
  workers are profiled automatically (requires `fork` start method —
  default on Linux; `spawn`/`forkserver` are not supported). Direct
  `multiprocessing.Process` usage is not covered.
- Child processes started with `python -S` skip `sitecustomize.py`
  loading, so subprocess profiling is silently disabled.
- C extension functions are skipped (no Python bytecode to trace).
- `tracemalloc` shows net allocation delta only — transient
  allocations (alloc + free within the run) appear as ~0.
- `tracemalloc` adds ~30% memory overhead for its own bookkeeping.

See [`benchmarks/README.md`](benchmarks/README.md) for detailed
overhead measurements and methodology.
