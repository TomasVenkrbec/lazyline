import importlib
import sys
from importlib.metadata import EntryPoint

import pytest
from line_profiler import LineProfiler

from lazyline.models import FunctionProfile, LineProfile
from lazyline.profiling import (
    _build_file_to_module_map,
    _is_valid_module_path,
    _parse_command,
    _resolve_console_script,
    _resolve_module_name,
    collect_results,
    create_profiler,
    enrich_results,
    execute_command,
    register_module_level_code,
)


def test_create_profiler():
    profiler = create_profiler()
    assert isinstance(profiler, LineProfiler)


# --- _parse_command ---


def test_parse_command_python_m():
    runner, target, extra = _parse_command(["python", "-m", "json.tool", "f.json"])
    assert (runner, target, extra) == ("module", "json.tool", ["f.json"])


def test_parse_command_script():
    runner, target, extra = _parse_command(["python", "script.py", "--flag"])
    assert (runner, target, extra) == ("script", "script.py", ["--flag"])


def test_parse_command_bare():
    runner, target, extra = _parse_command(["pytest", "tests/"])
    assert (runner, target, extra) == ("module", "pytest", ["tests/"])


def test_parse_command_python_c():
    runner, target, extra = _parse_command(["python", "-c", "print(1)"])
    assert (runner, target, extra) == ("code", "print(1)", [])


def test_parse_command_python3_variant():
    runner, target, _ = _parse_command(["python3.12", "-m", "json.tool"])
    assert (runner, target) == ("module", "json.tool")


def test_parse_command_skips_interpreter_flags():
    runner, target, extra = _parse_command(["python", "-u", "-O", "-m", "pytest", "-v"])
    assert (runner, target, extra) == ("module", "pytest", ["-v"])


def test_parse_command_flags_before_script():
    runner, target, extra = _parse_command(["python", "-B", "run.py", "--arg"])
    assert (runner, target, extra) == ("script", "run.py", ["--arg"])


def test_parse_command_W_flag_consumes_argument():
    runner, target, extra = _parse_command(
        ["python", "-W", "ignore", "-m", "pytest", "-v"]
    )
    assert (runner, target, extra) == ("module", "pytest", ["-v"])


def test_parse_command_X_flag_consumes_argument():
    runner, target, extra = _parse_command(
        ["python", "-X", "utf8", "script.py", "--arg"]
    )
    assert (runner, target, extra) == ("script", "script.py", ["--arg"])


def test_parse_command_mixed_no_arg_and_with_arg_flags():
    runner, target, extra = _parse_command(
        ["python", "-u", "-W", "error", "-B", "-X", "dev", "-m", "mymod"]
    )
    assert (runner, target, extra) == ("module", "mymod", [])


def test_parse_command_empty_after_strip():
    with pytest.raises(ValueError, match="Empty command"):
        _parse_command(["python"])


def test_parse_command_only_flags():
    with pytest.raises(ValueError, match="Only interpreter flags"):
        _parse_command(["python", "-u", "-B"])


def test_parse_command_m_without_module():
    with pytest.raises(ValueError, match="'-m' requires a module name"):
        _parse_command(["python", "-m"])


def test_parse_command_c_without_code():
    with pytest.raises(ValueError, match="'-c' requires a code string"):
        _parse_command(["python", "-c"])


# --- _is_valid_module_path ---


def test_valid_module_path_simple():
    assert _is_valid_module_path("pytest") is True


def test_valid_module_path_dotted():
    assert _is_valid_module_path("acme.evaluation") is True


def test_valid_module_path_hyphenated():
    assert _is_valid_module_path("tattoo-evaluation") is False


def test_valid_module_path_empty():
    assert _is_valid_module_path("") is False


# --- _resolve_console_script ---


def test_resolve_console_script_found():
    # lazyline is installed in this environment
    ep = _resolve_console_script("lazyline")
    assert isinstance(ep, EntryPoint)
    assert ep.value == "lazyline.__main__:app"


