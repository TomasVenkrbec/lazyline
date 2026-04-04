import io
import linecache

from lazyline.models import FunctionProfile, LineProfile
from lazyline.reporting import (
    _format_memory,
    _normalize_patterns,
    _qualified_name,
    _shorten_path,
    print_summary,
)


def _make_source_file(tmp_path):
    """Create a temp source file and prime linecache."""
    src = tmp_path / "mod.py"
    src.write_text(
        "def fast():\n    x = 1\n    return x\n\ndef empty_func():\n    pass\n"
    )
    linecache.checkcache(str(src))
    return str(src)


def test_print_summary_empty():
    stream = io.StringIO()
    print_summary([], stream=stream)
    assert "No profiling data collected." in stream.getvalue()


def test_print_summary_with_results(tmp_path):
    filename = _make_source_file(tmp_path)
    results = [
        FunctionProfile(
            module="mod",
            name="fast",
            filename=filename,
            start_line=1,
            total_time=0.5,
            call_count=10,
            lines=[
                LineProfile(lineno=2, hits=10, time=0.3),
                LineProfile(lineno=3, hits=10, time=0.2),
            ],
        ),
        FunctionProfile(
            module="mod",
            name="empty_func",
            filename=filename,
            start_line=5,
            total_time=0.1,
            call_count=1,
            lines=[],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "mod.fast" in output
    assert "mod.empty_func" in output
    assert "Total" in output
    # Function-level stats line before per-line detail (auto selects ms for 0.5s)
    assert "500.00ms total | 10 calls | 50.0000ms/call" in output
    # Per-line detail shows full source including def line
    assert "def fast():" in output
    assert "x = 1" in output


def test_print_summary_top_limits_output():
    results = [
        FunctionProfile(
            module="a",
            name="slow",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
        FunctionProfile(
            module="b",
            name="fast",
            filename="b.py",
            start_line=1,
            total_time=0.01,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.01)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, top=1, stream=stream)
    output = stream.getvalue()
    assert "a.slow" in output
    assert "b.fast" not in output


def test_print_summary_long_name_truncated():
    long_module = "a" * 70
    results = [
        FunctionProfile(
            module=long_module,
            name="func",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
    ]
    stream = io.StringIO()
    # Use width=80 to force truncation (name_width=37, max_len=36)
    print_summary(results, stream=stream, width=80)
    output = stream.getvalue()
    assert "..." in output


def test_print_summary_zero_total_time():
    results = [
        FunctionProfile(
            module="mod",
            name="noop",
            filename="mod.py",
            start_line=1,
            total_time=0.0,
            call_count=0,
            lines=[LineProfile(lineno=2, hits=0, time=0.0)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "mod.noop" in output


# --- memory column ---


def test_print_summary_with_memory(tmp_path):
    filename = _make_source_file(tmp_path)
    results = [
        FunctionProfile(
            module="mod",
            name="fast",
            filename=filename,
            start_line=1,
            total_time=0.5,
            call_count=10,
            lines=[
                LineProfile(lineno=2, hits=10, time=0.3, memory=2048.0),
                LineProfile(lineno=3, hits=10, time=0.2, memory=512.0),
            ],
            memory=2560.0,
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "Net Mem" in output
    assert "500.00ms total | 10 calls | 50.0000ms/call | 2.5 KB net mem" in output
    assert "2.0 KB" in output  # line-level: 2048 bytes


def test_print_summary_without_memory_no_column():
    results = [
        FunctionProfile(
            module="mod",
            name="f",
            filename="mod.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "Net Mem" not in output


# --- _format_memory ---


def test_format_memory_none():
    assert _format_memory(None) == ""


def test_format_memory_bytes():
    assert _format_memory(100.0) == "100 B"


def test_format_memory_kilobytes():
    assert _format_memory(2048.0) == "2.0 KB"


def test_format_memory_megabytes():
    assert _format_memory(2 * 1024 * 1024.0) == "2.0 MB"


def test_format_memory_negative():
    assert _format_memory(-4096.0) == "-4.0 KB"


def test_format_memory_gigabytes():
    assert _format_memory(2 * 1024 * 1024 * 1024.0) == "2.0 GB"


def test_format_memory_zero():
    assert _format_memory(0.0) == "0 B"


def test_print_summary_with_embedded_source():
    results = [
        FunctionProfile(
            module="mod",
            name="func",
            filename="/nonexistent/mod.py",
            start_line=1,
            total_time=0.5,
            call_count=10,
            lines=[
                LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
                LineProfile(lineno=2, hits=10, time=0.3, source="    x = 1"),
                LineProfile(lineno=3, hits=10, time=0.2, source="    return x"),
            ],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "mod.func" in output
    assert "def func():" in output
    assert "x = 1" in output
    assert "return x" in output


def test_print_summary_with_negative_memory(tmp_path):
    filename = _make_source_file(tmp_path)
    results = [
        FunctionProfile(
            module="mod",
            name="fast",
            filename=filename,
            start_line=1,
            total_time=0.5,
            call_count=10,
            lines=[
                LineProfile(lineno=2, hits=10, time=0.3, memory=-8192.0),
                LineProfile(lineno=3, hits=10, time=0.2, memory=512.0),
            ],
            memory=-7680.0,
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "Net Mem" in output
    assert "-8.0 KB" in output
    assert "-7.5 KB" in output


# --- compact mode ---


def _make_profile_with_docstring():
    """Create a profile with a function header, docstring, and hit lines."""
    return [
        FunctionProfile(
            module="mod",
            name="func",
            filename="/fake/mod.py",
            start_line=1,
            total_time=0.5,
            call_count=10,
            lines=[
                LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
                LineProfile(
                    lineno=2, hits=0, time=0.0, source='    """Docstring line 1.'
                ),
                LineProfile(lineno=3, hits=0, time=0.0, source=""),
                LineProfile(
                    lineno=4, hits=0, time=0.0, source='    Docstring line 3."""'
                ),
                LineProfile(lineno=5, hits=10, time=0.3, source="    x = slow()"),
                LineProfile(lineno=6, hits=0, time=0.0, source="    # comment"),
                LineProfile(lineno=7, hits=10, time=0.2, source="    return x"),
            ],
        ),
    ]


def test_compact_collapses_unhit_lines():
    results = _make_profile_with_docstring()
    stream = io.StringIO()
    print_summary(results, compact=True, stream=stream)
    output = stream.getvalue()
    # Function header (first line) is kept
    assert "def func():" in output
    # Hit lines are kept
    assert "x = slow()" in output
    assert "return x" in output
    # Docstring lines are collapsed
    assert "Docstring line 1" not in output
    assert "Docstring line 3" not in output
    # Ellipsis marker appears
    assert "..." in output


def test_compact_single_ellipsis_for_consecutive_unhit():
    results = _make_profile_with_docstring()
    stream = io.StringIO()
    print_summary(results, compact=True, stream=stream)
    output = stream.getvalue()
    # Lines 2-4 (docstring) should produce one ellipsis, not three
    lines = [x for x in output.splitlines() if "..." in x and "mod.func" not in x]
    # Two ellipsis groups: lines 2-4 and line 6
    assert len(lines) == 2


def test_compact_false_shows_all_lines():
    results = _make_profile_with_docstring()
    stream = io.StringIO()
    print_summary(results, compact=False, stream=stream)
    output = stream.getvalue()
    assert "Docstring line 1" in output
    assert "Docstring line 3" in output


def test_default_compact_hides_docstrings():
    """Default (compact=True) should hide un-hit docstring lines."""
    results = _make_profile_with_docstring()
    stream = io.StringIO()
    print_summary(results, stream=stream)  # default compact=True
    output = stream.getvalue()
    assert "Docstring line 1" not in output
    assert "..." in output  # ellipsis marker


def test_full_mode_shows_all_lines():
    """compact=False (--full) should show all lines including un-hit."""
    results = _make_profile_with_docstring()
    stream = io.StringIO()
    print_summary(results, compact=False, stream=stream)
    output = stream.getvalue()
    assert "Docstring line 1" in output
    assert "Docstring line 3" in output


# --- summary mode ---


def test_summary_shows_table_no_detail():
    results = _make_profile_with_docstring()
    stream = io.StringIO()
    print_summary(results, summary=True, stream=stream)
    output = stream.getvalue()
    # Summary table is present
    assert "mod.func" in output
    assert "Total" in output
    # Per-line detail is absent
    assert "def func():" not in output
    assert "x = slow()" not in output


def test_summary_with_top():
    results = [
        FunctionProfile(
            module="a",
            name="slow",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0, source="    x = 1")],
        ),
        FunctionProfile(
            module="b",
            name="fast",
            filename="b.py",
            start_line=1,
            total_time=0.01,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.01, source="    y = 2")],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, top=1, summary=True, stream=stream)
    output = stream.getvalue()
    assert "a.slow" in output
    assert "b.fast" not in output
    assert "x = 1" not in output  # no per-line detail


# --- top N of M header ---


def test_top_shows_showing_n_of_m():
    results = [
        FunctionProfile(
            module="a",
            name="f1",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
        FunctionProfile(
            module="b",
            name="f2",
            filename="b.py",
            start_line=1,
            total_time=0.5,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.5)],
        ),
        FunctionProfile(
            module="c",
            name="f3",
            filename="c.py",
            start_line=1,
            total_time=0.1,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.1)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, top=2, stream=stream)
    output = stream.getvalue()
    assert "Showing top 2 of 3 functions" in output
    # Total should include ALL functions, not just displayed
    # auto selects s for max=1.0s
    assert "1.6000" in output


def test_no_showing_header_without_top():
    results = [
        FunctionProfile(
            module="a",
            name="f1",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, stream=stream)
    output = stream.getvalue()
    assert "Showing top" not in output


# --- filter ---


def test_filter_matches_functions():
    results = [
        FunctionProfile(
            module="pkg.matchers",
            name="search",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0, source="    x = 1")],
        ),
        FunctionProfile(
            module="pkg.utils",
            name="helper",
            filename="b.py",
            start_line=1,
            total_time=0.5,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.5, source="    y = 2")],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, filter_pattern="*matchers*", stream=stream)
    output = stream.getvalue()
    assert "pkg.matchers.search" in output
    assert "pkg.utils.helper" not in output


def test_filter_no_match():
    results = [
        FunctionProfile(
            module="pkg",
            name="func",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, filter_pattern="*nonexistent*", stream=stream)
    output = stream.getvalue()
    assert "No functions matching" in output


def test_filter_multiple_comma_separated():
    results = [
        FunctionProfile(
            module="pkg.matchers",
            name="match",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0, source="x = 1")],
        ),
        FunctionProfile(
            module="pkg.extract",
            name="run",
            filename="b.py",
            start_line=1,
            total_time=0.5,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.5, source="y = 2")],
        ),
        FunctionProfile(
            module="pkg.other",
            name="skip",
            filename="c.py",
            start_line=1,
            total_time=0.1,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=0.1, source="z = 3")],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, filter_pattern="*matchers*,*extract*", stream=stream)
    output = stream.getvalue()
    assert "match" in output
    assert "run" in output
    assert "skip" not in output


def test_no_showing_header_when_top_equals_total():
    results = [
        FunctionProfile(
            module="a",
            name="f1",
            filename="a.py",
            start_line=1,
            total_time=1.0,
            call_count=1,
            lines=[LineProfile(lineno=2, hits=1, time=1.0)],
        ),
    ]
    stream = io.StringIO()
    print_summary(results, top=5, stream=stream)
    output = stream.getvalue()
    assert "Showing top" not in output


# --- terminal width ---


def _simple_result(module="mod", name="func"):
    """Create a minimal FunctionProfile for width tests."""
    return FunctionProfile(
        module=module,
        name=name,
        filename="mod.py",
        start_line=1,
        total_time=1.0,
        call_count=1,
        lines=[LineProfile(lineno=2, hits=1, time=1.0, source="    x = 1")],
    )


def test_width_non_tty_default():
    stream = io.StringIO()
    print_summary([_simple_result()], stream=stream)
    output = stream.getvalue()
    lines = output.splitlines()
    banner = [ln for ln in lines if ln and all(c == "=" for c in ln)]
    assert banner
    assert len(banner[0]) == 120


def test_width_explicit_override():
    stream = io.StringIO()
    print_summary([_simple_result()], stream=stream, width=80)
    output = stream.getvalue()
    lines = output.splitlines()
    banner = [ln for ln in lines if ln and all(c == "=" for c in ln)]
    assert banner
    assert len(banner[0]) == 80


def test_narrow_terminal_truncates_names():
    # width=80, no mem, auto→s: fixed=42, name_width=38, max_len=37
    long_name = "a" * 40  # "a"*40 + ".func" = 45 > 37 → truncated
    stream = io.StringIO()
    print_summary([_simple_result(module=long_name)], stream=stream, width=80)
    output = stream.getvalue()
    # Name should be truncated with ...
    data_lines = [ln for ln in output.splitlines() if "..." in ln and "func" in ln]
    assert data_lines


def test_wide_terminal_preserves_names():
    # At width=200 (no mem, auto→s): fixed = 42, name_width = 158, max_len = 157
    long_name = "a" * 100  # 100 + ".func" = 105 < 157 → NOT truncated
    stream = io.StringIO()
    print_summary([_simple_result(module=long_name)], stream=stream, width=200)
    output = stream.getvalue()
    assert f"{'a' * 100}.func" in output


def test_min_name_width_floor():
    # At width=50 (no mem, auto→s): max(50 - 42, 30) = 30 (floor kicks in)
    stream = io.StringIO()
    print_summary([_simple_result()], stream=stream, width=50)
    output = stream.getvalue()
    # Function header should still be present (name fits in 30 chars)
    assert "mod.func" in output
    # Banner should be 50 wide
    banner = [ln for ln in output.splitlines() if ln and all(c == "=" for c in ln)]
    assert len(banner[0]) == 50


def test_width_with_memory():
    # At width=100 (with mem, auto→s): fixed = 4+9+16+13+11 = 53, name_width = 47
    result = FunctionProfile(
        module="a" * 50,
        name="func",
        filename="mod.py",
        start_line=1,
        total_time=1.0,
        call_count=1,
        lines=[
            LineProfile(lineno=2, hits=1, time=1.0, source="    x = 1", memory=100.0)
        ],
        memory=100.0,
    )
    stream = io.StringIO()
    print_summary([result], stream=stream, width=100)
    output = stream.getvalue()
    assert "Net Mem" in output
    # Name "a"*50 + ".func" = 55 chars > 46 → truncated
    data_lines = [ln for ln in output.splitlines() if "..." in ln and "func" in ln]
    assert data_lines


def test_detail_separator_matches_width():
    stream = io.StringIO()
    print_summary([_simple_result()], stream=stream, width=100)
    output = stream.getvalue()
    # Detail separator is full-width dashes (no indent)
    lines = output.splitlines()
    # Find the detail separator: a dash-only line that follows the column header
    detail_sep = None
    for i, ln in enumerate(lines):
        if "Line" in ln and "Hits" in ln:
            detail_sep = lines[i + 1]
            break
    assert detail_sep is not None
    assert all(c == "-" for c in detail_sep)
    assert len(detail_sep) == 100


def test_banner_matches_width():
    stream = io.StringIO()
    print_summary([_simple_result()], stream=stream, width=100)
    output = stream.getvalue()
    banner = [ln for ln in output.splitlines() if ln and all(c == "=" for c in ln)]
    assert len(banner[0]) == 100


def test_function_header_truncated():
    long_module = "very.long.deeply.nested.module.path.that.exceeds"
    result = FunctionProfile(
        module=long_module,
        name="function_name",
        filename="/some/very/long/path/to/the/module/file.py",
        start_line=1,
        total_time=1.0,
        call_count=1,
        lines=[LineProfile(lineno=2, hits=1, time=1.0, source="    x = 1")],
    )
    stream = io.StringIO()
    print_summary([result], stream=stream, width=60)
    output = stream.getvalue()
    # Path is truncated from the left (...tail), keeping :line) at the end
    header_line = [
        ln
        for ln in output.splitlines()
        if long_module[:10] in ln and "..." in ln and ln.endswith(":1)")
    ]
    assert header_line


# --- time unit ---


def _result_with_time(total_time=0.5, call_count=10):
    """Create a FunctionProfile with a specific total_time."""
    return FunctionProfile(
        module="mod",
        name="func",
        filename="/fake/mod.py",
        start_line=1,
        total_time=total_time,
        call_count=call_count,
        lines=[
            LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
            LineProfile(
                lineno=2,
                hits=call_count,
                time=total_time,
                source="    x = 1",
            ),
        ],
    )


def test_unit_auto_is_default():
    # Default unit is auto; 0.5s max → selects ms
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream)
    output = stream.getvalue()
    assert "Total (ms)" in output
    assert "Time/Call (ms)" in output
    assert "500.00" in output


def test_unit_milliseconds():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="ms")
    output = stream.getvalue()
    assert "Total (ms)" in output
    assert "Time/Call (ms)" in output
    assert "500.00" in output  # 0.5 * 1e3, .2f


def test_unit_microseconds():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="us")
    output = stream.getvalue()
    assert "Total (us)" in output
    assert "Time/Call (us)" in output
    assert "500000.0" in output  # 0.5 * 1e6, .1f


def test_unit_nanoseconds():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="ns")
    output = stream.getvalue()
    assert "Total (ns)" in output
    assert "Time/Call (ns)" in output
    assert "500000000" in output  # 0.5 * 1e9, .0f


def test_auto_selects_seconds():
    stream = io.StringIO()
    print_summary([_result_with_time(total_time=2.0)], stream=stream, unit="auto")
    assert "Total (s)" in stream.getvalue()


def test_auto_selects_milliseconds():
    stream = io.StringIO()
    print_summary([_result_with_time(total_time=0.005)], stream=stream, unit="auto")
    assert "Total (ms)" in stream.getvalue()


def test_auto_selects_microseconds():
    stream = io.StringIO()
    print_summary([_result_with_time(total_time=0.000050)], stream=stream, unit="auto")
    assert "Total (us)" in stream.getvalue()


def test_auto_selects_nanoseconds():
    stream = io.StringIO()
    print_summary([_result_with_time(total_time=5e-8)], stream=stream, unit="auto")
    assert "Total (ns)" in stream.getvalue()


def test_auto_empty_defaults_to_seconds():
    stream = io.StringIO()
    # Empty results after filter → "No functions matching" path, but the banner
    # is still printed with default unit. Test via direct auto with empty list.
    print_summary([], stream=stream, unit="auto")
    output = stream.getvalue()
    # No data → no headers printed, but no crash
    assert "No profiling data collected." in output


def test_ms_precision():
    stream = io.StringIO()
    # total_time=0.5s, call_count=10 → time/call=0.05s=50ms
    print_summary([_result_with_time()], stream=stream, unit="ms")
    output = stream.getvalue()
    # Summary total: 500.00 (.2f)
    assert "500.00" in output
    # Per-function stats line: value-first format
    assert "500.00ms total" in output
    assert "50.0000ms/call" in output


def test_tpc_column_wider_for_ms():
    # At width=120 with unit "s": fixed = 4+9+16+13 = 42, name_width = 78
    # At width=120 with unit "ms": fixed = 4+10+16+14 = 44, name_width = 76
    stream_s = io.StringIO()
    stream_ms = io.StringIO()
    results = [_result_with_time()]
    print_summary(results, stream=stream_s, width=120, unit="s", summary=True)
    print_summary(results, stream=stream_ms, width=120, unit="ms", summary=True)

    hdr_s = [ln for ln in stream_s.getvalue().splitlines() if "Function" in ln][0]
    hdr_ms = [ln for ln in stream_ms.getvalue().splitlines() if "Function" in ln][0]
    # Both lines span full terminal width, but the Function column is 1 char
    # narrower with "ms" (tpc_width 14 vs 13), so the gap before "Total"
    # shrinks by 1. Verify via position of "Total" in each header.
    assert hdr_s.index("Total") > hdr_ms.index("Total")


def test_function_stats_line_ms():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="ms")
    output = stream.getvalue()
    assert "500.00ms total | 10 calls | 50.0000ms/call" in output


