"""Tests for Python edge cases: registration, profiling, result collection."""

import asyncio
import warnings
from pathlib import Path

from lazyline.profiling import (
    build_scope_paths,
    collect_results,
    create_profiler,
    enrich_results,
    register_modules,
)

# Import the fixture module.
from tests import _spicy_constructs as spicy


def _register_and_profile(func, *call_args, **call_kwargs):
    """Register one function, call it under profiling, return the profiler."""
    profiler = create_profiler()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
        warnings.filterwarnings("ignore", message=r".*Could not extract.*")
        profiler.add_function(func)
    profiler.enable_by_count()
    try:
        func(*call_args, **call_kwargs)
    finally:
        profiler.disable_by_count()
    return profiler


def _register_module_and_profile(mod, exercise_func, *args, **kwargs):
    """Register a whole module via register_modules, exercise it, return profiler."""
    profiler = create_profiler()
    register_modules(profiler, [mod])
    profiler.enable_by_count()
    try:
        exercise_func(*args, **kwargs)
    finally:
        profiler.disable_by_count()
    return profiler


def _names(results):
    """Return the set of function names from profiling results."""
    return {r.name for r in results}


# ---------------------------------------------------------------------------
# Registration: module-level add_module doesn't crash
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_add_module_does_not_crash(self):
        profiler = create_profiler()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            n = profiler.add_module(spicy, scoping_policy="children")
        assert n > 0

    def test_registered_count_is_reasonable(self):
        profiler = create_profiler()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            n = profiler.add_module(spicy, scoping_policy="children")
        # We have ~25 top-level functions + many class methods. Should be > 30.
        assert n >= 30

    def test_lambdas_are_registered(self):
        profiler = create_profiler()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            profiler.add_module(spicy, scoping_policy="children")
        lambda_funcs = [
            f for f in profiler.functions if getattr(f, "__name__", "") == "<lambda>"
        ]
        assert len(lambda_funcs) >= 2

    def test_property_getter_and_setter_both_registered(self):
        profiler = create_profiler()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            profiler.add_module(spicy, scoping_policy="children")
        value_funcs = [
            f
            for f in profiler.functions
            if getattr(f, "__qualname__", "") == "WithDescriptors.value"
        ]
        # Getter and setter are separate function objects.
        assert len(value_funcs) == 2

    def test_lru_cache_unwrapped_by_register_modules(self):
        """register_modules should unwrap module-level C-extension wrappers."""
        profiler = create_profiler()
        register_modules(profiler, [spicy])
        codes = {
            getattr(f, "__code__", None) for f in profiler.functions if f is not None
        }
        # The original cached_fib function's code should be registered.
        assert spicy.cached_fib.__wrapped__.__code__ in codes

    def test_lru_cache_on_class_method_unwrapped(self):
        """register_modules should unwrap lru_cache buried inside a class."""
        profiler = create_profiler()
        register_modules(profiler, [spicy])
        codes = {
            getattr(f, "__code__", None) for f in profiler.functions if f is not None
        }
        assert spicy.WithDescriptors.cached_static.__wrapped__.__code__ in codes


# ---------------------------------------------------------------------------
# Out-of-scope leak: stdlib functions registered via __module__ matching
# ---------------------------------------------------------------------------
class TestOutOfScopeFiltering:
    """Functions from stdlib that leak in via singledispatch/contextmanager."""

    def _get_registered_filenames(self):
        profiler = create_profiler()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            profiler.add_module(spicy, scoping_policy="children")
        filenames = set()
        for func in profiler.functions:
            code = getattr(func, "__code__", None)
            if code:
                filenames.add(code.co_filename)
        return filenames

    def test_stdlib_files_leak_into_registration(self):
        """Confirm the leak exists at the registration level."""
        filenames = self._get_registered_filenames()
        basenames = {Path(f).name for f in filenames}
        # These are registered because their __module__ matches our module.
        assert "functools.py" in basenames or "contextlib.py" in basenames

    def test_collect_results_filters_out_of_scope(self):
        """Results should only contain functions from in-scope files."""
        profiler = _register_module_and_profile(spicy, spicy.run_all_exercises)
        scope_paths = build_scope_paths([spicy])
        results = collect_results(profiler, scope_paths=scope_paths)
        for fp in results:
            assert "_spicy_constructs" in fp.filename, (
                f"Out-of-scope function in results: {fp.name} from {fp.filename}"
            )

    def test_unfiltered_results_include_stdlib_leaks(self):
        """Without scope filtering, stdlib wrapper functions appear in results."""
        profiler = _register_module_and_profile(spicy, spicy.run_all_exercises)
        results = collect_results(profiler)
        filenames = {Path(fp.filename).name for fp in results}
        # Stdlib wrappers leak in via __module__ matching.
        assert "functools.py" in filenames or "contextlib.py" in filenames


