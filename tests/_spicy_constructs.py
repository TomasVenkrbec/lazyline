"""Module with every nasty Python construct to stress-test the profiler.

Each section exercises a different edge case for line_profiler registration,
profiling, and result collection. Functions are designed to be callable so
profiling actually generates timing data.
"""

from __future__ import annotations

import asyncio
import functools
from abc import ABC, abstractmethod
from contextlib import contextmanager
from enum import Enum, auto
from typing import TYPE_CHECKING, NamedTuple, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


# ---------------------------------------------------------------------------
# 1. Closures / nested functions
# ---------------------------------------------------------------------------
def outer_with_closure(n: int = 5) -> int:
    """Outer function that defines and calls an inner closure."""
    captured = n * 2

    def inner():
        return captured + 1

    return inner()


# ---------------------------------------------------------------------------
# 2. Generators
# ---------------------------------------------------------------------------
def fibonacci_generator(limit: int = 10) -> Generator[int, None, None]:
    """Yield Fibonacci numbers up to *limit*."""
    a, b = 0, 1
    while a < limit:
        yield a
        a, b = b, a + b


def consume_generator() -> list[int]:
    """Consume the Fibonacci generator to drive profiling."""
    return list(fibonacci_generator(100))


# ---------------------------------------------------------------------------
# 3. Async functions / async generators
# ---------------------------------------------------------------------------
async def async_work(n: int = 10) -> int:
    """Async function that does some compute."""
    total = 0
    for i in range(n):
        total += i
    return total


async def async_generator(n: int = 5):
    """Async generator yielding squares."""
    for i in range(n):
        yield i * i


async def consume_async() -> tuple[int, list[int]]:
    """Drive async function + async generator."""
    result = await async_work(20)
    items = [x async for x in async_generator(10)]
    return result, items


def run_async() -> tuple[int, list[int]]:
    """Sync wrapper to exercise async constructs."""
    return asyncio.run(consume_async())


# ---------------------------------------------------------------------------
# 4. Lambdas (module-level)
# ---------------------------------------------------------------------------
square_lambda = lambda x: x * x  # noqa: E731
triple_lambda = lambda x: x * 3  # noqa: E731


def call_lambdas() -> tuple[int, int]:
    """Exercise module-level lambdas."""
    return square_lambda(7), triple_lambda(7)


# ---------------------------------------------------------------------------
# 5. Decorators: @staticmethod, @classmethod, @property
# ---------------------------------------------------------------------------
class WithDescriptors:
    """Class with every descriptor type."""

    def __init__(self, value: int = 42):
        self._value = value
        self._cache: dict = {}

    @staticmethod
    def static_work(n: int) -> int:
        return sum(range(n))

    @classmethod
    def from_double(cls, n: int) -> WithDescriptors:
        return cls(n * 2)

    @property
    def value(self) -> int:
        return self._value

    @value.setter
    def value(self, new: int) -> None:
        self._value = new

    def regular_method(self) -> int:
        return self._value + 1

    @staticmethod
    @functools.lru_cache(maxsize=16)
    def cached_static(n: int) -> int:
        """lru_cache on a static method — buried inside a class."""
        return sum(i * i for i in range(n))


def exercise_descriptors() -> tuple:
    """Call every descriptor method."""
    obj = WithDescriptors(10)
    WithDescriptors.cached_static.cache_clear()
    s = WithDescriptors.static_work(100)
    cs = WithDescriptors.cached_static(20)
    obj2 = WithDescriptors.from_double(5)
    v = obj.value
    obj.value = 99
    r = obj.regular_method()
    return s, cs, obj2._value, v, r


# ---------------------------------------------------------------------------
# 6. functools wrappers: lru_cache, wraps, singledispatch, partial
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=32)
def cached_fib(n: int) -> int:
    """Fibonacci with lru_cache — wrapper around the real function."""
    if n < 2:
        return n
    return cached_fib(n - 1) + cached_fib(n - 2)