def test_detail_headers_show_unit():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="us")
    output = stream.getvalue()
    assert "Time (us)" in output
    assert "Time/Hit (us)" in output


def test_auto_zero_times_defaults_to_seconds():
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=0.0, call_count=0)], stream=stream, unit="auto"
    )
    assert "Total (s)" in stream.getvalue()


def test_auto_uses_max_not_median():
    # Many fast functions (~50ns) + one slow function (2s).
    # Median would pick "ns" → 2_000_000_000 ns (unreadable).
    # Max picks "s" so the slow function stays readable.
    fast = [_result_with_time(total_time=5e-8, call_count=1) for _ in range(20)]
    slow = [_result_with_time(total_time=2.0, call_count=1)]
    stream = io.StringIO()
    print_summary(fast + slow, stream=stream, unit="auto")
    assert "Total (s)" in stream.getvalue()


def test_invalid_unit_raises_valueerror():
    import pytest

    with pytest.raises(ValueError, match="unit must be one of"):
        print_summary([_result_with_time()], stream=io.StringIO(), unit="invalid")


def test_ns_column_overflow_aligned():
    # 100s in ns = 100_000_000_000 — overflows the default 10-char Total column.
    # Dynamic column widths should keep header and data aligned.
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=100.0, call_count=1)],
        stream=stream,
        unit="ns",
        width=200,
    )
    output = stream.getvalue()
    lines = output.splitlines()
    hdr = [ln for ln in lines if "Function" in ln and "Total (ns)" in ln]
    data = [ln for ln in lines if "mod.func" in ln and "100000000000" in ln]
    assert hdr
    assert data
    # The value right-aligns in the same field as the header — verify both present
    assert "100000000000" in data[0]


