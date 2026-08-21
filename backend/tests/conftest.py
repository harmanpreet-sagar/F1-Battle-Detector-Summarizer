"""
Shared pytest configuration.

`pytest-asyncio` is the intended runner for the async client tests and is listed
in requirements-dev.txt. When it is not installed the fallback below runs
coroutine tests directly, so the suite is never silently skipped.
"""
import asyncio
import inspect

try:
    import pytest_asyncio  # noqa: F401
    HAS_PYTEST_ASYNCIO = True
except ImportError:
    HAS_PYTEST_ASYNCIO = False


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run this coroutine test in an event loop")


def pytest_pyfunc_call(pyfuncitem):
    """Run coroutine tests when pytest-asyncio is unavailable."""
    if HAS_PYTEST_ASYNCIO:
        return None
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None

    kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True
