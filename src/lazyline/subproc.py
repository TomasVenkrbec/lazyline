"""Subprocess profiling: inject line_profiler into child Python processes.

When the profiled command spawns child Python processes (e.g., ``dvc repro``
running stages as ``python -m ...``), the parent's in-process ``LineProfiler``
is blind to functions executed in those children. This module bridges the gap
by injecting a ``sitecustomize.py`` bootstrap into every child interpreter via
``PYTHONPATH`` manipulation.

Mechanism
---------
1. Parent creates a temp directory containing a ``sitecustomize.py``.
2. Parent prepends that directory to ``PYTHONPATH`` and sets env vars
   (``_LAZYLINE_STATS_DIR``, ``_LAZYLINE_SCOPES``).
3. Any child ``python`` process loads the injected ``sitecustomize.py`` at
   startup, which chains to the original ``sitecustomize`` (if any) and
   then calls :func:`_subprocess_bootstrap`.
4. The bootstrap discovers the target scope, registers functions, enables
   a fresh ``LineProfiler``, and installs an ``atexit`` handler that dumps
   stats to a pickle file in the shared stats directory.
5. After the command finishes, the parent collects and merges all pickle
   files, combining subprocess stats with its own.

Limitations
-----------
- Child processes started with ``python -S`` (no site) skip
  ``sitecustomize.py``, so profiling injection is silently disabled.
- Scoped modules are imported eagerly at child interpreter startup (before
  the child's ``__main__``). This matches the parent's behavior but can
  change import order for modules with import-time side effects.
- ``os._exit()`` in child processes bypasses ``atexit`` handlers, losing
  profiling data from that process.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import textwrap
from pathlib import Path

from line_profiler.line_profiler import LineStats

logger = logging.getLogger(__name__)

_SCOPE_SEPARATOR = "\x1f"  # Unit separator — safe in paths and module names.

# Environment variables used to communicate with the subprocess bootstrap.
_ENV_STATS_DIR = "_LAZYLINE_STATS_DIR"
_ENV_SCOPES = "_LAZYLINE_SCOPES"


@contextlib.contextmanager
def subprocess_hooks(scopes: list[str]):
    """Inject profiling into child Python processes via ``sitecustomize.py``.

    Creates a temporary directory with a ``sitecustomize.py`` that activates
    profiling in any child Python process spawned during the ``with`` block.
    On exit, collects stats written by subprocesses and stores them on the
    yielded holder.

    Parameters
    ----------
    scopes
        Scope strings (dotted module names or ``.py`` file paths).

    Yields
    ------
    _StatsHolder
        Mutable holder whose ``.stats`` attribute is populated after the
        ``with`` block exits (``None`` if no subprocesses produced stats).
    """
    stats_dir = tempfile.mkdtemp(prefix="lazyline_sub_stats_")
    bootstrap_dir = tempfile.mkdtemp(prefix="lazyline_bootstrap_")
    _write_sitecustomize(bootstrap_dir)

    orig_env = {
        "PYTHONPATH": os.environ.get("PYTHONPATH"),
        _ENV_STATS_DIR: os.environ.get(_ENV_STATS_DIR),
        _ENV_SCOPES: os.environ.get(_ENV_SCOPES),
    }

    prev_pythonpath = orig_env["PYTHONPATH"]
    os.environ["PYTHONPATH"] = (
        f"{bootstrap_dir}{os.pathsep}{prev_pythonpath}"
        if prev_pythonpath
        else bootstrap_dir
    )
    os.environ[_ENV_STATS_DIR] = stats_dir
    os.environ[_ENV_SCOPES] = _encode_scopes(scopes)

    holder = _StatsHolder()
    try:
        yield holder
    finally:
        for key, orig in orig_env.items():
            if orig is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = orig

        try:
            holder.stats = _collect_subprocess_stats(stats_dir)
        except Exception:
            logger.debug("Failed to collect subprocess stats.", exc_info=True)

        shutil.rmtree(stats_dir, ignore_errors=True)
        shutil.rmtree(bootstrap_dir, ignore_errors=True)


class _StatsHolder:
    """Mutable container yielded by :func:`subprocess_hooks`."""

    __slots__ = ("stats",)

    def __init__(self) -> None:
        self.stats: LineStats | None = None


def _encode_scopes(scopes: list[str]) -> str:
    """Encode scopes for the subprocess environment variable.

    ``.py`` file scopes are resolved to absolute paths so subprocesses
    can find them regardless of working directory.
    """
    resolved = []
    for scope in scopes:
        if scope.endswith(".py"):
            resolved.append(str(Path(scope).resolve()))
        else:
            resolved.append(scope)
    return _SCOPE_SEPARATOR.join(resolved)


# ---------------------------------------------------------------------------
# sitecustomize.py template
# ---------------------------------------------------------------------------
# Kept minimal — the real logic lives in _subprocess_bootstrap() so that it
# can be tested normally.  Guarded by an env-var check so it's a no-op for
# subprocesses that aren't part of a lazyline run.
#
# If a real sitecustomize.py already exists in the environment, we chain to
# it via importlib so that coverage.py, virtualenv, conda, etc. still work.
# The original path is detected at write-time (parent process) and baked
# into the generated file.
_SITECUSTOMIZE_TEMPLATE = textwrap.dedent("""\
    import os as _os

    # Chain to original sitecustomize (path detected by parent, may be None).
    _ORIGINAL_SITECUSTOMIZE = {original_path!r}
    if _ORIGINAL_SITECUSTOMIZE:
        try:
            import importlib.util as _iu
            _spec = _iu.spec_from_file_location("_orig_sitecustomize",
                                                 _ORIGINAL_SITECUSTOMIZE)
            if _spec and _spec.loader:
                _mod = _iu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
        except Exception:
            pass

    if _os.environ.get("_LAZYLINE_STATS_DIR"):
        try:
            from lazyline.subproc import _subprocess_bootstrap
            _subprocess_bootstrap()
        except Exception:
            pass