def test_grand_total_wider_than_individual():
    # 5 functions at 0.5s each → grand_total=2.5s. In ns: individual=500000000
    # (9 digits), but grand_total=2500000000 (10 digits). Column must fit both.
    results = [_result_with_time(total_time=0.5, call_count=1) for _ in range(5)]
    # Give each a unique name so they're distinguishable
    for i, fp in enumerate(results):
        results[i] = FunctionProfile(
            module=f"mod{i}",
            name="func",
            filename=fp.filename,
            start_line=fp.start_line,
            total_time=fp.total_time,
            call_count=fp.call_count,
            lines=fp.lines,
        )
    stream = io.StringIO()
    print_summary(results, stream=stream, unit="ns", width=200, summary=True)
    output = stream.getvalue()
    # Grand total = 2500000000 must appear in the Total footer row
    assert "2500000000" in output


# --- Dim column separator tests ---


def _tty_stream():
    """Create a StringIO that reports itself as a TTY."""
    stream = io.StringIO()
    stream.isatty = lambda: True  # type: ignore[attr-defined]
    return stream


def test_dim_separators_in_tty_mode():
    stream = _tty_stream()
    print_summary([_result_with_time()], stream=stream, width=120)
    output = stream.getvalue()
    dim_pipe = "\033[2;90m│\033[0m"
    # Summary table header and data rows contain dim pipes
    lines = output.splitlines()
    header = [ln for ln in lines if "Function" in ln and "Total" in ln][0]
    assert dim_pipe in header
    data = [ln for ln in lines if "mod.func" in ln][0]
    assert dim_pipe in data
    # Detail section also contains dim pipes
    detail_hdr = [ln for ln in lines if "Line" in ln and "Hits" in ln][0]
    assert dim_pipe in detail_hdr


