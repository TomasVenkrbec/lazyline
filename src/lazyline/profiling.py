"""LineProfiler integration: setup, registration, execution, and result collection."""

from __future__ import annotations

import linecache
import logging
import runpy
import sys
import types
import warnings
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import TYPE_CHECKING

from line_profiler import LineProfiler

from lazyline.models import FunctionProfile, LineProfile

if TYPE_CHECKING:
    from line_profiler.line_profiler import LineStats

logger = logging.getLogger(__name__)


def create_profiler() -> LineProfiler:
    """Create a new LineProfiler instance."""
    return LineProfiler()


def register_modules(profiler: LineProfiler, modules: list[types.ModuleType]) -> int:
    """Register all functions in the given modules with the profiler.

    After ``add_module``, performs a second pass to unwrap C-extension
    wrappers (e.g., ``functools.lru_cache``) that ``add_module`` skips
    because they lack Python bytecode.

    Parameters
    ----------
    profiler
        The LineProfiler instance.
    modules
        Imported module objects whose functions should be profiled.

    Returns
    -------
    int
        Number of functions registered (incremental, not cumulative).
    """
    before = len(profiler.functions)  # ty: ignore[unresolved-attribute]
    for mod in modules:
        with warnings.catch_warnings():
            # line_profiler warns about functions with __wrapped__ (decorated with
            # functools.wraps). Not actionable in a zero-config tool — suppress it.
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            warnings.filterwarnings("ignore", message=r".*Could not extract a code.*")
            profiler.add_module(mod, scoping_policy="children")
            _register_unwrapped(profiler, mod)
    return len(profiler.functions) - before  # ty: ignore[unresolved-attribute]


def register_module_level_code(
    profiler: LineProfiler, modules: list[types.ModuleType], scopes: list[str]
) -> int:
    """Register module-level code objects for ``.py`` file scopes.

    For each scope that is a ``.py`` file, wraps the module-level code
    object in a ``types.FunctionType`` so that ``line_profiler`` can
    instrument it. This enables profiling of flat scripts that have no
    function definitions.

    Parameters
    ----------
    profiler
        The LineProfiler instance.
    modules
        Imported module objects from discovery.
    scopes
        Original scope strings from the CLI.

    Returns
    -------
    int
        Number of module-level code objects registered (incremental).
    """
    py_scopes = [s for s in scopes if s.endswith(".py")]
    if not py_scopes:
        return 0

    mod_by_path: dict[str, types.ModuleType] = {}
    for mod in modules:
        mod_file = getattr(mod, "__file__", None)
        if mod_file:
            try:
                mod_by_path[str(Path(mod_file).resolve())] = mod
            except (OSError, ValueError):
                continue

    registered = set()
    count = 0
    for scope in py_scopes:
        try:
            resolved = str(Path(scope).resolve())
        except (OSError, ValueError):
            continue
        if resolved not in mod_by_path or resolved in registered:
            continue
        try:
            # read_bytes so compile() handles PEP 263 encoding cookies natively.
            source = Path(resolved).read_bytes()
            code = compile(source, resolved, "exec")
        except (OSError, SyntaxError):
            continue
        # Wrapper is never called — empty globals is intentional.  line_profiler
        # only inspects the code object for bytecode hash matching.
        wrapper = types.FunctionType(code, {})
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*Could not extract a code.*")
            profiler.add_function(wrapper)
        registered.add(resolved)
        count += 1
        logger.debug("Registered module-level code for '%s'.", scope)
    return count


def _register_unwrapped(profiler: LineProfiler, mod: types.ModuleType) -> int:
    """Register functions hidden inside wrappers that ``add_module`` skips.

    Handles two patterns:

    1. **``__wrapped__`` (C-extension wrappers)** — e.g., ``lru_cache``
       replaces the Python function with a C callable that has a
       ``__wrapped__`` attribute pointing to the original.
    2. **Callable instances storing the original as an attribute** — e.g.,
       a decorator that replaces a function with a callable instance that
       keeps the original function as an instance attribute.

    Pattern 2 uses a heuristic: it registers every ``types.FunctionType``
    found in ``vars(candidate)`` whose ``__module__`` matches the
    containing module.  This can produce false positives when a callable
    instance stores references to same-module functions that are not the
    "wrapped original" (e.g., callbacks, strategies).  The ``__module__``
    check limits the blast radius, and duplicate-code guards in the caller
    prevent double-registration, so the practical risk is low.

    Parameters
    ----------
    profiler
        The LineProfiler instance.
    mod
        Module to scan.

    Returns
    -------
    int
        Number of additional functions registered.
    """
    registered_codes = {
        getattr(f, "__code__", None)
        for f in profiler.functions  # ty: ignore[unresolved-attribute]
    }
    count = 0

    for candidate in _iter_unwrap_candidates(mod):
        for func in _find_hidden_functions(candidate, mod.__name__):
            if getattr(func, "__code__", None) in registered_codes:
                continue
            profiler.add_function(func)
            registered_codes.add(func.__code__)
            logger.debug("Unwrapped %s from wrapper.", func.__qualname__)
            count += 1
    return count