""")


def _find_original_sitecustomize() -> str | None:
    """Find the path to any existing ``sitecustomize.py`` before we shadow it."""
    import importlib.util

    spec = importlib.util.find_spec("sitecustomize")
    if spec is not None and spec.origin is not None:
        return spec.origin
    return None


def _write_sitecustomize(directory: str) -> None:
    """Write the bootstrap ``sitecustomize.py`` into *directory*."""
    original_path = _find_original_sitecustomize()
    content = _SITECUSTOMIZE_TEMPLATE.format(original_path=original_path)
    path = os.path.join(directory, "sitecustomize.py")
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Subprocess-side bootstrap (runs inside the child interpreter)
# ---------------------------------------------------------------------------
def _subprocess_bootstrap() -> None:
    """Set up profiling in a child process.

    Called from the injected ``sitecustomize.py`` at interpreter startup.
    Reads scope and stats-dir from environment variables, discovers
    modules, registers them with a fresh ``LineProfiler``, installs
    multiprocessing worker hooks (same as the parent process), and
    registers an ``atexit`` handler to dump merged stats to a pickle file.
    """
    import atexit
    import time
    import warnings

    from line_profiler import LineProfiler

    from lazyline.discovery import discover_modules
    from lazyline.parallel import profiling_hooks
    from lazyline.profiling import register_modules

    stats_dir = os.environ.get(_ENV_STATS_DIR)
    scopes_raw = os.environ.get(_ENV_SCOPES)
    if not stats_dir or not scopes_raw or not os.path.isdir(stats_dir):
        return

    modules = []
    for scope in scopes_raw.split(_SCOPE_SEPARATOR):
        if scope:
            modules.extend(discover_modules(scope))

    if not modules:
        return

    profiler = LineProfiler()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
        warnings.filterwarnings("ignore", message=r".*Could not extract a code.*")
        register_modules(profiler, modules)

    # Install multiprocessing worker hooks so stdlib multiprocessing
    # workers inside this subprocess are also profiled (mirrors _profile()).
    # Wrapped in try/finally so __exit__ is always called even if
    # enable_by_count or atexit.register somehow fails — avoids leaking
    # the worker temp dir and the monkey-patched worker entry points.
    module_names = [m.__name__ for m in modules]
    hooks_cm = profiling_hooks(module_names)
    worker_holder = hooks_cm.__enter__()
    try:
        profiler.enable_by_count()

        def _dump(
            profiler=profiler,
            stats_dir=stats_dir,
            hooks_cm=hooks_cm,
            worker_holder=worker_holder,
        ):
            try:
                profiler.disable_by_count()
                # Collect worker stats (cleans up worker temp dir).
                hooks_cm.__exit__(None, None, None)
                stats = profiler.get_stats()
                if worker_holder.stats:
                    stats = stats + worker_holder.stats
                if not stats.timings:
                    return
                tag = f"{os.getpid()}_{time.monotonic_ns()}"
                path = os.path.join(stats_dir, f"{tag}.pkl")
                old_umask = os.umask(0o177)
                try:
                    stats.to_file(path)
                finally:
                    os.umask(old_umask)
            except Exception:
                logger.debug("Failed to dump subprocess stats.", exc_info=True)

        atexit.register(_dump)
    except Exception:
        hooks_cm.__exit__(None, None, None)
        raise


# ---------------------------------------------------------------------------
# Parent-side stats collection
# ---------------------------------------------------------------------------
def _collect_subprocess_stats(stats_dir: str) -> LineStats | None:
    """Read and merge subprocess profiling stats from *stats_dir*.

    Loads each pickle file individually so a single corrupt file does
    not discard stats from other subprocesses.
    """
    try:
        entries = os.listdir(stats_dir)
    except OSError:
        return None
    pkl_files = sorted(
        os.path.join(stats_dir, f) for f in entries if f.endswith(".pkl")
    )
    if not pkl_files:
        return None

    merged: LineStats | None = None
    for path in pkl_files:
        try:
            loaded = LineStats.from_files(path)
        except Exception:
            logger.debug("Skipping corrupt subprocess stats: %s", path, exc_info=True)
            continue
        merged = loaded if merged is None else merged + loaded
    return merged