# ---------------------------------------------------------------------------
# Closures / nested functions
# ---------------------------------------------------------------------------
class TestClosures:
    def test_outer_function_profiled(self):
        profiler = _register_and_profile(spicy.outer_with_closure)
        results = collect_results(profiler)
        assert "outer_with_closure" in _names(results)

    def test_inner_closure_not_in_module_registration(self):
        """Inner functions are not module-level — add_module won't find them."""
        profiler = create_profiler()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*__wrapped__.*")
            profiler.add_module(spicy, scoping_policy="children")
        qualnames = [getattr(f, "__qualname__", "") for f in profiler.functions]
        assert "outer_with_closure.<locals>.inner" not in qualnames


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------
class TestGenerators:
    def test_generator_function_profiled(self):
        profiler = _register_and_profile(spicy.consume_generator)
        results = collect_results(profiler)
        assert "consume_generator" in _names(results)

    def test_generator_yields_produce_timing(self):
        profiler = create_profiler()
        profiler.add_function(spicy.fibonacci_generator)
        profiler.enable_by_count()
        try:
            list(spicy.fibonacci_generator(50))
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        gen = next((r for r in results if r.name == "fibonacci_generator"), None)
        assert gen is not None
        assert gen.total_time >= 0
        # Generator is "called" once but yields multiple times.
        assert gen.call_count >= 1


# ---------------------------------------------------------------------------
# Async functions
# ---------------------------------------------------------------------------
class TestAsync:
    def test_async_function_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.async_work)
        profiler.enable_by_count()
        try:
            asyncio.run(spicy.async_work(20))
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        assert "async_work" in _names(results)

    def test_async_generator_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.async_generator)
        profiler.enable_by_count()
        try:

            async def _consume():
                return [x async for x in spicy.async_generator(5)]

            asyncio.run(_consume())
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        assert "async_generator" in _names(results)


# ---------------------------------------------------------------------------
# Lambdas
# ---------------------------------------------------------------------------
class TestLambdas:
    def test_lambda_profiled(self):
        profiler = _register_and_profile(spicy.square_lambda, 7)
        results = collect_results(profiler)
        assert "<lambda>" in _names(results)

    def test_multiple_lambdas_distinguished_by_line(self):
        profiler = create_profiler()
        profiler.add_function(spicy.square_lambda)
        profiler.add_function(spicy.triple_lambda)
        profiler.enable_by_count()
        try:
            spicy.square_lambda(3)
            spicy.triple_lambda(3)
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        lambda_results = [r for r in results if r.name == "<lambda>"]
        assert len(lambda_results) == 2
        # They should have different start lines.
        lines = {r.start_line for r in lambda_results}
        assert len(lines) == 2

    def test_lambda_enrichment_single_line(self):
        """Lambda source should be a single line."""
        profiler = _register_and_profile(spicy.square_lambda, 7)
        results = collect_results(profiler)
        enrich_results(results)
        lam = next(r for r in results if r.name == "<lambda>")
        assert len(lam.lines) >= 1
        assert any("lambda" in lp.source for lp in lam.lines)


# ---------------------------------------------------------------------------
# Descriptors: @staticmethod, @classmethod, @property
#
# Python 3.12+ qualnames: line_profiler reports "Class.method" not "method".
# ---------------------------------------------------------------------------
class TestDescriptors:
    def test_staticmethod_profiled(self):
        profiler = _register_and_profile(spicy.WithDescriptors.static_work, 50)
        results = collect_results(profiler)
        assert any("static_work" in r.name for r in results)

    def test_classmethod_profiled(self):
        profiler = _register_and_profile(spicy.WithDescriptors.from_double, 5)
        results = collect_results(profiler)
        assert any("from_double" in r.name for r in results)

    def test_property_getter_profiled(self):
        obj = spicy.WithDescriptors(10)
        fget = type(obj).__dict__["value"].fget
        profiler = _register_and_profile(fget, obj)
        results = collect_results(profiler)
        assert any("value" in r.name for r in results)

    def test_property_setter_profiled(self):
        obj = spicy.WithDescriptors(10)
        fset = type(obj).__dict__["value"].fset
        profiler = _register_and_profile(fset, obj, 99)
        results = collect_results(profiler)
        assert any("value" in r.name for r in results)


