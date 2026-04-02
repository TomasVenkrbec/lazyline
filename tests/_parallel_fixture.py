"""Fixture module for parallel profiling tests."""

import concurrent.futures
import multiprocessing


def slow_computation(x):
    """A regular function called from within a parallel worker."""
    total = 0
    for i in range(100):
        total += x * i
    return total


def run_with_process_pool(items):
    """Run slow_computation via ProcessPoolExecutor."""
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
        return list(pool.map(slow_computation, items))


def run_with_mp_pool(items):
    """Run slow_computation via multiprocessing.Pool."""
    with multiprocessing.Pool(2) as pool:
        return pool.map(slow_computation, items)


def run_with_mp_pool_maxtasks(items):
    """Run slow_computation via Pool with maxtasksperchild=5."""
    with multiprocessing.Pool(2, maxtasksperchild=5) as pool:
        return pool.map(slow_computation, items)