def test_resolve_console_script_not_found():
    assert _resolve_console_script("no-such-command-xyz") is None


# --- _parse_command (console script) ---


def _fake_ep(name, value):
    """Create a fake EntryPoint for testing."""
    return EntryPoint(name=name, value=value, group="console_scripts")


def test_parse_command_console_script(monkeypatch):
    monkeypatch.setattr(
        "lazyline.profiling._resolve_console_script",
        lambda name: _fake_ep("my-tool", "pkg.cli:main") if name == "my-tool" else None,
    )
    runner, target, extra = _parse_command(["my-tool", "--flag", "arg"])
    assert runner == "entry_point"
    assert target == "pkg.cli:main"
    assert extra == ["my-tool", "--flag", "arg"]


def test_parse_command_console_script_no_args(monkeypatch):
    monkeypatch.setattr(
        "lazyline.profiling._resolve_console_script",
        lambda name: _fake_ep("my-tool", "pkg.cli:main") if name == "my-tool" else None,
    )
    runner, target, extra = _parse_command(["my-tool"])
    assert runner == "entry_point"
    assert target == "pkg.cli:main"
    assert extra == ["my-tool"]


def test_parse_command_unknown_hyphenated_falls_back_to_module():
    """Unknown hyphenated command falls back to module (will error at runtime)."""
    runner, target, extra = _parse_command(["no-such-cmd-xyz", "arg"])
    assert runner == "module"
    assert target == "no-such-cmd-xyz"


# --- execute_command ---


def test_execute_command_module(tmp_path):
    script = tmp_path / "modtarget.py"
    script.write_text("x = 1\n")
    sys.path.insert(0, str(tmp_path))
    try:
        profiler = create_profiler()
        exit_code = execute_command(profiler, ["-m", "modtarget"])
        assert exit_code == 0
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("modtarget", None)


def test_execute_command_script(tmp_path):
    script = tmp_path / "run_me.py"
    script.write_text("x = 1\n")
    profiler = create_profiler()
    exit_code = execute_command(profiler, ["python", str(script)])
    assert exit_code == 0


def test_execute_command_code():
    profiler = create_profiler()
    exit_code = execute_command(profiler, ["python", "-c", "x = 1"])
    assert exit_code == 0


def test_execute_command_zero_exit_returns_0(tmp_path):
    script = tmp_path / "exits.py"
    script.write_text("import sys; sys.exit(0)\n")
    profiler = create_profiler()
    assert execute_command(profiler, ["python", str(script)]) == 0


def test_execute_command_nonzero_exit_returns_code(tmp_path):
    script = tmp_path / "fails.py"
    script.write_text("import sys; sys.exit(42)\n")
    profiler = create_profiler()
    assert execute_command(profiler, ["python", str(script)]) == 42


def test_execute_command_enum_exit_code(tmp_path):
    """Enum exit codes (e.g. pytest ExitCode) should resolve to int."""
    script = tmp_path / "enum_exit.py"
    script.write_text(
        "import enum, sys\n"
        "class ExitCode(enum.IntEnum):\n"
        "    TESTS_FAILED = 1\n"
        "sys.exit(ExitCode.TESTS_FAILED)\n"
    )
    profiler = create_profiler()
    code = execute_command(profiler, ["python", str(script)])
    assert code == 1
    assert isinstance(code, int)


def test_execute_command_string_exit_code(tmp_path):
    """String exit codes should map to 1."""
    script = tmp_path / "str_exit.py"
    script.write_text("import sys; sys.exit('error message')\n")
    profiler = create_profiler()
    assert execute_command(profiler, ["python", str(script)]) == 1


def test_execute_command_exception_returns_1(tmp_path):
    script = tmp_path / "crashes.py"
    script.write_text("raise RuntimeError('boom')\n")
    profiler = create_profiler()
    assert execute_command(profiler, ["python", str(script)]) == 1