def _find_hidden_functions(
    candidate: object, module_name: str
) -> list[types.FunctionType]:
    """Extract Python functions hidden inside a wrapper object.

    Returns functions that belong to *module_name* and are not directly
    visible to ``add_module`` because the wrapper is not a
    ``types.FunctionType``.
    """
    # Plain functions are already handled by add_module.
    if isinstance(candidate, types.FunctionType):
        return []

    # Pattern 1: __wrapped__ (lru_cache, singledispatch, etc.).
    wrapped = getattr(candidate, "__wrapped__", None)
    if (
        isinstance(wrapped, types.FunctionType)
        and getattr(wrapped, "__module__", None) == module_name
    ):
        return [wrapped]
    # If __wrapped__ exists but belongs to another module, fall through
    # to Pattern 2 in case the wrapper also stores a local function.

    # Pattern 2: callable instances that store the original function as an
    # instance attribute (e.g. ParallelWorker.worker_func).
    if isinstance(candidate, type) or not callable(candidate):
        return []
    try:
        attrs = vars(candidate)
    except TypeError:
        return []
    return [
        val
        for val in attrs.values()
        if isinstance(val, types.FunctionType)
        and getattr(val, "__module__", None) == module_name
    ]


def _iter_unwrap_candidates(mod: types.ModuleType):
    """Yield callables from a module, including those inside classes.

    Peels through ``staticmethod``/``classmethod`` descriptors to expose
    the underlying callable (e.g., an ``lru_cache`` wrapper).
    """
    for attr in vars(mod).values():
        yield attr
        # Recurse into classes: scan their __dict__ for descriptors.
        if isinstance(attr, type):
            for member in vars(attr).values():
                # staticmethod/classmethod store the real callable in __func__.
                inner = getattr(member, "__func__", None)
                if inner is not None and inner is not member:
                    yield inner
                else:
                    yield member


def execute_command(profiler: LineProfiler, command: list[str]) -> int:
    """Execute the user's command with profiling enabled.

    Exceptions from the user command are caught so that profiling data
    collected up to the point of failure is preserved. A non-zero
    ``SystemExit`` or unexpected exception is logged as a warning.

    Parameters
    ----------
    profiler
        The LineProfiler instance (with functions already registered).
    command
        Command tokens (e.g., ``["python", "-m", "pytest", "tests/"]``).

    Returns
    -------
    int
        The command's exit code (0 for success, non-zero for failure).
    """
    runner, target, extra_args = _parse_command(command)
    exit_code = 0

    original_argv = sys.argv[:]
    try:
        _dispatch_runner(profiler, runner, target, extra_args, command)
    except SystemExit as exc:
        code = exc.code
        if code is not None and code != 0:
            numeric = int(code) if isinstance(code, int) else getattr(code, "value", 1)
            logger.warning("Command exited with code %s.", numeric)
            exit_code = numeric if isinstance(numeric, int) else 1
    except KeyboardInterrupt:
        logger.warning("Command interrupted by user.")
        exit_code = 130
    except Exception:
        logger.warning("Command raised an exception.", exc_info=True)
        exit_code = 1
    finally:
        sys.argv = original_argv

    return exit_code


def _dispatch_runner(
    profiler: LineProfiler,
    runner: str,
    target: str,
    extra_args: list[str],
    command: list[str],
) -> None:
    """Set up sys.argv and run the user's command under the profiler."""
    if runner == "module":
        sys.argv = [target, *extra_args]
        profiler.enable_by_count()
        try:
            runpy.run_module(target, run_name="__main__", alter_sys=True)
        finally:
            profiler.disable_by_count()

    elif runner == "script":
        sys.argv = [target, *extra_args]
        profiler.enable_by_count()
        try:
            runpy.run_path(target, run_name="__main__")
        finally:
            profiler.disable_by_count()

    elif runner == "entry_point":
        # target is "module:attr" from _parse_command; extra_args is the
        # full argv (command name + arguments).
        ep = EntryPoint(name="_lazyline", value=target, group="console_scripts")
        sys.argv = list(extra_args)
        func = ep.load()
        profiler.enable_by_count()
        try:
            func()
        finally:
            profiler.disable_by_count()

    elif runner == "code":
        sys.argv = ["-c", *extra_args]
        profiler.enable_by_count()
        try:
            exec(  # noqa: S102
                compile(target, "<string>", "exec"),
                {"__name__": "__main__", "__builtins__": __builtins__},
            )
        finally:
            profiler.disable_by_count()

    else:
        msg = f"Cannot determine how to run command: {command}"
        raise ValueError(msg)


