"""Lazyline CLI — zero-config line-level profiler."""

from __future__ import annotations

import contextlib
import platform
import sys
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Annotated, Final

import typer

from lazyline import __version__
from lazyline.discovery import discover_modules
from lazyline.export import from_json, to_json
from lazyline.memory import start_tracking, stop_tracking
from lazyline.models import FunctionProfile, ProfileRun, RunMetadata
from lazyline.parallel import merge_stats, profiling_hooks
from lazyline.profiling import (
    build_scope_paths,
    collect_results,
    create_profiler,
    enrich_results,
    execute_command,
    register_module_level_code,
    register_modules,
)
from lazyline.reporting import print_summary
from lazyline.subproc import subprocess_hooks

app = typer.Typer(no_args_is_help=True, pretty_exceptions_show_locals=False)

# --- Constants ---

_HIGH_HIT_THRESHOLD: Final[int] = 1_000_000
_STDOUT_PATH: Final[str] = "-"
_VALID_UNITS: Final[frozenset[str]] = frozenset({"s", "ms", "us", "ns", "auto"})
_VALID_UNITS_DISPLAY: Final[tuple[str, ...]] = ("auto", "s", "ms", "us", "ns")
_VALID_SORTS: Final[frozenset[str]] = frozenset(
    {"time", "calls", "time-per-call", "name"}
)
_VALID_SORTS_DISPLAY: Final[tuple[str, ...]] = (
    "time",
    "calls",
    "time-per-call",
    "name",
)

# Flag-to-field mappings for inter-scope option reparsing.
_BOOL_TRUE_FLAGS: Final[dict[str, str]] = {
    "--memory": "memory",
    "--compact": "compact",
    "--summary": "summary",
    "--quiet": "quiet",
    "-q": "quiet",
    "--no-subprocess": "no_subprocess",
    "--no-multiprocessing": "no_multiprocessing",
}
_BOOL_FALSE_FLAGS: Final[dict[str, str]] = {
    "--no-memory": "memory",
    "--full": "compact",
}
_ARG_FLAGS: Final[dict[str, str]] = {
    "--top": "top",
    "-n": "top",
    "--output": "output",
    "-o": "output",
    "--filter": "filter_pattern",
    "-f": "filter_pattern",
    "--unit": "unit",
    "--exclude": "exclude_pattern",
    "-e": "exclude_pattern",
    "--sort": "sort",
}


# --- Helpers ---


def _error(msg: str) -> None:
    """Print error to stderr and exit with code 1."""
    typer.echo(f"Error: {msg}", err=True)
    raise typer.Exit(code=1)


# --- Options ---


@dataclass
class _DisplayOptions:
    """CLI options controlling display, output, and profiling behavior."""

    top: int | None = None
    output: Path | None = None
    memory: bool = False
    compact: bool = True
    summary: bool = False
    quiet: bool = False
    filter_pattern: str | None = None
    unit: str = "auto"
    exclude_pattern: str | None = None
    sort: str = "time"
    no_subprocess: bool = False
    no_multiprocessing: bool = False

    def validate(self) -> None:
        """Validate options, raising ``typer.Exit`` on error."""
        if self.top is not None and self.top < 1:
            _error("--top must be at least 1.")
        if self.unit not in _VALID_UNITS:
            _error(f"--unit must be one of: {', '.join(_VALID_UNITS_DISPLAY)}.")
        if self.sort not in _VALID_SORTS:
            _error(f"--sort must be one of: {', '.join(_VALID_SORTS_DISPLAY)}.")


# --- Argument parsing ---


