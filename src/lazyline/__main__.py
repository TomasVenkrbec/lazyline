"""Lazyline CLI — zero-config line-level profiler."""

from __future__ import annotations

import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

if TYPE_CHECKING:
    import types

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
        str, typer.Argument(help="Package path, module name, or directory to profile")
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
        typer.Option("--quiet", "-q", help="Suppress informational messages"),
    ] = False,
    filter_pattern: Annotated[
        str | None,
        typer.Option(
            "-f", "--filter", help="Only show functions matching this fnmatch pattern"
        ),
    ] = None,
    unit: Annotated[
        str,
        typer.Option("--unit", help="Time unit: auto, s, ms, us, or ns"),
    ] = "auto",
) -> None:
    """Profile a command, instrumenting all functions in the given scope(s).

    Multiple scopes require the -- separator before the command.

    Usage: lazyline run [OPTIONS] SCOPE [SCOPE...] [--] COMMAND [ARGS]

    Examples
    --------
      lazyline run json -- python -c "import json; json.dumps(1)"
      lazyline run --top 5 --memory my_package -- pytest tests/
      lazyline run --output results.json my_package -- pytest -q
      lazyline run file1.py file2.py my_package -- python script.py
    """
    raw_args = list(ctx.args)
    scopes = [scope]

    # When args contain --, tokens before it are additional scopes or options.
    if "--" in raw_args:
        sep_idx = raw_args.index("--")
        pre, post = raw_args[:sep_idx], raw_args[sep_idx + 1 :]
        extra_scopes, options = _split_scopes_and_options(pre)
        scopes.extend(extra_scopes)
        top, output, memory, compact, summary, quiet, filter_pattern, unit = (
            _reparse_options(
                options,
                top,
                output,
                memory,
                compact,
                summary,
                quiet,
                filter_pattern,
                unit,
            )
        )
        command = post
    else:
        command = raw_args
        _check_misplaced_flags(command)

    scope_label = ", ".join(scopes)

    if not command:
        typer.echo(
            f"Error: No command provided after scope '{scope_label}'.\n"
            f'Example: lazyline run {scopes[0]} -- python -c "pass"',
            err=True,
        )
        raise typer.Exit(code=1)

    if top is not None and top < 1:
        typer.echo("Error: --top must be at least 1.", err=True)
        raise typer.Exit(code=1)
    if unit not in _VALID_UNITS:
        typer.echo(
            f"Error: --unit must be one of: {', '.join(sorted(_VALID_UNITS))}.",
            err=True,
        )
        raise typer.Exit(code=1)

    results, exit_code, n_registered, wall_time = _profile(
        scopes, command, memory, quiet
    )
    # When writing JSON to stdout, redirect the human report to stderr
    # so stdout contains clean JSON for piping.
    report_stream = (
        sys.stderr if output is not None and str(output) == _STDOUT_PATH else None
    )
    print_summary(
        results,
        top=top,
        compact=compact,
        summary=summary,
        filter_pattern=filter_pattern,
        unit=unit,
        scope=scope_label,
        n_registered=n_registered,
        wall_time=wall_time,
        stream=report_stream,
    )

    if output is not None:
        if str(output) != _STDOUT_PATH and output.is_dir():
            typer.echo(f"Error: --output '{output}' is a directory.", err=True)
            raise typer.Exit(code=1)
        _export_results(
            output,
            results,
            command,
            scope_label,
            memory,
            exit_code,
            quiet,
            n_registered,
            wall_time,
        )

    if exit_code:
        raise typer.Exit(code=exit_code)


def _discover_all(scopes: list[str]) -> list[types.ModuleType]:
    """Discover modules from one or more scopes, deduplicating by name."""
    seen: set[str] = set()
    modules = []
    for scope in scopes:
        for mod in discover_modules(scope):
            if mod.__name__ not in seen:
                seen.add(mod.__name__)
                modules.append(mod)
    return modules


