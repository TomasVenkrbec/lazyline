import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from lazyline.__main__ import (
    _DisplayOptions,
    _reparse_options,
    _split_scopes_and_options,
    _warn_high_hit_functions,
    app,
)
from lazyline.export import to_json
from lazyline.models import (
    FunctionProfile,
    LineProfile,
    ProfileRun,
    RunMetadata,
)

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "lazyline" in result.output


def test_no_args_shows_help():
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in result.output


def test_run_no_command():
    result = runner.invoke(app, ["run", "json", "--"])
    assert result.exit_code == 1
    assert "No command provided" in result.output
    assert "json" in result.output  # scope echoed in error


def test_run_bad_scope():
    result = runner.invoke(
        app, ["run", "nonexistent.xyz.abc", "--", "python", "-c", "pass"]
    )
    assert result.exit_code == 1
    assert "No modules found" in result.output


def test_run_happy_path():
    result = runner.invoke(
        app,
        ["run", "json", "--", "python", "-c", "import json; json.dumps(1)"],
    )
    assert result.exit_code == 0
    assert "Discovered" in result.output
    assert "Registered" in result.output
    # Verify actual profiling results appear
    assert "Function" in result.output  # summary table header
    assert "json.dumps" in result.output  # at least one profiled function


def test_run_with_top():
    result = runner.invoke(
        app,
        [
            "run",
            "--top",
            "1",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0


def test_run_with_output_creates_json(tmp_path):
    out = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "run",
            "--output",
            str(out),
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Results exported to" in result.output
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["version"] == 1
    assert "metadata" in data
    assert data["metadata"]["scope"] == "json"
    assert len(data["functions"]) >= 1


def test_run_with_output_dash_writes_to_stdout():
    result = runner.invoke(
        app,
        [
            "run",
            "--output",
            "-",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    # Output contains JSON (version key present)
    assert '"version"' in result.output
    # No "exported to" message
    assert "Results exported to" not in result.output


def test_run_with_output_directory_rejected(tmp_path):
    result = runner.invoke(
        app,
        ["run", "--output", str(tmp_path), "json", "--", "python", "-c", "pass"],
    )
    assert result.exit_code == 1
    assert "is a directory" in result.output


def test_run_with_output_and_memory(tmp_path):
    out = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "run",
            "--memory",
            "--output",
            str(out),
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(list(range(100)))",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(out.read_text())
    assert data["metadata"]["memory_tracking"] is True


def test_run_top_zero_rejected():
    result = runner.invoke(
        app,
        ["run", "--top", "0", "json", "--", "python", "-c", "pass"],
    )
    assert result.exit_code == 1
    assert "--top must be at least 1" in result.output


def test_run_propagates_nonzero_exit():
    result = runner.invoke(
        app,
        ["run", "json", "--", "python", "-c", "import sys; sys.exit(2)"],
    )
    assert result.exit_code == 2


def test_run_with_memory_flag():
    result = runner.invoke(
        app,
        [
            "run",
            "--memory",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(list(range(100)))",
        ],
    )
    assert result.exit_code == 0
    assert "Memory tracking enabled" in result.output
    # Note: "Net Mem" column only appears if tracemalloc captures allocations
    # matching profiled lines. With the json stdlib (mostly C code), this is
    # not guaranteed — so we only assert the flag was acknowledged.


def test_run_without_memory_flag_no_memory_column():
    result = runner.invoke(
        app,
        ["run", "json", "--", "python", "-c", "import json; json.dumps(1)"],
    )
    assert result.exit_code == 0
    assert "Net Mem" not in result.output


def test_run_with_no_memory_flag_explicit():
    result = runner.invoke(
        app,
        [
            "run",
            "--no-memory",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Net Mem" not in result.output
    assert "Memory tracking enabled" not in result.output


def _write_sample_json(path):
    run = ProfileRun(
        version=1,
        lazyline_version="0.0.4",
        metadata=RunMetadata(
            command=["python", "-c", "pass"],
            scope="mod",
            timestamp="2026-03-27T12:00:00+00:00",
            memory_tracking=False,
            python_version="3.12.3",
            exit_code=0,
        ),
        functions=[
            FunctionProfile(
                module="mod",
                name="func",
                filename="/tmp/mod.py",
                start_line=1,
                total_time=0.5,
                call_count=10,
                lines=[
                    LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
                    LineProfile(lineno=2, hits=10, time=0.5, source="    return 1"),
                ],
            ),
            FunctionProfile(
                module="mod",
                name="other",
                filename="/tmp/mod.py",
                start_line=5,
                total_time=0.1,
                call_count=1,
                lines=[
                    LineProfile(lineno=5, hits=0, time=0.0, source="def other():"),
                    LineProfile(lineno=6, hits=1, time=0.1, source="    pass"),
                ],
            ),
        ],
    )
    to_json(run, path)


def test_show_displays_results(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path)])
    assert result.exit_code == 0
    assert "mod.func" in result.output
    assert "mod.other" in result.output
    assert "def func():" in result.output


def test_show_with_top(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--top", "1"])
    assert result.exit_code == 0
    assert "mod.func" in result.output
    assert "mod.other" not in result.output


def test_show_nonexistent_file():
    result = runner.invoke(app, ["show", "/nonexistent/path.json"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_show_directory_rejected(tmp_path):
    result = runner.invoke(app, ["show", str(tmp_path)])
    assert result.exit_code == 1
    assert "is a directory" in result.output


def test_show_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{")
    result = runner.invoke(app, ["show", str(path)])
    assert result.exit_code == 1
    assert "Invalid JSON file" in result.output


def test_show_with_memory(tmp_path):
    path = tmp_path / "mem.json"
    run = ProfileRun(
        version=1,
        lazyline_version="0.0.4",
        metadata=RunMetadata(
            command=["python", "-c", "pass"],
            scope="mod",
            timestamp="2026-03-27T12:00:00+00:00",
            memory_tracking=True,
            python_version="3.12.3",
            exit_code=0,
        ),
        functions=[
            FunctionProfile(
                module="mod",
                name="func",
                filename="/tmp/mod.py",
                start_line=1,
                total_time=0.5,
                call_count=10,
                lines=[
                    LineProfile(lineno=1, hits=0, time=0.0, source="def func():"),
                    LineProfile(
                        lineno=2,
                        hits=10,
                        time=0.5,
                        source="    return 1",
                        memory=2048.0,
                    ),
                ],
                memory=2048.0,
            ),
        ],
    )
    to_json(run, path)
    result = runner.invoke(app, ["show", str(path)])
    assert result.exit_code == 0
    assert "Net Mem" in result.output
    assert "2.0 KB" in result.output


def test_show_top_zero_rejected(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--top", "0"])
    assert result.exit_code == 1
    assert "--top must be at least 1" in result.output


def test_run_missing_separator_error():
    result = runner.invoke(
        app,
        ["run", "json", "python", "-c", "import json; json.dumps(1)"],
    )
    assert result.exit_code == 1
    assert "Missing '--'" in result.output


def test_run_then_show_roundtrip(tmp_path):
    out = tmp_path / "out.json"
    run_result = runner.invoke(
        app,
        [
            "run",
            "--output",
            str(out),
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps([1, 2, 3])",
        ],
    )
    assert run_result.exit_code == 0
    show_result = runner.invoke(app, ["show", str(out)])
    assert show_result.exit_code == 0
    # The show output should contain the same function names as the run output.
    assert "json" in show_result.output.lower()
    assert "Function" in show_result.output


# --- _warn_high_hit_functions ---


def _make_profile(name, hits):
    return FunctionProfile(
        module="mod",
        name=name,
        filename="/tmp/mod.py",
        start_line=1,
        total_time=1.0,
        call_count=hits,
        lines=[LineProfile(lineno=1, hits=hits, time=1.0, source="pass")],
    )


def test_warn_high_hit_functions_triggers(capsys):
    results = [_make_profile("hot_func", 2_000_000)]
    _warn_high_hit_functions(results)
    captured = capsys.readouterr()
    assert "mod.hot_func" in captured.err
    assert "2,000,000 line hits" in captured.err
    assert "inflated" in captured.err


def test_warn_high_hit_functions_silent_below_threshold(capsys):
    results = [_make_profile("fast_func", 500_000)]
    _warn_high_hit_functions(results)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_warn_high_hit_functions_multiple(capsys):
    results = [
        _make_profile("hot_a", 1_500_000),
        _make_profile("hot_b", 3_000_000),
        _make_profile("cool_c", 100),
    ]
    _warn_high_hit_functions(results)
    captured = capsys.readouterr()
    assert "hot_a" in captured.err
    assert "hot_b" in captured.err
    assert "cool_c" not in captured.err


def test_warn_high_hit_functions_exact_threshold(capsys):
    results = [_make_profile("exact", 1_000_000)]
    _warn_high_hit_functions(results)
    captured = capsys.readouterr()
    assert "exact" in captured.err


def test_warn_high_hit_functions_just_below_threshold(capsys):
    results = [_make_profile("just_under", 999_999)]
    _warn_high_hit_functions(results)
    captured = capsys.readouterr()
    assert captured.err == ""


# --- option re-parsing ---


def test_options_after_scope_before_separator():
    """lazyline run json --top 1 -- python -c '...' should work."""
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "--top",
            "1",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Showing top 1 of" in result.output


def test_compact_after_scope_before_separator():
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "--compact",
            "--summary",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    # summary mode: no per-line detail
    assert "def " not in result.output


def test_missing_separator_with_flags():
    """lazyline run json --top 1 python -c '...' without -- should error."""
    result = runner.invoke(
        app,
        ["run", "json", "--top", "1", "python", "-c", "pass"],
    )
    assert result.exit_code == 1
    assert "Missing '--'" in result.output


def test_quiet_flag():
    result = runner.invoke(
        app,
        ["run", "--quiet", "json", "--", "python", "-c", "import json; json.dumps(1)"],
    )
    assert result.exit_code == 0
    assert "Discovered" not in result.output
    assert "Registered" not in result.output


def test_filter_flag():
    result = runner.invoke(
        app,
        [
            "run",
            "--filter",
            "*dumps*",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    # If any results, they should all match *dumps*
    if (
        "No profiling data" not in result.output
        and "No functions matching" not in result.output
    ):
        assert "dumps" in result.output


def test_filter_auto_wraps_bare_pattern():
    """--filter 'dumps' (no wildcards) should match like '*dumps*'."""
    result = runner.invoke(
        app,
        [
            "run",
            "--filter",
            "dumps",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    if (
        "No profiling data" not in result.output
        and "No functions matching" not in result.output
    ):
        assert "dumps" in result.output


# --- unit flag ---


def test_run_with_unit_ms():
    result = runner.invoke(
        app,
        [
            "run",
            "--unit",
            "ms",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Total (ms)" in result.output


def test_show_with_unit_ms(tmp_path):
    # Create a JSON file via run
    out = tmp_path / "results.json"
    runner.invoke(
        app,
        [
            "run",
            "--output",
            str(out),
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    result = runner.invoke(app, ["show", "--unit", "ms", str(out)])
    assert result.exit_code == 0
    assert "Total (ms)" in result.output


def test_unit_after_scope_before_separator():
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "--unit",
            "ms",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Total (ms)" in result.output


def test_run_with_invalid_unit():
    result = runner.invoke(
        app,
        ["run", "--unit", "invalid", "json", "--", "python", "-c", "pass"],
    )
    assert result.exit_code == 1
    assert "--unit must be one of" in result.output


def test_run_with_unit_auto():
    result = runner.invoke(
        app,
        [
            "run",
            "--unit",
            "auto",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    # auto should pick one of the valid unit labels
    assert any(f"Total ({u})" in result.output for u in ("s", "ms", "us", "ns"))


def test_info_messages_go_to_stderr():
    result = runner.invoke(
        app,
        ["run", "json", "--", "python", "-c", "import json; json.dumps(1)"],
    )
    assert result.exit_code == 0
    # Info messages should be on stderr, not stdout
    assert "Discovered" in result.stderr
    assert "Registered" in result.stderr
    assert "Discovered" not in result.stdout
    assert "Registered" not in result.stdout


# --- multi-scope ---


def test_multi_scope_run():
    """Multiple scopes before -- should all be profiled."""
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "pathlib",
            "--",
            "python",
            "-c",
            "import json, pathlib; json.dumps(1); pathlib.PurePosixPath('a/b')",
        ],
    )
    assert result.exit_code == 0
    assert "json, pathlib" in result.stderr
    assert "json, pathlib" in result.output
    # Both scopes should have functions in the output.
    assert "json." in result.output
    assert "pathlib." in result.output


def test_multi_scope_with_options():
    """Options mixed with additional scopes before -- should all parse."""
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "--summary",
            "pathlib",
            "--",
            "python",
            "-c",
            "import json, pathlib; json.dumps(1); pathlib.PurePosixPath('a/b')",
        ],
    )
    assert result.exit_code == 0
    # summary mode — no per-line detail
    assert "def " not in result.output


def test_multi_scope_deduplication():
    """Same module listed twice should be discovered only once."""
    single = runner.invoke(
        app, ["run", "json", "--", "python", "-c", "import json; json.dumps(1)"]
    )
    double = runner.invoke(
        app,
        ["run", "json", "json", "--", "python", "-c", "import json; json.dumps(1)"],
    )
    assert double.exit_code == 0
    # Extract module count from "Discovered N module(s)" message.
    import re

    single_n = re.search(r"Discovered (\d+)", single.stderr)
    double_n = re.search(r"Discovered (\d+)", double.stderr)
    assert single_n and double_n
    assert single_n.group(1) == double_n.group(1)


def test_split_scopes_and_options_basic():
    """Non-flag tokens are scopes, flag tokens are options."""
    scopes, opts = _split_scopes_and_options(["mod2", "--summary", "mod3"])
    assert scopes == ["mod2", "mod3"]
    assert opts == ["--summary"]


def test_split_scopes_and_options_arg_flag_consumes_value():
    """Flags that take a value should consume the next token."""
    scopes, opts = _split_scopes_and_options(["mod2", "--filter", "*.foo", "mod3"])
    assert scopes == ["mod2", "mod3"]
    assert opts == ["--filter", "*.foo"]


def test_split_scopes_and_options_empty():
    scopes, opts = _split_scopes_and_options([])
    assert scopes == []
    assert opts == []


def test_single_scope_requires_separator():
    """Single scope without -- now errors."""
    result = runner.invoke(
        app,
        ["run", "json", "python", "-c", "import json; json.dumps(1)"],
    )
    assert result.exit_code == 1
    assert "Missing '--'" in result.output


# --- module-level code profiling ---


def test_run_flat_script_produces_profiling_data(tmp_path):
    """A flat .py script with no functions should still produce profiling data."""
    script = tmp_path / "flat_script.py"
    script.write_text("x = 0\nfor i in range(1000):\n    x += i\n")
    result = runner.invoke(
        app,
        ["run", str(script), "--", "python", str(script)],
    )
    assert result.exit_code == 0
    assert "No profiling data" not in result.output
    assert "<module>" in result.output
    # Source lines should appear in per-line detail.
    assert "range(1000)" in result.output


def test_run_mixed_scope_package_and_script(tmp_path):
    """Mixed scopes: package + .py file both produce profiling data."""
    script = tmp_path / "mixed_target.py"
    script.write_text("import json\ndata = json.dumps([1, 2, 3])\n")
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            str(script),
            "--",
            "python",
            str(script),
        ],
    )
    assert result.exit_code == 0
    # Both scopes should have results.
    assert "json." in result.output
    assert "<module>" in result.output


# --- exclude flag ---


def test_exclude_flag(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--exclude", "*other*"])
    assert result.exit_code == 0
    assert "mod.func" in result.output
    assert "mod.other" not in result.output


def test_exclude_auto_wraps_bare_pattern(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--exclude", "other"])
    assert result.exit_code == 0
    assert "mod.func" in result.output
    assert "mod.other" not in result.output


def test_exclude_all_shows_no_match(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--exclude", "*mod*"])
    assert result.exit_code == 0
    assert "No functions matching" in result.output


# --- sort flag ---


def test_sort_by_name(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--sort", "name", "--summary"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    assert len(func_lines) == 2
    # mod.func should come before mod.other alphabetically
    assert func_lines[0].strip().startswith("mod.func")
    assert func_lines[1].strip().startswith("mod.other")


def test_sort_by_calls(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--sort", "calls", "--summary"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    # mod.func has 10 calls, mod.other has 1 — sorted by calls desc
    assert func_lines[0].strip().startswith("mod.func")


def test_sort_invalid_value():
    result = runner.invoke(app, ["show", "/dev/null", "--sort", "invalid"])
    assert result.exit_code == 1
    assert "--sort must be one of" in result.output


# --- no-subprocess / no-multiprocessing flags ---


def test_no_subprocess_flag():
    result = runner.invoke(
        app,
        [
            "run",
            "--no-subprocess",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Discovered" in result.output


def test_no_multiprocessing_flag():
    result = runner.invoke(
        app,
        [
            "run",
            "--no-multiprocessing",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    assert "Discovered" in result.output


# --- run command: --sort validation ---


def test_run_with_invalid_sort():
    result = runner.invoke(
        app,
        ["run", "--sort", "invalid", "json", "--", "python", "-c", "pass"],
    )
    assert result.exit_code == 1
    assert "--sort must be one of" in result.output


def test_run_sort_after_scope():
    """--sort between scope and -- should be reparsed."""
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "--sort",
            "name",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0


# --- show command: --unit and --sort validation ---


def test_show_with_invalid_unit(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--unit", "invalid"])
    assert result.exit_code == 1
    assert "--unit must be one of" in result.output


def test_show_with_invalid_sort(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(app, ["show", str(path), "--sort", "invalid"])
    assert result.exit_code == 1
    assert "--sort must be one of" in result.output


# --- _reparse_options edge cases ---


def test_reparse_bool_false_flags():
    """--full and --no-memory should set their keys to False."""
    result = _reparse_options(
        ["--full", "--no-memory"],
        _DisplayOptions(memory=True),
    )
    assert result.memory is False
    assert result.compact is False


def test_reparse_missing_value_error():
    """A flag that requires a value but has none should error."""
    import pytest
    from click.exceptions import Exit

    with pytest.raises(Exit):
        _reparse_options(["--top"], _DisplayOptions())


def test_reparse_invalid_top_value():
    """Non-integer --top value should error."""
    import pytest
    from click.exceptions import Exit

    with pytest.raises(Exit):
        _reparse_options(["--top", "abc"], _DisplayOptions())


def test_reparse_unrecognized_option():
    """Unknown flag between scope and -- should error."""
    import pytest
    from click.exceptions import Exit

    with pytest.raises(Exit):
        _reparse_options(["--bogus"], _DisplayOptions())


def test_reparse_output_flag():
    """--output / -o should set the output path."""
    result = _reparse_options(["-o", "/tmp/out.json"], _DisplayOptions())
    assert result.output == Path("/tmp/out.json")


def test_reparse_exclude_and_sort():
    """--exclude and --sort should be parsed from inter-scope tokens."""
    result = _reparse_options(
        ["--exclude", "*foo*", "--sort", "calls"], _DisplayOptions()
    )
    assert result.exclude_pattern == "*foo*"
    assert result.sort == "calls"


def test_reparse_no_subprocess_no_multiprocessing():
    """--no-subprocess and --no-multiprocessing should set their flags."""
    result = _reparse_options(
        ["--no-subprocess", "--no-multiprocessing"], _DisplayOptions()
    )
    assert result.no_subprocess is True
    assert result.no_multiprocessing is True


# --- exclude via run command (end-to-end) ---


def test_run_exclude_flag():
    result = runner.invoke(
        app,
        [
            "run",
            "--exclude",
            "encoder",
            "json",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0
    # The summary table should not list any encoder functions.
    # Check each summary line (lines starting with "json.") for "encoder".
    for line in result.output.splitlines():
        stripped = line.strip()
        if stripped.startswith("json."):
            assert "encoder" not in stripped.lower(), (
                f"Excluded function appeared in summary: {stripped}"
            )


# --- sort via run command (end-to-end) ---


def test_run_sort_by_time_per_call(tmp_path):
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(
        app, ["show", str(path), "--sort", "time-per-call", "--summary"]
    )
    assert result.exit_code == 0
    lines = result.output.splitlines()
    func_lines = [ln for ln in lines if ln.strip().startswith("mod.")]
    # mod.other has 0.1/1=0.1 tpc, mod.func has 0.5/10=0.05 tpc
    # sorted desc by tpc → mod.other first
    assert func_lines[0].strip().startswith("mod.other")


# --- filter + exclude combined ---


def test_filter_and_exclude_combined(tmp_path):
    """Filter includes, then exclude removes from the filtered set."""
    path = tmp_path / "results.json"
    _write_sample_json(path)
    result = runner.invoke(
        app, ["show", str(path), "--filter", "*mod*", "--exclude", "*other*"]
    )
    assert result.exit_code == 0
    assert "mod.func" in result.output
    assert "mod.other" not in result.output


# --- exclude after scope before separator ---


def test_exclude_after_scope_before_separator():
    result = runner.invoke(
        app,
        [
            "run",
            "json",
            "--exclude",
            "encoder",
            "--",
            "python",
            "-c",
            "import json; json.dumps(1)",
        ],
    )
    assert result.exit_code == 0


# --- CWD on sys.path (local package discovery) ---


def test_run_discovers_local_package_not_on_sys_path(tmp_path, monkeypatch):
    """A local package in CWD should be discoverable even without pip install."""
    pkg = tmp_path / "localpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("def greet():\n    return 'hello'\n")
    script = tmp_path / "runner.py"
    script.write_text("from localpkg.core import greet\ngreet()\n")
    monkeypatch.chdir(tmp_path)
    # Remove tmp_path from sys.path if present to simulate console-script launch
    monkeypatch.setattr(
        "sys.path",
        [p for p in sys.path if p not in (str(tmp_path), "")],
    )
    result = runner.invoke(
        app,
        ["run", "localpkg", "--", "python", str(script)],
    )
    assert result.exit_code == 0
    assert "Discovered" in result.output
    assert "localpkg" in result.output


def test_ensure_cwd_on_path_idempotent(monkeypatch):
    """Calling _ensure_cwd_on_path twice should not add duplicate entries."""
    from lazyline.__main__ import _ensure_cwd_on_path

    monkeypatch.setattr(
        "sys.path",
        [p for p in sys.path if p not in (str(Path.cwd()), "")],
    )
    _ensure_cwd_on_path()
    count_before = sys.path.count("")
    _ensure_cwd_on_path()
    assert sys.path.count("") == count_before


def test_ensure_cwd_on_path_noop_when_already_present(monkeypatch):
    """If CWD is already on sys.path, nothing should be added."""
    from lazyline.__main__ import _ensure_cwd_on_path

    monkeypatch.setattr("sys.path", ["", "/other"])
    _ensure_cwd_on_path()
    assert sys.path == ["", "/other"]
