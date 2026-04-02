"""JSON serialization and deserialization for profiling results."""

from __future__ import annotations

import dataclasses
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lazyline.models import (
    FunctionProfile,
    LineProfile,
    ProfileRun,
    RunMetadata,
)

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;]*[A-Za-z]"  # CSI sequences (e.g., \033[1m)
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"  # OSC sequences
    r"|\x1b[@-Z\\-_]"  # Two-char ESC sequences
    r"|\x9b[0-9;]*[A-Za-z]"  # 8-bit CSI (0x9B)
    r"|[\x80-\x9f]"  # Remaining C1 control characters
)


def _sanitize(value: str) -> str:
    """Strip ANSI escape sequences from a string."""
    return _ANSI_ESCAPE_RE.sub("", value)


def to_dict(run: ProfileRun) -> dict:
    """Convert a profiling run to a plain dict suitable for JSON serialization.

    Parameters
    ----------
    run
        The profiling run to serialize.

    Returns
    -------
    dict
        Nested dict matching the JSON schema (version 1).
    """
    return dataclasses.asdict(run)


def from_dict(data: dict) -> ProfileRun:
    """Reconstruct a profiling run from a plain dict.

    Parameters
    ----------
    data
        Dict parsed from JSON, expected to match version 1 schema.

    Returns
    -------
    ProfileRun
        The reconstructed profiling run.

    Raises
    ------
    ValueError
        If the schema version is not supported.
    KeyError
        If required fields are missing.
    """
    version = data.get("version")
    if version is None:
        msg = "Not a valid lazyline results file (missing 'version' field)"
        raise ValueError(msg)
    if version != 1:
        msg = f"Unsupported schema version: {version} (expected 1)"
        raise ValueError(msg)

    meta_raw = data["metadata"]
    metadata = RunMetadata(
        command=[_sanitize(c) for c in meta_raw["command"]],
        scope=_sanitize(meta_raw["scope"]),
        timestamp=_sanitize(meta_raw["timestamp"]),
        memory_tracking=meta_raw["memory_tracking"],
        python_version=_sanitize(meta_raw["python_version"]),
        exit_code=meta_raw["exit_code"],
        n_registered=meta_raw.get("n_registered"),
        wall_time=meta_raw.get("wall_time"),
    )

    functions = []
    for fp_raw in data["functions"]:
        lines = [
            LineProfile(
                lineno=lp["lineno"],
                hits=lp["hits"],
                time=lp["time"],
                source=_sanitize(lp.get("source", "")),
                memory=lp.get("memory"),
            )
            for lp in fp_raw.get("lines", [])
        ]
        functions.append(
            FunctionProfile(
                module=_sanitize(fp_raw["module"]),
                name=_sanitize(fp_raw["name"]),
                filename=_sanitize(fp_raw["filename"]),
                start_line=fp_raw["start_line"],
                total_time=fp_raw["total_time"],
                call_count=fp_raw["call_count"],
                lines=lines,
                memory=fp_raw.get("memory"),
            )
        )

    return ProfileRun(
        version=data["version"],
        lazyline_version=data.get("lazyline_version", "unknown"),
        metadata=metadata,
        functions=functions,
    )


def to_json(run: ProfileRun, path: Path) -> None:
    """Export a profiling run to a JSON file or stdout.

    Parameters
    ----------
    run
        The profiling run to export.
    path
        Destination file path. Use ``Path("-")`` to write to stdout.
    """
    import sys

    data = to_dict(run)
    if str(path) == "-":
        json.dump(data, sys.stdout, indent=2, allow_nan=False)
        sys.stdout.write("\n")
        sys.stdout.flush()
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, allow_nan=False)
            f.write("\n")


def from_json(path: Path) -> ProfileRun:
    """Load a profiling run from a JSON file.

    Parameters
    ----------
    path
        Path to the JSON file.

    Returns
    -------
    ProfileRun
        The loaded profiling run.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    json.JSONDecodeError
        If the file contains invalid JSON.
    ValueError
        If the schema version is not supported.
    KeyError
        If required fields are missing.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f, parse_constant=_reject_nan_inf)
    return from_dict(data)


def _reject_nan_inf(value: str) -> None:
    """Reject NaN/Infinity constants during JSON parsing."""
    msg = f"Invalid JSON constant: {value!r} (NaN/Infinity not allowed)"
    raise ValueError(msg)