# ---------------------------------------------------------------------------
# functools wrappers: lru_cache, wraps, singledispatch
# ---------------------------------------------------------------------------
class TestFunctools:
    def test_lru_cached_function_profiled_via_unwrap(self):
        """lru_cache wraps in C — profile the __wrapped__ Python function."""
        spicy.cached_fib.cache_clear()
        profiler = _register_and_profile(spicy.cached_fib.__wrapped__, 10)
        results = collect_results(profiler)
        assert any("cached_fib" in r.name for r in results)

    def test_lru_cache_auto_unwrapped_by_register_modules(self):
        """register_modules auto-unwraps lru_cache and profiles the original."""
        spicy.cached_fib.cache_clear()
        profiler = _register_module_and_profile(spicy, spicy.exercise_functools)
        results = collect_results(profiler)
        assert any("cached_fib" in r.name for r in results)

    def test_custom_decorated_function_profiled(self):
        profiler = _register_and_profile(spicy.decorated_function, 5)
        results = collect_results(profiler)
        # The wrapper is what gets profiled.
        assert len(results) >= 1

    def test_singledispatch_implementations_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy._process_int)
        profiler.add_function(spicy._process_float)
        profiler.enable_by_count()
        try:
            spicy.process(42)
            spicy.process(3.14)
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        names = _names(results)
        assert "_process_int" in names
        assert "_process_float" in names


# ---------------------------------------------------------------------------
# ABC / abstract methods
# ---------------------------------------------------------------------------
class TestABC:
    def test_concrete_methods_profiled(self):
        profiler = create_profiler()
        c = spicy.Circle(5.0)
        profiler.add_function(spicy.Circle.area)
        profiler.add_function(spicy.Circle.perimeter)
        profiler.add_function(spicy.Shape.describe)
        profiler.enable_by_count()
        try:
            c.describe()
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        names = _names(results)
        # Python 3.12+ qualnames: "Circle.area", "Shape.describe", etc.
        assert any("area" in n for n in names)
        assert any("perimeter" in n for n in names)
        assert any("describe" in n for n in names)


# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------
class TestContextManagers:
    def test_class_context_manager_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.ManagedResource.__enter__)
        profiler.add_function(spicy.ManagedResource.__exit__)
        profiler.enable_by_count()
        try:
            with spicy.ManagedResource():
                pass
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        names = _names(results)
        assert any("__enter__" in n for n in names)
        assert any("__exit__" in n for n in names)


# ---------------------------------------------------------------------------
# NamedTuple, Enum, __slots__
# ---------------------------------------------------------------------------
class TestSpecialClasses:
    def test_namedtuple_method_profiled(self):
        profiler = _register_and_profile(
            spicy.Point.distance_to_origin, spicy.Point(3.0, 4.0)
        )
        results = collect_results(profiler)
        assert any("distance_to_origin" in r.name for r in results)

    def test_enum_method_profiled(self):
        profiler = _register_and_profile(spicy.Color.is_primary, spicy.Color.RED)
        results = collect_results(profiler)
        assert any("is_primary" in r.name for r in results)

    def test_slotted_class_methods_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.SlottedPoint.__init__)
        profiler.add_function(spicy.SlottedPoint.magnitude)
        profiler.enable_by_count()
        try:
            p = spicy.SlottedPoint(3.0, 4.0)
            p.magnitude()
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        names = _names(results)
        assert any("__init__" in n for n in names)
        assert any("magnitude" in n for n in names)


# ---------------------------------------------------------------------------
# Multiple inheritance / MRO
# ---------------------------------------------------------------------------
class TestMRO:
    def test_diamond_mro_all_methods_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.Diamond.compute)
        profiler.add_function(spicy.MixinA.compute)
        profiler.add_function(spicy.MixinB.compute)
        profiler.add_function(spicy.Base.compute)
        profiler.enable_by_count()
        try:
            spicy.Diamond().compute()
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        # All four compute() implementations should appear, disambiguated
        # by qualname (Diamond.compute, MixinA.compute, etc.).
        compute_results = [r for r in results if "compute" in r.name]
        assert len(compute_results) == 4


