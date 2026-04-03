"""Multiprocessing support: profile stdlib multiprocessing workers.

Automatically instruments worker processes created by
``concurrent.futures.ProcessPoolExecutor`` and ``multiprocessing.Pool``
so that line-level profiling data is captured from forked workers.

Requires the ``fork`` start method (default on Linux). With ``spawn`` or
``forkserver``, workers start fresh interpreters that don't inherit the
monkey-patched globals, so profiling is silently skipped and a warning
is emitted.
"""

from __future__ import annotations

import concurrent.futures.process
import contextlib
import logging
import multiprocessing
import multiprocessing.pool
import os
import shutil
import signal
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any

from line_profiler.line_profiler import LineStats

if TYPE_CHECKING:
    import types
    from collections.abc import Callable

    from line_profiler import LineProfiler

logger = logging.getLogger(__name__)

# Globals set before fork, read by workers via CoW.
_LAZYLINE_MODULE_NAMES: list[str] | None = None
_LAZYLINE_STATS_DIR: str | None = None
_LAZYLINE_PARENT_PROFILER: LineProfiler | None = None

# Stashed originals for unpatching.
_ORIGINAL_PROCESS_WORKER: Callable[..., Any] | None = None
_ORIGINAL_POOL_WORKER: Callable[..., Any] | None = None


@contextlib.contextmanager
def profiling_hooks(
    module_names: list[str], parent_profiler: LineProfiler | None = None
):
    """Install per-worker profiling hooks for stdlib multiprocessing.

    Monkey-patches ``concurrent.futures.process._process_worker`` and
    ``multiprocessing.pool.worker`` so that every forked worker creates its own
    ``LineProfiler``, profiles the worker body, and writes stats to a temporary
    directory. On exit the context manager collects worker stats, cleans up the
    temp directory, and stores the merged ``LineStats`` on the yielded holder.

    Parameters
    ----------
    module_names
        Dotted module names to register in each worker's profiler.
    parent_profiler
        The parent process's ``LineProfiler``.  When provided, forked
        workers inherit it (via copy-on-write) instead of creating a
        fresh profiler.  This is necessary because ``line_profiler``'s
        bytecode hash matching — which allows a profiler to trace code
        objects that differ by identity but share the same bytecode —
        does not survive ``fork()`` for fresh profiler instances.
        Inheriting the parent profiler preserves the hash mappings
        established in the parent process (e.g. between discovery-time
        and ``runpy``-time code objects).

    Yields
    ------
    _StatsHolder
        Mutable holder whose ``.stats`` attribute is populated after the
        ``with`` block exits (``None`` if no workers produced stats).
    """
    global _LAZYLINE_MODULE_NAMES, _LAZYLINE_STATS_DIR
    global _ORIGINAL_PROCESS_WORKER, _ORIGINAL_POOL_WORKER
    global _LAZYLINE_PARENT_PROFILER

    if _ORIGINAL_PROCESS_WORKER is not None:
        msg = "profiling_hooks is not reentrant — already active"
        raise RuntimeError(msg)

    stats_dir = tempfile.mkdtemp(prefix="lazyline_worker_stats_")
    _LAZYLINE_MODULE_NAMES = module_names
    _LAZYLINE_STATS_DIR = stats_dir
    _LAZYLINE_PARENT_PROFILER = parent_profiler
    _ORIGINAL_PROCESS_WORKER = concurrent.futures.process._process_worker
    _ORIGINAL_POOL_WORKER = (
        multiprocessing.pool.worker  # ty: ignore[unresolved-attribute]
    )
    concurrent.futures.process._process_worker = (  # ty: ignore[invalid-assignment]
        _patched_process_worker
    )
    multiprocessing.pool.worker = (  # ty: ignore[unresolved-attribute]
        _patched_pool_worker
    )

    holder = _StatsHolder()
    try:
        yield holder
    finally:
        concurrent.futures.process._process_worker = _ORIGINAL_PROCESS_WORKER
        multiprocessing.pool.worker = (  # ty: ignore[unresolved-attribute]
            _ORIGINAL_POOL_WORKER
        )
        _LAZYLINE_MODULE_NAMES = None
        _LAZYLINE_STATS_DIR = None
        _LAZYLINE_PARENT_PROFILER = None
        _ORIGINAL_PROCESS_WORKER = None
        _ORIGINAL_POOL_WORKER = None

        try:
            holder.stats = _collect_worker_stats(stats_dir)
        except Exception:
            logger.debug("Failed to collect worker stats.", exc_info=True)
        shutil.rmtree(stats_dir, ignore_errors=True)
        _warn_non_fork_start_method()


