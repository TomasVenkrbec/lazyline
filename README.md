# Lazyline

[![PyPI version](https://img.shields.io/pypi/v/lazyline)](https://pypi.org/project/lazyline/)
[![Python versions](https://img.shields.io/pypi/pyversions/lazyline)](https://pypi.org/project/lazyline/)
[![Tests](https://github.com/TomasVenkrbec/lazyline/actions/workflows/ci.yml/badge.svg)](https://github.com/TomasVenkrbec/lazyline/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/TomasVenkrbec/lazyline/graph/badge.svg)](https://codecov.io/gh/TomasVenkrbec/lazyline)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/TomasVenkrbec/lazyline/blob/main/LICENSE)

**Zero-config, deterministic, line-level Python profiler.**
No `@profile` decorators, no code changes — point it at a package or
script and go. Subprocesses and multiprocessing pools profiled
automatically. Find the lazy lines.

## Quick Start

```bash
pip install lazyline

# Profile a package while running its tests:
lazyline run my_package -- pytest tests/

# Profile a script:
lazyline run script.py -- python script.py
```

```text
Discovered 8 module(s) in scope 'my_package'.
Registered 42 function(s) for profiling.

==========================================================================================
  🔥 Lazyline results for my_package
  3 of 42 functions called | Total: 4.1832s | Wall time: 4.0100s | Unit: s (auto)

Summary

Function                                        │Total (s)│ % Total│   Calls│Time/Call (s)
------------------------------------------------------------------------------------------
my_package.cleanup.deduplicate                  │   3.8315│   91.6%│       1│     3.831500
my_package.io.read_csv                          │   0.3412│    8.2%│       1│     0.341200
my_package.cleanup.normalize                    │   0.0105│    0.3%│   10000│     0.000001
------------------------------------------------------------------------------------------
Total                                           │   4.1832

Functions

my_package.cleanup.deduplicate (.../cleanup.py:10)
3.8315s total │ 1 call │ 3.831500s/call

  Line │    Hits │ Time (s) │  Time/Hit (s) │  % Func │Source
------------------------------------------------------------------------------------------
    10 │         │          │               │         │def deduplicate(records):
    11 │       1 │ 0.000100 │      0.000100 │    0.0% │    seen = []
    12 │       1 │ 0.000000 │      0.000000 │    0.0% │    result = []
    13 │   10000 │ 0.003400 │      0.000000 │    0.1% │    for r in records:
    14 │   10000 │ 3.822100 │      0.000382 │   99.8% │        if r not in seen:
    15 │    9813 │ 0.004200 │      0.000000 │    0.1% │            seen.append(r)
    16 │    9813 │ 0.001700 │      0.000000 │    0.0% │            result.append(r)
    17 │       1 │ 0.000000 │      0.000000 │    0.0% │    return result
```

Line 14 burned 99.8% of `deduplicate` checking membership in a list
on every iteration — that's your lazy line. Change `seen` to a `set`
and it drops from O(n²) to O(n). Syntax highlighting makes hot lines
stand out immediately.

## Why Lazyline?

Lazyline wraps [line_profiler](https://github.com/pyutils/line_profiler)
and adds everything needed to go from "I want to profile this package"
to "here are the bottlenecks" in a single command:

- **Zero configuration** — point at a package name, directory, or
  `.py` file. Every function is discovered and instrumented
  automatically. No `@profile` decorators, no code changes — be lazy,
  let the tool do the work.

- **Subprocess and multiprocessing** — `ProcessPoolExecutor`,
  `multiprocessing.Pool`, and child Python processes (Celery workers,
  Airflow tasks) are profiled automatically. Results are merged
  into a single report.

- **Deterministic precision** — exact hit counts and timing for every
  line. "This line ran 47,382 times and took 3.2s." Sampling profilers
  give statistical approximations; lazyline gives facts. When you need
  to distinguish O(n) from O(n²), exact counts are the difference.

- **Focused scope, clean output** — you choose exactly which package
  to profile. Unlike tools that profile everything in your working
  directory, lazyline keeps output relevant and overhead contained.

## When to Use What

No tool is best for everything. Pick the right one for the job:

| You need... | Use | Why |
|-------------|-----|-----|
| Exact line-level timing across a package, no code changes | **lazyline** | Deterministic tracing with auto-discovery and subprocess support |
| Low-overhead profiling with memory, GPU, and AI suggestions | **[Scalene](https://github.com/plasma-umass/scalene)** | Sampling (~10-20% overhead), broad feature set, web UI |
| Attach to a running process in production | **[py-spy](https://github.com/benfred/py-spy)** | Out-of-process sampling, near-zero overhead, no restart needed |
| "Which function is slow?" with beautiful call trees | **[Pyinstrument](https://github.com/joerick/pyinstrument)** | Statistical profiler, tree output, low overhead |
| Line-level timing for specific functions you choose | **[kernprof](https://github.com/pyutils/line_profiler)** | Deterministic, but requires `@profile` decorators |
| Quick function-level triage, no install | **cProfile** | Stdlib, always available, function-level only |

### Feature comparison

| Feature | lazyline | kernprof | Scalene | py-spy | Pyinstrument | cProfile |
|---------|----------|----------|---------|--------|--------------|----------|
| Granularity | Line | Line | Line | Line | Function | Function |
| Method | Deterministic | Deterministic | Sampling | Sampling | Sampling | Deterministic |
| Code changes needed | None | `@profile` | None | None | None | None |
| Exact hit counts | Yes | Yes | No | No | No | Yes (fn-level) |
| Subprocess profiling | Automatic | No | Partial | Yes | No | No |
| Multiprocessing pools | Automatic | No | Partial | Yes | No | No |
| Memory profiling | Opt-in | No | Built-in | No | No | No |
| GPU profiling | No | No | Yes | No | No | No |
| Overhead | 1.2–7x | 1.2–7x | ~10–20% | ~0% | Low | Moderate |

**Lazyline trades overhead for precision.** Deterministic tracing fires
a callback on every line execution. For functions with real work (>0.1ms
per call), overhead is negligible (~1.2x). For tight loops calling tiny
functions millions of times, it can reach ~7x. Relative rankings are
always reliable — use lazyline to find *which* code is lazy, not to
measure *how fast* it runs. See
[benchmarks](https://github.com/TomasVenkrbec/lazyline/blob/main/benchmarks/README.md)
for detailed measurements.

## Usage

```bash
# Profile a package during its test suite
lazyline run my_package -- pytest tests/

# Profile while running a script
lazyline run my_package -- python evaluate.py

# Profile a CLI tool (hyphenated console scripts work too)
lazyline run my_package -- my-tool run-all

# Export results, view later
lazyline run -o results.json my_package -- pytest tests/
lazyline show results.json --top 10

# Multiple scopes in one run
lazyline run utils.py my_package -- python script.py
```

Requires Python 3.10+. The target package must be importable in the
same environment.

See the
[full usage guide](https://github.com/TomasVenkrbec/lazyline/blob/main/docs/usage.md)
for all CLI options, scope formats, command resolution, output details,
and more examples.

## Documentation

- **[Usage Guide][docs-usage]** — CLI reference, scope formats,
  output details
- **[How It Works][docs-how]** — architecture, overhead, limitations
- **[Benchmarks][docs-bench]** — overhead measurements and methodology
- **[Contributing][docs-contrib]** — development setup, tests,
  code style
- **[Changelog][docs-changelog]**

[docs-usage]: https://github.com/TomasVenkrbec/lazyline/blob/main/docs/usage.md
[docs-how]: https://github.com/TomasVenkrbec/lazyline/blob/main/docs/how-it-works.md
[docs-bench]: https://github.com/TomasVenkrbec/lazyline/blob/main/benchmarks/README.md
[docs-contrib]: https://github.com/TomasVenkrbec/lazyline/blob/main/CONTRIBUTING.md
[docs-changelog]: https://github.com/TomasVenkrbec/lazyline/blob/main/CHANGELOG.md

---

Found a problem with lazyline?
[Open an issue](https://github.com/TomasVenkrbec/lazyline/issues) and
tell us about it, or give the project a
[star](https://github.com/TomasVenkrbec/lazyline) if you found it useful.

## License

MIT — see [LICENSE](https://github.com/TomasVenkrbec/lazyline/blob/main/LICENSE).