def _is_valid_module_path(name: str) -> bool:
    """Check if a string is a valid dotted Python module path."""
    return bool(name) and all(part.isidentifier() for part in name.split("."))


def _resolve_console_script(name: str) -> EntryPoint | None:
    """Look up a console script entry point by command name.

    Returns
    -------
    EntryPoint or None
        The matching entry point if found.
    """
    for ep in entry_points(group="console_scripts", name=name):
        return ep
    return None


_PYTHON_FLAGS_NO_ARG = frozenset(
    [
        "-b",
        "-bb",
        "-B",
        "-d",
        "-E",
        "-I",
        "-i",
        "-O",
        "-OO",
        "-P",
        "-q",
        "-R",
        "-s",
        "-S",
        "-u",
        "-v",
        "-vv",
        "-x",
    ]
)

_PYTHON_FLAGS_WITH_ARG = frozenset(["-W", "-X"])


def _skip_python_flags(tokens: list[str]) -> list[str]:
    """Strip leading Python interpreter flags, returning remaining tokens."""
    while tokens:
        if tokens[0] in _PYTHON_FLAGS_NO_ARG:
            tokens = tokens[1:]
        elif tokens[0] in _PYTHON_FLAGS_WITH_ARG:
            tokens = tokens[2:]  # skip flag + its required argument
        else:
            break
    return tokens


def _parse_command(command: list[str]) -> tuple[str, str, list[str]]:
    """Parse command tokens into runner type, target, and extra args.

    Returns
    -------
    tuple[str, str, list[str]]
        ``(runner, target, extra_args)`` where runner is ``"module"``,
        ``"script"``, ``"entry_point"``, or ``"code"``.
        For ``"entry_point"``, target is the entry point value
        (e.g., ``"pkg.cli:main"``) and extra_args includes the
        command name as first element (used as ``sys.argv``).
    """
    tokens = list(command)

    if tokens and _is_python_executable(tokens[0]):
        tokens = tokens[1:]

    if not tokens:
        msg = "Empty command after stripping Python executable."
        raise ValueError(msg)

    tokens = _skip_python_flags(tokens)

    if not tokens:
        msg = "Only interpreter flags found, no command to run."
        raise ValueError(msg)

    # python -m module_name [args...]
    if tokens[0] == "-m":
        if len(tokens) < 2:
            msg = "'-m' requires a module name."
            raise ValueError(msg)
        return ("module", tokens[1], tokens[2:])

    # python -c "code" [args...]
    if tokens[0] == "-c":
        if len(tokens) < 2:
            msg = "'-c' requires a code string."
            raise ValueError(msg)
        return ("code", tokens[1], tokens[2:])

    # python script.py [args...]
    first = tokens[0]
    if first.endswith(".py"):
        return ("script", first, tokens[1:])

    return _resolve_bare_command(first, tokens)


def _resolve_bare_command(first: str, tokens: list[str]) -> tuple[str, str, list[str]]:
    """Resolve a bare command to a runner type.

    Valid module paths (e.g., ``"pytest"``) are treated as modules.
    Otherwise, the command is looked up in installed console_scripts
    entry points. Falls back to module if not found.
    """
    if _is_valid_module_path(first):
        return ("module", first, tokens[1:])

    ep = _resolve_console_script(first)
    if ep is not None:
        return ("entry_point", ep.value, tokens)

    # Unknown bare command — try as module anyway (will fail with a clear error).
    return ("module", first, tokens[1:])


def _is_python_executable(token: str) -> bool:
    """Check if a token looks like a Python interpreter name."""
    name = Path(token).name
    return name in ("python", "python3") or name.startswith("python3.")


def _resolve_filename(filename: str) -> str | None:
    """Resolve a filename to an absolute path, returning None on failure."""
    try:
        return str(Path(filename).resolve())
    except (OSError, ValueError):
        return None


