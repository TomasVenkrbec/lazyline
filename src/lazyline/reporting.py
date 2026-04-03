"""Terminal reporting for profiling results."""

from __future__ import annotations

import fnmatch
import linecache
import os
import shutil
import sys
from typing import TYPE_CHECKING, Final, NamedTuple, TextIO

from lazyline.models import FunctionProfile, LineProfile

try:
    from pygments import highlight as _pygments_highlight
    from pygments.formatters.terminal256 import (
        Terminal256Formatter as _Terminal256Formatter,
    )
    from pygments.lexers.python import PythonLexer as _PythonLexer

    _PYGMENTS_AVAILABLE: bool = True
except ImportError:
    _PYGMENTS_AVAILABLE = False

if TYPE_CHECKING:
    from pygments.formatter import Formatter
    from pygments.lexer import Lexer

_MIN_NAME_WIDTH: Final[int] = 30
_DEFAULT_WIDTH: Final[int] = 120
_DIM_PIPE: Final[str] = "\033[2;90m│\033[0m"
_BOLD_START: Final[str] = "\033[1m"
_BOLD_END: Final[str] = "\033[0m"
_DIM_START: Final[str] = "\033[2m"
_DIM_END: Final[str] = "\033[0m"
_MAX_NUM_WIDTH: Final[int] = 20
_OVERFLOW: Final[str] = ">"


def _make_highlighter(is_tty: bool) -> tuple[Lexer | None, Formatter | None]:
    """Create Pygments lexer and formatter if available and appropriate."""
    if is_tty and _PYGMENTS_AVAILABLE:
        return _PythonLexer(), _Terminal256Formatter(style="monokai")
    return None, None


def _highlight_source(
    source: str, lexer: Lexer | None, formatter: Formatter | None
) -> str:
    """Syntax-highlight a source line for terminal display."""
    if lexer is None or formatter is None or not source.strip():
        return source
    return _pygments_highlight(source, lexer, formatter).rstrip("\n")


class _TimeUnit(NamedTuple):
    """Display configuration for a time unit."""

    label: str
    multiplier: float
    total_prec: int  # decimal places for summary totals / grand total
    detail_prec: int  # decimal places for per-call, per-hit, per-line


_UNITS: dict[str, _TimeUnit] = {
    "s": _TimeUnit("s", 1, 4, 6),
    "ms": _TimeUnit("ms", 1e3, 2, 4),
    "us": _TimeUnit("us", 1e6, 1, 2),
    "ns": _TimeUnit("ns", 1e9, 0, 1),
}


def _auto_select_unit(results: list[FunctionProfile]) -> _TimeUnit:
    """Pick the best display unit based on the maximum total_time.

    Using max ensures that the largest value is always displayed in a
    human-readable range, avoiding multi-billion nanosecond numbers when
    a single slow function dominates an otherwise fast profile.
    """
    if not results:
        return _UNITS["s"]
    peak = max(fp.total_time for fp in results)
    if peak <= 0 or peak >= 1.0:
        return _UNITS["s"]
    if peak >= 1e-3:
        return _UNITS["ms"]
    if peak >= 1e-6:
        return _UNITS["us"]
    return _UNITS["ns"]


def _fmt_num(value: float, width: int, prec: int) -> str:
    """Format a float, truncating with '>' if it overflows *width*."""
    s = f"{value:.{prec}f}"
    if len(s) <= width:
        return f"{s:>{width}}"
    return _OVERFLOW + s[-(width - 1) :]