def test_no_dim_separators_in_non_tty():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, width=120)
    output = stream.getvalue()
    assert "\033[" not in output


def test_dim_separators_with_memory_columns():
    result = FunctionProfile(
        module="mod",
        name="func",
        filename="/fake/mod.py",
        start_line=1,
        total_time=1.0,
        call_count=5,
        memory=2560.0,
        lines=[
            LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
            LineProfile(lineno=2, hits=5, time=1.0, source="    x = 1", memory=2560.0),
        ],
    )
    stream = _tty_stream()
    print_summary([result], stream=stream, width=140)
    output = stream.getvalue()
    dim_pipe = "\033[2;90m│\033[0m"
    # Memory column in summary table should also use dim pipe
    lines = output.splitlines()
    header = [ln for ln in lines if "Net Mem" in ln and "Function" in ln][0]
    assert dim_pipe in header
    # Memory column in detail section
    detail_hdr = [ln for ln in lines if "Net Mem" in ln and "Line" in ln][0]
    assert dim_pipe in detail_hdr


# --- Results header block tests ---


def test_results_header_shows_scope():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="acme.pkg")
    output = stream.getvalue()
    assert "Lazyline results for acme.pkg" in output
    # Single top banner line (visual boundary), no bottom banner around title
    banners = [ln for ln in output.splitlines() if ln and all(c == "=" for c in ln)]
    assert len(banners) == 1
    # Section labels present
    assert "Summary" in output
    assert "Functions" in output


