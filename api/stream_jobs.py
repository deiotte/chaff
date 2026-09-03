"""Streaming job runner (ADR-0017): run a push sink (kafka/mqtt/http/tcp/udp)
as a bounded, stoppable background job, so the UI can Start / poll Status /
Stop a live feed to a broker or endpoint.

The engine stays a pure library (INV-1); this lifecycle lives in the API
layer. Every job carries a **mandatory record + time cap**, each clamped to a
hard server ceiling — the Day-2 guardrail so a forgotten stream can't flood a
broker forever. A job ends for a reason the UI can act on: `completed` /
`stopped` need no follow-up; `limit` / `duration` mean "hit your cap — run
again?" (the re-confirm).

Single-process assumption: jobs live in an in-memory registry, so Start/Stop/
Status must hit the same worker that owns the job. Fine for the dev server and
the single-process Docker image; a multi-worker deploy would need shared state
(out of scope — noted in the ADR).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from chaff.engine import resolve_stream_limit, stream_encoded
from chaff.formats import get_record_encoder
from chaff.sinks import get_stream_sink, is_stream_sink
from chaff.spec import DatasetSpec

from .netpolicy import DestinationBlocked, check_destination


class StreamJobError(ValueError):
    """A bad job request: unstreamable sink/format, a missing/oversized cap, or
    a destination egress policy forbids (SSRF guard, ADR-0018)."""


class TooManyJobs(StreamJobError):
    """The active-job cap is full (ADR-0029). Distinct from `StreamJobError`
    because the request isn't wrong — it's early. The API answers 429 so a
    client knows to retry rather than to fix the spec."""


def _ceiling_records() -> int:
    try:
        return int(os.environ.get("CHAFF_STREAM_MAX_RECORDS", 1_000_000))
    except ValueError:
        return 1_000_000


def _ceiling_seconds() -> float:
    try:
        return float(os.environ.get("CHAFF_STREAM_MAX_SECONDS", 300))
    except ValueError:
        return 300.0


#: How many jobs may run at once (ADR-0029). Every active job owns an OS
#: thread and a socket, and the existing caps bound each job's *length*, not
#: how many start — 70 concurrent jobs were launched with no refusal. Eight
#: leaves room for the several feeds a real demo runs (a broker, a TAK feed,
#: an HTTP endpoint) while keeping the ceiling somewhere a person chose.
DEFAULT_MAX_ACTIVE_JOBS = 8


def _max_active_jobs() -> int:
    try:
        return int(os.environ.get("CHAFF_STREAM_MAX_ACTIVE_JOBS",
                                  DEFAULT_MAX_ACTIVE_JOBS))
    except ValueError:
        return DEFAULT_MAX_ACTIVE_JOBS


@dataclass
class StreamJob:
    id: str
    sink: str
    destination: str          # human label (topic/url/host) for the UI
    max_records: int
    max_seconds: float
    rate: Optional[float]
    status: str = "running"   # running | done | stopped | error
    sent: int = 0
    started_at: float = 0.0
    finished_at: Optional[float] = None
    receipt: Optional[str] = None
    error: Optional[str] = None
    ended_reason: Optional[str] = None  # completed | limit | duration | stopped | error
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return round(end - self.started_at, 2)

    @property
    def hit_limit(self) -> bool:
        """True when the run stopped because it hit a cap — the UI re-confirms."""
        return self.ended_reason in ("limit", "duration")

    def public(self) -> dict:
        return {
            "id": self.id, "sink": self.sink, "destination": self.destination,
            "status": self.status, "sent": self.sent, "elapsed": self.elapsed,
            "max_records": self.max_records, "max_seconds": self.max_seconds,
            "rate": self.rate, "receipt": self.receipt, "error": self.error,
            "ended_reason": self.ended_reason, "hit_limit": self.hit_limit,
        }


_JOBS: dict[str, StreamJob] = {}
_LOCK = threading.Lock()
_MAX_KEPT = 50  # prune oldest finished jobs beyond this so the registry can't grow unbounded


def _require_cap(value, ceiling: float, name: str) -> float:
    if value is None:
        raise StreamJobError(f"{name} is required — a stream must be bounded")
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise StreamJobError(f"{name} must be a number")
    if v <= 0:
        raise StreamJobError(f"{name} must be positive")
    if v > ceiling:
        raise StreamJobError(
            f"{name} of {int(v) if name == 'max_records' else v} exceeds the server "
            f"ceiling of {int(ceiling) if name == 'max_records' else ceiling}")
    return v


def _destination_label(spec: DatasetSpec) -> str:
    o = spec.sink.options
    return str(o.get("topic") or o.get("url") or o.get("host") or spec.sink.sink)


def start_job(spec: DatasetSpec, *, max_records, max_seconds, rate=None) -> StreamJob:
    """Validate the guardrail + streamability, then launch a background job."""
    if spec.tables:
        raise StreamJobError("multi-table specs can't be streamed (one file per table)")
    sink_id = spec.sink.sink
    if not is_stream_sink(sink_id):
        raise StreamJobError(
            f"sink '{sink_id}' isn't a streaming sink — pick kafka, mqtt, http, tcp, or udp")
    try:
        get_record_encoder(spec.output.format)  # whole-file formats can't stream; fail fast
    except KeyError as e:
        raise StreamJobError(str(e)) from None

    # SSRF guard (ADR-0018): vet the destination before a thread/socket exists,
    # so a blocked host is a clean 422 and never a launched job.
    try:
        check_destination(spec)
    except DestinationBlocked as e:
        raise StreamJobError(str(e)) from None

    max_records = int(_require_cap(max_records, _ceiling_records(), "max_records"))
    max_seconds = _require_cap(max_seconds, _ceiling_seconds(), "max_seconds")

    job = StreamJob(
        id=uuid.uuid4().hex[:12], sink=sink_id, destination=_destination_label(spec),
        max_records=max_records, max_seconds=max_seconds,
        rate=(float(rate) if rate else None), started_at=time.monotonic())
    # Admission and registration share one lock hold. Counting first and
    # inserting afterwards would let N simultaneous requests all read the same
    # under-cap count and all be admitted — the cap has to be indivisible to
    # be a cap at all.
    with _LOCK:
        active = sum(1 for j in _JOBS.values() if j.status == "running")
        cap = _max_active_jobs()
        if active >= cap:
            raise TooManyJobs(
                f"{active} of {cap} stream jobs are already running. Stop one "
                "first, or raise CHAFF_STREAM_MAX_ACTIVE_JOBS.")
        _JOBS[job.id] = job
        _prune_locked()
    threading.Thread(target=_run_job, args=(job, spec), daemon=True).start()
    return job


def active_job_count() -> int:
    """How many jobs are running right now. Used by the status endpoint and
    the tests; kept here so nothing else has to know how `running` is spelled."""
    with _LOCK:
        return sum(1 for j in _JOBS.values() if j.status == "running")


def _run_job(job: StreamJob, spec: DatasetSpec) -> None:
    """Worker: push encoded records to the sink until a cap or Stop, counting
    as it goes. Bounds come from the job (not the spec), so the caps are
    authoritative. Any sink/network failure is captured on the job, never
    raised out of the thread."""
    try:
        base = stream_encoded(
            spec,
            limit=resolve_stream_limit(job.max_records, job.max_seconds),
            rate=job.rate,
            duration=job.max_seconds,
        )

        def instrumented():
            for rec in base:
                if job._stop.is_set():
                    return
                yield rec
                job.sent += 1  # counts records handed to the sink (GIL-atomic increment)

        job.receipt = get_stream_sink(job.sink)(spec, instrumented())
        job.finished_at = time.monotonic()
        if job._stop.is_set():
            job.status, job.ended_reason = "stopped", "stopped"
        else:
            job.status = "done"
            job.ended_reason = _end_reason(job)
    except Exception as e:  # sink/network/encode failure — surface, don't crash the thread
        job.finished_at = time.monotonic()
        job.status, job.ended_reason = "error", "error"
        job.error = f"{type(e).__name__}: {e}"


def _end_reason(job: StreamJob) -> str:
    """Why a non-stopped, non-errored run ended. In stream mode the record cap
    *is* the length (it extends past the spec's `rows`, ADR-0016), so a run
    always ends by hitting a bound: the record cap, or the clock cutting it
    first. Either way the UI offers "run again?" (hit_limit)."""
    return "limit" if job.sent >= job.max_records else "duration"


def get_job(job_id: str) -> StreamJob:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise KeyError(job_id)
    return job


def list_jobs() -> list[dict]:
    with _LOCK:
        return [j.public() for j in _JOBS.values()]


def stop_job(job_id: str) -> StreamJob:
    """Signal a job to stop. Cooperative: the worker halts between records, so
    the returned status may briefly still read 'running' — poll to confirm."""
    job = get_job(job_id)
    job._stop.set()
    return job


def _prune_locked() -> None:
    """Drop the oldest finished jobs beyond _MAX_KEPT (caller holds _LOCK)."""
    if len(_JOBS) <= _MAX_KEPT:
        return
    finished = [j for j in _JOBS.values() if j.status != "running"]
    finished.sort(key=lambda j: j.finished_at or 0.0)
    for j in finished[: len(_JOBS) - _MAX_KEPT]:
        _JOBS.pop(j.id, None)