def _compute_col_widths(
    display: list[FunctionProfile], tu: _TimeUnit
) -> tuple[int, int, int, int]:
    """Compute minimum column widths from actual formatted values.

    Returns
    -------
    tuple
        (total_w, tpc_w, time_w, tph_w) — minimum widths for the
        Total, Time/Call, per-line Time, and per-line Time/Hit columns.
    """
    m, tp, dp = tu.multiplier, tu.total_prec, tu.detail_prec
    total_w = len(f"Total ({tu.label})")
    tpc_w = len(f"Time/Call ({tu.label})")
    time_w = len(f"Time ({tu.label})")
    tph_w = len(f"Time/Hit ({tu.label})")
    for fp in display:
        total_w = max(total_w, len(f"{fp.total_time * m:.{tp}f}"))
        tpc = fp.total_time / fp.call_count if fp.call_count > 0 else 0.0
        tpc_w = max(tpc_w, len(f"{tpc * m:.{dp}f}"))
        for lp in fp.lines:
            if lp.hits > 0:
                time_w = max(time_w, len(f"{lp.time * m:.{dp}f}"))
                tph = lp.time / lp.hits
                tph_w = max(tph_w, len(f"{tph * m:.{dp}f}"))
    return (
        min(total_w, _MAX_NUM_WIDTH),
        min(tpc_w, _MAX_NUM_WIDTH),
        min(time_w, _MAX_NUM_WIDTH),
        min(tph_w, _MAX_NUM_WIDTH),
    )


def _get_width(stream: TextIO, width: int | None) -> int:
    """Determine the effective terminal width for output formatting."""
    if width is not None:
        return width
    try:
        if stream.isatty():
            return shutil.get_terminal_size().columns
    except AttributeError:
        pass
    # Respect COLUMNS env var for non-tty output (pipes, files).
    columns = os.environ.get("COLUMNS")
    if columns is not None:
        try:
            return int(columns)
        except ValueError:
            pass
    return _DEFAULT_WIDTH


def _is_tty(stream: TextIO) -> bool:
    """Check whether the output stream is connected to a terminal."""
    try:
        return stream.isatty()
    except AttributeError:
        return False


def _warn_negative_times(results: list[FunctionProfile]) -> None:
    """Warn to stderr if any functions have negative total_time."""
    neg = [fp for fp in results if fp.total_time < 0]
    if not neg:
        return
    names = ", ".join(f"{fp.module}.{fp.name}" for fp in neg[:3])
    suffix = f" (and {len(neg) - 3} more)" if len(neg) > 3 else ""
    print(
        f"Warning: {len(neg)} function(s) have negative times"
        f" ({names}{suffix}). Data may be corrupt.",
        file=sys.stderr,
    )


def _filter_and_select(
    results: list[FunctionProfile],
    filter_pattern: str | None,
    top: int | None,
) -> tuple[list[FunctionProfile] | None, list[FunctionProfile]]:
    """Apply filter and top-N selection, returning (filtered, display)."""
    if filter_pattern is not None:
        patterns = [p.strip() for p in filter_pattern.split(",")]
        results = [
            fp
            for fp in results
            if any(fnmatch.fnmatch(f"{fp.module}.{fp.name}", p) for p in patterns)
        ]
        if not results:
            return None, []
    display = results[:top] if top is not None else results
    return results, display


def _print_header_block(
    scope: str,
    n_called: int,
    n_registered: int | None,
    grand_total: float,
    tu: _TimeUnit,
    unit: str,
    stream: TextIO,
    is_tty: bool = False,
    wall_time: float | None = None,
) -> None:
    """Print the results header block (scope, coverage, total, unit)."""
    if n_registered is not None:
        coverage = f"{n_called} of {n_registered} functions called"
    else:
        s = "s" if n_called != 1 else ""
        coverage = f"{n_called} function{s}"
    total_str = (
        f"{grand_total * tu.multiplier:.{tu.total_prec}f}"[:_MAX_NUM_WIDTH] + tu.label
    )
    unit_str = f"{tu.label} (auto)" if unit == "auto" else tu.label
    prefix = "\U0001f525 " if is_tty else ""
    print(f"  {prefix}Lazyline results for {scope}", file=stream)
    if wall_time is not None:
        wall_fmt = (
            f"{wall_time * tu.multiplier:.{tu.total_prec}f}"[:_MAX_NUM_WIDTH] + tu.label
        )
        wall_str = f" | Wall time: {wall_fmt}"
    else:
        wall_str = ""
    print(
        f"  {coverage} | Total: {total_str}{wall_str} | Unit: {unit_str}", file=stream
    )
    if wall_time is not None and wall_time > 0 and grand_total > wall_time * 1.5:
        print("  (total includes parallel worker time)", file=stream)


