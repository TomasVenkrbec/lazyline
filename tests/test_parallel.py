"""Tests for multiprocessing worker profiling."""

from __future__ import annotations

import concurrent.futures.process
import multiprocessing.pool
import os
import shutil
import tempfile

import pytest
from line_profiler.line_profiler import LineStats

from lazyline.parallel import (
    _collect_worker_stats,
    _subtract_stats,
    merge_stats,
    profiling_hooks,
)

# ---------------------------------------------------------------------------
# profiling_hooks
# ---------------------------------------------------------------------------


def test_profiling_hooks_patches_and_restores():
    """Both stdlib worker functions are patched inside the CM and restored after."""
    orig_process = concurrent.futures.process._process_worker
    orig_pool = multiprocessing.pool.worker

    with profiling_hooks(["json"]):
        assert concurrent.futures.process._process_worker is not orig_process
        assert multiprocessing.pool.worker is not orig_pool

    assert concurrent.futures.process._process_worker is orig_process
    assert multiprocessing.pool.worker is orig_pool


def test_profiling_hooks_reentrancy_raises():
    """Nested profiling_hooks calls raise RuntimeError."""
    with profiling_hooks(["json"]), pytest.raises(RuntimeError, match="not reentrant"):  # noqa: SIM117
        with profiling_hooks(["json"]):
            pass  # pragma: no cover


# ---------------------------------------------------------------------------
# _collect_worker_stats
# ---------------------------------------------------------------------------


def test_collect_worker_stats_empty_dir():
    d = tempfile.mkdtemp()
    assert _collect_worker_stats(d) is None
    os.rmdir(d)


def test_collect_worker_stats_merges_files():
    d = tempfile.mkdtemp()
    s1 = LineStats({("f.py", 1, "foo"): [(1, 10, 5000)]}, 1e-9)
    s2 = LineStats({("f.py", 1, "foo"): [(1, 20, 3000)]}, 1e-9)
    s1.to_file(os.path.join(d, "100.pkl"))
    s2.to_file(os.path.join(d, "101.pkl"))

    merged = _collect_worker_stats(d)
    assert merged is not None
    assert merged.timings[("f.py", 1, "foo")] == [(1, 30, 8000)]
    shutil.rmtree(d)


def test_collect_worker_stats_single_file():
    d = tempfile.mkdtemp()
    s = LineStats({("g.py", 5, "bar"): [(5, 3, 900)]}, 1e-9)
    s.to_file(os.path.join(d, "200.pkl"))

    result = _collect_worker_stats(d)
    assert result is not None
    assert ("g.py", 5, "bar") in result.timings
    shutil.rmtree(d)


def test_collect_worker_stats_skips_corrupt_file():
    """One bad pickle doesn't discard stats from other workers."""
    d = tempfile.mkdtemp()
    good = LineStats({("f.py", 1, "foo"): [(1, 5, 100)]}, 1e-9)
    good.to_file(os.path.join(d, "100.pkl"))
    with open(os.path.join(d, "999.pkl"), "wb") as f:
        f.write(b"not a pickle")

    result = _collect_worker_stats(d)
    assert result is not None
    assert result.timings[("f.py", 1, "foo")] == [(1, 5, 100)]
    shutil.rmtree(d)


# ---------------------------------------------------------------------------
# merge_stats
# ---------------------------------------------------------------------------


def test_merge_stats_none_worker():
    parent = LineStats({("a.py", 1, "f"): [(1, 5, 100)]}, 1e-9)
    result = merge_stats(parent, None)
    assert result is parent


def test_merge_stats_combines():
    parent = LineStats({("a.py", 1, "f"): [(1, 5, 100)]}, 1e-9)
    worker = LineStats({("a.py", 1, "f"): [(1, 10, 200)]}, 1e-9)
    result = merge_stats(parent, worker)
    assert result.timings[("a.py", 1, "f")] == [(1, 15, 300)]


def test_merge_stats_disjoint_functions():
    parent = LineStats({("a.py", 1, "f"): [(1, 5, 100)]}, 1e-9)
    worker = LineStats({("b.py", 1, "g"): [(1, 10, 200)]}, 1e-9)
    result = merge_stats(parent, worker)
    assert ("a.py", 1, "f") in result.timings
    assert ("b.py", 1, "g") in result.timings


