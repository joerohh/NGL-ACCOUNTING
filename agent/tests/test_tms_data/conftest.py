"""Shared pytest config for tms_data tests."""

# pytest_plugins moved to top-level agent/tests/conftest.py (newer pytest
# requires this declaration in the topmost conftest only).


def pytest_collection_modifyitems(config, items):
    """Auto-mark async tests so we don't need @pytest.mark.asyncio everywhere."""
    import asyncio
    import pytest as _pytest
    for item in items:
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(_pytest.mark.asyncio)
