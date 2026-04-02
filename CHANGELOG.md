# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