# ---------------------------------------------------------------------------
# Integration: full parallel profiling roundtrip
# ---------------------------------------------------------------------------


def _run_parallel_and_collect(invoke_fn):
    """Run a parallel function with profiling hooks and return results."""
    from lazyline.profiling import (
        build_scope_paths,
        collect_results,
        create_profiler,
        register_modules,
    )
    from tests import _parallel_fixture as mod

    modules = [mod]

    profiler = create_profiler()
    scope_paths = build_scope_paths(modules)
    register_modules(profiler, modules)

    module_names = [m.__name__ for m in modules]
    with profiling_hooks(module_names) as worker_holder:
        profiler.enable_by_count()
        try:
            invoke_fn(mod)
        finally:
            profiler.disable_by_count()

    stats = merge_stats(profiler.get_stats(), worker_holder.stats)
    return collect_results(stats, scope_paths=scope_paths)


def test_process_pool_executor_profiled():
    """ProcessPoolExecutor worker functions appear in profiling results."""
    results = _run_parallel_and_collect(
        lambda mod: mod.run_with_process_pool(list(range(20)))
    )
    func_names = [r.name for r in results]
    assert "slow_computation" in func_names

    sc = next(r for r in results if r.name == "slow_computation")
    assert sc.total_time > 0
    assert sc.call_count > 0


def test_multiprocessing_pool_profiled():
    """multiprocessing.Pool worker functions appear in profiling results."""
    results = _run_parallel_and_collect(
        lambda mod: mod.run_with_mp_pool(list(range(20)))
    )
    func_names = [r.name for r in results]
    assert "slow_computation" in func_names

    sc = next(r for r in results if r.name == "slow_computation")
    assert sc.total_time > 0
    assert sc.call_count > 0


def test_pool_terminate_still_collects_stats():
    """Pool.terminate() sends SIGTERM — stats are still collected via the handler."""
    import multiprocessing
    import time as time_mod

    from lazyline.profiling import create_profiler, register_modules
    from tests import _parallel_fixture as mod

    profiler = create_profiler()
    register_modules(profiler, [mod])
    module_names = [mod.__name__]

    with profiling_hooks(module_names) as worker_holder:
        # Create a pool manually so we can call terminate() explicitly
        # while workers are still busy.
        pool = multiprocessing.Pool(2)
        pool.map_async(mod.slow_computation, range(200))
        # Give workers a moment to start processing, then terminate.
        time_mod.sleep(0.05)
        pool.terminate()
        pool.join()

    assert worker_holder.stats is not None
    func_names = [k[2] for k in worker_holder.stats.timings]
    assert "slow_computation" in func_names


def test_pool_maxtasksperchild_profiled():
    """Worker replacement via maxtasksperchild still produces stats."""
    results = _run_parallel_and_collect(
        lambda mod: mod.run_with_mp_pool_maxtasks(list(range(20)))
    )
    func_names = [r.name for r in results]
    assert "slow_computation" in func_names

    sc = next(r for r in results if r.name == "slow_computation")
    assert sc.total_time > 0
    assert sc.call_count > 0


# ---------------------------------------------------------------------------
# _subtract_stats
# ---------------------------------------------------------------------------