def print_summary(
    results: list[FunctionProfile],
    *,
    top: int | None = None,
    compact: bool = True,
    summary: bool = False,
    filter_pattern: str | None = None,
    stream: TextIO | None = None,
    width: int | None = None,
    unit: str = "auto",
    scope: str | None = None,
    n_registered: int | None = None,
    wall_time: float | None = None,
) -> None:
    """Print a ranked summary of profiling results.

    Parameters
    ----------
    results
        Function profiles sorted by total time (descending).
    top
        If set, only show the top N functions.
    compact
        If True, collapse consecutive un-hit lines into ``...``.
    summary
        If True, print only the summary table (no per-line detail).
    filter_pattern
        If set, only show functions whose qualified name matches
        the given fnmatch pattern (e.g., ``"*Matcher*"``).
    stream
        Output stream. Defaults to ``sys.stdout``.
    width
        Terminal width override. Auto-detected when ``None``.
    unit
        Time display unit: ``"s"``, ``"ms"``, ``"us"``, ``"ns"``, or ``"auto"``.
    scope
        Scope name for the results header block. When ``None``, no header is shown.
    n_registered
        Total registered functions for coverage display (``N of M``).
    wall_time
        Wall-clock execution time in seconds, displayed in the header.
    """
    if unit != "auto" and unit not in _UNITS:
        raise ValueError(f"unit must be one of: {', '.join(sorted(_UNITS))} or 'auto'")

    stream = stream or sys.stdout

    term_width = _get_width(stream, width)
    tty = _is_tty(stream)
    lexer, formatter = _make_highlighter(tty)
    if tty:
        sep, dsep = _DIM_PIPE, f" {_DIM_PIPE}"
        stats_sep = f" {_DIM_PIPE} "
    else:
        sep, dsep = " ", "  "
        stats_sep = " | "

    banner = "=" * term_width

    print("", file=stream)
    print(banner, file=stream)

    if not results:
        print("\nNo profiling data collected.", file=stream)
        return

    _warn_negative_times(results)
    grand_total = sum(fp.total_time for fp in results)
    n_called = len(results)

    filtered, display = _filter_and_select(results, filter_pattern, top)
    if filtered is None:
        print(f"No functions matching '{filter_pattern}'.", file=stream)
        return
    results = filtered
    show_memory = any(fp.memory is not None for fp in results)

    tu = _auto_select_unit(display) if unit == "auto" else _UNITS[unit]
    total_w, tpc_w, time_w, tph_w = _compute_col_widths(display, tu)
    tph_w += 2  # Extra padding for readability.
    # Ensure grand total fits in the Total column too (capped).
    grand_fmt_w = min(
        len(f"{grand_total * tu.multiplier:.{tu.total_prec}f}"), _MAX_NUM_WIDTH
    )
    total_w = max(total_w, grand_fmt_w)
    # Fixed numeric columns: ' '+total_w+' '+8(%Total)+' '+8(Calls)+' '+tpc_w
    fixed = 4 + total_w + 16 + tpc_w + (11 if show_memory else 0)
    name_width = max(term_width - fixed, _MIN_NAME_WIDTH)
    # Widen term_width for separators/banner if MIN_NAME_WIDTH caused overflow.
    term_width = max(term_width, name_width + fixed)
    banner = "=" * term_width

    if scope is not None:
        _print_header_block(
            scope,
            n_called,
            n_registered,
            grand_total,
            tu,
            unit,
            stream,
            tty,
            wall_time,
        )

    total_hdr = f"Total ({tu.label})"
    tpc_hdr = f"Time/Call ({tu.label})"
    mem_col = f"{sep}{'Net Mem':>10}" if show_memory else ""

    if top is not None and top < len(results):
        print(
            f"\nShowing top {len(display)} of {len(results)} functions.",
            file=stream,
        )

    print("", file=stream)
    print("Summary", file=stream)
    print("", file=stream)
    print(
        f"{'Function':<{name_width}}{sep}{total_hdr:>{total_w}}{sep}{'% Total':>8}"
        f"{sep}{'Calls':>8}{sep}{tpc_hdr:>{tpc_w}}{mem_col}",
        file=stream,
    )
    print("-" * term_width, file=stream)

    tp, dp = tu.total_prec, tu.detail_prec
    m = tu.multiplier
    for fp in display:
        pct = (fp.total_time / grand_total * 100) if grand_total > 0 else 0.0
        name = _qualified_name(fp, max_len=name_width - 1)
        mem_str = f"{sep}{_format_memory(fp.memory):>10}" if show_memory else ""
        tpc = fp.total_time / fp.call_count if fp.call_count > 0 else 0.0
        line = (
            f"{name:<{name_width}}{sep}{_fmt_num(fp.total_time * m, total_w, tp)}"
            f"{sep}{pct:>7.1f}%"
        )
        tpc_str = _fmt_num(tpc * m, tpc_w, dp)
        print(
            f"{line}{sep}{fp.call_count:>8}{sep}{tpc_str}{mem_str}",
            file=stream,
        )

    print("-" * term_width, file=stream)
    print(
        f"{'Total':<{name_width}}{sep}{_fmt_num(grand_total * m, total_w, tp)}",
        file=stream,
    )
    print("", file=stream)

    if summary:
        return

    print("Functions", file=stream)
    print("", file=stream)
    for fp in display:
        _print_function_detail(
            fp,
            stream=stream,
            show_memory=show_memory,
            compact=compact,
            term_width=term_width,
            tu=tu,
            time_w=time_w,
            tph_w=tph_w,
            dsep=dsep,
            is_tty=tty,
            stats_sep=stats_sep,
            lexer=lexer,
            formatter=formatter,
        )


