import importlib
import sys

import pytest

from lazyline.discovery import (
    _is_valid_module_name,
    _on_import_error,
    _resolve_module_name,
    discover_modules,
)


def test_resolve_module_name_dotted():
    assert _resolve_module_name("json") == "json"


def test_resolve_module_name_path():
    assert _resolve_module_name("acme/mylib") == "acme.mylib"


def test_resolve_module_name_path_with_py():
    assert _resolve_module_name("acme/mylib/cli.py") == "acme.mylib.cli"


def test_discover_stdlib_package():
    modules = discover_modules("json")
    names = [m.__name__ for m in modules]
    assert "json" in names
    assert len(modules) >= 1


def test_discover_own_package():
    modules = discover_modules("lazyline")
    names = [m.__name__ for m in modules]
    assert "lazyline" in names
    assert len(modules) >= 1


def test_discover_nonexistent():
    modules = discover_modules("nonexistent.module.xyz")
    assert modules == []


@pytest.mark.parametrize("scope", ["", ".", "/", ".."])
def test_discover_invalid_scope_returns_empty(scope):
    """Invalid scope strings should return empty list, not crash."""
    modules = discover_modules(scope)
    assert modules == []


def test_is_valid_module_name():
    assert _is_valid_module_name("json")
    assert _is_valid_module_name("lazyline")
    assert not _is_valid_module_name("")
    assert not _is_valid_module_name(".")
    assert not _is_valid_module_name("..")
    assert not _is_valid_module_name("123invalid")


def test_resolve_module_name_absolute_path_under_cwd():
    import os

    cwd = os.getcwd()
    result = _resolve_module_name(f"{cwd}/acme/mylib")
    assert result == "acme.mylib"


def test_resolve_module_name_absolute_path_outside_cwd():
    result = _resolve_module_name("/totally/unrelated/path/mypkg")
    assert result == "mypkg"


def test_resolve_module_name_absolute_py_file_outside_cwd():
    result = _resolve_module_name("/totally/unrelated/script.py")
    assert result == "script"


def test_on_import_error_does_not_raise():
    _on_import_error("some.broken.module")


def test_discover_skips_dunder_main(tmp_path):
    """__main__.py modules should not be imported during discovery."""
    pkg = tmp_path / "mainpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("x = 1\n")
    (pkg / "__main__.py").write_text("raise RuntimeError('should not be imported')\n")
    sys.path.insert(0, str(tmp_path))
    try:
        modules = discover_modules("mainpkg")
        names = [m.__name__ for m in modules]
        assert "mainpkg" in names
        assert "mainpkg.core" in names
        assert "mainpkg.__main__" not in names
    finally:
        sys.path.remove(str(tmp_path))
        for key in list(sys.modules):
            if key.startswith("mainpkg"):
                del sys.modules[key]
        importlib.invalidate_caches()


def test_discover_skips_broken_submodule(tmp_path):
    pkg = tmp_path / "brokenpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text("x = 1\n")
    (pkg / "bad.py").write_text("raise RuntimeError('broken')\n")
    sys.path.insert(0, str(tmp_path))
    try:
        modules = discover_modules("brokenpkg")
        names = [m.__name__ for m in modules]
        assert "brokenpkg" in names
        assert "brokenpkg.good" in names
        assert "brokenpkg.bad" not in names
    finally:
        sys.path.remove(str(tmp_path))
        for key in list(sys.modules):
            if key.startswith("brokenpkg"):
                del sys.modules[key]
        importlib.invalidate_caches()


# --- single .py file scope ---


def test_discover_single_py_file(tmp_path, monkeypatch):
    """A .py file scope should import the file directly."""
    script = tmp_path / "myutil.py"
    script.write_text("def greet(): return 'hello'\n")
    monkeypatch.chdir(tmp_path)
    modules = discover_modules("myutil.py")
    assert len(modules) == 1
    assert hasattr(modules[0], "greet")
    sys.modules.pop(modules[0].__name__, None)


def test_discover_single_py_file_absolute_path(tmp_path):
    """An absolute .py file path should work regardless of CWD."""
    script = tmp_path / "absutil.py"
    script.write_text("X = 42\n")
    modules = discover_modules(str(script))
    assert len(modules) == 1
    assert modules[0].X == 42
    sys.modules.pop(modules[0].__name__, None)


def test_discover_single_py_file_not_found(tmp_path, monkeypatch):
    """A .py file scope pointing to a nonexistent file returns empty."""
    monkeypatch.chdir(tmp_path)
    modules = discover_modules("nosuchfile.py")
    assert modules == []


def test_discover_single_py_file_broken(tmp_path, monkeypatch):
    """A .py file with import errors returns empty list, not crash."""
    script = tmp_path / "broken.py"
    script.write_text("raise RuntimeError('boom')\n")
    monkeypatch.chdir(tmp_path)
    modules = discover_modules("broken.py")
    assert modules == []
    sys.modules.pop("broken", None)


def test_discover_single_py_file_not_on_sys_path(tmp_path, monkeypatch):
    """File import should work even when the directory is NOT on sys.path."""
    script = tmp_path / "isolated.py"
    script.write_text("def func(): pass\n")
    monkeypatch.chdir(tmp_path)
    # Ensure tmp_path is not on sys.path
    original = sys.path[:]
    sys.path[:] = [p for p in sys.path if str(tmp_path) not in p]
    try:
        modules = discover_modules("isolated.py")
        assert len(modules) == 1
        assert modules[0].__name__ == "isolated"
    finally:
        sys.path[:] = original
        sys.modules.pop("isolated", None)


def test_discover_single_py_file_subdir_no_name_collision(tmp_path, monkeypatch):
    """A file in a subdirectory uses a dotted name to avoid shadowing."""
    subdir = tmp_path / "pkg" / "matchers"
    subdir.mkdir(parents=True)
    script = subdir / "json.py"
    script.write_text("import json as _real_json\nX = _real_json.dumps([1])\n")
    monkeypatch.chdir(tmp_path)
    modules = discover_modules("pkg/matchers/json.py")
    assert len(modules) == 1
    assert modules[0].__name__ == "pkg.matchers.json"
    assert modules[0].X == "[1]"
    sys.modules.pop(modules[0].__name__, None)


def test_discover_single_py_file_bare_name_collision(tmp_path, monkeypatch):
    """A bare file named after a stdlib module should still import correctly."""
    import json as real_json

    script = tmp_path / "json.py"
    script.write_text("import json as _real\nX = _real.dumps([1])\n")
    monkeypatch.chdir(tmp_path)
    modules = discover_modules("json.py")
    assert len(modules) == 1
    assert modules[0].X == "[1]"
    # Real stdlib json must NOT be overwritten in sys.modules.
    assert sys.modules["json"] is real_json
