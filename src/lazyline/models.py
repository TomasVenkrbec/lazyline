"""Data models for profiling results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LineProfile:
    """Profiling data for a single source line."""

    lineno: int
    hits: int
    time: float  # seconds
    source: str = ""
    memory: float | None = None  # bytes, net allocation delta


@dataclass
class FunctionProfile:
    """Profiling data for a single function."""

    module: str
    name: str
    filename: str
    start_line: int
    total_time: float  # seconds
    call_count: int
    lines: list[LineProfile] = field(default_factory=list)
    memory: float | None = None  # bytes, net allocation delta


@dataclass
class RunMetadata:
    """Metadata about a profiling run."""

    command: list[str]
    scope: str
    timestamp: str  # ISO 8601
    memory_tracking: bool
    python_version: str
    exit_code: int
    n_registered: int | None = None
    wall_time: float | None = None  # seconds


@dataclass
class ProfileRun:
    """Complete profiling run with metadata and results."""

    version: int
    lazyline_version: str
    metadata: RunMetadata
    functions: list[FunctionProfile]
