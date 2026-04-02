"""Tests for the overhead benchmark — validates structure and sanity.

All tests use minimal iteration counts to run in under a second.
The actual benchmark (overhead.py) uses much larger workloads.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from benchmarks.overhead import (
    BenchmarkReport,
    ScalingResult,
    WorkloadResult,
    cold_function,
    hot_loop,
    measure_baseline,
    measure_profiled,
    measure_scaling,
    moderate_work,
    run_benchmarks,
)

# --- Workload sanity ---


def test_hot_loop_returns_float():
    assert isinstance(hot_loop(10), float)


def test_moderate_work_returns_list():
    result = moderate_work(3)
    assert len(result) == 3


def test_cold_function_returns_json():
    result = cold_function()
    assert isinstance(result, str)
    assert result.startswith("{")


# --- Measurement helpers ---


def test_measure_baseline_positive():
    assert measure_baseline(lambda: hot_loop(10), repeats=1) > 0


def test_measure_profiled_returns_two_floats():
    wall, reported = measure_profiled(lambda: hot_loop(10), [hot_loop], repeats=1)
    assert wall > 0
    assert reported >= 0


def test_measure_profiled_with_memory():
    wall, reported = measure_profiled(
        lambda: hot_loop(10), [hot_loop], repeats=1, memory=True
    )
    assert wall > 0


def test_measure_scaling_returns_results():
    baseline = measure_baseline(lambda: hot_loop(10), repeats=1)
    results = measure_scaling([0, 5], repeats=1, baseline=baseline, hot_loop_n=10)
    assert len(results) == 2
    assert all(isinstance(r, ScalingResult) for r in results)


# --- Full benchmark run ---


def test_run_benchmarks_quick():
    report = run_benchmarks(repeats=1, quick=True)
    assert isinstance(report, BenchmarkReport)
    assert len(report.workloads) == 3
    assert len(report.scaling) >= 2
    assert report.cpu != ""
    for w in report.workloads:
        assert isinstance(w, WorkloadResult)
        assert w.baseline_s > 0


def test_run_benchmarks_report_serializable():
    report = run_benchmarks(repeats=1, quick=True)
    data = json.loads(json.dumps(asdict(report)))
    assert "workloads" in data
    assert "scaling" in data
    assert "cpu" in data
    assert data["python_version"] == report.python_version