def test_execute_command_restores_argv():
    original = sys.argv[:]
    profiler = create_profiler()
    execute_command(profiler, ["python", "-c", "pass"])
    assert sys.argv == original


def test_execute_command_entry_point(tmp_path, monkeypatch):
    """Entry point runner loads and calls the entry point function."""
    mod_file = tmp_path / "ep_target.py"
    mod_file.write_text(
        "called = False\ndef main():\n    global called\n    called = True\n"
    )
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setattr(
        "lazyline.profiling._resolve_console_script",
        lambda name: _fake_ep("my-cli", "ep_target:main") if name == "my-cli" else None,
    )
    try:
        profiler = create_profiler()
        exit_code = execute_command(profiler, ["my-cli", "--verbose"])
        assert exit_code == 0
        assert sys.modules["ep_target"].called is True
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("ep_target", None)


def test_execute_command_entry_point_argv(tmp_path, monkeypatch):
    """Entry point runner sets sys.argv to the full command."""
    mod_file = tmp_path / "ep_argv.py"
    mod_file.write_text(
        "import sys\ncaptured = None\n"
        "def main():\n    global captured\n    captured = list(sys.argv)\n"
    )
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setattr(
        "lazyline.profiling._resolve_console_script",
        lambda name: _fake_ep("my-cli", "ep_argv:main") if name == "my-cli" else None,
    )
    try:
        profiler = create_profiler()
        execute_command(profiler, ["my-cli", "sub", "--flag"])
        assert sys.modules["ep_argv"].captured == ["my-cli", "sub", "--flag"]
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("ep_argv", None)


def test_execute_command_entry_point_dotted_attr(tmp_path, monkeypatch):
    """Entry point with dotted attribute (e.g., Class.method) is resolved."""
    mod_file = tmp_path / "ep_dotted.py"
    mod_file.write_text(
        "called = False\n"
        "class App:\n"
        "    @staticmethod\n"
        "    def launch():\n"
        "        global called\n"
        "        called = True\n"
    )
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setattr(
        "lazyline.profiling._resolve_console_script",
        lambda name: (
            _fake_ep("my-app", "ep_dotted:App.launch") if name == "my-app" else None
        ),
    )
    try:
        profiler = create_profiler()
        exit_code = execute_command(profiler, ["my-app"])
        assert exit_code == 0
        assert sys.modules["ep_dotted"].called is True
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("ep_dotted", None)


def test_execute_command_entry_point_system_exit(tmp_path, monkeypatch):
    """SystemExit from entry point is caught and returns the exit code."""
    mod_file = tmp_path / "ep_exit.py"
    mod_file.write_text("import sys\ndef main():\n    sys.exit(2)\n")
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setattr(
        "lazyline.profiling._resolve_console_script",
        lambda name: _fake_ep("my-cli", "ep_exit:main") if name == "my-cli" else None,
    )
    try:
        profiler = create_profiler()
        assert execute_command(profiler, ["my-cli"]) == 2
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("ep_exit", None)


# --- module name resolution ---


def test_resolve_module_name_known():
    import json

    file_map = _build_file_to_module_map()
    name = _resolve_module_name(json.__file__, file_map)
    assert name == "json"


def test_resolve_module_name_unknown():
    name = _resolve_module_name("/nonexistent/path/foo.py", {})
    assert name == "foo"


# --- collect_results ---


def test_collect_results_empty():
    profiler = create_profiler()
    results = collect_results(profiler)
    assert results == []


def _profile_function(func):
    """Profile a function via direct call (avoids runpy/sys.monitoring reuse issues)."""
    profiler = create_profiler()
    profiler.add_function(func)
    profiler.enable_by_count()
    try:
        func()
    finally:
        profiler.disable_by_count()
    return profiler