def _print_function_detail(
    fp: FunctionProfile,
    *,
    stream: TextIO,
    show_memory: bool = False,
    compact: bool = False,
    term_width: int = _DEFAULT_WIDTH,
    tu: _TimeUnit = _UNITS["s"],
    time_w: int = 10,
    tph_w: int = 13,
    dsep: str = "  ",
    is_tty: bool = False,
    stats_sep: str = " | ",
    lexer: Lexer | None = None,
    formatter: Formatter | None = None,
) -> None:
    """Print per-line detail for a single function, showing full source."""
    if not fp.lines:
        return

    func_total = fp.total_time
    qual_name = f"{fp.module}.{fp.name}"
    short_path = _shorten_path(fp.filename, fp.module)
    loc = f"({short_path}:{fp.start_line})"
    header = f"{qual_name} {loc}"
    if len(header) > term_width:
        # Truncate the file path from the left to keep filename:line visible.
        prefix = f"{qual_name} ("
        suffix = f":{fp.start_line})"
        available = term_width - len(prefix) - len(suffix)
        if available > 3:
            path = "..." + short_path[-(available - 3) :]
            loc = f"({path}{suffix}"
            header = f"{qual_name} {loc}"
        else:
            header = header[: term_width - 3] + "..."
    if is_tty:
        # Bold the qualified name only, not the file path.
        bold_name = f"{_BOLD_START}{qual_name}{_BOLD_END}"
        print(header.replace(qual_name, bold_name, 1), file=stream)
    else:
        print(header, file=stream)

    tp, dp = tu.total_prec, tu.detail_prec
    m = tu.multiplier
    tpc = func_total / fp.call_count if fp.call_count > 0 else 0.0
    fmt_total = f"{func_total * m:.{tp}f}"[:_MAX_NUM_WIDTH]
    fmt_tpc = f"{tpc * m:.{dp}f}"[:_MAX_NUM_WIDTH]
    stats_parts = [
        f"{fmt_total}{tu.label} total",
        f"{fp.call_count} calls",
        f"{fmt_tpc}{tu.label}/call",
    ]
    if show_memory and fp.memory is not None:
        stats_parts.append(f"{_format_memory(fp.memory)} net mem")
    print(stats_sep.join(stats_parts), file=stream)
    print("", file=stream)

    time_hdr = f"Time ({tu.label})"
    tph_hdr = f"Time/Hit ({tu.label})"
    mem_hdr = f"{dsep}{'Net Mem':>10}" if show_memory else ""
    cols = (
        f"{'Line':>6}{dsep}{'Hits':>8}"
        f"{dsep}{time_hdr:>{time_w}}{dsep}{tph_hdr:>{tph_w}}"
        f"{dsep}{'% Func':>8}{mem_hdr}{dsep}{'Source'}"
    )
    print(cols, file=stream)
    print("-" * term_width, file=stream)

    lines = _prepare_lines(fp, compact)
    for i, lp in enumerate(lines):
        if lp is _ELLIPSIS_SENTINEL:
            _print_ellipsis(stream, show_memory, time_w, tph_w, dsep)
        else:
            # In compact mode, don't dim the first line (def line) — it
            # anchors the reader even though it has zero hits.
            assert isinstance(lp, LineProfile)
            dim_unhit = is_tty and not (compact and i == 0)
            _print_line(
                lp,
                func_total,
                stream,
                show_memory,
                tu,
                time_w,
                tph_w,
                dsep,
                dim_unhit,
                lexer,
                formatter,
            )

    print("", file=stream)
    print("", file=stream)