def _profile(
    scopes: list[str],
    command: list[str],
    memory: bool,
    quiet: bool,
) -> tuple[list[FunctionProfile], int, int, float]:
    """Run the profiling pipeline: discover, register, execute, collect."""
    modules = _discover_all(scopes)
    if not modules:
        label = ", ".join(scopes)
        typer.echo(f"Error: No modules found in scope '{label}'.", err=True)
        raise typer.Exit(code=1)

    if not quiet:
        label = ", ".join(scopes)
        typer.echo(f"Discovered {len(modules)} module(s) in scope '{label}'.", err=True)

    profiler = create_profiler()
    scope_files = build_scope_paths(modules)
    n_registered = register_modules(profiler, modules)
    n_registered += register_module_level_code(profiler, modules, scopes)
    if not quiet:
        typer.echo(f"Registered {n_registered} function(s) for profiling.", err=True)

    mem_before = None
    if memory:
        if not quiet:
            typer.echo("Memory tracking enabled (tracemalloc).", err=True)
        mem_before = start_tracking()

    module_names = [m.__name__ for m in modules]
    if not quiet:
        typer.echo("", err=True)
    wall_start = time.monotonic()
    with (
        subprocess_hooks(scopes) as sub_holder,
        profiling_hooks(module_names) as worker_holder,
    ):
        exit_code = execute_command(profiler, command)
    wall_time = time.monotonic() - wall_start

    mem_stats = stop_tracking(mem_before)
    stats = merge_stats(profiler.get_stats(), worker_holder.stats)
    if sub_holder.stats:
        stats = merge_stats(stats, sub_holder.stats)
    results = collect_results(stats, memory_stats=mem_stats, scope_paths=scope_files)
    enrich_results(results)
    if not quiet:
        _warn_high_hit_functions(results)
        if not results:
            _print_no_data_hint(exit_code, n_registered)
    return results, exit_code, n_registered, wall_time