class TestSubtractStats:
    def test_basic_subtraction(self):
        """Worker contribution = total - baseline."""
        baseline = LineStats({("f.py", 1, "foo"): [(1, 10, 5000)]}, 1e-9)
        total = LineStats({("f.py", 1, "foo"): [(1, 25, 12000)]}, 1e-9)
        delta = _subtract_stats(total, baseline)
        assert delta.timings[("f.py", 1, "foo")] == [(1, 15, 7000)]

    def test_empty_baseline(self):
        """All total entries kept when baseline has no matching key."""
        total = LineStats({("f.py", 1, "foo"): [(1, 10, 5000)]}, 1e-9)
        baseline = LineStats({}, 1e-9)
        delta = _subtract_stats(total, baseline)
        assert delta.timings[("f.py", 1, "foo")] == [(1, 10, 5000)]

    def test_zero_delta_filtered(self):
        """Lines where worker added zero hits are excluded."""
        baseline = LineStats({("f.py", 1, "foo"): [(1, 10, 5000), (2, 5, 3000)]}, 1e-9)
        total = LineStats({("f.py", 1, "foo"): [(1, 10, 5000), (2, 8, 4500)]}, 1e-9)
        delta = _subtract_stats(total, baseline)
        # Line 1: dh=0 → excluded.  Line 2: dh=3 → kept.
        assert delta.timings[("f.py", 1, "foo")] == [(2, 3, 1500)]

    def test_function_with_all_zero_delta_excluded(self):
        """Function dropped entirely when no lines have positive delta."""
        baseline = LineStats({("f.py", 1, "foo"): [(1, 10, 5000)]}, 1e-9)
        total = LineStats({("f.py", 1, "foo"): [(1, 10, 5000)]}, 1e-9)
        delta = _subtract_stats(total, baseline)
        assert ("f.py", 1, "foo") not in delta.timings

    def test_disjoint_functions(self):
        """Function in total but not in baseline is kept as-is."""
        baseline = LineStats({("a.py", 1, "f"): [(1, 5, 100)]}, 1e-9)
        total = LineStats(
            {
                ("a.py", 1, "f"): [(1, 5, 100)],
                ("b.py", 1, "g"): [(1, 10, 200)],
            },
            1e-9,
        )
        delta = _subtract_stats(total, baseline)
        # "f" had zero delta, excluded.  "g" is new, kept.
        assert ("a.py", 1, "f") not in delta.timings
        assert delta.timings[("b.py", 1, "g")] == [(1, 10, 200)]

    def test_multiline_function(self):
        """Multiple lines within a function are handled independently."""
        baseline = LineStats(
            {("f.py", 1, "foo"): [(1, 5, 100), (2, 0, 0), (3, 5, 200)]},
            1e-9,
        )
        total = LineStats(
            {("f.py", 1, "foo"): [(1, 5, 100), (2, 3, 50), (3, 10, 500)]},
            1e-9,
        )
        delta = _subtract_stats(total, baseline)
        # Line 1: dh=0 excluded.  Line 2: dh=3, new.  Line 3: dh=5.
        assert delta.timings[("f.py", 1, "foo")] == [(2, 3, 50), (3, 5, 300)]

    def test_preserves_unit(self):
        """Output LineStats has the same unit as the total."""
        baseline = LineStats({}, 1e-6)
        total = LineStats({("f.py", 1, "foo"): [(1, 1, 100)]}, 1e-6)
        delta = _subtract_stats(total, baseline)
        assert delta.unit == 1e-6


# ---------------------------------------------------------------------------
# Parent profiler inheritance: integration
# ---------------------------------------------------------------------------


def _run_parallel_with_parent_profiler(invoke_fn):
    """Run parallel work passing the parent profiler to profiling_hooks."""
    from lazyline.profiling import (
        build_scope_paths,
        collect_results,
        create_profiler,
        register_modules,
    )
    from tests import _parallel_fixture as mod

    modules = [mod]
    profiler = create_profiler()
    scope_paths = build_scope_paths(modules)
    register_modules(profiler, modules)

    module_names = [m.__name__ for m in modules]
    with profiling_hooks(module_names, parent_profiler=profiler) as worker_holder:
        profiler.enable_by_count()
        try:
            invoke_fn(mod)
        finally:
            profiler.disable_by_count()

    stats = merge_stats(profiler.get_stats(), worker_holder.stats)
    return collect_results(stats, scope_paths=scope_paths)


def test_parent_profiler_process_pool():
    """ProcessPoolExecutor with parent profiler produces worker results."""
    results = _run_parallel_with_parent_profiler(
        lambda mod: mod.run_with_process_pool(list(range(20)))
    )
    func_names = [r.name for r in results]
    assert "slow_computation" in func_names

    sc = next(r for r in results if r.name == "slow_computation")
    assert sc.total_time > 0
    assert sc.call_count > 0


def test_parent_profiler_mp_pool():
    """multiprocessing.Pool with parent profiler produces worker results."""
    results = _run_parallel_with_parent_profiler(
        lambda mod: mod.run_with_mp_pool(list(range(20)))
    )
    func_names = [r.name for r in results]
    assert "slow_computation" in func_names

    sc = next(r for r in results if r.name == "slow_computation")
    assert sc.total_time > 0
    assert sc.call_count > 0