def _split_scopes_and_options(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Separate additional scope tokens from option tokens.

    Tokens that start with ``-`` (and their values) are options;
    everything else is an additional scope.
    """
    scopes: list[str] = []
    options: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            options.append(tok)
            if tok in _ARG_FLAGS and i + 1 < len(tokens):
                options.append(tokens[i + 1])
                i += 2
            else:
                i += 1
        else:
            scopes.append(tok)
            i += 1
    return scopes, options


def _reparse_options(tokens: list[str], defaults: _DisplayOptions) -> _DisplayOptions:
    """Re-parse lazyline options found between scope and ``--``.

    Overrides fields in *defaults* with values from *tokens*.
    Raises ``typer.Exit`` on invalid values or unrecognized tokens.
    """
    vals = {f.name: getattr(defaults, f.name) for f in fields(defaults)}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _BOOL_TRUE_FLAGS:
            vals[_BOOL_TRUE_FLAGS[tok]] = True
            i += 1
        elif tok in _BOOL_FALSE_FLAGS:
            vals[_BOOL_FALSE_FLAGS[tok]] = False
            i += 1
        elif tok in _ARG_FLAGS:
            if i + 1 >= len(tokens):
                _error(f"'{tok}' requires a value.")
            key, raw = _ARG_FLAGS[tok], tokens[i + 1]
            if key == "top":
                try:
                    vals[key] = int(raw)
                except ValueError:
                    _error(
                        f"Invalid value for '--top': '{raw}' is not a valid integer."
                    )
            elif key == "output":
                vals[key] = Path(raw)
            else:
                vals[key] = raw
            i += 2
        else:
            _error(f"Unrecognized option '{tok}' between SCOPE and --.")
    return _DisplayOptions(**vals)


def _parse_run_args(
    raw_args: list[str], first_scope: str, defaults: _DisplayOptions
) -> tuple[list[str], list[str], _DisplayOptions]:
    """Parse raw typer context args into (scopes, command, options)."""
    args = list(raw_args)

    if "--" not in args:
        typer.echo(
            "Error: Missing '--' separator between SCOPE and COMMAND.\n"
            "Usage: lazyline run [OPTIONS] SCOPE [SCOPE...] -- COMMAND [ARGS...]\n"
            f'Example: lazyline run {first_scope} -- python -c "pass"',
            err=True,
        )
        raise typer.Exit(code=1)

    sep_idx = args.index("--")
    extra_scopes, option_tokens = _split_scopes_and_options(args[:sep_idx])
    command = args[sep_idx + 1 :]
    scopes = [first_scope, *extra_scopes]

    if not command:
        scope_label = ", ".join(scopes)
        typer.echo(
            f"Error: No command provided after scope '{scope_label}'.\n"
            f'Example: lazyline run {scopes[0]} -- python -c "pass"',
            err=True,
        )
        raise typer.Exit(code=1)

    return scopes, command, _reparse_options(option_tokens, defaults)


# --- Commands ---


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[
        bool, typer.Option("--version", "-V", help="Show version and exit.")
    ] = False,
) -> None:
    """Lazyline — zero-config line-level profiler for Python packages."""
    if version:
        typer.echo(f"lazyline {__version__}")
        raise typer.Exit()


@app.command(
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
    no_args_is_help=True,
)
def run(
    ctx: typer.Context,
    scope: Annotated[
        str,
        typer.Argument(
            help="Package path, module name, directory, or .py file to profile"
        ),
    ],
    top: Annotated[
        int | None,
        typer.Option("-n", "--top", help="Show only the top N slowest functions"),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Export results to JSON file")
    ] = None,
    memory: Annotated[
        bool,
        typer.Option("--memory/--no-memory", help="Enable tracemalloc memory tracking"),
    ] = False,
    compact: Annotated[
        bool,
        typer.Option(
            "--compact/--full",
            help="Collapse un-hit source lines (default) or show all lines",
        ),
    ] = True,
    summary: Annotated[
        bool,
        typer.Option(
            "--summary", help="Print only the summary table, no per-line detail"
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet", "-q", help="Suppress discovery and registration messages"
        ),
    ] = False,
    filter_pattern: Annotated[
        str | None,
        typer.Option(
            "-f",
            "--filter",
            help="Only show functions matching fnmatch pattern(s) (comma-separated)",
        ),
    ] = None,
    unit: Annotated[
        str,
        typer.Option("--unit", help="Time unit: auto, s, ms, us, or ns"),
    ] = "auto",
    exclude_pattern: Annotated[
        str | None,
        typer.Option(
            "-e",
            "--exclude",
            help="Exclude functions matching fnmatch pattern(s) (comma-separated)",
        ),
    ] = None,
    sort: Annotated[
        str,
        typer.Option(
            "--sort", help="Sort by: time (default), calls, time-per-call, name"
        ),
    ] = "time",
    no_subprocess: Annotated[
        bool,
        typer.Option("--no-subprocess", help="Disable subprocess profiling injection"),
    ] = False,
    no_multiprocessing: Annotated[
        bool,
        typer.Option(
            "--no-multiprocessing", help="Disable multiprocessing worker profiling"
        ),
    ] = False,
) -> None:
    """Profile a command, instrumenting all functions in the given scope(s).

    Usage: lazyline run [OPTIONS] SCOPE [SCOPE...] -- COMMAND [ARGS]

    Examples
    --------
      lazyline run json -- python -c "import json; json.dumps(1)"
      lazyline run --top 5 --memory my_package -- pytest tests/
      lazyline run --output results.json my_package -- pytest -q
      lazyline run file1.py file2.py my_package -- python script.py
    """
    scopes, command, opts = _parse_run_args(
        ctx.args,
        scope,
        _DisplayOptions(
            top=top,
            output=output,
            memory=memory,
            compact=compact,
            summary=summary,
            quiet=quiet,
            filter_pattern=filter_pattern,
            unit=unit,
            exclude_pattern=exclude_pattern,
            sort=sort,
            no_subprocess=no_subprocess,
            no_multiprocessing=no_multiprocessing,
        ),
    )
    opts.validate()

    results, exit_code, n_registered, wall_time = _profile(scopes, command, opts)

    report_stream = (
        sys.stderr
        if opts.output is not None and str(opts.output) == _STDOUT_PATH
        else None
    )
    scope_label = ", ".join(scopes)
    print_summary(
        results,
        top=opts.top,
        compact=opts.compact,
        summary=opts.summary,
        filter_pattern=opts.filter_pattern,
        exclude_pattern=opts.exclude_pattern,
        unit=opts.unit,
        sort=opts.sort,
        scope=scope_label,
        n_registered=n_registered,
        wall_time=wall_time,
        stream=report_stream,
    )

    if opts.output is not None:
        _export_results(
            opts, results, command, scope_label, exit_code, n_registered, wall_time
        )

    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command(no_args_is_help=True)
def show(
    path: Annotated[Path, typer.Argument(help="Path to a JSON results file")],
    top: Annotated[
        int | None,
        typer.Option("-n", "--top", help="Show only the top N slowest functions"),
    ] = None,
    compact: Annotated[
        bool,
        typer.Option(
            "--compact/--full",
            help="Collapse un-hit source lines (default) or show all lines",
        ),
    ] = True,
    summary: Annotated[
        bool,
        typer.Option(
            "--summary", help="Print only the summary table, no per-line detail"
        ),
    ] = False,
    filter_pattern: Annotated[
        str | None,
        typer.Option(
            "-f",
            "--filter",
            help="Only show functions matching fnmatch pattern(s) (comma-separated)",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet", "-q", help="Suppress discovery and registration messages"
        ),
    ] = False,
    unit: Annotated[
        str,
        typer.Option("--unit", help="Time unit: auto, s, ms, us, or ns"),
    ] = "auto",
    exclude_pattern: Annotated[
        str | None,
        typer.Option(
            "-e",
            "--exclude",
            help="Exclude functions matching fnmatch pattern(s) (comma-separated)",
        ),
    ] = None,
    sort: Annotated[
        str,
        typer.Option(
            "--sort", help="Sort by: time (default), calls, time-per-call, name"
        ),
    ] = "time",
) -> None:
    """Display profiling results from a saved JSON file.

    Examples
    --------
      lazyline show results.json
      lazyline show results.json --top 5 --filter "*transform*"
      lazyline show results.json --summary --unit ms
      lazyline show results.json --full
    """
    opts = _DisplayOptions(
        top=top,
        compact=compact,
        summary=summary,
        quiet=quiet,
        filter_pattern=filter_pattern,
        unit=unit,
        exclude_pattern=exclude_pattern,
        sort=sort,
    )
    opts.validate()

    if not path.exists():
        _error(f"File '{path}' not found.")
    if path.is_dir():
        _error(f"'{path}' is a directory, not a file.")

    try:
        run_data = from_json(path)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        _error(f"Invalid JSON file: {exc}")

    if not opts.quiet:
        _warn_high_hit_functions(run_data.functions)
    print_summary(
        run_data.functions,
        top=opts.top,
        compact=opts.compact,
        summary=opts.summary,
        filter_pattern=opts.filter_pattern,
        exclude_pattern=opts.exclude_pattern,
        unit=opts.unit,
        sort=opts.sort,
        scope=run_data.metadata.scope,
        n_registered=run_data.metadata.n_registered,
        wall_time=run_data.metadata.wall_time,
    )


# --- Profiling pipeline ---


def _ensure_cwd_on_path() -> None:
    """Ensure the current working directory is on ``sys.path``.

    ``python -m`` adds ``""`` (CWD) to ``sys.path[0]``, but console
    scripts do not.  Without this, local packages that are not
    pip-installed cannot be discovered via ``importlib.import_module``.
    """
    cwd = str(Path.cwd())
    if "" not in sys.path and cwd not in sys.path:
        sys.path.insert(0, "")


def _discover_all(scopes: list[str]) -> list[ModuleType]:
    """Discover modules from one or more scopes, deduplicating by name."""
    _ensure_cwd_on_path()
    seen: set[str] = set()
    modules = []
    for scope in scopes:
        for mod in discover_modules(scope):
            if mod.__name__ not in seen:
                seen.add(mod.__name__)
                modules.append(mod)
    return modules


def _profile(
    scopes: list[str], command: list[str], opts: _DisplayOptions
) -> tuple[list[FunctionProfile], int, int, float]:
    """Run the profiling pipeline: discover, register, execute, collect."""
    modules = _discover_all(scopes)
    if not modules:
        _error(f"No modules found in scope '{', '.join(scopes)}'.")

    if not opts.quiet:
        label = ", ".join(scopes)
        typer.echo(f"Discovered {len(modules)} module(s) in scope '{label}'.", err=True)

    profiler = create_profiler()
    scope_files = build_scope_paths(modules)
    n_registered = register_modules(profiler, modules)
    n_registered += register_module_level_code(profiler, modules, scopes)
    if not opts.quiet:
        typer.echo(f"Registered {n_registered} function(s) for profiling.", err=True)

    mem_before = None
    if opts.memory:
        if not opts.quiet:
            typer.echo("Memory tracking enabled (tracemalloc).", err=True)
        mem_before = start_tracking()

    module_names = [m.__name__ for m in modules]
    if not opts.quiet:
        typer.echo("", err=True)

    sub_ctx = (
        contextlib.nullcontext(SimpleNamespace(stats=None))
        if opts.no_subprocess
        else subprocess_hooks(scopes)
    )
    mp_ctx = (
        contextlib.nullcontext(SimpleNamespace(stats=None))
        if opts.no_multiprocessing
        else profiling_hooks(module_names, parent_profiler=profiler)
    )

    wall_start = time.monotonic()
    with sub_ctx as sub_holder, mp_ctx as worker_holder:
        exit_code = execute_command(profiler, command)
    wall_time = time.monotonic() - wall_start

    mem_stats = stop_tracking(mem_before)
    stats = merge_stats(profiler.get_stats(), worker_holder.stats)
    if sub_holder.stats:
        stats = merge_stats(stats, sub_holder.stats)
    results = collect_results(stats, memory_stats=mem_stats, scope_paths=scope_files)
    enrich_results(results)
    if not opts.quiet:
        _warn_high_hit_functions(results)
        if not results:
            _print_no_data_hint(exit_code, n_registered)
    return results, exit_code, n_registered, wall_time


def _export_results(
    opts: _DisplayOptions,
    results: list[FunctionProfile],
    command: list[str],
    scope: str,
    exit_code: int,
    n_registered: int,
    wall_time: float,
) -> None:
    """Export profiling results to JSON."""
    if not results and not opts.quiet:
        typer.echo(
            "Warning: no profiling data collected; exported empty results.",
            err=True,
        )
    run_data = ProfileRun(
        version=1,
        lazyline_version=__version__,
        metadata=RunMetadata(
            command=command,
            scope=scope,
            timestamp=datetime.now(timezone.utc).isoformat(),
            memory_tracking=opts.memory,
            python_version=platform.python_version(),
            exit_code=exit_code,
            n_registered=n_registered,
            wall_time=wall_time,
        ),
        functions=results,
    )
    output = opts.output
    if output is None:
        return
    if str(output) != _STDOUT_PATH and output.is_dir():
        _error(f"--output '{output}' is a directory.")
    to_json(run_data, output)
    if not opts.quiet and str(output) != _STDOUT_PATH:
        typer.echo(f"Results exported to '{output}'.", err=True)
        if exit_code:
            typer.echo(
                "Warning: command exited with non-zero status; data may be incomplete.",
                err=True,
            )


# --- Warnings ---


def _warn_high_hit_functions(results: list[FunctionProfile]) -> None:
    """Warn about functions with very high line hit counts."""
    for fp in results:
        total_hits = sum(lp.hits for lp in fp.lines)
        if total_hits >= _HIGH_HIT_THRESHOLD:
            name = f"{fp.module}.{fp.name}"
            typer.echo(
                f"Note: '{name}' had {total_hits:,} line hits — "
                f"reported times may be inflated by tracing overhead.",
                err=True,
            )


def _print_no_data_hint(exit_code: int, n_registered: int) -> None:
    """Print a contextual hint when no profiling data was collected."""
    if exit_code != 0:
        typer.echo(
            "Hint: The command exited with an error — profiling data may not "
            "have been collected.",
            err=True,
        )
    elif n_registered > 0:
        typer.echo(
            f"Hint: {n_registered} function(s) were registered but none were"
            " called. C extension functions cannot be profiled — verify that"
            " the command exercises Python code in the profiled scope.",
            err=True,
        )


if __name__ == "__main__":
    app()