def test_collect_results_without_memory_stats_has_none_memory(tmp_path):
    script = tmp_path / "mem_target.py"
    script.write_text("def work():\n    return sum(range(100))\n\nwork()\n")
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("mem_target")
        profiler = _profile_function(mod.work)
        results = collect_results(profiler)
        assert len(results) >= 1
        for fp in results:
            assert fp.memory is None
            for lp in fp.lines:
                assert lp.memory is None
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("mem_target", None)


def test_collect_results_with_memory_stats(tmp_path):
    script = tmp_path / "alloc_target.py"
    script.write_text(
        "def alloc():\n    data = list(range(100))\n    return data\n\nalloc()\n"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("alloc_target")
        profiler = _profile_function(mod.alloc)

        # Build a fake memory_stats dict keyed on the resolved script path.
        resolved = str(script.resolve())
        memory_stats = {(resolved, 2): 512.0, (resolved, 3): 128.0}

        results = collect_results(profiler, memory_stats=memory_stats)
        alloc_fp = next((r for r in results if r.name == "alloc"), None)
        assert alloc_fp is not None
        assert alloc_fp.memory is not None
        assert alloc_fp.memory > 0
        # At least one line should have memory data.
        assert any(lp.memory is not None for lp in alloc_fp.lines)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("alloc_target", None)


# --- end-to-end ---


def test_end_to_end(tmp_path):
    # Uses direct function call instead of execute_command (runpy) because
    # line_profiler's sys.monitoring state on Python 3.12+ becomes unreliable
    # across multiple profiler instances in the same process.
    script = tmp_path / "target.py"
    script.write_text(
        "def slow():\n    total = sum(range(10000))\n    return total\n\nslow()\n"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("target")
        profiler = _profile_function(mod.slow)
        results = collect_results(profiler)
        assert len(results) >= 1
        assert any(r.name == "slow" for r in results)
        slow = next(r for r in results if r.name == "slow")
        assert slow.total_time > 0
        assert slow.call_count >= 1
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("target", None)


def test_collect_results_skips_synthetic_filenames(tmp_path):
    script = tmp_path / "dc_target.py"
    script.write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Point:\n"
        "    x: int\n"
        "    y: int\n\n"
        "def make_point():\n"
        "    return Point(1, 2)\n\n"
        "make_point()\n"
    )
    sys.path.insert(0, str(tmp_path))
    try:
        mod = importlib.import_module("dc_target")
        profiler = _profile_function(mod.make_point)
        # Also register the dataclass-generated __init__ so it gets profiled.
        profiler.add_function(mod.Point.__init__)
        profiler.enable_by_count()
        try:
            mod.make_point()
        finally:
            profiler.disable_by_count()

        results = collect_results(profiler)
        filenames = [r.filename for r in results]
        # Synthetic <string> sources from dataclass __init__ should be filtered out.
        assert not any(f.startswith("<") for f in filenames)
        # The real function should still be present.
        assert any(r.name == "make_point" for r in results)
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("dc_target", None)


# --- enrich_results ---


def test_enrich_results_populates_source(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("def f():\n    return 1\n")
    fp = FunctionProfile(
        module="mod",
        name="f",
        filename=str(src),
        start_line=1,
        total_time=0.1,
        call_count=1,
        lines=[LineProfile(lineno=2, hits=1, time=0.1)],
    )
    enrich_results([fp])
    assert fp.lines[0].source == "def f():"
    assert fp.lines[1].source == "    return 1"


def test_enrich_results_adds_non_hit_lines(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("def g():\n    x = 1\n    y = 2\n    return x + y\n")
    fp = FunctionProfile(
        module="mod",
        name="g",
        filename=str(src),
        start_line=1,
        total_time=0.2,
        call_count=1,
        lines=[
            LineProfile(lineno=2, hits=1, time=0.1),
            LineProfile(lineno=4, hits=1, time=0.1),
        ],
    )
    enrich_results([fp])
    assert len(fp.lines) == 4
    assert [lp.lineno for lp in fp.lines] == [1, 2, 3, 4]
    assert fp.lines[0].hits == 0  # def line
    assert fp.lines[2].hits == 0  # y = 2 (non-hit)
    assert fp.lines[1].hits == 1
    assert fp.lines[3].hits == 1


def test_enrich_results_empty_lines():
    fp = FunctionProfile(
        module="mod",
        name="f",
        filename="x.py",
        start_line=1,
        total_time=0.0,
        call_count=0,
        lines=[],
    )
    enrich_results([fp])
    assert fp.lines == []


def test_enrich_results_preserves_memory(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("def f():\n    return 1\n")
    fp = FunctionProfile(
        module="mod",
        name="f",
        filename=str(src),
        start_line=1,
        total_time=0.1,
        call_count=1,
        lines=[LineProfile(lineno=2, hits=1, time=0.1, memory=256.0)],
        memory=256.0,
    )
    enrich_results([fp])
    hit_line = next(lp for lp in fp.lines if lp.lineno == 2)
    assert hit_line.memory == 256.0
    assert fp.memory == 256.0


# --- register_module_level_code ---


def _import_file_module(path):
    """Import a .py file as a module for testing."""
    import importlib.util

    name = path.stem
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_register_module_level_code_flat_script(tmp_path):
    script = tmp_path / "flat.py"
    script.write_text("x = sum(range(100))\n")
    mod = _import_file_module(script)
    profiler = create_profiler()
    count = register_module_level_code(profiler, [mod], [str(script)])
    assert count == 1
    assert len(profiler.functions) == 1


def test_register_module_level_code_ignores_non_py_scopes(tmp_path):
    script = tmp_path / "pkg_target.py"
    script.write_text("x = 1\n")
    mod = _import_file_module(script)
    profiler = create_profiler()
    count = register_module_level_code(profiler, [mod], ["json"])
    assert count == 0


def test_register_module_level_code_registers_correct_code_object(tmp_path):
    """Module-level code registration produces a stats key with '<module>'."""
    script = tmp_path / "timed.py"
    script.write_text("x = 0\nfor i in range(1000):\n    x += i\n")
    mod = _import_file_module(script)

    profiler = create_profiler()
    register_module_level_code(profiler, [mod], [str(script)])

    # The registered function should have the module-level code object.
    wrapper = profiler.functions[-1]
    assert wrapper.__code__.co_name == "<module>"
    assert wrapper.__code__.co_filename == str(script.resolve())


def test_register_module_level_code_mixed_script(tmp_path):
    """Script with both functions and module-level code registers both."""
    script = tmp_path / "mixed.py"
    script.write_text("def greet():\n    return 'hi'\n\nresult = greet()\n")
    mod = _import_file_module(script)

    profiler = create_profiler()
    profiler.add_function(mod.greet)
    register_module_level_code(profiler, [mod], [str(script)])
    assert len(profiler.functions) == 2


def test_register_module_level_code_file_deleted_after_import(tmp_path):
    """Gracefully skips when source file disappears after import."""
    script = tmp_path / "ephemeral.py"
    script.write_text("x = 1\n")
    mod = _import_file_module(script)
    script.unlink()
    profiler = create_profiler()
    count = register_module_level_code(profiler, [mod], [str(script)])
    assert count == 0


def test_register_module_level_code_syntax_error_after_import(tmp_path):
    """Gracefully skips when the file is modified to invalid syntax after import."""
    script = tmp_path / "broken.py"
    script.write_text("x = 1\n")
    mod = _import_file_module(script)
    script.write_text("def (\n")  # invalid syntax
    profiler = create_profiler()
    count = register_module_level_code(profiler, [mod], [str(script)])
    assert count == 0


def test_register_module_level_code_deduplicates(tmp_path):
    """Same .py scope listed twice registers only once."""
    script = tmp_path / "dup.py"
    script.write_text("x = 1\n")
    mod = _import_file_module(script)
    profiler = create_profiler()
    count = register_module_level_code(profiler, [mod], [str(script), str(script)])
    assert count == 1
    assert len(profiler.functions) == 1