# ---------------------------------------------------------------------------
# Metaclass
# ---------------------------------------------------------------------------
class TestMetaclass:
    def test_metaclass_new_profiled(self):
        profiler = _register_and_profile(
            spicy.RegistryMeta.__new__, spicy.RegistryMeta, "Tmp", (), {}
        )
        results = collect_results(profiler)
        assert any("__new__" in r.name for r in results)

    def test_metaclass_instance_method_profiled(self):
        profiler = _register_and_profile(spicy.Registered.work, spicy.Registered())
        results = collect_results(profiler)
        assert any("work" in r.name for r in results)


# ---------------------------------------------------------------------------
# Descriptors (custom __get__/__set__)
# ---------------------------------------------------------------------------
class TestCustomDescriptors:
    def test_descriptor_get_and_set_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.ValidatedField.__get__)
        profiler.add_function(spicy.ValidatedField.__set__)
        profiler.enable_by_count()
        try:
            a = spicy.Account(100)
            _ = a.balance
            a.balance = 200
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        names = _names(results)
        # Python 3.12+ qualnames: "ValidatedField.__get__", etc.
        assert any("__get__" in n for n in names)
        assert any("__set__" in n for n in names)


# ---------------------------------------------------------------------------
# Recursive functions
# ---------------------------------------------------------------------------
class TestRecursion:
    def test_recursive_function_profiled(self):
        profiler = _register_and_profile(spicy.recursive_sum, 10)
        results = collect_results(profiler)
        rec = next(r for r in results if r.name == "recursive_sum")
        # Recursive: first line hit count = number of calls (11: 10..0).
        assert rec.call_count == 11

    def test_recursive_enrichment(self):
        profiler = _register_and_profile(spicy.recursive_sum, 5)
        results = collect_results(profiler)
        enrich_results(results)
        rec = next(r for r in results if r.name == "recursive_sum")
        assert any("recursive_sum" in lp.source for lp in rec.lines)


# ---------------------------------------------------------------------------
# Iterator protocol
# ---------------------------------------------------------------------------
class TestIteratorProtocol:
    def test_custom_iterator_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.CountDown.__next__)
        profiler.enable_by_count()
        try:
            list(spicy.CountDown(5))
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        assert any("__next__" in r.name for r in results)
        nxt = next(r for r in results if "__next__" in r.name)
        # __next__ called 6 times (5 yields + 1 StopIteration).
        assert nxt.call_count == 6


# ---------------------------------------------------------------------------
# __call__ (callable objects)
# ---------------------------------------------------------------------------
class TestCallable:
    def test_callable_object_profiled(self):
        m = spicy.Multiplier(3)
        profiler = _register_and_profile(spicy.Multiplier.__call__, m, 14)
        results = collect_results(profiler)
        assert any("__call__" in r.name for r in results)


# ---------------------------------------------------------------------------
# Exception-heavy control flow
# ---------------------------------------------------------------------------
class TestExceptionFlow:
    def test_exception_heavy_profiled(self):
        profiler = _register_and_profile(spicy.exception_heavy, 20)
        results = collect_results(profiler)
        exc = next(r for r in results if r.name == "exception_heavy")
        assert exc.total_time >= 0
        assert exc.call_count == 1

    def test_exception_heavy_all_branches_have_hits(self):
        profiler = _register_and_profile(spicy.exception_heavy, 20)
        results = collect_results(profiler)
        enrich_results(results)
        exc = next(r for r in results if r.name == "exception_heavy")
        hit_lines = [lp for lp in exc.lines if lp.hits > 0]
        # Multiple lines should be hit (loop, try, except, etc.).
        assert len(hit_lines) >= 4


# ---------------------------------------------------------------------------
# Many branches (partial coverage)
# ---------------------------------------------------------------------------
class TestBranches:
    def test_many_branches_partial_coverage(self):
        profiler = _register_and_profile(spicy.many_branches, 42)
        results = collect_results(profiler)
        enrich_results(results)
        mb = next(r for r in results if r.name == "many_branches")
        hit_lines = [lp for lp in mb.lines if lp.hits > 0]
        # Python evaluates elif conditions sequentially until one matches.
        # For x=42: checks x<0, x==0, x<10, x<100 (true) → return "medium".
        # So 4 condition checks + 1 return = 5 hit lines.
        assert len(hit_lines) == 5
        # But not ALL lines are hit — the else/early returns are skipped.
        all_lines = [lp for lp in mb.lines if lp.source.strip()]
        assert len(hit_lines) < len(all_lines)