_ELLIPSIS_SENTINEL = object()


def _prepare_lines(fp: FunctionProfile, compact: bool) -> list[LineProfile | object]:
    """Build the list of lines to print, inserting ellipsis markers in compact mode."""
    if any(lp.source for lp in fp.lines):
        raw_lines: list[LineProfile | object] = fp.lines  # ty: ignore
    else:
        raw_lines = _fill_source_from_cache(fp)

    if not compact:
        return raw_lines  # ty: ignore[invalid-return-type]

    # Compact: keep function header (first line), hit lines, and insert
    # ellipsis markers for collapsed consecutive un-hit lines.
    result: list[LineProfile | object] = []
    skipping = False
    for i, lp in enumerate(raw_lines):
        is_header = i == 0
        is_hit = lp.hits > 0
        if is_header or is_hit:
            skipping = False
            result.append(lp)
        elif not skipping:
            result.append(_ELLIPSIS_SENTINEL)
            skipping = True
    return result


_SAFE_SOURCE_SUFFIXES: Final[frozenset[str]] = frozenset({".py", ".pyw"})


def _fill_source_from_cache(fp: FunctionProfile) -> list[LineProfile]:
    """Build lines with source from linecache (legacy/non-enriched path).

    Only reads from linecache when the filename has a Python extension
    to prevent arbitrary file reads from crafted JSON inputs.
    """
    safe = any(fp.filename.endswith(s) for s in _SAFE_SOURCE_SUFFIXES)
    timing_by_line = {lp.lineno: lp for lp in fp.lines}
    last_profiled_line = max(lp.lineno for lp in fp.lines)
    result: list[LineProfile] = []
    for lineno in range(fp.start_line, last_profiled_line + 1):
        source = linecache.getline(fp.filename, lineno).rstrip() if safe else ""
        lp = timing_by_line.get(lineno)
        if lp is not None:
            result.append(
                LineProfile(
                    lineno=lineno,
                    hits=lp.hits,
                    time=lp.time,
                    source=source,
                    memory=lp.memory,
                )
            )
        else:
            result.append(LineProfile(lineno=lineno, hits=0, time=0.0, source=source))
    return result


def _shorten_path(filename: str, module: str) -> str:
    """Shorten a file path by stripping the prefix before the root package.

    Given ``module="my_package.extract"`` and a filename like
    ``/home/user/.venv/.../my_package/extract.py``, returns
    ``my_package/extract.py``. Uses ``rfind`` so that if the root
    name appears earlier in the path (e.g., ``/home/my_package/...``), the
    last (correct) occurrence is used.
    """
    root = module.split(".")[0]
    sep = "/" + root + "/"
    idx = filename.rfind(sep)
    if idx != -1:
        return filename[idx + 1 :]
    return filename