def test_non_py_filename_not_read_by_linecache():
    """Filenames without .py extension should not be read via linecache."""
    result = FunctionProfile(
        module="mod",
        name="func",
        filename="/etc/shadow",
        start_line=1,
        total_time=0.1,
        call_count=1,
        lines=[LineProfile(lineno=1, hits=1, time=0.1, source="")],
    )
    stream = io.StringIO()
    print_summary([result], stream=stream, unit="ms")
    output = stream.getvalue()
    # Should not contain any content from /etc/shadow
    assert "root:" not in output


def test_columns_env_var_respected_in_non_tty(monkeypatch):
    """COLUMNS env var should control width for non-tty output."""
    monkeypatch.setenv("COLUMNS", "80")
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="pkg")
    lines = stream.getvalue().splitlines()
    banners = [ln for ln in lines if ln and all(c == "=" for c in ln)]
    assert banners
    assert len(banners[0]) == 80


def test_emoji_suppressed_in_non_tty():
    """Emoji should not appear in non-tty (StringIO) output."""
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="pkg")
    output = stream.getvalue()
    assert "\U0001f525" not in output
    assert "Lazyline results for pkg" in output


def test_wall_time_in_header():
    stream = io.StringIO()
    print_summary(
        [_result_with_time()], stream=stream, scope="pkg", wall_time=31.5, unit="s"
    )
    output = stream.getvalue()
    assert "Wall time: 31.5000s" in output


def test_wall_time_respects_unit():
    """Wall-clock time should use the same unit as the rest of the report."""
    stream = io.StringIO()
    print_summary(
        [_result_with_time()], stream=stream, scope="pkg", wall_time=0.5, unit="ms"
    )
    output = stream.getvalue()
    assert "Wall time: 500.00ms" in output


def test_shorten_path_strips_prefix():
    result = _shorten_path(
        "/home/user/.venv/lib/python3.12/site-packages/acme/toolkit/extract.py",
        "acme.toolkit.extract",
    )
    assert result == "acme/toolkit/extract.py"


def test_shorten_path_no_match():
    result = _shorten_path("/some/random/path.py", "pkg.mod")
    assert result == "/some/random/path.py"


def test_shorten_path_ambiguous_root_uses_last():
    """When root name appears multiple times, use the last (rightmost) match."""
    result = _shorten_path(
        "/home/acme/projects/venv/lib/acme/toolkit/extract.py",
        "acme.toolkit.extract",
    )
    assert result == "acme/toolkit/extract.py"


def test_parallel_worker_time_note():
    """Show parallel note when total >> wall time."""
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=120.0)],
        stream=stream,
        scope="pkg",
        wall_time=30.0,
    )
    assert "parallel worker time" in stream.getvalue()


def test_no_parallel_note_when_times_close():
    """No parallel note when total is close to wall time."""
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=30.0)],
        stream=stream,
        scope="pkg",
        wall_time=28.0,
    )
    assert "parallel worker time" not in stream.getvalue()


def test_wall_time_omitted_when_none():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="pkg")
    output = stream.getvalue()
    assert "Wall time:" not in output


def test_qualified_name_dot_boundary_truncation():
    """Truncation should snap to dot boundaries, not cut mid-word."""
    fp = FunctionProfile(
        module="acme.evaluation.cli.eval_stages",
        name="run_pipeline",
        filename="/fake.py",
        start_line=1,
        total_time=0.1,
        call_count=1,
        lines=[],
    )
    result = _qualified_name(fp, max_len=35)
    assert result.startswith("...")
    # Should not cut mid-word
    assert ".." not in result[3:]  # no double dots after the prefix
    # Should end with the function name
    assert result.endswith("run_pipeline")


def test_qualified_name_no_truncation_when_short():
    fp = FunctionProfile(
        module="mod",
        name="func",
        filename="/fake.py",
        start_line=1,
        total_time=0.1,
        call_count=1,
        lines=[],
    )
    assert _qualified_name(fp) == "mod.func"


def test_results_header_coverage_with_n_registered():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="pkg", n_registered=50)
    output = stream.getvalue()
    assert "1 of 50 functions called" in output