class _StatsHolder:
    """Mutable container yielded by :func:`profiling_hooks`.

    The context manager populates ``stats`` in its ``finally`` block so the
    caller can read it after the ``with`` block exits.
    """

    __slots__ = ("stats",)

    def __init__(self) -> None:
        self.stats: LineStats | None = None


def _get_worker_profiler():
    """Obtain a profiler for this worker process.

    When a parent profiler is available (set via :func:`profiling_hooks`),
    inherits it and snapshots its baseline stats so only the worker's
    contribution can be extracted later.

    Falls back to creating a fresh profiler from ``sys.modules`` when no
    parent profiler was provided.

    Returns
    -------
    tuple[LineProfiler, LineStats | None]
        The profiler to use and a baseline snapshot (``None`` when a
        fresh profiler was created — all its stats are worker-only).
    """
    if _LAZYLINE_PARENT_PROFILER is not None:
        profiler = _LAZYLINE_PARENT_PROFILER
        # The inherited profiler carries the parent's accumulated timings.
        # Snapshot them so _dump_worker_stats can subtract the baseline and
        # emit only the worker's contribution.
        profiler.disable_by_count()
        baseline = profiler.get_stats()
        return profiler, baseline

    return _setup_fresh_profiler(), None


def _setup_fresh_profiler():
    """Create a fresh ``LineProfiler`` and register modules.

    Used as fallback when no parent profiler is available (e.g. in
    subprocess bootstrap or when ``profiling_hooks`` was called without
    a ``parent_profiler``).
    """
    import warnings

    from line_profiler import LineProfiler

    from lazyline.profiling import register_modules

    profiler = LineProfiler()
    modules: list[types.ModuleType] = []
    for name in _LAZYLINE_MODULE_NAMES or []:
        mod = sys.modules.get(name)
        if mod is not None:
            modules.append(mod)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
        warnings.filterwarnings("ignore", message=r".*Could not extract a code.*")
        register_modules(profiler, modules)

    return profiler


def _dump_worker_stats(
    profiler,  # noqa: ANN001
    baseline: LineStats | None = None,
) -> None:
    """Disable the profiler and write stats to the shared temp directory.

    Parameters
    ----------
    profiler
        The worker's ``LineProfiler`` instance.
    baseline
        When the profiler was inherited from the parent, this is the
        snapshot taken before the worker started.  The baseline is
        subtracted so only the worker's contribution is written.
    """
    try:
        profiler.disable_by_count()
        stats = profiler.get_stats()
        if baseline is not None:
            stats = _subtract_stats(stats, baseline)
        if stats.timings and _LAZYLINE_STATS_DIR is not None:
            tag = f"{os.getpid()}_{time.monotonic_ns()}"
            stats_path = os.path.join(_LAZYLINE_STATS_DIR, f"{tag}.pkl")
            old_umask = os.umask(0o177)  # Files created as 0600.
            try:
                stats.to_file(stats_path)
            finally:
                os.umask(old_umask)
    except Exception:
        logger.debug(
            "Failed to collect worker stats for PID %d.",
            os.getpid(),
            exc_info=True,
        )


