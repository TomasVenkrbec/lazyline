"""Tests for subprocess profiling injection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from line_profiler.line_profiler import LineStats

from lazyline.subproc import (
    _ENV_SCOPES,
    _ENV_STATS_DIR,
    _SCOPE_SEPARATOR,
    _collect_subprocess_stats,
    _encode_scopes,
    _write_sitecustomize,
    subprocess_hooks,
)

# ---------------------------------------------------------------------------
# _encode_scopes
# ---------------------------------------------------------------------------


def test_encode_scopes_module_names():
    result = _encode_scopes(["acme.pkg", "json"])
    assert result == f"acme.pkg{_SCOPE_SEPARATOR}json"


def test_encode_scopes_py_file_absolute(tmp_path):
    f = tmp_path / "my_module.py"
    f.write_text("x = 1\n")
    result = _encode_scopes([str(f)])
    assert result == str(f.resolve())


def test_encode_scopes_py_file_relative(tmp_path, monkeypatch):
    f = tmp_path / "rel.py"
    f.write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    result = _encode_scopes(["rel.py"])
    assert result == str(f.resolve())


def test_encode_scopes_mixed(tmp_path):
    f = tmp_path / "utils.py"
    f.write_text("x = 1\n")
    result = _encode_scopes(["acme.pkg", str(f)])
    parts = result.split(_SCOPE_SEPARATOR)
    assert parts[0] == "acme.pkg"
    assert parts[1] == str(f.resolve())


# ---------------------------------------------------------------------------
# _write_sitecustomize
# ---------------------------------------------------------------------------


def test_write_sitecustomize_creates_file(tmp_path):
    _write_sitecustomize(str(tmp_path))
    sc = tmp_path / "sitecustomize.py"
    assert sc.exists()
    content = sc.read_text()
    assert "_LAZYLINE_STATS_DIR" in content
    assert "lazyline.subproc" in content
    # Should contain the chaining logic.
    assert "_ORIGINAL_SITECUSTOMIZE" in content


# ---------------------------------------------------------------------------
# _collect_subprocess_stats
# ---------------------------------------------------------------------------


def test_collect_stats_empty_dir(tmp_path):
    assert _collect_subprocess_stats(str(tmp_path)) is None


def test_collect_stats_merges_files(tmp_path):
    s1 = LineStats({("f.py", 1, "foo"): [(1, 10, 5000)]}, 1e-9)
    s2 = LineStats({("f.py", 1, "foo"): [(1, 20, 3000)]}, 1e-9)
    s1.to_file(str(tmp_path / "100.pkl"))
    s2.to_file(str(tmp_path / "101.pkl"))

    merged = _collect_subprocess_stats(str(tmp_path))
    assert merged is not None
    assert merged.timings[("f.py", 1, "foo")] == [(1, 30, 8000)]


def test_collect_stats_skips_corrupt_file(tmp_path):
    good = LineStats({("f.py", 1, "foo"): [(1, 5, 100)]}, 1e-9)
    good.to_file(str(tmp_path / "100.pkl"))
    (tmp_path / "bad.pkl").write_bytes(b"not a pickle")

    result = _collect_subprocess_stats(str(tmp_path))
    assert result is not None
    assert result.timings[("f.py", 1, "foo")] == [(1, 5, 100)]


def test_collect_stats_nonexistent_dir():
    assert _collect_subprocess_stats("/nonexistent/path") is None


# ---------------------------------------------------------------------------
# subprocess_hooks context manager
# ---------------------------------------------------------------------------


def test_subprocess_hooks_sets_env_vars():
    """Env vars are set inside the with block and cleaned up after."""
    orig_pp = os.environ.get("PYTHONPATH")
    orig_sd = os.environ.get(_ENV_STATS_DIR)
    orig_sc = os.environ.get(_ENV_SCOPES)

    with subprocess_hooks(["json"]) as holder:
        assert _ENV_STATS_DIR in os.environ
        assert _ENV_SCOPES in os.environ
        assert "PYTHONPATH" in os.environ
        pp = os.environ["PYTHONPATH"]
        assert "lazyline_bootstrap_" in pp

    assert os.environ.get("PYTHONPATH") == orig_pp
    assert os.environ.get(_ENV_STATS_DIR) == orig_sd
    assert os.environ.get(_ENV_SCOPES) == orig_sc
    assert holder.stats is None


def test_subprocess_hooks_preserves_existing_pythonpath(monkeypatch):
    """Existing PYTHONPATH entries are preserved (appended after bootstrap)."""
    monkeypatch.setenv("PYTHONPATH", "/existing/path")

    with subprocess_hooks(["json"]):
        pp = os.environ["PYTHONPATH"]
        assert "/existing/path" in pp
        parts = pp.split(os.pathsep)
        assert "lazyline_bootstrap_" in parts[0]

    # monkeypatch restores automatically.


def test_subprocess_hooks_restores_env_on_exception():
    """Env vars are restored even if the body raises."""
    orig_pp = os.environ.get("PYTHONPATH")
    with pytest.raises(RuntimeError, match="boom"), subprocess_hooks(["json"]):
        raise RuntimeError("boom")
    assert os.environ.get("PYTHONPATH") == orig_pp


def test_subprocess_hooks_cleans_up_temp_dirs():
    """Both stats dir and bootstrap dir are removed after the block."""
    with subprocess_hooks(["json"]):
        stats_dir = os.environ[_ENV_STATS_DIR]
        pp = os.environ["PYTHONPATH"]
        bootstrap_dir = pp.split(os.pathsep)[0]
        assert os.path.isdir(stats_dir)
        assert os.path.isdir(bootstrap_dir)

    assert not os.path.exists(stats_dir)
    assert not os.path.exists(bootstrap_dir)


# ---------------------------------------------------------------------------
# Integration: actual subprocess profiling
# ---------------------------------------------------------------------------


def test_subprocess_profiling_captures_child_stats(tmp_path):
    """Functions called in a child subprocess are captured via the bootstrap."""
    target = tmp_path / "target_mod.py"
    target.write_text("def compute():\n    return sum(range(1000))\n")

    with subprocess_hooks([str(target)]) as holder:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                "import target_mod; target_mod.compute()",
            ],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode()

    assert holder.stats is not None
    func_names = [k[2] for k in holder.stats.timings]
    assert "compute" in func_names


def test_subprocess_profiling_multiple_children(tmp_path):
    """Stats from multiple child processes are merged."""
    target = tmp_path / "multi_mod.py"
    target.write_text("def work():\n    return list(range(100))\n")

    with subprocess_hooks([str(target)]) as holder:
        for _ in range(3):
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                    "import multi_mod; multi_mod.work()",
                ],
                capture_output=True,
                timeout=30,
            )

    assert holder.stats is not None
    key = next(k for k in holder.stats.timings if k[2] == "work")
    first_line = holder.stats.timings[key][0]
    assert first_line[1] >= 3  # nhits merged from 3 children


def test_subprocess_profiling_no_stats_without_subprocesses():
    """No stats when no child processes are spawned."""
    with subprocess_hooks(["json"]) as holder:
        pass
    assert holder.stats is None


def test_subprocess_profiling_with_module_scope():
    """Module name scopes (not just .py files) work in subprocesses."""
    with subprocess_hooks(["json"]) as holder:
        subprocess.run(
            [sys.executable, "-c", "import json; json.dumps([1, 2, 3])"],
            capture_output=True,
            timeout=30,
        )

    assert holder.stats is not None
    func_names = [k[2] for k in holder.stats.timings]
    assert len(func_names) > 0


def test_subprocess_profiling_grandchild(tmp_path):
    """Profiling propagates to grandchild processes (env vars inherited)."""
    target = tmp_path / "deep_mod.py"
    target.write_text("def deep_func():\n    return 42\n")

    grandchild = tmp_path / "grandchild.py"
    grandchild.write_text(
        f"import sys; sys.path.insert(0, {str(tmp_path)!r})\n"
        "import deep_mod; deep_mod.deep_func()\n"
    )

    driver = tmp_path / "driver.py"
    driver.write_text(
        "import subprocess, sys\n"
        f"subprocess.run([sys.executable, {str(grandchild)!r}], check=True)\n"
    )

    with subprocess_hooks([str(target)]) as holder:
        result = subprocess.run(
            [sys.executable, str(driver)],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode()

    assert holder.stats is not None
    func_names = [k[2] for k in holder.stats.timings]
    assert "deep_func" in func_names


def test_subprocess_profiling_with_multiprocessing(tmp_path):
    """Multiprocessing workers inside a subprocess are captured."""
    fixture = Path(__file__).parent / "_parallel_fixture.py"
    script = tmp_path / "run_parallel.py"
    script.write_text(
        f"import sys; sys.path.insert(0, {str(fixture.parent)!r})\n"
        "from _parallel_fixture import run_with_process_pool\n"
        "run_with_process_pool(list(range(20)))\n"
    )

    with subprocess_hooks([str(fixture)]) as holder:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr.decode()

    assert holder.stats is not None
    func_names = [k[2] for k in holder.stats.timings]
    assert "slow_computation" in func_names


def test_subprocess_profiling_scope_import_fails(tmp_path):
    """Bootstrap is a silent no-op when the scope cannot be imported."""
    with subprocess_hooks(["nonexistent.package.that.does.not.exist"]) as holder:
        subprocess.run(
            [sys.executable, "-c", "pass"],
            capture_output=True,
            timeout=30,
        )

    assert holder.stats is None


def test_subprocess_profiling_python_S_flag(tmp_path):
    """python -S skips site.py — profiling silently skipped."""
    target = tmp_path / "skipped_mod.py"
    target.write_text("def skipped():\n    return 1\n")

    with subprocess_hooks([str(target)]) as holder:
        subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
                "import skipped_mod; skipped_mod.skipped()",
            ],
            capture_output=True,
            timeout=30,
        )

    # -S disables site.py (and thus sitecustomize.py), so no stats.
    assert holder.stats is None
