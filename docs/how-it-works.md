# How It Works

[← Back to README](../README.md)

## Profiling Pipeline

Lazyline wraps [line_profiler](https://github.com/pyutils/line_profiler)
and orchestrates a seven-step pipeline:

### 1. Discovery

Imports the target scope and walks all submodules via
`pkgutil.walk_packages()`, supplemented with filesystem scanning
for implicit namespace packages (directories without `__init__.py`).

### 2. Registration

Registers every Python function with `line_profiler`'s
`LineProfiler.add_module()`. C extension functions are skipped
(no Python bytecode to trace). Wrappers like `lru_cache` and
callable wrapper instances are auto-unwrapped to reach the
underlying function.

### 3. Execution

Runs the user's command with profiling enabled. `line_profiler`
uses `sys.monitoring` (Python 3.12+) or `sys.settrace` (older
versions) for deterministic per-line tracing.

### 4. Collection

Extracts per-line timing data from `line_profiler` and filters
out stdlib wrapper leaks via scope path matching.

### 5. Memory tracking (optional)

When `--memory` is passed, `tracemalloc` takes before/after
snapshots and computes per-line net allocation deltas.

### 6. Multiprocessing

`concurrent.futures.ProcessPoolExecutor` and `multiprocessing.Pool`
worker processes are automatically profiled with per-worker
`LineProfiler` instances. Stats are merged after execution via
pickle files in a temporary directory.

### 7. Subprocess injection

If the command spawns child Python processes (e.g., Celery workers,
Airflow tasks), lazyline injects a `sitecustomize.py` bootstrap
via `PYTHONPATH` so child interpreters profile the same scope
automatically.

## Overhead and Limitations

### Deterministic vs. sampling

Deterministic tracing (lazyline, line_profiler, cProfile) fires a
callback on every line or function call, measuring exact execution
counts and times. Sampling profilers (Scalene, py-spy) periodically
interrupt the program and record where it is — much lower overhead,
but statistical approximations rather than exact counts.

### Overhead characteristics

Lazyline's deterministic approach means overhead depends on how many
line executions occur in profiled functions:

- **Functions with meaningful work (>0.1ms per call):** negligible
  overhead (~1.2x).
- **Tight loops calling tiny functions millions of times:** overhead
  can reach ~7x wall-clock and ~9x reported-time inflation.
- **Registering uncalled functions:** zero measurable overhead.
  The cost comes entirely from per-line callbacks in functions
  that actually execute.

**Relative rankings are always reliable.** If function A appears 10x
slower than function B, that ratio holds regardless of overhead. Use
lazyline to find *which* code is slowest, not to measure absolute
execution time.

Lazyline warns when a function exceeds 1M total line hits, as
reported times for such functions are unreliable.

### Memory mode overhead

`tracemalloc` hooks every `malloc` and `free` call. Code doing many
small allocations (JSON serialization, string formatting) can see
20x+ additional wall-clock cost. Code with few allocations (numeric
loops) sees ~39% extra. Use `--memory` only when you need allocation
data, not as a default.

**Memory measurements are not inflated** by line-profiler tracing.
`tracemalloc` hooks the memory allocator separately. However,
`tracemalloc` adds ~30% memory overhead for its own bookkeeping.

### Known limitations

- **Multiprocessing start method:** `ProcessPoolExecutor` and
  `multiprocessing.Pool` profiling requires the `fork` start
  method (default on Linux). `spawn` and `forkserver` are not
  supported. Direct `multiprocessing.Process` usage is not covered.
- **`python -S` flag:** child processes started with `python -S`
  skip `sitecustomize.py` loading, so subprocess profiling is
  silently disabled.
- **C extensions:** C extension functions are skipped — there is
  no Python bytecode to trace.
- **Transient allocations:** `tracemalloc` shows net allocation
  delta only. Allocations that are freed within the run appear
  as ~0.

See [benchmarks/README.md](../benchmarks/README.md) for detailed
overhead measurements, methodology, and instructions for
reproducing results.
