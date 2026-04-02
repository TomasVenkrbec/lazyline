"""Overhead benchmark for lazyline's deterministic line-level profiling.

Measures wall-clock overhead, reported-time inflation, and memory-mode
cost across three workloads (hot loop, moderate, cold) and a function
count scaling test.

Run standalone::

    python benchmarks/overhead.py          # full run (5 repeats)
    python benchmarks/overhead.py --quick  # fast smoke test (2 repeats)
    python benchmarks/overhead.py --json results.json  # export to JSON
"""

from __future__ import annotations

import json as json_mod
import math
import platform
import random
import statistics
import time
import tracemalloc
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from line_profiler import LineProfiler

from lazyline.profiling import collect_results

# ---------------------------------------------------------------------------
# Workload functions — kept minimal, deterministic, no I/O
# ---------------------------------------------------------------------------

_RNG = random.Random(42)


def _hot_loop_inner(a: float, b: float) -> float:
    """Tiny function called millions of times — worst case for tracing."""
    return math.sqrt((a - b) ** 2)


def hot_loop(n: int = 1_000_000) -> float:
    """Call a tiny function n times in a tight loop."""
    total = 0.0
    for _ in range(n):
        total += _hot_loop_inner(_RNG.random(), _RNG.random())
    return total


def moderate_work(n: int = 5_000) -> list[str]:
    """JSON-serialize a medium dict n times — typical per-call cost."""
    data = {f"key_{i}": list(range(50)) for i in range(20)}
    results = []
    for _ in range(n):
        results.append(json_mod.dumps(data))
    return results


def cold_function() -> str:
    """Single call where heavy lifting is in C — best case for tracing.

    ``json.dumps`` on a large pre-built structure does almost all work
    in the C accelerator. The profiler traces only a few Python lines,
    so overhead should be negligible.
    """
    data = {f"k{i}": list(range(500)) for i in range(5_000)}
    return json_mod.dumps(data)


# Workloads registry — each entry is (name, callable, functions_to_profile)
WORKLOADS = [
    ("hot_loop", hot_loop, [hot_loop, _hot_loop_inner]),
    ("moderate", moderate_work, [moderate_work]),
    ("cold", cold_function, [cold_function]),
]


# ---------------------------------------------------------------------------
# Padding functions for the scaling test
# ---------------------------------------------------------------------------


def _generate_padding_module(n: int) -> types.ModuleType:
    """Create a synthetic module with n trivial functions."""
    mod = types.ModuleType(f"_padding_{n}")
    for i in range(n):
        fn = _make_padding_fn(i)
        fn.__module__ = mod.__name__
        setattr(mod, f"pad_{i}", fn)
    return mod


def _make_padding_fn(i: int):
    """Create a single padding function with unique bytecode."""

    def fn():
        return i

    fn.__name__ = f"pad_{i}"
    fn.__qualname__ = f"pad_{i}"
    return fn


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class WorkloadResult:
    """Results for a single workload across all modes."""

    name: str
    baseline_s: float
    profiled_wall_s: float
    profiled_reported_s: float
    memory_wall_s: float
    memory_reported_s: float
    wall_overhead: float  # profiled_wall / baseline
    reported_inflation: float  # profiled_reported / baseline
    memory_extra_pct: float  # (memory_wall - profiled_wall) / profiled_wall


@dataclass
class ScalingResult:
    """Results for one padding level in the function count scaling test."""

    padding_count: int
    wall_s: float
    reported_s: float
    wall_overhead: float