def test_results_header_coverage_without_n_registered():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="pkg")
    output = stream.getvalue()
    assert "1 function" in output
    assert "1 functions" not in output  # singular grammar
    assert " of " not in output.split("Lazyline")[1].split("Total")[0]


def test_results_header_shows_unit_auto():
    stream = io.StringIO()
    # 0.5s median → auto selects ms
    print_summary([_result_with_time()], stream=stream, scope="pkg")
    output = stream.getvalue()
    assert "Unit: ms (auto)" in output


def test_results_header_shows_unit_explicit():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, scope="pkg", unit="us")
    output = stream.getvalue()
    assert "Unit: us" in output
    assert "(auto)" not in output


def test_results_header_suppressed_without_scope():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream)
    output = stream.getvalue()
    assert "lazyline results" not in output
    # Only one banner line (top), no bottom
    banners = [ln for ln in output.splitlines() if ln and all(c == "=" for c in ln)]
    assert len(banners) == 1


def test_results_header_empty_results():
    stream = io.StringIO()
    print_summary([], stream=stream, scope="pkg")
    output = stream.getvalue()
    # No header on empty results (early return before unit resolution)
    assert "lazyline results" not in output
    assert "No profiling data collected." in output


def test_results_header_coverage_uses_pre_filter_count():
    # Two results, filter matches one — header should show "2 of 50" not "1 of 50"
    r1 = FunctionProfile(
        module="mod",
        name="alpha",
        filename="/fake/mod.py",
        start_line=1,
        total_time=1.0,
        call_count=1,
        lines=[LineProfile(lineno=1, hits=1, time=1.0, source="x = 1")],
    )
    r2 = FunctionProfile(
        module="mod",
        name="beta",
        filename="/fake/mod.py",
        start_line=5,
        total_time=0.5,
        call_count=1,
        lines=[LineProfile(lineno=5, hits=1, time=0.5, source="y = 2")],
    )
    stream = io.StringIO()
    print_summary(
        [r1, r2],
        stream=stream,
        scope="mod",
        n_registered=50,
        filter_pattern="*alpha*",
    )
    output = stream.getvalue()
    # Coverage should reflect all called functions, not just the filtered subset
    assert "2 of 50 functions called" in output


# --- Per-function detail readability (§1.15) ---


def test_bold_header_in_tty_mode():
    stream = _tty_stream()
    print_summary([_result_with_time()], stream=stream, width=120)
    output = stream.getvalue()
    assert "\033[1mmod.func\033[0m (" in output


def test_no_bold_header_in_non_tty():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, width=120)
    output = stream.getvalue()
    assert "\033[1m" not in output


def test_value_first_stats_format():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="ms")
    output = stream.getvalue()
    assert "500.00ms total | 10 calls | 50.0000ms/call" in output


def test_value_first_stats_with_memory():
    result = FunctionProfile(
        module="mod",
        name="func",
        filename="/fake/mod.py",
        start_line=1,
        total_time=0.5,
        call_count=10,
        memory=2560.0,
        lines=[
            LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
            LineProfile(lineno=2, hits=10, time=0.5, source="    x = 1", memory=2560.0),
        ],
    )
    stream = io.StringIO()
    print_summary([result], stream=stream, unit="ms")
    output = stream.getvalue()
    assert "500.00ms total | 10 calls | 50.0000ms/call | 2.5 KB net mem" in output


def test_stats_dim_separators_in_tty():
    stream = _tty_stream()
    print_summary([_result_with_time()], stream=stream, width=120)
    output = stream.getvalue()
    dim_pipe = "\033[2;90m│\033[0m"
    stats_lines = [ln for ln in output.splitlines() if "total" in ln and "calls" in ln]
    assert stats_lines
    assert dim_pipe in stats_lines[0]


def test_blank_line_between_stats_and_table():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="ms")
    lines = stream.getvalue().splitlines()
    stats_idx = next(i for i, ln in enumerate(lines) if "total" in ln and "calls" in ln)
    header_idx = next(i for i, ln in enumerate(lines) if "Line" in ln and "Hits" in ln)
    assert header_idx == stats_idx + 2
    assert lines[stats_idx + 1] == ""


def test_no_indent_in_detail_lines():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, unit="ms")
    lines = stream.getvalue().splitlines()
    data_lines = [ln for ln in lines if "x = 1" in ln]
    assert data_lines
    # Line number field is 6 chars wide (right-aligned), so a digit must
    # appear within the first 6 characters — no extra 4-space indent.
    first_digit = next(i for i, c in enumerate(data_lines[0]) if c.isdigit())
    assert first_digit < 6


def test_negative_time_values_warn(capsys):
    """Negative total_time should trigger a warning on stderr."""
    stream = io.StringIO()
    print_summary([_result_with_time(total_time=-1.0)], stream=stream)
    err = capsys.readouterr().err
    assert "negative times" in err
    assert "Data may be corrupt" in err


def test_huge_time_values_do_not_explode_layout():
    """Column widths should be capped so extreme values don't destroy layout."""
    stream = io.StringIO()
    print_summary([_result_with_time(total_time=1e308)], stream=stream, width=120)
    lines = stream.getvalue().splitlines()
    # No line should exceed a reasonable multiple of the terminal width.
    assert all(len(line) < 300 for line in lines)


# --- compact mode first line dimming ---


def _find_def_lines(lines):
    """Find output lines containing the 'def' keyword (with or without ANSI)."""
    return [ln for ln in lines if "def" in ln and "func" in ln and "Source" not in ln]


def test_compact_first_line_not_dimmed_in_tty():
    """In compact mode (default), the def line should NOT be dimmed on tty."""
    results = _make_profile_with_docstring()
    stream = _tty_stream()
    print_summary(results, compact=True, stream=stream, width=120)
    lines = stream.getvalue().splitlines()
    def_lines = _find_def_lines(lines)
    assert def_lines
    assert "\033[2m" not in def_lines[0]