# ---------------------------------------------------------------------------
# Heavily decorated stack
# ---------------------------------------------------------------------------
class TestDecoratorStack:
    def test_double_decorated_profiled(self):
        """Profiling the outermost wrapper should work."""
        profiler = _register_and_profile(spicy.double_decorated, 3)
        results = collect_results(profiler)
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# Protocol (structural subtyping)
# ---------------------------------------------------------------------------
class TestProtocol:
    def test_protocol_implementor_profiled(self):
        profiler = _register_and_profile(spicy.Box.draw, spicy.Box())
        results = collect_results(profiler)
        assert any("draw" in r.name for r in results)


# ---------------------------------------------------------------------------
# __init_subclass__
# ---------------------------------------------------------------------------
class TestInitSubclass:
    def test_plugin_methods_profiled(self):
        profiler = create_profiler()
        profiler.add_function(spicy.PluginA.run)
        profiler.add_function(spicy.PluginB.run)
        profiler.enable_by_count()
        try:
            spicy.exercise_init_subclass()
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Nested comprehension with walrus operator
# ---------------------------------------------------------------------------
class TestNestedComprehension:
    def test_nested_comprehension_profiled(self):
        profiler = _register_and_profile(spicy.nested_comprehension)
        results = collect_results(profiler)
        assert any("nested_comprehension" in r.name for r in results)
        nc = next(r for r in results if "nested_comprehension" in r.name)
        assert nc.call_count == 1


# ---------------------------------------------------------------------------
# exec-generated function
# ---------------------------------------------------------------------------
class TestExecGenerated:
    def test_exec_generated_callable(self):
        """exec-generated functions work but may have synthetic filenames."""
        assert spicy.exec_generated(5) == 6

    def test_exec_generated_filtered_from_results(self):
        """exec-generated functions have <string> filename — filtered out."""
        profiler = create_profiler()
        profiler.add_function(spicy.exec_generated)
        profiler.enable_by_count()
        try:
            spicy.exec_generated(5)
        finally:
            profiler.disable_by_count()
        results = collect_results(profiler)
        # <string> filenames are synthetic — should be filtered.
        assert not any(r.filename.startswith("<") for r in results)


# ---------------------------------------------------------------------------
# Class-level lru_cache
# ---------------------------------------------------------------------------
class TestClassLevelLruCache:
    def test_class_level_lru_cache_profiled(self):
        """lru_cache on a @staticmethod inside a class should be unwrapped."""
        spicy.WithDescriptors.cached_static.cache_clear()
        profiler = _register_module_and_profile(spicy, spicy.exercise_descriptors)
        results = collect_results(profiler)
        assert any("cached_static" in r.name for r in results)


# ---------------------------------------------------------------------------
# Full integration: register entire module, exercise everything, collect
# ---------------------------------------------------------------------------
class TestFullIntegration:
    def test_register_and_run_all_exercises(self):
        profiler = _register_module_and_profile(spicy, spicy.run_all_exercises)
        results = collect_results(profiler)
        assert len(results) > 0
        # Should have many functions profiled.
        assert len(results) >= 20

    def test_scope_filtering_removes_stdlib_leaks(self):
        """With scope_paths, stdlib wrappers are excluded from results."""
        profiler = _register_module_and_profile(spicy, spicy.run_all_exercises)
        scope_paths = build_scope_paths([spicy])
        unfiltered = collect_results(profiler)
        filtered = collect_results(profiler, scope_paths=scope_paths)
        # Filtered should have fewer (or equal) results.
        assert len(filtered) <= len(unfiltered)
        # All filtered results must be from our module.
        for fp in filtered:
            assert "_spicy_constructs" in fp.filename

    def test_enrich_all_results(self):
        profiler = _register_module_and_profile(spicy, spicy.run_all_exercises)
        scope_paths = build_scope_paths([spicy])
        results = collect_results(profiler, scope_paths=scope_paths)
        enrich_results(results)
        for fp in results:
            if fp.lines:
                # Every in-scope function should have source populated.
                assert any(lp.source for lp in fp.lines), (
                    f"{fp.name} at {fp.filename}:{fp.start_line} has no source"
                )

    def test_no_crashes_in_full_pipeline(self):
        """The full register -> profile -> collect -> enrich pipeline must not crash."""
        profiler = _register_module_and_profile(spicy, spicy.run_all_exercises)
        scope_paths = build_scope_paths([spicy])
        results = collect_results(profiler, scope_paths=scope_paths)
        enrich_results(results)
        # Verify basic data integrity.
        for fp in results:
            assert fp.name
            assert fp.filename
            assert fp.total_time >= 0
            assert fp.call_count >= 0
            for lp in fp.lines:
                assert lp.lineno > 0
                assert lp.hits >= 0
                assert lp.time >= 0