def collect_results(
    profiler_or_stats: LineProfiler | LineStats,
    memory_stats: dict[tuple[str, int], float] | None = None,
    scope_paths: set[str] | None = None,
) -> list[FunctionProfile]:
    """Extract profiling results from a LineProfiler or pre-merged LineStats.

    Parameters
    ----------
    profiler_or_stats
        A LineProfiler instance (calls ``get_stats()`` internally) or a
        pre-merged ``LineStats`` object (e.g., from worker stats aggregation).
    memory_stats
        Optional per-line memory deltas from :mod:`lazyline.memory`.
        Keys are ``(absolute_filename, lineno)``, values are net bytes.
    scope_paths
        Optional set of resolved file paths of in-scope modules. When
        provided, functions from files not in this set are excluded.
        This filters out stdlib/third-party wrappers that leak into
        registration via ``__module__`` matching (e.g., ``singledispatch``
        wrappers from ``functools.py``).

    Returns
    -------
    list[FunctionProfile]
        Profiling results sorted by total time (descending).
    """
    if isinstance(profiler_or_stats, LineProfiler):
        stats = profiler_or_stats.get_stats()
    else:
        stats = profiler_or_stats
    unit = stats.unit
    file_to_module = _build_file_to_module_map()

    results: list[FunctionProfile] = []

    need_resolved = memory_stats is not None or scope_paths is not None

    for (filename, start_line, func_name), timings in stats.timings.items():
        if not timings:
            continue

        # Skip synthetic sources (dataclass __init__/__eq__ from exec).
        if filename.startswith("<"):
            continue

        # Resolve once per function — used for both scope filtering and
        # memory stat lookups.
        resolved_filename = _resolve_filename(filename) if need_resolved else None

        # Skip functions whose source file is outside the profiled scope.
        if scope_paths is not None and (
            resolved_filename is None or resolved_filename not in scope_paths
        ):
            continue

        lines: list[LineProfile] = []
        total_time = 0.0

        for lineno, nhits, raw_time in timings:
            time_seconds = raw_time * unit
            total_time += time_seconds
            line_mem = None
            if resolved_filename is not None and memory_stats is not None:
                line_mem = memory_stats.get((resolved_filename, lineno))
            lines.append(
                LineProfile(
                    lineno=lineno, hits=nhits, time=time_seconds, memory=line_mem
                )
            )

        func_memory = None
        if memory_stats is not None:
            line_mems = [lp.memory for lp in lines if lp.memory is not None]
            func_memory = sum(line_mems) if line_mems else None

        # First profiled line's nhits is the best proxy for call count.
        call_count = timings[0][1]

        results.append(
            FunctionProfile(
                module=_resolve_module_name(filename, file_to_module),
                name=func_name,
                filename=filename,
                start_line=start_line,
                total_time=total_time,
                call_count=call_count,
                lines=lines,
                memory=func_memory,
            )
        )

    results.sort(key=lambda fp: fp.total_time, reverse=True)
    return results


def enrich_results(results: list[FunctionProfile]) -> None:
    """Populate source code and insert non-hit lines for each function profile.

    Fills ``LineProfile.source`` via ``linecache`` and inserts zero-hit entries
    for source lines in ``[start_line, last_profiled_line]`` that were not
    executed, making each function's line list a complete source representation.

    Parameters
    ----------
    results
        Function profiles from :func:`collect_results`. Mutated in place.
    """
    for fp in results:
        if not fp.lines:
            continue

        timing_by_line = {lp.lineno: lp for lp in fp.lines}
        last_profiled_line = max(lp.lineno for lp in fp.lines)

        enriched: list[LineProfile] = []
        for lineno in range(fp.start_line, last_profiled_line + 1):
            source = linecache.getline(fp.filename, lineno).rstrip()
            existing = timing_by_line.get(lineno)
            if existing is not None:
                if source or not existing.source:
                    existing.source = source
                enriched.append(existing)
            else:
                enriched.append(
                    LineProfile(lineno=lineno, hits=0, time=0.0, source=source)
                )

        fp.lines = enriched


def build_scope_paths(modules: list[types.ModuleType]) -> set[str]:
    """Build the set of resolved file paths for the given modules.

    Parameters
    ----------
    modules
        Imported module objects (from :func:`~lazyline.discovery.discover_modules`).

    Returns
    -------
    set[str]
        Resolved absolute file paths suitable for passing to
        :func:`collect_results` as ``scope_paths``.
    """
    paths: set[str] = set()
    for mod in modules:
        mod_file = getattr(mod, "__file__", None)
        if mod_file:
            try:
                paths.add(str(Path(mod_file).resolve()))
            except (OSError, ValueError):
                continue
    return paths


def _resolve_module_name(filename: str, file_to_module: dict[str, str]) -> str:
    """Look up module name for a filename, falling back to the file stem."""
    try:
        resolved = str(Path(filename).resolve())
    except (OSError, ValueError):
        return Path(filename).stem
    return file_to_module.get(resolved, Path(filename).stem)


def _build_file_to_module_map() -> dict[str, str]:
    """Build a reverse lookup from resolved file path to module name."""
    result: dict[str, str] = {}
    for name, mod in list(sys.modules.items()):
        mod_file = getattr(mod, "__file__", None)
        if mod_file:
            try:
                resolved = str(Path(mod_file).resolve())
            except (OSError, ValueError):
                continue
            result[resolved] = name
    return result