def test_full_first_line_dimmed_in_tty():
    """In full mode, the def line (un-hit) SHOULD be dimmed on tty."""
    results = _make_profile_with_docstring()
    stream = _tty_stream()
    print_summary(results, compact=False, stream=stream, width=120)
    lines = stream.getvalue().splitlines()
    def_lines = _find_def_lines(lines)
    assert def_lines
    assert "\033[2m" in def_lines[0]


# --- syntax highlighting ---


def test_highlight_source_plain_when_no_lexer():
    """_highlight_source returns input unchanged when lexer/formatter is None."""
    from lazyline.reporting import _highlight_source

    assert _highlight_source("x = 1", None, None) == "x = 1"


def test_highlight_source_skips_empty():
    """_highlight_source returns empty/whitespace input as-is."""
    from pygments.formatters import Terminal256Formatter
    from pygments.lexers import PythonLexer

    from lazyline.reporting import _highlight_source

    lexer, fmt = PythonLexer(), Terminal256Formatter()
    assert _highlight_source("", lexer, fmt) == ""
    assert _highlight_source("   ", lexer, fmt) == "   "


def test_highlight_source_adds_ansi():
    """_highlight_source produces ANSI output with no trailing newline."""
    from pygments.formatters import Terminal256Formatter
    from pygments.lexers import PythonLexer

    from lazyline.reporting import _highlight_source

    result = _highlight_source("x = 1", PythonLexer(), Terminal256Formatter())
    assert "\x1b[" in result
    assert not result.endswith("\n")


def test_hit_line_highlighted_in_tty():
    """Hit lines should contain Pygments ANSI codes in tty mode."""
    results = [_result_with_time()]
    stream = _tty_stream()
    print_summary(results, stream=stream, width=120)
    lines = stream.getvalue().splitlines()
    # Hit data lines have hits > 0, look for the line number of the source
    # (lineno=2 from _result_with_time). Pygments breaks up "x = 1" into
    # separate tokens, so match on the line number column instead.
    source_lines = [ln for ln in lines if "\x1b[38;5;" in ln and "100.0%" in ln]
    assert source_lines


def test_unhit_dimmed_line_not_highlighted():
    """Dimmed un-hit lines should not contain Pygments ANSI codes."""
    results = _make_profile_with_docstring()
    stream = _tty_stream()
    print_summary(results, compact=False, stream=stream, width=120)
    lines = stream.getvalue().splitlines()
    # Find dimmed lines (wrapped in \033[2m)
    dimmed = [ln for ln in lines if ln.startswith("\033[2m")]
    assert dimmed
    for ln in dimmed:
        # Should not contain Pygments 256-color codes
        assert "\x1b[38;5;" not in ln


def test_compact_def_line_highlighted_in_tty():
    """In compact mode, the def line (first, un-hit) should get highlighting."""
    results = _make_profile_with_docstring()
    stream = _tty_stream()
    print_summary(results, compact=True, stream=stream, width=120)
    lines = stream.getvalue().splitlines()
    def_lines = _find_def_lines(lines)
    assert def_lines
    # Should contain Pygments 256-color ANSI for 'def' keyword
    assert "\x1b[38;5;" in def_lines[0]


def test_no_highlighting_in_non_tty():
    """Non-tty output should contain no ANSI escape sequences at all."""
    results = [_result_with_time()]
    stream = io.StringIO()
    print_summary(results, stream=stream, width=120)
    output = stream.getvalue()
    assert "\x1b[" not in output


def test_fallback_without_pygments():
    """When Pygments is unavailable, output should be produced without errors."""
    from unittest.mock import patch

    def _boom(*args, **kwargs):
        raise RuntimeError("Pygments should not be called when unavailable")

    results = [_result_with_time()]
    stream = _tty_stream()
    with (
        patch("lazyline.reporting._PYGMENTS_AVAILABLE", False),
        patch("lazyline.reporting._pygments_highlight", _boom),
    ):
        print_summary(results, stream=stream, width=120)
    output = stream.getvalue()
    # Should still have output
    assert "x = 1" in output
    # Should not have Pygments 256-color ANSI
    assert "\x1b[38;5;" not in output


# --- _normalize_patterns tests ---


def test_normalize_patterns_bare_words():
    """Bare patterns without wildcards get auto-wrapped with *...*."""
    result = _normalize_patterns("dumps,loads")
    assert result == ["*dumps*", "*loads*"]


def test_normalize_patterns_preserves_wildcards():
    """Patterns with wildcards are left unchanged."""
    result = _normalize_patterns("*foo*,bar[0-9],baz?")
    assert result == ["*foo*", "bar[0-9]", "baz?"]


def test_normalize_patterns_mixed():
    """Mix of bare and wildcard patterns."""
    result = _normalize_patterns("plain,*wild*")
    assert result == ["*plain*", "*wild*"]


def test_normalize_patterns_strips_whitespace():
    """Whitespace around comma-separated patterns is trimmed."""
    result = _normalize_patterns("  foo , bar  ")
    assert result == ["*foo*", "*bar*"]


# --- exclude pattern tests (reporting layer) ---


def test_exclude_pattern_reporting():
    """Exclude pattern removes matching functions from output."""
    r1 = _result_with_time(total_time=1.0)
    r2 = FunctionProfile(
        module="mod",
        name="helper",
        filename="/fake/mod.py",
        start_line=5,
        total_time=0.1,
        call_count=1,
        lines=[LineProfile(lineno=5, hits=1, time=0.1, source="pass")],
    )
    stream = io.StringIO()
    print_summary([r1, r2], stream=stream, exclude_pattern="*helper*")
    output = stream.getvalue()
    assert "mod.func" in output
    assert "mod.helper" not in output


def test_exclude_all_functions_shows_no_match():
    stream = io.StringIO()
    print_summary([_result_with_time()], stream=stream, exclude_pattern="*func*")
    output = stream.getvalue()
    assert "No functions matching" in output