def custom_decorator(func):
    """A custom decorator using functools.wraps."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@custom_decorator
def decorated_function(x: int) -> int:
    """Function wrapped with a custom decorator."""
    return x * x + 1


@functools.singledispatch
def process(value):
    """Single-dispatch generic function."""
    return str(value)


@process.register(int)
def _process_int(value: int) -> str:
    return f"int:{value}"


@process.register(float)
def _process_float(value: float) -> str:
    return f"float:{value:.2f}"


add_ten = functools.partial(sum, [10])


def exercise_functools() -> tuple:
    """Drive all functools constructs."""
    fib = cached_fib(15)
    dec = decorated_function(5)
    p1 = process(42)
    p2 = process(3.14)
    p3 = process("hello")
    pt = add_ten()
    return fib, dec, p1, p2, p3, pt


# ---------------------------------------------------------------------------
# 7. ABC / abstract methods
# ---------------------------------------------------------------------------
class Shape(ABC):
    """Abstract base class."""

    @abstractmethod
    def area(self) -> float: ...

    @abstractmethod
    def perimeter(self) -> float: ...

    def describe(self) -> str:
        return f"area={self.area()}, perimeter={self.perimeter()}"


class Circle(Shape):
    def __init__(self, radius: float):
        self.radius = radius

    def area(self) -> float:
        return 3.14159 * self.radius**2

    def perimeter(self) -> float:
        return 2 * 3.14159 * self.radius


def exercise_abc() -> str:
    c = Circle(5.0)
    return c.describe()


# ---------------------------------------------------------------------------
# 8. Context managers (class-based and generator-based)
# ---------------------------------------------------------------------------
class ManagedResource:
    """Class-based context manager."""

    def __init__(self):
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False


@contextmanager
def generator_context(tag: str = "default"):
    """Generator-based context manager via contextlib."""
    state = {"entered": True, "tag": tag}
    yield state
    state["exited"] = True


def exercise_context_managers() -> tuple[bool, dict]:
    with ManagedResource() as r:
        pass
    with generator_context("test") as s:
        pass
    return r.exited, s


# ---------------------------------------------------------------------------
# 9. NamedTuple
# ---------------------------------------------------------------------------
class Point(NamedTuple):
    x: float
    y: float

    def distance_to_origin(self) -> float:
        return (self.x**2 + self.y**2) ** 0.5


def exercise_namedtuple() -> float:
    p = Point(3.0, 4.0)
    return p.distance_to_origin()


# ---------------------------------------------------------------------------
# 10. Enum
# ---------------------------------------------------------------------------
class Color(Enum):
    RED = auto()
    GREEN = auto()
    BLUE = auto()

    def is_primary(self) -> bool:
        return self in (Color.RED, Color.GREEN, Color.BLUE)


def exercise_enum() -> bool:
    return Color.RED.is_primary()


# ---------------------------------------------------------------------------
# 11. __slots__ class
# ---------------------------------------------------------------------------
class SlottedPoint:
    __slots__ = ("x", "y")

    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

    def magnitude(self) -> float:
        return (self.x**2 + self.y**2) ** 0.5


def exercise_slots() -> float:
    p = SlottedPoint(3.0, 4.0)
    return p.magnitude()


# ---------------------------------------------------------------------------
# 12. Multiple inheritance / MRO / super() chains
# ---------------------------------------------------------------------------
class Base:
    def compute(self) -> int:
        return 1


class MixinA(Base):
    def compute(self) -> int:
        return super().compute() + 10


class MixinB(Base):
    def compute(self) -> int:
        return super().compute() + 100


class Diamond(MixinA, MixinB):
    def compute(self) -> int:
        return super().compute() + 1000


def exercise_mro() -> int:
    return Diamond().compute()


# ---------------------------------------------------------------------------
# 13. Metaclass
# ---------------------------------------------------------------------------
class RegistryMeta(type):
    _registry: dict[str, type] = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        mcs._registry[name] = cls
        return cls


class Registered(metaclass=RegistryMeta):
    def work(self) -> str:
        return "registered"


def exercise_metaclass() -> str:
    return Registered().work()


# ---------------------------------------------------------------------------
# 14. Descriptors (custom __get__/__set__)
# ---------------------------------------------------------------------------
class ValidatedField:
    """Data descriptor with __get__ and __set__."""

    def __set_name__(self, owner, name):
        self.name = f"_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return getattr(obj, self.name, 0)

    def __set__(self, obj, value):
        if value < 0:
            msg = "Value must be non-negative"
            raise ValueError(msg)
        setattr(obj, self.name, value)


class Account:
    balance = ValidatedField()

    def __init__(self, initial: float):
        self.balance = initial


def exercise_descriptors_custom() -> float:
    a = Account(100.0)
    a.balance = 200.0
    return a.balance


# ---------------------------------------------------------------------------
# 15. Recursive function
# ---------------------------------------------------------------------------
def recursive_sum(n: int) -> int:
    """Recursive function — call count != first-line hits for n > 1."""
    if n <= 0:
        return 0
    return n + recursive_sum(n - 1)


# ---------------------------------------------------------------------------
# 16. Iterator protocol (__iter__ / __next__)
# ---------------------------------------------------------------------------
class CountDown:
    """Custom iterator class."""

    def __init__(self, start: int):
        self.current = start

    def __iter__(self) -> Iterator[int]:
        return self

    def __next__(self) -> int:
        if self.current <= 0:
            raise StopIteration
        self.current -= 1
        return self.current + 1


def exercise_iterator() -> list[int]:
    return list(CountDown(5))


# ---------------------------------------------------------------------------
# 17. __call__ (callable objects)
# ---------------------------------------------------------------------------
class Multiplier:
    """Callable object via __call__."""

    def __init__(self, factor: int):
        self.factor = factor

    def __call__(self, x: int) -> int:
        return x * self.factor


def exercise_callable_object() -> int:
    m = Multiplier(3)
    return m(14)


# ---------------------------------------------------------------------------
# 18. Class with __init_subclass__
# ---------------------------------------------------------------------------
class PluginBase:
    _plugins: list[type] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        PluginBase._plugins.append(cls)


class PluginA(PluginBase):
    def run(self) -> str:
        return "A"


class PluginB(PluginBase):
    def run(self) -> str:
        return "B"


def exercise_init_subclass() -> list[str]:
    return [p().run() for p in PluginBase._plugins]


# ---------------------------------------------------------------------------
# 19. Protocol (structural subtyping)
# ---------------------------------------------------------------------------
@runtime_checkable
class Drawable(Protocol):
    def draw(self) -> str: ...


class Box:
    def draw(self) -> str:
        return "[ ]"


def exercise_protocol() -> tuple[bool, str]:
    b = Box()
    return isinstance(b, Drawable), b.draw()


# ---------------------------------------------------------------------------
# 20. Deeply nested comprehensions + walrus operator
# ---------------------------------------------------------------------------
def nested_comprehension() -> list:
    """List comp with walrus operator and nested filtering."""
    return [(y, total) for x in range(10) if (y := x * x) > 5 if (total := y + x) < 80]


# ---------------------------------------------------------------------------
# 21. Exception-heavy flow
# ---------------------------------------------------------------------------
def exception_heavy(n: int = 10) -> int:
    """Function that uses exceptions for control flow."""
    total = 0
    for i in range(n):
        try:
            if i % 3 == 0:
                raise ValueError(i)
            total += i
        except ValueError:
            total -= 1
    return total


# ---------------------------------------------------------------------------
# 22. exec-generated function at module level
# ---------------------------------------------------------------------------
_exec_ns: dict = {}
exec("def exec_generated(x): return x + 1", _exec_ns)  # noqa: S102
exec_generated = _exec_ns["exec_generated"]


# ---------------------------------------------------------------------------
# 23. Heavily decorated stack (multiple decorators)
# ---------------------------------------------------------------------------
def decorator_a(func):
    @functools.wraps(func)
    def wrapper(*a, **kw):
        return func(*a, **kw) + 1

    return wrapper


def decorator_b(func):
    @functools.wraps(func)
    def wrapper(*a, **kw):
        return func(*a, **kw) * 2

    return wrapper


@decorator_a
@decorator_b
def double_decorated(x: int) -> int:
    return x


# ---------------------------------------------------------------------------
# 24. Global / module-level executable code
# ---------------------------------------------------------------------------
MODULE_CONSTANT = sum(range(50))
_PRIVATE_STATE = {i: i * i for i in range(20)}


# ---------------------------------------------------------------------------
# 25. Large function with many branches
# ---------------------------------------------------------------------------
def many_branches(x: int) -> str:
    """Function with lots of branches — some lines will never be hit."""
    if x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    elif x < 10:
        return "small"
    elif x < 100:
        return "medium"
    elif x < 1000:
        return "large"
    else:
        return "huge"


# ---------------------------------------------------------------------------
# 26. String of all exercises for easy invocation
# ---------------------------------------------------------------------------
def run_all_exercises() -> dict:
    """Call every exercise function and return results keyed by name."""
    return {
        "closure": outer_with_closure(),
        "generator": consume_generator(),
        "async": run_async(),
        "lambdas": call_lambdas(),
        "descriptors": exercise_descriptors(),
        "functools": exercise_functools(),
        "abc": exercise_abc(),
        "context_managers": exercise_context_managers(),
        "namedtuple": exercise_namedtuple(),
        "enum": exercise_enum(),
        "slots": exercise_slots(),
        "mro": exercise_mro(),
        "metaclass": exercise_metaclass(),
        "descriptors_custom": exercise_descriptors_custom(),
        "recursive": recursive_sum(10),
        "iterator": exercise_iterator(),
        "callable_object": exercise_callable_object(),
        "init_subclass": exercise_init_subclass(),
        "protocol": exercise_protocol(),
        "nested_comprehension": nested_comprehension(),
        "exception_heavy": exception_heavy(),
        "exec_generated": exec_generated(5),
        "double_decorated": double_decorated(3),
        "many_branches": many_branches(42),
    }
