# Overhead Benchmarks

Measures the cost of lazyline's deterministic line-level profiling
to help users understand when overhead matters and when it doesn't.

## Key Findings

1. **Overhead inflates both wall-clock runtime AND reported times.**
   Deterministic tracing fires a callback on every line execution.
   That callback cost is included in each line's measured time, so
   the numbers lazyline reports are higher than the true cost.

2. **Overhead depends on call frequency, not function count.**
   Registering 500 extra functions (never called) adds zero
   measurable overhead. What matters is how many line executions
   occur in profiled functions.

3. **Fast functions called many times are the worst case.**
   A tight loop calling a tiny function 1M times sees ~7x
   wall-clock overhead and ~9x reported-time inflation.
   Functions with meaningful per-call work see negligible
   overhead (~1.2x).

4. **Memory mode (`--memory`) adds significant overhead for
   allocation-heavy code.** `tracemalloc` hooks every `malloc`
   and `free`. Code doing many small allocations (e.g., JSON
   serialization) can see 20x+ additional wall-clock cost.
   Code with few allocations (hot numeric loops) sees ~38% extra.

## Results

### Profiling overhead (time only)

All times in seconds. 5 repeats (median).

- **Overhead** = wall-clock slowdown of the profiled script
- **Inflation** = how much lazyline's reported times exceed
  reality (callback cost is baked into each line's measurement)

| Workload | Baseline | Wall | Reported | Overhead | Inflation |
|----------|----------|------|----------|----------|-----------|
| hot_loop | 0.31     | 2.33 | 2.76     | 7.6x     | 9.0x      |
| moderate | 0.39     | 0.46 | 0.45     | 1.2x     | 1.2x      |
| cold     | 0.31     | 0.30 | 0.28     | 1.0x     | 0.9x      |

- **hot_loop**: tiny function called 1M times (worst case for
  deterministic tracing)
- **moderate**: JSON serialization of a medium dict, 5K calls
  (typical per-call cost ~0.08ms)
- **cold**: single `json.dumps` on a large structure — heavy
  lifting in C, profiler traces only a few Python lines (best
  case)

### Memory mode (`--memory`) additional cost

Compared to profiling with time only:

| Workload | Time Only | + Memory | Extra    |
|----------|-----------|----------|----------|
| hot_loop | 2.33      | 3.25     | +39%     |
| moderate | 0.46      | 11.55    | +2431%   |
| cold     | 0.30      | 6.42     | +2009%   |

`tracemalloc` hooks every `malloc`/`free` call. Code that does
many small allocations (JSON string building, list creation)
pays heavily. Hot numeric loops with few allocations pay ~39%.

### Function count scaling

Measures whether registering additional (uncalled) functions
affects overhead for the hot_loop workload.

| Padding Functions | Wall (s) | Reported (s) | Overhead |
|-------------------|----------|--------------|----------|
| 0                 | 2.25     | 2.66         | 7.3x     |
| 10                | 2.23     | 2.61         | 7.3x     |
| 100               | 2.26     | 2.70         | 7.4x     |
| 500               | 2.42     | 2.89         | 7.9x     |

No measurable effect. The cost comes entirely from per-line
callbacks in functions that actually execute, not from how
many functions are registered.

### Environment

- Python 3.12.12
- Linux 5.15.0-140-generic (x86_64)
- CPU: Intel Xeon E5-2690 v4 @ 2.60GHz

## Interpretation

### When to trust the numbers

- **Relative rankings are reliable.** If lazyline says function A
  takes 10x more time than function B, that ratio holds even with
  inflated absolute times. Use lazyline to find *which* functions
  are slowest, not *how fast* they are.

- **Functions with ~0.1ms+ per-call cost are measured accurately.**
  The per-line callback overhead is negligible relative to real
  work (moderate workload: 1.2x overhead).

### When to be cautious

- **Hot inner loops with sub-microsecond work per iteration.**
  The callback overhead dominates. The function will appear much
  slower than it actually is. Lazyline warns when a function
  exceeds 1M total line hits.

- **Memory mode with allocation-heavy code.** `tracemalloc` hooks
  every allocation. JSON serialization, string formatting, and
  similar code will see massive slowdowns. Use `--memory` only
  when you need allocation data, not as a default.

## Reproducing

```bash
# Full run (5 repeats, ~3 minutes)
python benchmarks/overhead.py

# Quick smoke test (2 repeats, ~30 seconds)
python benchmarks/overhead.py --quick

# Export to JSON for comparison across versions
python benchmarks/overhead.py --json benchmarks/results.json

# Run benchmark tests
pytest benchmarks/test_overhead.py -v
```

Results vary by machine, Python version, and system load.
The ratios (overhead multipliers) are more stable than absolute
times. The `results.json` file is committed so that changes
across lazyline versions can be compared.
