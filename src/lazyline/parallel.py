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

logger = logging.getLogger(__name__)

# Globals set before fork, read by workers via CoW.
_LAZYLINE_MODULE_NAMES: list[str] | None = None
_LAZYLINE_STATS_DIR: str | None = None

# Stashed originals for unpatching.
_ORIGINAL_PROCESS_WORKER: Callable[..., Any] | None = None
_ORIGINAL_POOL_WORKER: Callable[..., Any] | None = None


@contextlib.contextmanager
def profiling_hooks(module_names: list[str]):
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

    Yields
    ------
    _StatsHolder
        Mutable holder whose ``.stats`` attribute is populated after the
        ``with`` block exits (``None`` if no workers produced stats).
    """
    global _LAZYLINE_MODULE_NAMES, _LAZYLINE_STATS_DIR
    global _ORIGINAL_PROCESS_WORKER, _ORIGINAL_POOL_WORKER

    if _ORIGINAL_PROCESS_WORKER is not None:
        msg = "profiling_hooks is not reentrant — already active"
        raise RuntimeError(msg)

    stats_dir = tempfile.mkdtemp(prefix="lazyline_worker_stats_")
    _LAZYLINE_MODULE_NAMES = module_names
    _LAZYLINE_STATS_DIR = stats_dir
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


def _setup_worker_profiler():
    """Create a ``LineProfiler`` and register modules for this worker process."""
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


def _dump_worker_stats(profiler) -> None:  # noqa: ANN001
    """Disable the profiler and write stats to the shared temp directory."""
    try:
        profiler.disable_by_count()
        stats = profiler.get_stats()
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


def _patched_process_worker(*args, **kwargs):  # noqa: ANN002, ANN003
    """Wrap ``concurrent.futures.process._process_worker`` with profiling."""
    profiler = _setup_worker_profiler()
    profiler.enable_by_count()
    try:
        return _ORIGINAL_PROCESS_WORKER(  # ty: ignore[call-non-callable]
            *args, **kwargs
        )
    finally:
        _dump_worker_stats(profiler)


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
    profiler = _setup_worker_profiler()
    profiler.enable_by_count()
    signal.signal(signal.SIGTERM, _sigterm_to_exit)
    try:
        return _ORIGINAL_POOL_WORKER(*args, **kwargs)  # ty: ignore[call-non-callable]
    finally:
        # Ignore further SIGTERM during stats dump.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        _dump_worker_stats(profiler)


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
