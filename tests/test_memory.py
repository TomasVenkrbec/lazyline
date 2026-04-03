import tracemalloc
from pathlib import Path

from lazyline.memory import _compute_deltas, start_tracking, stop_tracking


def test_start_tracking_returns_snapshot():
    try:
        snap = start_tracking()
        assert isinstance(snap, tracemalloc.Snapshot)
    finally:
        tracemalloc.stop()


def test_stop_tracking_returns_dict():
    before = start_tracking()
    # Allocate something measurable between snapshots.
    _data = [bytearray(1024) for _ in range(100)]  # noqa: F841
    result = stop_tracking(before)
    assert isinstance(result, dict)
    assert len(result) > 0
    for key, value in result.items():
        assert isinstance(key, tuple)
        assert len(key) == 2
        assert isinstance(key[0], str)
        assert isinstance(key[1], int)
        assert isinstance(value, float)


def test_tracemalloc_stopped_after_stop_tracking():
    before = start_tracking()
    assert tracemalloc.is_tracing()
    stop_tracking(before)
    assert not tracemalloc.is_tracing()


def test_compute_deltas_detects_allocation():
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    _data = bytearray(4096)  # noqa: F841
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    deltas = _compute_deltas(before, after)
    # At least one entry should have positive size_diff from our allocation.
    assert any(v > 0 for v in deltas.values())


def test_compute_deltas_keys_are_absolute_paths():
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    _data = [0] * 1000  # noqa: F841
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    deltas = _compute_deltas(before, after)
    for filename, _lineno in deltas:
        assert Path(filename).is_absolute(), f"Expected absolute path, got: {filename}"


def test_compute_deltas_filters_synthetic_filenames():
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    _data = [0] * 1000  # noqa: F841
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    deltas = _compute_deltas(before, after)
    for filename, _lineno in deltas:
        assert not filename.startswith("<"), f"Synthetic filename leaked: {filename}"


def test_start_tracking_restarts_if_already_active():
    tracemalloc.start()
    assert tracemalloc.is_tracing()
    # start_tracking should handle this gracefully (restart).
    snap = start_tracking()
    try:
        assert isinstance(snap, tracemalloc.Snapshot)
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


def test_stop_tracking_none_returns_none():
    result = stop_tracking(None)
    assert result is None


def test_stop_tracking_graceful_when_tracemalloc_stopped_externally():
    before = start_tracking()
    tracemalloc.stop()
    result = stop_tracking(before)
    assert result is None


def test_zero_allocation_returns_empty_or_small_dict():
    before = start_tracking()
    # Pure computation, no heap growth.
    _ = sum(range(100))
    result = stop_tracking(before)
    assert isinstance(result, dict)
    # Should have very few entries (possibly some from tracemalloc internals)
    # but no large positive deltas from user code.
    user_file = str(Path(__file__).resolve())
    user_entries = {k: v for k, v in result.items() if k[0] == user_file}
    assert all(v <= 1024 for v in user_entries.values()), "Unexpected large allocation"
