"""Verify there is one TMSApiClient instance shared across main, routers, and JobManager."""

from unittest.mock import MagicMock

from routers import tms as tms_router
from services.job_manager import JobManager
from services.qbo_api import QBOApiClient


def test_set_tms_api_propagates_to_router():
    fake = MagicMock(name="tms_api_singleton")
    tms_router.set_tms_api(fake)
    assert tms_router._tms_api is fake


def test_set_tms_api_propagates_to_job_manager():
    fake = MagicMock(name="tms_api_singleton")
    jm = JobManager(QBOApiClient(), classifier=MagicMock())
    jm.set_tms_api(fake)
    assert jm._tms_api is fake
