"""Streaming job runner tests (ADR-0017): Start/Status/Stop over the API, the
mandatory caps + ceiling guardrail, and the ended-reason the UI re-confirms
on. A broker-free in-memory stream sink stands in for kafka/mqtt so the job
lifecycle is exercised without any network."""

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from chaff.sinks import stream_sink  # noqa: E402


# A broker-free streaming sink: consumes the (paced, capped, stoppable)
# iterator the job runner hands it. Registered once for the test session;
# the try/except tolerates a re-import registering it twice.
try:
    @stream_sink("mem_test")
    def _mem_sink(spec, records):
        n = sum(1 for _ in records)
        return f"collected {n} record(s)"
except ValueError:
    pass  # already registered on a prior import

client = TestClient(app)


def job_spec(rows=50, fmt="ndjson", sink="mem_test", options=None):
    return {
        "name": "js", "seed": 1, "rows": rows,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": fmt},
        "sink": {"sink": sink, "options": options or {"topic": "demo"}},
    }


def wait_done(job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/stream/jobs/{job_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


# ── Happy path: bounded run to completion ────────────────────────────

def test_job_runs_to_the_record_cap():
    r = client.post("/stream/jobs", json={"spec": job_spec(rows=1000),
                                          "max_records": 5, "max_seconds": 30})
    assert r.status_code == 200
    job = r.json()
    # A tiny unpaced job may already be done by the time the POST returns.
    assert job["status"] in ("running", "done") and job["destination"] == "demo"
    done = wait_done(job["id"])
    assert done["status"] == "done"
    assert done["sent"] == 5
    assert done["ended_reason"] == "limit" and done["hit_limit"] is True


def test_job_extends_past_the_spec_row_count():
    # rows=3 but cap=8 -> the stream keeps generating past the natural length.
    r = client.post("/stream/jobs", json={"spec": job_spec(rows=3),
                                          "max_records": 8, "max_seconds": 30})
    done = wait_done(r.json()["id"])
    assert done["sent"] == 8 and done["ended_reason"] == "limit"


def test_duration_cap_cuts_the_stream_before_the_record_cap():
    # A tiny time cap with a huge record cap: the clock ends it, not the count.
    r = client.post("/stream/jobs", json={"spec": job_spec(rows=10),
                                          "max_records": 1_000_000,
                                          "max_seconds": 0.3, "rate": 100})
    done = wait_done(r.json()["id"])
    assert done["status"] == "done" and done["ended_reason"] == "duration"
    assert 0 < done["sent"] < 1_000_000 and done["hit_limit"] is True


# ── Stop mid-stream ──────────────────────────────────────────────────

def test_stop_halts_a_running_job():
    # Paced + large cap so it stays running; stop lands within a rate interval.
    r = client.post("/stream/jobs", json={"spec": job_spec(rows=1_000_000),
                                          "max_records": 1_000_000, "max_seconds": 60,
                                          "rate": 20})
    job_id = r.json()["id"]
    client.delete(f"/stream/jobs/{job_id}")  # signal stop
    done = wait_done(job_id)
    assert done["status"] == "stopped" and done["ended_reason"] == "stopped"
    assert done["sent"] < 1_000_000 and done["hit_limit"] is False


# ── Guardrail: caps required + ceiling enforced ──────────────────────

def test_missing_cap_is_rejected():
    r = client.post("/stream/jobs", json={"spec": job_spec(), "max_records": 10})
    assert r.status_code == 422  # max_seconds missing (pydantic-required)


def test_cap_over_ceiling_is_rejected(monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_MAX_SECONDS", "10")
    r = client.post("/stream/jobs", json={"spec": job_spec(),
                                          "max_records": 5, "max_seconds": 99999})
    assert r.status_code == 422
    assert "ceiling" in r.json()["detail"]


def test_zero_cap_is_rejected():
    r = client.post("/stream/jobs", json={"spec": job_spec(),
                                          "max_records": 0, "max_seconds": 30})
    assert r.status_code == 422
    assert "positive" in r.json()["detail"]


# ── Streamability guards ─────────────────────────────────────────────

def test_non_streaming_sink_is_rejected():
    r = client.post("/stream/jobs", json={"spec": job_spec(sink="file", options={}),
                                          "max_records": 5, "max_seconds": 30})
    assert r.status_code == 422
    assert "streaming sink" in r.json()["detail"]


def test_whole_file_format_is_rejected_before_starting():
    r = client.post("/stream/jobs", json={"spec": job_spec(fmt="sql"),
                                          "max_records": 5, "max_seconds": 30})
    assert r.status_code == 422
    assert "no per-record encoder" in r.json()["detail"]


def test_status_404_for_unknown_job():
    assert client.get("/stream/jobs/nope").status_code == 404
    assert client.delete("/stream/jobs/nope").status_code == 404
