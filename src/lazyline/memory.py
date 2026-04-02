"""tracemalloc integration for per-line memory tracking."""

from __future__ import annotations

import logging
import tracemalloc
from pathlib import Path

logger = logging.getLogger(__name__)


def start_tracking() -> tracemalloc.Snapshot:
    """Start tracemalloc and return a before-snapshot.

    If tracemalloc is already running it is restarted to get a clean baseline.

    Returns
    -------
    tracemalloc.Snapshot
        Baseline snapshot taken immediately after tracemalloc starts.
    """
    if tracemalloc.is_tracing():
        logger.warning(
            "tracemalloc was already active — restarting for a clean baseline."
        )
        tracemalloc.stop()
    tracemalloc.start()
    return tracemalloc.take_snapshot()


def stop_tracking(
    before: tracemalloc.Snapshot | None,
) -> dict[tuple[str, int], float] | None:
    """Take an after-snapshot, stop tracemalloc, and return per-line deltas.

    Returns ``None`` if *before* is ``None`` (memory tracking was not enabled).
    Degrades gracefully if tracemalloc was stopped externally.

    Parameters
    ----------
    before
        Baseline snapshot from :func:`start_tracking`, or ``None``.

    Returns
    -------
    dict[tuple[str, int], float] or None
        Mapping of ``(absolute_filename, lineno)`` to net bytes allocated
        (positive = growth, negative = freed), or ``None``.
    """
    if before is None:
        return None
    try:
        after = tracemalloc.take_snapshot()
    except RuntimeError:
        logger.warning("tracemalloc was stopped externally — memory data unavailable.")
        return None
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
    return _compute_deltas(before, after)


def _compute_deltas(
    before: tracemalloc.Snapshot,
    after: tracemalloc.Snapshot,
) -> dict[tuple[str, int], float]:
    """Compute per-line memory deltas between two snapshots."""
    stats = after.compare_to(before, "lineno")
    result: dict[tuple[str, int], float] = {}
    for stat in stats:
        frame = stat.traceback[0]
        if frame.filename.startswith("<"):
            continue
        try:
            filename = str(Path(frame.filename).resolve())
        except (OSError, ValueError):
            continue
        result[(filename, frame.lineno)] = float(stat.size_diff)
    return result
