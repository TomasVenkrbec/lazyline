"""Tests for namespace package discovery (dirs without __init__.py)."""

import importlib
import os
import sys

import pytest

from lazyline.discovery import _has_python_shallow, discover_modules


@pytest.fixture()
def ns_package(tmp_path):
    """Create a package with both regular and namespace subpackages.

    Layout::

        mypkg/
        ├── __init__.py
        ├── models.py
        ├── regular/
        │   ├── __init__.py
        │   └── core.py
        ├── namespace_sub/          # no __init__.py
        │   ├── utils.py
        │   └── helpers.py
        ├── tests/                  # no __init__.py — should be excluded
        │   └── test_stuff.py
        ├── _private/               # no __init__.py — should be included
        │   └── internal.py
        ├── data/                   # no __init__.py, no .py files
        │   └── config.json
        └── nested_ns/              # no __init__.py
            └── deep/               # has __init__.py (regular inside namespace)
                ├── __init__.py
                └── impl.py
    """
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("__version__ = '0.1'\n")
    (pkg / "models.py").write_text("class Model: pass\n")

    # Regular subpackage
    regular = pkg / "regular"
    regular.mkdir()
    (regular / "__init__.py").write_text("")
    (regular / "core.py").write_text("def core_func(): pass\n")

    # Namespace subpackage (no __init__.py)
    ns_sub = pkg / "namespace_sub"
    ns_sub.mkdir()
    (ns_sub / "utils.py").write_text("def util_func(): pass\n")
    (ns_sub / "helpers.py").write_text("def helper_func(): pass\n")

    # Tests directory (should be excluded)
    tests = pkg / "tests"
    tests.mkdir()
    (tests / "test_stuff.py").write_text("def test_something(): pass\n")

    # Private namespace subpackage (should be included)
    private = pkg / "_private"
    private.mkdir()
    (private / "internal.py").write_text("SECRET = 42\n")

    # Data directory (no Python files — should be skipped)
    data = pkg / "data"
    data.mkdir()
    (data / "config.json").write_text("{}\n")

    # Namespace dir inside regular subpackage (the bug from #6)
    factories = regular / "factories"
    factories.mkdir()
    (factories / "builder.py").write_text("def build(): pass\n")

    # Nested: namespace package containing a regular package
    nested_ns = pkg / "nested_ns"
    nested_ns.mkdir()
    deep = nested_ns / "deep"
    deep.mkdir()
    (deep / "__init__.py").write_text("")
    (deep / "impl.py").write_text("def deep_func(): pass\n")
    (deep / "__main__.py").write_text("raise RuntimeError('should not be imported')\n")

    sys.path.insert(0, str(tmp_path))
    yield pkg
    sys.path.remove(str(tmp_path))
    for key in list(sys.modules):
        if key.startswith("mypkg"):
            del sys.modules[key]
    importlib.invalidate_caches()


def test_namespace_subpackage_discovered(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.namespace_sub.utils" in names
    assert "mypkg.namespace_sub.helpers" in names


def test_regular_subpackage_still_discovered(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.regular" in names
    assert "mypkg.regular.core" in names


def test_root_modules_discovered(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg" in names
    assert "mypkg.models" in names


def test_tests_directory_excluded(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.tests" not in names
    assert "mypkg.tests.test_stuff" not in names


def test_private_namespace_included(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg._private.internal" in names


def test_data_directory_skipped(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.data" not in names


def test_regular_inside_namespace_discovered(ns_package):
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.nested_ns.deep" in names
    assert "mypkg.nested_ns.deep.impl" in names


def test_dunder_main_skipped_in_regular_inside_namespace(ns_package):
    """__main__.py inside a regular package nested in a namespace is skipped."""
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.nested_ns.deep" in names
    assert "mypkg.nested_ns.deep.impl" in names
    assert "mypkg.nested_ns.deep.__main__" not in names


def test_pycache_skipped(ns_package):
    pycache = ns_package / "__pycache__"
    pycache.mkdir()
    (pycache / "models.cpython-312.pyc").write_bytes(b"")
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert not any("__pycache__" in n for n in names)


def test_non_identifier_directory_skipped(ns_package):
    bad_dir = ns_package / "123invalid"
    bad_dir.mkdir()
    (bad_dir / "mod.py").write_text("x = 1\n")
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert not any("123invalid" in n for n in names)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="OS does not support symlinks")
def test_symlink_directory_skipped(ns_package):
    link = ns_package / "link_to_ns"
    link.symlink_to(ns_package / "namespace_sub")
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.link_to_ns" not in names
    # Original namespace sub is still discovered
    assert "mypkg.namespace_sub.utils" in names


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="OS does not support symlinks")
def test_symlink_loop_does_not_hang(ns_package):
    loop = ns_package / "namespace_sub" / "loop_back"
    loop.symlink_to(ns_package)
    modules = discover_modules("mypkg")
    # Should complete without hanging; namespace_sub modules still found
    names = {m.__name__ for m in modules}
    assert "mypkg.namespace_sub.utils" in names


def test_broken_module_in_namespace_skipped(ns_package):
    ns_sub = ns_package / "namespace_sub"
    (ns_sub / "broken.py").write_text("raise RuntimeError('boom')\n")
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.namespace_sub.broken" not in names
    # Other modules in same namespace still discovered
    assert "mypkg.namespace_sub.utils" in names


def test_test_singular_directory_excluded(ns_package):
    test_dir = ns_package / "test"
    test_dir.mkdir()
    (test_dir / "conftest.py").write_text("x = 1\n")
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.test" not in names


def test_namespace_inside_regular_subpackage(ns_package):
    """Namespace dirs inside regular subpackages should be discovered."""
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    assert "mypkg.regular.factories" in names
    assert "mypkg.regular.factories.builder" in names


def test_total_module_count(ns_package):
    """Verify the expected total count of discovered modules."""
    modules = discover_modules("mypkg")
    names = {m.__name__ for m in modules}
    expected = {
        "mypkg",
        "mypkg.models",
        "mypkg.regular",
        "mypkg.regular.core",
        "mypkg.regular.factories",
        "mypkg.regular.factories.builder",
        "mypkg.namespace_sub",
        "mypkg.namespace_sub.utils",
        "mypkg.namespace_sub.helpers",
        "mypkg._private",
        "mypkg._private.internal",
        "mypkg.nested_ns",
        "mypkg.nested_ns.deep",
        "mypkg.nested_ns.deep.impl",
    }
    assert expected == names


# --- _has_python_shallow unit tests ---


def test_has_python_shallow_with_py_file(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "mod.py").write_text("")
    assert _has_python_shallow(d) is True


def test_has_python_shallow_empty_dir(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert _has_python_shallow(d) is False


def test_has_python_shallow_only_non_py(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "readme.txt").write_text("")
    (d / "config.yaml").write_text("")
    assert _has_python_shallow(d) is False


def test_has_python_shallow_nested_one_level(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    sub = d / "sub"
    sub.mkdir()
    (sub / "mod.py").write_text("")
    assert _has_python_shallow(d) is True


def test_has_python_shallow_skips_pycache(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    pycache = d / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-312.pyc").write_bytes(b"")
    assert _has_python_shallow(d) is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="OS does not support symlinks")
def test_has_python_shallow_skips_symlinks(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "mod.py").write_text("")

    d = tmp_path / "pkg"
    d.mkdir()
    (d / "link").symlink_to(real)
    assert _has_python_shallow(d) is False
