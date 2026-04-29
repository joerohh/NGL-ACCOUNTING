"""Shared pytest config for tms_data tests."""

import pytest_asyncio

# pytest-asyncio mode: auto runs all `async def` tests as async automatically.
pytest_plugins = ("pytest_asyncio",)


def pytest_collection_modifyitems(config, items):
    """Auto-mark async tests so we don't need @pytest.mark.asyncio everywhere."""
    import asyncio
    import pytest as _pytest
    for item in items:
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(_pytest.mark.asyncio)