def _qualified_name(fp: FunctionProfile, max_len: int = 69) -> str:
    """Build a qualified function name, truncating at dot boundaries."""
    name = f"{fp.module}.{fp.name}"
    if len(name) <= max_len:
        return name
    if max_len <= 3:
        return name[:max_len]
    tail = name[-(max_len - 3) :]
    # Snap to the next dot boundary to avoid cutting mid-word.
    dot = tail.find(".")
    if dot != -1 and dot < len(tail) - 1:
        tail = tail[dot + 1 :]
    return "..." + tail


def _print_line(
    lp: LineProfile,
    func_total: float,
    stream: TextIO,
    show_memory: bool,
    tu: _TimeUnit = _UNITS["s"],
    time_w: int = 10,
    tph_w: int = 13,
    dsep: str = "  ",
    dim_unhit: bool = False,
    lexer: Lexer | None = None,
    formatter: Formatter | None = None,
) -> None:
    """Print a single profiled line (hit or non-hit)."""
    if lp.hits > 0:
        pct = (lp.time / func_total * 100) if func_total > 0 else 0.0
        tph = lp.time / lp.hits if lp.hits > 0 else 0.0
        dp = tu.detail_prec
        m = tu.multiplier
        mem_str = f"{dsep}{_format_memory(lp.memory):>10}" if show_memory else ""
        t_str = _fmt_num(lp.time * m, time_w, dp)
        h_str = _fmt_num(tph * m, tph_w, dp)
        source = _highlight_source(lp.source, lexer, formatter)
        cols = f"{lp.lineno:>6}{dsep}{lp.hits:>8}{dsep}{t_str}{dsep}{h_str}"
        print(f"{cols}{dsep}{pct:>7.1f}%{mem_str}{dsep}{source}", file=stream)
    else:
        # Use plain separators for un-hit lines so the outer dim ANSI is
        # not interrupted by the reset inside _DIM_PIPE.
        plain = "  " if dim_unhit else dsep
        mem_pad = f"{plain}{'':>10}" if show_memory else ""
        # Highlight non-dimmed un-hit lines (e.g. def line in compact mode).
        source = (
            lp.source if dim_unhit else _highlight_source(lp.source, lexer, formatter)
        )
        line = (
            f"{lp.lineno:>6}{plain}{'':>8}{plain}{'':>{time_w}}{plain}{'':>{tph_w}}"
            f"{plain}{'':>8}{mem_pad}{plain}{source}"
        )
        if dim_unhit:
            line = f"{_DIM_START}{line}{_DIM_END}"
        print(line, file=stream)


def _print_ellipsis(
    stream: TextIO,
    show_memory: bool,
    time_w: int = 10,
    tph_w: int = 13,
    dsep: str = "  ",
) -> None:
    """Print a collapsed-lines marker for compact mode."""
    mem_pad = f"{dsep}{'':>10}" if show_memory else ""
    print(
        f"{'':>6}{dsep}{'':>8}{dsep}{'':>{time_w}}{dsep}{'':>{tph_w}}"
        f"{dsep}{'':>8}{mem_pad}{dsep}...",
        file=stream,
    )


def _format_memory(nbytes: float | None) -> str:
    """Format a byte count for display, auto-scaling to B/KB/MB/GB."""
    if nbytes is None:
        return ""
    sign = "-" if nbytes < 0 else ""
    value = abs(nbytes)
    if value < 1024:
        return f"{sign}{value:.0f} B"
    if value < 1024 * 1024:
        return f"{sign}{value / 1024:.1f} KB"
    if value < 1024 * 1024 * 1024:
        return f"{sign}{value / (1024 * 1024):.1f} MB"
    return f"{sign}{value / (1024 * 1024 * 1024):.1f} GB"
