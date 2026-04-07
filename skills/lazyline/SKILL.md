---
name: lazyline
description: "Profile Python code with lazyline to find line-level bottlenecks and optimize iteratively. Use when analyzing performance, finding slow code, profiling functions, or optimizing Python."
argument-hint: "<scope> -- <command>"
---

# Lazyline: Line-Level Python Profiling

Profile Python code to find exactly which lines are slow
— the "lazy lines" — then optimize iteratively.

## Environment

Lazyline installed:
!`python -c "import lazyline; print('yes')" 2>/dev/null || echo "no"`

If not installed: `pip install lazyline` (or `uv add lazyline --dev`).
Requires Python 3.10+. The target package must be importable.

## Determine scope and command

```text
lazyline run [OPTIONS] SCOPE [SCOPE...] -- COMMAND [ARGS...]
```

The `--` separator between SCOPE and COMMAND is **mandatory**.
Options go BEFORE `--`, not after.

### Scope (what to profile)

Scope is what you'd write after `import` — the dotted
module path, or a `.py` file path. Multiple scopes are
space-separated.

```text
lazyline run my_package -- ...              # import my_package
lazyline run org.pipeline.transforms -- ... # import org.pipeline.transforms
lazyline run utils.py -- ...                # single file
lazyline run utils.py my_package -- ...     # multiple targets
```

Do NOT use filesystem paths like `src/my_package` or
`org/pipeline/transforms` — use the dotted import name.

To profile test code itself (helpers, fixtures), use the test
file as scope:

```bash
lazyline run --top 10 --output before.json tests/test_slow.py -- pytest tests/test_slow.py
```

### Command (what exercises the code)

| You want to... | Command |
| --- | --- |
| Run tests | `pytest tests/` |
| Run a script | `python script.py` |
| Run a CLI tool | `my-tool run-all` |

If the user provided arguments
(e.g., `/lazyline my_package -- pytest tests/`),
use them directly:

```bash
lazyline run $ARGUMENTS
```

Otherwise, ask the user what code to profile
and what command exercises it.

## Run the profiler

Save the baseline and start with a focused overview:

```bash
lazyline run --top 10 --quiet --output before.json my_package -- pytest tests/
```

Options (place BEFORE `--`):

- `--top N` — show only the N slowest functions
- `--filter "pattern"` — only functions matching fnmatch pattern(s), comma-separated
- `--summary` — summary table only, skip per-line detail
- `--quiet` — suppress discovery/registration messages
- `--output file.json` — save results for later with `lazyline show`
- `--memory` — track memory allocations (adds 20x+ overhead, use sparingly)

## Interpret results

### Summary table

Functions ranked by total time. If one function dominates
>50% of total, that is your target. Wrapper/orchestrator
functions (`<module>`, `main()`) include child function time
— look past these to find actual bottleneck functions.

```text
Function                                     |Total (ms)| % Total|   Calls
my_package.cleanup.deduplicate               |  3831.50 |   91.6%|       1
my_package.io.read_csv                       |   341.20 |    8.2%|       1
my_package.cleanup.normalize                 |    10.50 |    0.3%|   10000
```

### Per-line detail

Each function shows line-by-line timing. The **lazy line**
— the single slowest line — has the highest `% Func`:

```text
my_package.cleanup.deduplicate (cleanup.py:10)
3831.5ms total | 1 call | 3831.500ms/call

  Line |    Hits | Time (ms) |  % Func |Source
    10 |         |           |         |def deduplicate(records):
    11 |       1 |      0.10 |    0.0% |    seen = []
    12 |       1 |      0.00 |    0.0% |    result = []
    13 |   10000 |      3.40 |    0.1% |    for r in records:
    14 |   10000 |   3822.10 |   99.8% |        if r not in seen:  <-- lazy line
    15 |    9813 |      4.20 |    0.1% |            seen.append(r)
    16 |    9813 |      1.70 |    0.0% |            result.append(r)
    17 |       1 |      0.00 |    0.0% |    return result
```

Line 14 is 99.8% of the function — O(n²) membership check
on a list. Changing `seen` to a `set` fixes it.

## Optimize

1. Identify the top bottleneck function and its lazy line(s).
2. Understand *why* that line is slow.
3. Make **one focused change** — do not refactor broadly.
4. Ensure correctness: run existing tests or compare output.

## Re-profile and compare

```bash
lazyline run --top 10 --quiet --output after.json my_package -- pytest tests/
lazyline show before.json --top 5
lazyline show after.json --top 5
```

Check that the target function dropped in `% Total` and wall
time decreased. If still unsatisfactory, target the next
bottleneck. One change at a time.

## Anti-patterns

- **Do not** omit the `--` separator.
- **Do not** place lazyline options after `--`.
- **Do not** profile with `--memory` by default (20x+ overhead).
- **Do not** optimize multiple functions at once.
