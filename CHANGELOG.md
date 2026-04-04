# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-04-04

### Added

- Callable instance unwrapping: functions hidden inside callable wrapper
  instances (e.g., a decorator that replaces a function with a callable
  object storing the original as an attribute) are now discovered and
  profiled automatically.
- `--exclude` / `-e` flag to exclude functions matching fnmatch pattern(s).
- `--sort` flag to sort results by `time`, `calls`, `time-per-call`, or `name`.
- `--no-subprocess` flag to disable subprocess profiling injection.
- `--no-multiprocessing` flag to disable multiprocessing worker profiling.
- `--filter` and `--exclude` now auto-wrap bare patterns without wildcards
  (e.g., `--filter dumps` matches like `--filter "*dumps*"`).

### Changed

- **Breaking:** The `--` separator between SCOPE and COMMAND is now
  mandatory. Previously, single-scope invocations could omit it.

### Fixed

- Auto time unit selection now uses the maximum `total_time` instead of the
  median, preventing unreadable values when a single slow function dominates
  an otherwise fast profile.
- Multiprocessing workers now inherit the parent process's profiler instead
  of creating a fresh instance, fixing missing results when `line_profiler`'s
  bytecode hash mappings did not survive `fork()`.
- `--unit` validation error now lists units in magnitude order
  (`auto, s, ms, us, ns`) instead of alphabetical.
- Discovery-level error messages no longer duplicate the CLI-layer
  "No modules found" error.

## [0.1.0] - 2026-04-03

Initial public release. Zero-config line-level profiler for Python packages,
built on top of [line_profiler](https://github.com/pyutils/line_profiler).

### Added

- `lazyline run` — profile all functions in given scope(s) while running a command
- `lazyline show` — display results from a saved JSON file
- Module discovery: dotted paths, directories, single `.py` files, namespace packages
- Automatic `lru_cache` / C-extension wrapper unwrapping
- Multiprocessing support (`ProcessPoolExecutor`, `multiprocessing.Pool`)
- Subprocess profiling via `sitecustomize.py` injection
- Optional memory tracking via `--memory` (`tracemalloc`)
- JSON export with `--output`
- Syntax highlighting via optional `[color]` extra (`Pygments`)
