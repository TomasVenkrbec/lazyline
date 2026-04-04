"""Discover Python modules within a given scope for profiling."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import types

logger = logging.getLogger(__name__)

_TEST_DIR_NAMES = frozenset({"test", "tests"})


def discover_modules(scope: str) -> list[types.ModuleType]:
    """Discover all Python modules within the given scope.

    Discovers both regular packages (with ``__init__.py``) via
    ``pkgutil.walk_packages`` and implicit namespace packages
    (directories without ``__init__.py``, supported since Python 3.3) via filesystem
    scanning. Single ``.py`` files are imported directly via
    ``importlib.util.spec_from_file_location``.

    Parameters
    ----------
    scope
        A dotted module path (e.g., ``"lazyline"``), a filesystem
        directory path (e.g., ``"mypackage/utils"``), or a single ``.py``
        file (e.g., ``"utils.py"``).

    Returns
    -------
    list[types.ModuleType]
        Imported module objects ready for profiling registration.
    """
    # Single .py file — import directly from the filesystem.
    if scope.endswith(".py"):
        file_path = Path(scope)
        if file_path.is_file():
            return _import_from_file(file_path)
        logger.debug("File not found: '%s'.", scope)
        return []

    module_name = _resolve_module_name(scope)
    if not _is_valid_module_name(module_name):
        logger.debug("Invalid scope '%s' (resolved to '%s').", scope, module_name)
        return []
    return _import_module_tree(module_name)


def _resolve_module_name(scope: str) -> str:
    """Convert a scope string to a dotted module name.

    Single ``.py`` files are handled earlier by ``discover_modules``
    and never reach this function.
    """
    if "/" in scope:
        return _path_to_module_name(scope)
    return scope


def _is_valid_module_name(name: str) -> bool:
    """Check if a string is a valid dotted Python module name."""
    if not name:
        return False
    return all(part.isidentifier() for part in name.split("."))


def _path_to_module_name(path_str: str) -> str:
    """Convert a filesystem path to a dotted module name.

    Absolute paths are made relative to the current working directory.
    """
    path = Path(path_str)
    if path.is_absolute():
        try:
            path = path.relative_to(Path.cwd())
        except ValueError:
            # Path is not under CWD — use the name/stem as a last resort
            return path.stem if path.suffix == ".py" else path.name
    if path.suffix == ".py":
        path = path.with_suffix("")
    return ".".join(path.parts)


def _import_from_file(file_path: Path) -> list[types.ModuleType]:
    """Import a single ``.py`` file directly from its filesystem path.

    Uses ``importlib.util.spec_from_file_location`` to bypass
    ``sys.path`` lookup, so files in the current directory (or
    arbitrary paths) work without ``sys.path`` modification.

    Parameters
    ----------
    file_path
        Path to a ``.py`` file.

    Returns
    -------
    list[types.ModuleType]
        A single-element list with the imported module, or empty on failure.
    """
    resolved = file_path.resolve()
    # Use the dotted path (e.g. "my_package.matchers.numpy")
    # instead of the bare stem ("numpy") to avoid shadowing stdlib/third-party
    # packages when the file shares a name with them.
    module_name = _path_to_module_name(str(file_path))
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        logger.error("Could not create import spec for '%s'.", file_path)
        return []
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules AFTER exec, not before — otherwise a file
    # named numpy.py would shadow the real numpy during its own imports.
    try:
        spec.loader.exec_module(mod)
    except Exception:
        logger.error("Failed to import '%s'.", file_path, exc_info=True)
        return []
    # Only register if we wouldn't overwrite an existing module (e.g., a
    # bare "json.py" must not replace the real stdlib json for the profiled
    # command).  The module is still returned for profiling either way.
    if module_name not in sys.modules:
        sys.modules[module_name] = mod
    return [mod]


def _import_module_tree(module_name: str) -> list[types.ModuleType]:
    """Import a module and all its submodules.

    Uses ``pkgutil.walk_packages`` for regular packages (with
    ``__init__.py``), then supplements with a filesystem scan for
    implicit namespace packages (directories without ``__init__.py``).

    Parameters
    ----------
    module_name
        Dotted module name.

    Returns
    -------
    list[types.ModuleType]
        All imported modules (the root plus any submodules).
    """
    modules: list[types.ModuleType] = []

    try:
        root = importlib.import_module(module_name)
    except (ImportError, ValueError, TypeError):
        logger.debug("Could not import module '%s'.", module_name)
        return modules

    modules.append(root)

    package_path = getattr(root, "__path__", None)
    if package_path is not None:
        seen: set[str] = {module_name}

        # Primary: walk regular packages (dirs with __init__.py).
        prefix = module_name + "."
        for _importer, name, _is_pkg in pkgutil.walk_packages(
            package_path, prefix=prefix, onerror=_on_import_error
        ):
            if name.rsplit(".", 1)[-1] == "__main__":
                continue
            seen.add(name)
            try:
                mod = importlib.import_module(name)
                modules.append(mod)
            except Exception:
                logger.warning("Failed to import '%s', skipping.", name)

        # Supplement: discover namespace packages (dirs without __init__.py).
        # Scan root and all regular subpackages — namespace dirs can nest
        # inside regular packages that pkgutil already walked.
        _walk_namespace_children(modules, seen)

    return modules


def _walk_namespace_children(modules: list[types.ModuleType], seen: set[str]) -> None:
    """Scan all discovered packages for namespace sub-packages.

    Called after ``pkgutil.walk_packages`` and before returning. Iterates
    a snapshot of *modules* (which may grow during iteration) so that
    namespace packages nested inside regular sub-packages are found.
    """
    i = 0
    while i < len(modules):
        mod = modules[i]
        pkg_path = getattr(mod, "__path__", None)
        if pkg_path is not None:
            _walk_namespace_packages(mod.__name__, list(pkg_path), modules, seen)
        i += 1


def _walk_namespace_packages(
    parent_name: str,
    search_paths: list[str],
    modules: list[types.ModuleType],
    seen: set[str],
) -> None:
    """Discover modules inside implicit namespace packages.

    Scans directories under *search_paths* for subdirectories without
    ``__init__.py`` that contain Python files, imports them as namespace
    packages, and recursively discovers their children.

    Parameters
    ----------
    parent_name
        Dotted name of the parent package.
    search_paths
        Filesystem paths to scan (typically from ``parent.__path__``).
    modules
        Accumulator list — discovered modules are appended in place.
    seen
        Module names already discovered (for deduplication).
    """
    for search_path in search_paths:
        root_dir = Path(search_path)
        if not root_dir.is_dir():
            continue

        for child in sorted(root_dir.iterdir()):
            if child.is_symlink() or child.name.startswith("__"):
                continue

            if child.is_file() and child.suffix == ".py":
                _try_import_module(parent_name, child.stem, modules, seen)
            elif child.is_dir():
                _process_namespace_dir(parent_name, child, modules, seen)


def _try_import_module(
    parent_name: str,
    name: str,
    modules: list[types.ModuleType],
    seen: set[str],
) -> None:
    """Try to import a single module, skipping if already seen or broken."""
    mod_name = f"{parent_name}.{name}"
    if mod_name in seen:
        return
    seen.add(mod_name)
    try:
        mod = importlib.import_module(mod_name)
        modules.append(mod)
    except Exception:
        logger.warning("Failed to import '%s', skipping.", mod_name)


def _process_namespace_dir(
    parent_name: str,
    child: Path,
    modules: list[types.ModuleType],
    seen: set[str],
) -> None:
    """Process a subdirectory that may be a regular or namespace package."""
    if (child / "__init__.py").exists():
        # Regular package inside a namespace parent — pkgutil never saw it.
        _import_regular_subpackage(parent_name, child.name, modules, seen)
        return

    # Namespace package candidate (no __init__.py).
    if not child.name.isidentifier():
        return
    if child.name in _TEST_DIR_NAMES:
        return
    if not _has_python_shallow(child):
        return

    pkg_name = f"{parent_name}.{child.name}"
    if pkg_name in seen:
        return
    seen.add(pkg_name)
    try:
        mod = importlib.import_module(pkg_name)
        modules.append(mod)
    except Exception:
        logger.warning("Failed to import '%s', skipping.", pkg_name)
        return
    sub_path = getattr(mod, "__path__", None)
    if sub_path is not None:
        _walk_namespace_packages(pkg_name, list(sub_path), modules, seen)


def _import_regular_subpackage(
    parent_name: str,
    name: str,
    modules: list[types.ModuleType],
    seen: set[str],
) -> None:
    """Import a regular package found inside a namespace parent and walk it."""
    pkg_name = f"{parent_name}.{name}"
    if pkg_name in seen:
        return
    seen.add(pkg_name)
    try:
        mod = importlib.import_module(pkg_name)
        modules.append(mod)
    except Exception:
        logger.warning("Failed to import '%s', skipping.", pkg_name)
        return
    sub_path = getattr(mod, "__path__", None)
    if sub_path is not None:
        _walk_regular_subpackages(pkg_name, sub_path, modules, seen)
        _walk_namespace_packages(pkg_name, list(sub_path), modules, seen)


def _walk_regular_subpackages(
    parent_name: str,
    search_paths,
    modules: list[types.ModuleType],
    seen: set[str],
) -> None:
    """Walk a regular package's children via pkgutil.

    Used for regular packages found inside namespace packages, where
    the top-level ``pkgutil.walk_packages`` call never reached them.
    """
    prefix = parent_name + "."
    for _importer, name, _is_pkg in pkgutil.walk_packages(
        search_paths, prefix=prefix, onerror=_on_import_error
    ):
        if name in seen or name.rsplit(".", 1)[-1] == "__main__":
            continue
        seen.add(name)
        try:
            mod = importlib.import_module(name)
            modules.append(mod)
        except Exception:
            logger.warning("Failed to import '%s', skipping.", name)


def _has_python_shallow(directory: Path) -> bool:
    """Check if a directory contains Python files within two levels.

    A two-level check handles the common ``cli/stages/base.py`` pattern
    without the cost of a full recursive scan. Symlinks are skipped.
    """
    try:
        children = list(directory.iterdir())
    except OSError:
        return False
    for entry in children:
        if entry.is_symlink():
            continue
        if entry.is_file() and entry.suffix == ".py":
            return True
        if entry.is_dir() and not entry.name.startswith("__"):
            try:
                for sub in entry.iterdir():
                    if sub.is_symlink():
                        continue
                    if sub.is_file() and sub.suffix == ".py":
                        return True
            except OSError:
                continue
    return False


def _on_import_error(name: str) -> None:
    """Log import errors during package walking without halting discovery."""
    logger.warning("Error walking package '%s', skipping.", name)
