# pytest-asyncio loaded at the top-level conftest per pytest's requirement that
# pytest_plugins only be declared in the topmost conftest. Previously this lived
# inside agent/tests/test_tms_data/conftest.py and tripped a collection error on
# newer pytest versions.
pytest_plugins = ("pytest_asyncio",)


def pytest_configure(config):
    config.addinivalue_line("markers", "requires_excel: requires Microsoft Excel + pywin32")
    config.addinivalue_line("markers", "requires_qbo: requires live QBO OAuth session")
