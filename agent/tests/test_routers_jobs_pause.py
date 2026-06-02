"""Tests for POST /jobs/{id}/pause and /jobs/{id}/resume endpoints (Task 16)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure agent/ on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routers import jobs as jobs_router


class _FakeJob:
    """Minimal stand-in for services.job_manager.Job for endpoint tests."""

    def __init__(self, job_id: str):
        self.id = job_id
        self.paused = False
        self.status = "running"
        self.progress = 0
        self.total = 10


class _FakeJobManager:
    """Minimal stand-in for JobManager — only get_job() is exercised here."""

    def __init__(self):
        self.jobs: dict[str, _FakeJob] = {}

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)


@pytest.fixture
def fake_job_manager():
    return _FakeJobManager()


@pytest.fixture
def sample_job(fake_job_manager):
    job = _FakeJob("job-test-1")
    fake_job_manager.jobs[job.id] = job
    return job


@pytest.fixture
def app(fake_job_manager):
    app = FastAPI()
    app.include_router(jobs_router.router)
    jobs_router.set_job_manager(fake_job_manager)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_pause_endpoint_flips_job_flag(client, sample_job):
    r = client.post(f"/jobs/{sample_job.id}/pause")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["paused"] is True
    assert data["jobId"] == sample_job.id
    assert sample_job.paused is True


def test_resume_endpoint_flips_job_flag(client, sample_job):
    sample_job.paused = True
    r = client.post(f"/jobs/{sample_job.id}/resume")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["paused"] is False
    assert data["jobId"] == sample_job.id
    assert sample_job.paused is False


def test_pause_404_when_job_missing(client):
    r = client.post("/jobs/nope-not-a-job/pause")
    assert r.status_code == 404


def test_resume_404_when_job_missing(client):
    r = client.post("/jobs/nope-not-a-job/resume")
    assert r.status_code == 404


def test_pause_503_when_manager_not_initialized():
    """If the job manager has not been injected, return 503 not 500."""
    app = FastAPI()
    app.include_router(jobs_router.router)
    jobs_router.set_job_manager(None)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/jobs/whatever/pause")
    assert r.status_code == 503


def test_resume_503_when_manager_not_initialized():
    app = FastAPI()
    app.include_router(jobs_router.router)
    jobs_router.set_job_manager(None)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/jobs/whatever/resume")
    assert r.status_code == 503


def test_pause_resume_roundtrip_idempotent(client, sample_job):
    """Calling pause twice or resume twice should remain idempotent."""
    client.post(f"/jobs/{sample_job.id}/pause")
    client.post(f"/jobs/{sample_job.id}/pause")
    assert sample_job.paused is True

    client.post(f"/jobs/{sample_job.id}/resume")
    client.post(f"/jobs/{sample_job.id}/resume")
    assert sample_job.paused is False


def test_real_job_class_has_paused_attribute():
    """The real Job dataclass should default `paused` to False."""
    from services.job_manager import Job, ContainerRequest

    requests = [ContainerRequest("ABCU0000001", "INV-1")]
    job = Job("real-test-1", requests)
    assert hasattr(job, "paused")
    assert job.paused is False