def _cpu_model() -> str:
    """Read CPU model name from /proc/cpuinfo, or return 'unknown'."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


@dataclass
class BenchmarkReport:
    """Full benchmark output."""

    python_version: str = field(default_factory=platform.python_version)
    platform_info: str = field(default_factory=platform.platform)
    cpu: str = field(default_factory=_cpu_model)
    repeats: int = 0
    workloads: list[WorkloadResult] = field(default_factory=list)
    scaling: list[ScalingResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def measure_baseline(fn, repeats: int) -> float:
    """Return median wall-clock seconds for fn() without profiling."""
    times = []
    for _ in range(repeats):
        _RNG.seed(42)
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def measure_profiled(
    fn, funcs_to_profile: list, repeats: int, *, memory: bool = False
) -> tuple[float, float]:
    """Return (median_wall_seconds, median_reported_seconds).

    Parameters
    ----------
    fn
        The workload callable.
    funcs_to_profile
        Functions to register with the profiler.
    repeats
        Number of measurement repetitions.
    memory
        Whether to enable tracemalloc alongside profiling.
    """
    wall_times = []
    reported_times = []

    for _ in range(repeats):
        _RNG.seed(42)
        profiler = LineProfiler()
        for f in funcs_to_profile:
            profiler.add_function(f)

        if memory:
            tracemalloc.start()

        t0 = time.perf_counter()
        profiler.enable_by_count()
        fn()
        profiler.disable_by_count()
        wall = time.perf_counter() - t0

        if memory:
            tracemalloc.stop()

        results = collect_results(profiler)
        reported = sum(fp.total_time for fp in results)

        wall_times.append(wall)
        reported_times.append(reported)

    return statistics.median(wall_times), statistics.median(reported_times)


def measure_scaling(
    padding_counts: list[int],
    repeats: int,
    baseline: float,
    *,
    hot_loop_n: int = 1_000_000,
) -> list[ScalingResult]:
    """Measure hot_loop overhead with varying numbers of padding functions.

    Parameters
    ----------
    padding_counts
        List of padding function counts to test.
    repeats
        Number of repetitions per measurement.
    baseline
        Baseline wall-clock time (same hot_loop_n, no profiling).
    hot_loop_n
        Iteration count passed to ``hot_loop`` — must match the
        baseline measurement.
    """
    results = []
    for n in padding_counts:
        pad_mod = _generate_padding_module(n)

        wall_times = []
        reported_times = []
        for _ in range(repeats):
            _RNG.seed(42)
            p = LineProfiler()
            p.add_function(hot_loop)
            p.add_function(_hot_loop_inner)
            p.add_module(pad_mod)

            t0 = time.perf_counter()
            p.enable_by_count()
            hot_loop(hot_loop_n)
            p.disable_by_count()
            wall = time.perf_counter() - t0

            r = collect_results(p)
            reported = sum(fp.total_time for fp in r)
            wall_times.append(wall)
            reported_times.append(reported)

        med_wall = statistics.median(wall_times)
        med_reported = statistics.median(reported_times)
        results.append(
            ScalingResult(
                padding_count=n,
                wall_s=med_wall,
                reported_s=med_reported,
                wall_overhead=med_wall / baseline if baseline > 0 else 0,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Run all benchmarks
# ---------------------------------------------------------------------------


def run_benchmarks(repeats: int = 5, quick: bool = False) -> BenchmarkReport:
    """Execute all benchmarks and return structured results.

    Parameters
    ----------
    repeats
        Number of repetitions per measurement (median is taken).
    quick
        If True, reduce workload sizes for faster execution.
    """
    report = BenchmarkReport(repeats=repeats)

    # Adjust workload sizes for quick mode
    if quick:
        workloads = [
            ("hot_loop", lambda: hot_loop(100_000), [hot_loop, _hot_loop_inner]),
            ("moderate", lambda: moderate_work(500), [moderate_work]),
            ("cold", cold_function, [cold_function]),
        ]
    else:
        workloads = WORKLOADS

    for name, fn, funcs in workloads:
        baseline = measure_baseline(fn, repeats)
        prof_wall, prof_reported = measure_profiled(fn, funcs, repeats)
        mem_wall, mem_reported = measure_profiled(fn, funcs, repeats, memory=True)

        wall_oh = prof_wall / baseline if baseline > 0 else 0
        rep_infl = prof_reported / baseline if baseline > 0 else 0
        mem_extra = (mem_wall - prof_wall) / prof_wall if prof_wall > 0 else 0

        report.workloads.append(
            WorkloadResult(
                name=name,
                baseline_s=baseline,
                profiled_wall_s=prof_wall,
                profiled_reported_s=prof_reported,
                memory_wall_s=mem_wall,
                memory_reported_s=mem_reported,
                wall_overhead=wall_oh,
                reported_inflation=rep_infl,
                memory_extra_pct=mem_extra * 100,
            )
        )

    # Function count scaling test (hot_loop only, same iteration count)
    hot_loop_n = 100_000 if quick else 1_000_000
    baseline_for_scaling = report.workloads[0].baseline_s
    padding_counts = [0, 10, 100, 500] if not quick else [0, 10, 100]
    report.scaling = measure_scaling(
        padding_counts, repeats, baseline_for_scaling, hot_loop_n=hot_loop_n
    )

    return report


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def print_report(report: BenchmarkReport) -> None:
    """Print a human-readable summary of benchmark results."""
    print(f"\nPython {report.python_version} on {report.platform_info}")
    print(f"CPU: {report.cpu}")
    print(f"Repeats per measurement: {report.repeats}")

    # Table 1: Profiling overhead (time only)
    print("\n--- Profiling overhead (time only) ---")
    print("  Overhead = wall-clock slowdown of the profiled script")
    print("  Inflation = how much lazyline's reported times exceed reality\n")
    hdr1 = (
        f"{'Workload':<12} {'Baseline':>10} {'Wall':>10} "
        f"{'Reported':>10} {'Overhead':>9} {'Inflation':>10}"
    )
    print(hdr1)
    print("-" * len(hdr1))
    for w in report.workloads:
        print(
            f"{w.name:<12} {w.baseline_s:>10.4f} "
            f"{w.profiled_wall_s:>10.4f} "
            f"{w.profiled_reported_s:>10.4f} "
            f"{w.wall_overhead:>8.1f}x "
            f"{w.reported_inflation:>9.1f}x"
        )

    # Table 2: Memory mode additional cost
    print("\n--- Memory mode (--memory) additional cost ---\n")
    hdr2 = f"{'Workload':<12} {'Time Only':>10} {'+ Memory':>10} {'Extra':>10}"
    print(hdr2)
    print("-" * len(hdr2))
    for w in report.workloads:
        print(
            f"{w.name:<12} {w.profiled_wall_s:>10.4f} "
            f"{w.memory_wall_s:>10.4f} "
            f"{w.memory_extra_pct:>+9.1f}%"
        )

    # Table 3: Function count scaling
    bl = report.workloads[0].baseline_s
    print(f"\n--- Function count scaling (hot_loop, baseline={bl:.4f}s) ---\n")
    hdr3 = f"{'Padding Fns':>12} {'Wall (s)':>10} {'Reported (s)':>13} {'Overhead':>9}"
    print(hdr3)
    print("-" * len(hdr3))
    for s in report.scaling:
        print(
            f"{s.padding_count:>12} {s.wall_s:>10.4f} "
            f"{s.reported_s:>13.4f} {s.wall_overhead:>8.1f}x"
        )
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

app = typer.Typer(no_args_is_help=False, pretty_exceptions_show_locals=False)


@app.command()
def main(
    quick: Annotated[
        bool, typer.Option("--quick", help="Reduce workload sizes for fast runs")
    ] = False,
    repeats: Annotated[int, typer.Option(help="Repetitions per measurement")] = 5,
    json_output: Annotated[
        Path | None, typer.Option("--json", help="Export results to JSON file")
    ] = None,
) -> None:
    """Run lazyline overhead benchmarks."""
    if quick and repeats == 5:
        repeats = 2

    report = run_benchmarks(repeats=repeats, quick=quick)
    print_report(report)

    if json_output is not None:
        json_output.write_text(json_mod.dumps(asdict(report), indent=2))
        print(f"Results written to {json_output}")


if __name__ == "__main__":
    app()