def _subtract_stats(total: LineStats, baseline: LineStats) -> LineStats:
    """Subtract baseline timings from total to get the delta.

    Only keeps entries where the worker added hits (i.e. the function
    was actually called in the worker).  Matches lines by ``lineno``
    rather than position for robustness.
    """
    delta: dict[tuple[str, int, str], list[tuple[int, int, int]]] = {}
    for key, after_lines in total.timings.items():
        before_lines = baseline.timings.get(key, [])
        before_by_lineno = {ln: (h, t) for ln, h, t in before_lines}
        new_lines = []
        for lineno, hits, raw_time in after_lines:
            bh, bt = before_by_lineno.get(lineno, (0, 0))
            dh = hits - bh
            if dh > 0:
                new_lines.append((lineno, dh, raw_time - bt))
        if new_lines:
            delta[key] = new_lines
    return LineStats(delta, total.unit)


def _patched_process_worker(*args, **kwargs):  # noqa: ANN002, ANN003
    """Wrap ``concurrent.futures.process._process_worker`` with profiling."""
    profiler, baseline = _get_worker_profiler()
    profiler.enable_by_count()
    try:
        return _ORIGINAL_PROCESS_WORKER(  # ty: ignore[call-non-callable]
            *args, **kwargs
        )
    finally:
        _dump_worker_stats(profiler, baseline)


def _sigterm_to_exit(signum, frame):  # noqa: ANN001, ARG001
    """Convert SIGTERM to ``SystemExit`` so ``finally`` blocks run."""
    raise SystemExit(0)


def _patched_pool_worker(*args, **kwargs):  # noqa: ANN002, ANN003
    """Wrap ``multiprocessing.pool.worker`` with profiling.

    ``Pool.terminate()`` (and ``Pool.__exit__``) sends SIGTERM to workers,
    which kills them before ``finally`` blocks can run. We install a SIGTERM
    handler that converts the signal to ``SystemExit`` so stats are dumped
    cleanly. ``SystemExit`` propagates through the worker loop (which only
    catches ``Exception``) and is handled by ``BaseProcess._bootstrap``.
    """
    profiler, baseline = _get_worker_profiler()
    profiler.enable_by_count()
    signal.signal(signal.SIGTERM, _sigterm_to_exit)
    try:
        return _ORIGINAL_POOL_WORKER(*args, **kwargs)  # ty: ignore[call-non-callable]
    finally:
        # Ignore further SIGTERM during stats dump.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        _dump_worker_stats(profiler, baseline)


def _warn_non_fork_start_method() -> None:
    """Warn if the active multiprocessing start method is not ``fork``."""
    method = multiprocessing.get_start_method(allow_none=True)
    if method is not None and method != "fork":
        logger.warning(
            "Multiprocessing start method is '%s' — worker profiling "
            "requires 'fork'. Worker stats will be empty.",
            method,
        )


def _collect_worker_stats(stats_dir: str) -> LineStats | None:
    """Read and merge worker profiling stats from the temporary directory.

    Loads each pickle file individually so a single corrupted file does
    not discard stats from other workers.
    """
    pkl_files = sorted(
        os.path.join(stats_dir, f) for f in os.listdir(stats_dir) if f.endswith(".pkl")
    )
    if not pkl_files:
        return None

    merged: LineStats | None = None
    for path in pkl_files:
        try:
            loaded = LineStats.from_files(path)
        except Exception:
            logger.debug("Skipping corrupt worker stats: %s", path, exc_info=True)
            continue
        merged = loaded if merged is None else merged + loaded
    return merged


def merge_stats(parent_stats: LineStats, worker_stats: LineStats | None) -> LineStats:
    """Merge parent and worker stats into a single ``LineStats``.

    Parameters
    ----------
    parent_stats
        Stats from the parent process profiler.
    worker_stats
        Merged worker stats, or ``None`` if no workers were profiled.

    Returns
    -------
    LineStats
        Combined stats.
    """
    if worker_stats is None:
        return parent_stats
    return parent_stats + worker_stats