# --- sort tests (reporting layer) ---


def test_sort_by_name_reporting():
    r1 = FunctionProfile(
        module="mod",
        name="zebra",
        filename="/fake/mod.py",
        start_line=1,
        total_time=2.0,
        call_count=1,
        lines=[LineProfile(lineno=1, hits=1, time=2.0, source="pass")],
    )
    r2 = FunctionProfile(
        module="mod",
        name="alpha",
        filename="/fake/mod.py",
        start_line=5,
        total_time=1.0,
        call_count=1,
        lines=[LineProfile(lineno=5, hits=1, time=1.0, source="pass")],
    )
    stream = io.StringIO()
    print_summary([r1, r2], stream=stream, sort="name", summary=True)
    lines = stream.getvalue().splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    assert func_lines[0].strip().startswith("mod.alpha")
    assert func_lines[1].strip().startswith("mod.zebra")


def test_sort_by_calls_reporting():
    r1 = FunctionProfile(
        module="mod",
        name="few_calls",
        filename="/fake/mod.py",
        start_line=1,
        total_time=2.0,
        call_count=5,
        lines=[LineProfile(lineno=1, hits=5, time=2.0, source="pass")],
    )
    r2 = FunctionProfile(
        module="mod",
        name="many_calls",
        filename="/fake/mod.py",
        start_line=5,
        total_time=1.0,
        call_count=100,
        lines=[LineProfile(lineno=5, hits=100, time=1.0, source="pass")],
    )
    stream = io.StringIO()
    print_summary([r1, r2], stream=stream, sort="calls", summary=True)
    lines = stream.getvalue().splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    # Sorted by calls desc → many_calls first
    assert func_lines[0].strip().startswith("mod.many_calls")


def test_sort_by_time_per_call_reporting():
    r1 = FunctionProfile(
        module="mod",
        name="cheap",
        filename="/fake/mod.py",
        start_line=1,
        total_time=1.0,
        call_count=100,
        lines=[LineProfile(lineno=1, hits=100, time=1.0, source="pass")],
    )
    r2 = FunctionProfile(
        module="mod",
        name="expensive",
        filename="/fake/mod.py",
        start_line=5,
        total_time=0.5,
        call_count=1,
        lines=[LineProfile(lineno=5, hits=1, time=0.5, source="pass")],
    )
    stream = io.StringIO()
    print_summary([r1, r2], stream=stream, sort="time-per-call", summary=True)
    lines = stream.getvalue().splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    # expensive: 0.5/1=0.5 tpc, cheap: 1.0/100=0.01 tpc → expensive first
    assert func_lines[0].strip().startswith("mod.expensive")


def test_sort_time_is_default_order():
    """Default sort (time) should keep original descending-time order."""
    r1 = _result_with_time(total_time=2.0)
    r2 = FunctionProfile(
        module="mod",
        name="slower",
        filename="/fake/mod.py",
        start_line=5,
        total_time=5.0,
        call_count=1,
        lines=[LineProfile(lineno=5, hits=1, time=5.0, source="pass")],
    )
    # Pass r1 first (lower time) — sort=time should not reorder since
    # the code skips sorting for "time" (results are pre-sorted)
    stream = io.StringIO()
    print_summary([r1, r2], stream=stream, sort="time", summary=True)
    lines = stream.getvalue().splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    # Original order preserved: mod.func (r1) first, mod.slower (r2) second
    assert func_lines[0].strip().startswith("mod.func")


# --- profiled time label ---


def test_profiled_time_label_when_total_less_than_wall():
    """When profiled time << wall time, label should say 'Profiled time'."""
    stream = io.StringIO()
    # total=0.1s, wall=10s → ratio=0.01, well outside 0.9-1.1
    print_summary(
        [_result_with_time(total_time=0.1)],
        stream=stream,
        scope="pkg",
        wall_time=10.0,
        unit="s",
    )
    output = stream.getvalue()
    assert "Profiled time:" in output
    assert "Total:" not in output.split("Summary")[0]


def test_profiled_time_label_when_total_much_greater_than_wall():
    """When total >> wall (parallel workers), label should say 'Profiled time'."""
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=100.0)],
        stream=stream,
        scope="pkg",
        wall_time=30.0,
        unit="s",
    )
    output = stream.getvalue()
    assert "Profiled time:" in output


def test_total_label_when_times_close():
    """When total ≈ wall time, label should say 'Total'."""
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=10.0)],
        stream=stream,
        scope="pkg",
        wall_time=10.5,
        unit="s",
    )
    output = stream.getvalue()
    header_part = output.split("Summary")[0]
    assert "Total:" in header_part


# --- un-profiled code hint ---


def test_unprofiled_code_hint():
    """When profiled time << wall time (< 0.5x), show un-profiled code hint."""
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=1.0)],
        stream=stream,
        scope="pkg",
        wall_time=10.0,
    )
    output = stream.getvalue()
    assert "un-profiled code" in output


def test_no_unprofiled_hint_when_times_close():
    stream = io.StringIO()
    print_summary(
        [_result_with_time(total_time=10.0)],
        stream=stream,
        scope="pkg",
        wall_time=12.0,
    )
    output = stream.getvalue()
    assert "un-profiled code" not in output


# --- filter + exclude "No functions matching" label ---


def test_filter_no_match_shows_pattern():
    stream = io.StringIO()
    print_summary(
        [_result_with_time()],
        stream=stream,
        filter_pattern="*nonexistent*",
    )
    output = stream.getvalue()
    assert "No functions matching '*nonexistent*'" in output


def test_exclude_no_match_shows_pattern():
    stream = io.StringIO()
    print_summary(
        [_result_with_time()],
        stream=stream,
        exclude_pattern="*func*",
    )
    output = stream.getvalue()
    assert "No functions matching '*func*'" in output
