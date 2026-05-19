def pytest_configure(config):
    config.addinivalue_line("markers", "requires_excel: requires Microsoft Excel + pywin32")
    config.addinivalue_line("markers", "requires_qbo: requires live QBO OAuth session")