def _export_results(
    output: Path,
    results: list[FunctionProfile],
    command: list[str],
    scope: str,
    memory: bool,
    exit_code: int,
    quiet: bool,
    n_registered: int = 0,
    wall_time: float | None = None,
) -> None:
    """Export profiling results to JSON."""
    if not results and not quiet:
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
            memory_tracking=memory,
            python_version=platform.python_version(),
            exit_code=exit_code,
            n_registered=n_registered,
            wall_time=wall_time,
        ),
        functions=results,
    )
    to_json(run_data, output)
    if not quiet and str(output) != _STDOUT_PATH:
        typer.echo(f"Results exported to '{output}'.", err=True)
        if exit_code:
            typer.echo(
                "Warning: command exited with non-zero status; data may be incomplete.",
                err=True,
            )


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
            "-f", "--filter", help="Only show functions matching this fnmatch pattern"
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress informational messages"),
    ] = False,
    unit: Annotated[
        str,
        typer.Option("--unit", help="Time unit: auto, s, ms, us, or ns"),
    ] = "auto",
) -> None:
    """Display profiling results from a saved JSON file.

    Examples
    --------
      lazyline show results.json
      lazyline show results.json --top 5 --filter "*transform*"
      lazyline show results.json --summary --unit ms
      lazyline show results.json --full
    """
    if top is not None and top < 1:
        typer.echo("Error: --top must be at least 1.", err=True)
        raise typer.Exit(code=1)
    if unit not in _VALID_UNITS:
        typer.echo(
            f"Error: --unit must be one of: {', '.join(sorted(_VALID_UNITS))}.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not path.exists():
        typer.echo(f"Error: File '{path}' not found.", err=True)
        raise typer.Exit(code=1)
    if path.is_dir():
        typer.echo(f"Error: '{path}' is a directory, not a file.", err=True)
        raise typer.Exit(code=1)

    try:
        run_data = from_json(path)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        typer.echo(f"Error: Invalid JSON file: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if not quiet:
        _warn_high_hit_functions(run_data.functions)
    print_summary(
        run_data.functions,
        top=top,
        compact=compact,
        summary=summary,
        filter_pattern=filter_pattern,
        unit=unit,
        scope=run_data.metadata.scope,
        n_registered=run_data.metadata.n_registered,
        wall_time=run_data.metadata.wall_time,
    )


_HIGH_HIT_THRESHOLD: Final[int] = 1_000_000


def _warn_high_hit_functions(results: list[FunctionProfile]) -> None:
    """Warn about functions with very high line hit counts.

    Deterministic tracing adds a per-line callback whose cost is
    baked into reported times. For functions with >1M total line
    hits, this overhead can dominate, making reported times
    unreliable for absolute measurements.
    """
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
            f"Hint: {n_registered} function(s) were registered but none were called. "
            "C extension functions cannot be profiled — verify that the command "
            "exercises Python code in the profiled scope.",
            err=True,
        )


_VALID_UNITS: Final[frozenset[str]] = frozenset({"s", "ms", "us", "ns", "auto"})
_STDOUT_PATH: Final[str] = "-"

_BOOL_TRUE_FLAGS = {
    "--memory": "memory",
    "--compact": "compact",
    "--summary": "summary",
    "--quiet": "quiet",
    "-q": "quiet",
}
_ARG_FLAGS = {
    "--top": "top",
    "-n": "top",
    "--output": "output",
    "-o": "output",
    "--filter": "filter_pattern",
    "-f": "filter_pattern",
    "--unit": "unit",
}
_BOOL_FALSE_FLAGS = {
    "--no-memory": "memory",
    "--full": "compact",
}
_KNOWN_FLAGS = (
    frozenset(_BOOL_TRUE_FLAGS) | frozenset(_ARG_FLAGS) | frozenset(_BOOL_FALSE_FLAGS)
)


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
            # Flags that take a value consume the next token too.
            if tok in _ARG_FLAGS and i + 1 < len(tokens):
                options.append(tokens[i + 1])
                i += 2
            else:
                i += 1
        else:
            scopes.append(tok)
            i += 1
    return scopes, options


def _reparse_options(
    tokens: list[str],
    top,
    output,
    memory,
    compact,
    summary,
    quiet,
    filter_pattern,
    unit,
):
    """Re-parse lazyline options from tokens found between scope and ``--``.

    Raises ``typer.Exit`` on invalid values or unrecognized tokens.
    """
    vals = {
        "top": top,
        "output": output,
        "memory": memory,
        "compact": compact,
        "summary": summary,
        "quiet": quiet,
        "filter_pattern": filter_pattern,
        "unit": unit,
    }
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
            key = _ARG_FLAGS[tok]
            if i + 1 >= len(tokens):
                typer.echo(f"Error: '{tok}' requires a value.", err=True)
                raise typer.Exit(code=1)
            raw = tokens[i + 1]
            if key == "top":
                try:
                    vals[key] = int(raw)
                except ValueError as exc:
                    typer.echo(
                        f"Error: Invalid value for '--top': '{raw}' "
                        "is not a valid integer.",
                        err=True,
                    )
                    raise typer.Exit(code=1) from exc
            elif key == "output":
                vals[key] = Path(raw)
            else:
                vals[key] = raw
            i += 2
        else:
            typer.echo(
                f"Error: Unrecognized option '{tok}' between SCOPE and --.",
                err=True,
            )
            raise typer.Exit(code=1)
    return (
        vals["top"],
        vals["output"],
        vals["memory"],
        vals["compact"],
        vals["summary"],
        vals["quiet"],
        vals["filter_pattern"],
        vals["unit"],
    )


def _check_misplaced_flags(command: list[str]) -> None:
    """Warn when the first command token looks like a misplaced lazyline flag."""
    if command and command[0] in _KNOWN_FLAGS:
        typer.echo(
            f"Error: '{command[0]}' looks like a lazyline option, not a command.\n"
            f"Options must appear before SCOPE, or use -- to separate:\n"
            f"  lazyline run [OPTIONS] SCOPE -- COMMAND",
            err=True,
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
