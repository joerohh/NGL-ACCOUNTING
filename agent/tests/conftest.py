def pytest_configure(config):
    config.addinivalue_line("markers", "requires_excel: requires Microsoft Excel + pywin32")
