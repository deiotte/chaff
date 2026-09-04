"""chaff API — a thin transport for specs.

The API never contains generation logic; it builds/validates specs and
hands them to the engine (INV-1). Run: uvicorn api.main:app --reload
(requires extras: pip install -e '.[api]').
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path
from typing import Callable, Iterator, Optional

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import auth

from chaff import __version__, library
from chaff.engine import (
    effective_row_count,
    encode_observers,
    encode_tables,
    encode_view,
    generate_records,
    generate_tables,
    observer_views,
    iter_records,
)
from chaff.formats import get_encoder, get_extension, get_record_encoder, list_formats
from chaff.generators import (
    list_generator_examples,
    list_generator_groups,
    list_generators,
)
from chaff.sinks import list_sinks
from chaff.spec import DatasetSpec, load_spec
from chaff.updaters import list_updater_examples, list_updaters

app = FastAPI(title="chaff", version=__version__)

# Request size limit for the inline API. The spec allows up to 10M rows,
# but synchronous generate/preview over HTTP needs its own ceiling so a
# single request can't tie up the process. Override with CHAFF_API_MAX_ROWS.
DEFAULT_MAX_ROWS = 100_000
PREVIEW_MAX_ROWS = 100
DOWNLOAD_CHUNK = 64 * 1024

# Media types by format id; anything unmapped downloads as octet-stream.
_MEDIA_TYPES = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "ndjson": "application/x-ndjson",
    "sql": "application/sql",
    "xml": "application/xml",
    "cot": "application/xml",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "parquet": "application/vnd.apache.parquet",
    "avro": "application/vnd.apache.avro",
}


def _max_rows() -> int:
    try:
        return int(os.environ.get("CHAFF_API_MAX_ROWS", DEFAULT_MAX_ROWS))
    except ValueError:
        return DEFAULT_MAX_ROWS


def _enforce_row_limit(rows: int) -> None:
    cap = _max_rows()
    if rows > cap:
        raise HTTPException(
            status_code=413,
            detail=f"spec requests {rows} rows; this API caps inline generation at "
                   f"{cap} (set CHAFF_API_MAX_ROWS to change). Use the CLI for bulk output.",
        )


# ── Access control (ADR-0025) ────────────────────────────────────────
# Middleware, deliberately, not a per-route dependency. F-02 happened exactly
# because routes were added and nobody remembered to hang `require_token` on
# them — with a dependency, a new route is open until someone notices; with
# middleware, a new route is protected until someone deliberately exempts it.
# The failure mode this fixes is forgetting, so the default has to be closed.
#
# Only what you need to bootstrap stays open: the page itself (you must be
# able to load it to type the token in) and the licence text (attribution has
# to be readable in a redistributed build).
_OPEN_PREFIXES = ("/licenses",)
_OPEN_PATHS = {"/", "/index.html", "/favicon.ico"}


def _is_open(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


@app.middleware("http")
async def enforce_access(request: Request, call_next):
    if _is_open(request.url.path):
        return await call_next(request)
    reason = auth.access_denied_reason(
        request.client.host if request.client else None,
        auth.token_from_headers(request.headers),
    )
    if reason:
        return JSONResponse(status_code=401, content={"detail": reason})
    return await call_next(request)


@app.get("/registry")
def registry():
    """The UI populates its dropdowns from this — never hardcode."""
    return {
        "generators": list_generators(),                # flat list (back-compat)
        "generator_groups": list_generator_groups(),     # grouped for the dropdown
        "generator_examples": list_generator_examples(),  # gen_id -> params example

        "formats": list_formats(),
        "sinks": list_sinks(),
        "updaters": list_updaters(),
        "updater_examples": list_updater_examples(),  # params shape per updater
        # Desktop builds get a Quit button; the web/Docker UI must not.
        "desktop": DESKTOP_MODE,
    }


def _content_disposition(filename: str) -> str:
    """A Content-Disposition value that a hostile `spec.name` can't break.

    `name` is free text the user types, and it lands in a response header.
    A CR/LF or a quote in it produced an invalid header, which uvicorn
    rejects at the wire — the download died with a dropped connection
    instead of a file. Strip anything that isn't safe in a quoted filename
    and fall back to a usable default if nothing survives.
    """
    cleaned = re.sub(r'[^A-Za-z0-9._-]', "_", filename).lstrip(".") or "dataset"
    return f'attachment; filename="{cleaned[:120]}"'


# ── Desktop mode (ADR-0023) ──────────────────────────────────────────
# The packaged desktop apps have no terminal to close: a macOS .app bundle
# runs windowless, and asking someone to hunt for a console window (or Force
# Quit) is not a quit story. So the desktop launcher sets CHAFF_DESKTOP=1 and
# registers a hook, and the UI grows a Quit button.
#
# Off by default, so the Docker/web deployment never exposes it — a public
# /shutdown would be a denial-of-service button. It is additionally refused
# from anything but loopback, so a desktop instance can't be stopped by
# something else on the network.

DESKTOP_MODE = os.environ.get("CHAFF_DESKTOP") == "1"

_shutdown_hook: Optional[Callable[[], None]] = None


def set_shutdown_hook(fn: Callable[[], None]) -> None:
    """Let the launcher own how the server stops (it holds the uvicorn
    Server). Signals would be the alternative and behave differently on
    Windows; this keeps one path on every platform."""
    global _shutdown_hook
    _shutdown_hook = fn


@app.post("/shutdown")
def shutdown(request: Request):
    """Stop the local desktop app. Desktop mode, loopback, same-origin only."""
    if not DESKTOP_MODE:
        raise HTTPException(status_code=404, detail="not found")

    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="shutdown is loopback-only")

    # Loopback alone isn't enough: any page the user happens to visit can POST
    # to 127.0.0.1 and would look local. A cross-site POST carries an `Origin`
    # that isn't ours, so reject those — our own page's fetch() sends a
    # matching one. A missing Origin (curl, the CI smoke test) is allowed:
    # something already on this machine can kill the process anyway.
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="shutdown is same-origin only")
    if _shutdown_hook is None:
        raise HTTPException(status_code=503, detail="no shutdown hook registered")
    _shutdown_hook()
    return {"stopping": True}


def _capped_tables(spec: DatasetSpec, limit: int) -> DatasetSpec:
    """A multi-table spec with every table's row count capped, for preview.

    FK integrity survives the cap: children still draw their keys from the
    (smaller) parents actually generated, so a preview never shows a dangling
    reference.
    """
    return spec.model_copy(update={
        "rows": min(spec.rows, limit),
        "tables": [t.model_copy(update={"rows": min(t.rows, limit)}) for t in spec.tables],
    })


@app.post("/preview")
def preview(spec: DatasetSpec, limit: int = 10):
    """First N rows for the UI's live preview pane.

    Always returns `rows` (the primary table) so single-table callers are
    unchanged. A multi-table spec additionally returns `tables`, mapping each
    table name to its own sample rows — the UI shows one section per table so
    Joe can see the foreign keys line up before he downloads anything.
    """
    limit = max(1, min(limit, PREVIEW_MAX_ROWS))
    if spec.tables:
        try:
            tables = generate_tables(_capped_tables(spec, limit))
        except (KeyError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        sampled = {name: rows[:limit] for name, rows in tables.items()}
        return {"rows": sampled.get(spec.name, []), "tables": sampled}
    if spec.entity:
        # Bound preview work for entity specs: a few entities over a few ticks.
        capped_entity = spec.entity.model_copy(update={
            "count": min(spec.entity.count, limit), "ticks": min(spec.entity.ticks, limit)})
        capped = spec.model_copy(update={"entity": capped_entity})
        if spec.entity.observers:
            # Preview what the FEEDS carry, never the scene behind them. The
            # underlying truth is the one thing no observer emits, and showing
            # it here would preview a file that does not exist.
            try:
                feeds = {name: rows[:limit] for name, _, rows in observer_views(capped)}
            except (KeyError, ValueError) as e:
                raise HTTPException(status_code=400, detail=str(e))
            first = next(iter(feeds.values()), [])
            return {"rows": first, "feeds": feeds}
    else:
        capped = spec.model_copy(update={"rows": min(spec.rows, limit)})
    try:
        return {"rows": generate_records(capped)[:limit]}
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/generate")
def generate(spec: DatasetSpec):
    """Generate the dataset and stream it back as a file download.

    Encoders are whole-payload pure functions (INV-2), so this streams the
    finished buffer in chunks — enough to hand the browser a real file
    without holding the response in one string. True per-record streaming
    (ndjson/csv straight from the generator) arrives with the Phase 2
    streaming-encoder signature.
    """
    _enforce_row_limit(effective_row_count(spec))
    if spec.tables:
        return _generate_multitable_zip(spec)
    if spec.entity and spec.entity.observers:
        # One file per observer plus the truth key — the same shape the CLI
        # writes, so a download and a `chaff generate` are interchangeable.
        return _zip_response(spec, encode_observers)
    try:
        rows = generate_records(spec)
        payload = get_encoder(spec.output.format)(encode_view(spec), rows)
        ext = get_extension(spec.output.format)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = f"{spec.name}{ext}"
    media_type = _MEDIA_TYPES.get(spec.output.format, "application/octet-stream")

    def _stream() -> Iterator[bytes]:
        for i in range(0, len(payload), DOWNLOAD_CHUNK):
            yield payload[i:i + DOWNLOAD_CHUNK]

    return StreamingResponse(
        _stream(),
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(filename),
            "Content-Length": str(len(payload)),
        },
    )


# ── Multi-table download (ADR-0020) ──────────────────────────────────
# One HTTP request still yields one file — that file is a zip holding one
# encoded file per table. The engine does the generating and encoding
# (INV-1); this only packages the bytes it hands back.

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)  # zip's minimum date_time; pinned for INV-3


def _generate_multitable_zip(spec: DatasetSpec) -> StreamingResponse:
    """Bundle a multi-table spec's tables into one deterministic zip.

    Entry timestamps are pinned to the zip epoch and entries are written in
    the engine's dependency order, so the same spec + seed produces a
    byte-identical archive every time (INV-3) — not just identical members.
    """
    return _zip_response(spec, encode_tables)


def _zip_response(spec: DatasetSpec, encode) -> StreamingResponse:
    """One request, one file: a deterministic zip holding each member.

    Shared by the multi-table and multi-observer paths — both are "one spec,
    several files", and a second zip builder would be a second place for the
    reproducibility pins to drift.
    """
    try:
        encoded = encode(spec)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    buf = io.BytesIO()
    # No compression: deflate output can vary across zlib builds, which would
    # break byte-for-byte reproducibility of the archive itself.
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for _name, filename, payload in encoded:
            zf.writestr(zipfile.ZipInfo(filename, date_time=_ZIP_EPOCH), payload)
    payload = buf.getvalue()

    def _stream() -> Iterator[bytes]:
        for i in range(0, len(payload), DOWNLOAD_CHUNK):
            yield payload[i:i + DOWNLOAD_CHUNK]

    return StreamingResponse(
        _stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition(f"{spec.name}.zip"),
            "Content-Length": str(len(payload)),
            # Table names are user text; keep the header wire-safe.
            "X-Chaff-Tables": ",".join(
                re.sub(r"[^A-Za-z0-9._-]", "_", name) for name, _, _ in encoded),
        },
    )


# ── Live stream (serve, not download) — Phase 6, ADR-0016 ────────────
# chaff *is* the server here: a client opens the socket, sends a spec, and
# chaff streams paced, encoded records back until the client disconnects or a
# duration/max_records bound is hit. No external broker (that's the kafka/mqtt
# push sinks). The spec's own `sink` is ignored — the socket is the delivery.
#
# Protocol:
#   1. client connects to /stream (run-mode via query params below)
#   2. client sends ONE text frame: the DatasetSpec as JSON
#   3. server streams record frames (text), one encoded record per frame,
#      then closes the socket to mark end-of-stream
#   On a bad spec / unstreamable format the server sends a single
#   {"error": "..."} JSON frame and closes without streaming.
#
# Query params (delivery session, not data): rate (records/sec),
# duration (seconds), max_records. Mirrors the streaming sink options so the
# same spec streams the same way to a socket or a broker.

STREAM_DEFAULT_CAP = 10_000  # a plain connect (no bound) serves at most this
STREAM_HANDSHAKE_TIMEOUT = 10.0  # seconds to wait for the opening spec frame
STREAM_MAX_SESSIONS = 64  # concurrent /stream sockets one process will hold

# Live /stream sockets currently in flight. The WS handlers are coroutines on
# the single event loop, so a plain int is safe here: the capacity check and
# the increment run back-to-back with no `await` between them, so no two
# admissions can interleave. (Push jobs run in threads and are counted
# separately by stream_jobs — this is only the browser-held sockets.)
_active_ws_sessions = 0


def _handshake_timeout() -> float:
    """Seconds to wait for the client's opening spec frame before dropping an
    idle socket (ADR-0019). Override with CHAFF_STREAM_HANDSHAKE_TIMEOUT."""
    try:
        return float(os.environ.get(
            "CHAFF_STREAM_HANDSHAKE_TIMEOUT", STREAM_HANDSHAKE_TIMEOUT))
    except ValueError:
        return STREAM_HANDSHAKE_TIMEOUT


def _max_ws_sessions() -> int:
    """How many live /stream sockets one process will hold at once (ADR-0019).
    Override with CHAFF_STREAM_MAX_SESSIONS."""
    try:
        return int(os.environ.get("CHAFF_STREAM_MAX_SESSIONS", STREAM_MAX_SESSIONS))
    except ValueError:
        return STREAM_MAX_SESSIONS


def _stream_ceilings() -> tuple[int, float]:
    """The hard server ceilings the push-job runner enforces, reused here so
    the WS serve path is bounded by the *same* guardrail (ADR-0017/0018)."""
    from . import stream_jobs
    return stream_jobs._ceiling_records(), stream_jobs._ceiling_seconds()


def _q_positive(qp, key: str, cast, label: str):
    """Parse a positive query param, or None if absent. A present-but-invalid
    value is an error, never a silent fall-through to 'unbounded' (that
    degrade-to-unlimited was its own finding)."""
    raw = qp.get(key)
    if raw in (None, ""):
        return None
    try:
        val = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"invalid '{key}': {raw!r} is not a valid {label}")
    if val <= 0:
        raise ValueError(f"invalid '{key}': must be a positive {label}")
    return val


def _parse_stream_params(qp) -> tuple[Optional[float], Optional[float], Optional[int]]:
    """(rate, duration, max_records) from the WS query string. Raises ValueError
    on any present-but-malformed value so the handler can reject it explicitly."""
    return (
        _q_positive(qp, "rate", float, "number"),
        _q_positive(qp, "duration", float, "number"),
        _q_positive(qp, "max_records", int, "integer"),
    )


def _resolve_stream_limit(spec: DatasetSpec, max_records: Optional[int],
                          duration: Optional[float], rec_ceiling: int) -> float | int:
    """How many records to serve, always under the server's record ceiling.
    Explicit `max_records` wins (clamped); a `duration` with no record cap
    serves up to the ceiling (the clock usually cuts it first); otherwise serve
    the spec's natural length, capped so a plain connect can't run away."""
    if max_records is not None:
        return min(max_records, rec_ceiling)
    if duration is not None:
        return rec_ceiling
    return min(effective_row_count(spec), STREAM_DEFAULT_CAP)


@app.websocket("/stream")
async def stream(websocket: WebSocket):
    global _active_ws_sessions
    await websocket.accept()
    # Access gate (ADR-0018, widened ADR-0025). Checked after accept so we can
    # hand the client a readable error frame, not a bare close. The *reason*
    # comes from the same helper the HTTP middleware uses: a remote caller on
    # a server with no token configured needs to be told that, not sent
    # hunting for a token that doesn't exist.
    ws_denied = auth.ws_denied_reason(websocket)
    if ws_denied:
        await websocket.send_json({"error": ws_denied})
        await websocket.close(code=1008)  # policy violation
        return

    # Concurrency ceiling (ADR-0019): cap how many live sockets one process
    # holds so a flood of connections can't exhaust the single-process server.
    # Counted only for admitted sessions and released in `finally` below on every
    # exit path (handshake timeout, bad spec, disconnect, or clean end-of-stream).
    if _active_ws_sessions >= _max_ws_sessions():
        await websocket.send_json(
            {"error": "server is at its live-stream capacity; try again shortly"})
        await websocket.close(code=1013)  # try again later
        return
    _active_ws_sessions += 1
    try:
        await _serve_stream(websocket)
    finally:
        _active_ws_sessions -= 1


async def _serve_stream(websocket: WebSocket) -> None:
    # Opening handshake (ADR-0019): wait only a bounded time for the client's
    # spec frame. Without this, an idle socket that connects and never sends a
    # spec is held open forever — and with auth off by default and the Docker
    # image bound to 0.0.0.0, that is an *unauthenticated* connection-exhaustion
    # DoS on the exact surface ADR-0018 set out to harden.
    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=_handshake_timeout())
    except asyncio.TimeoutError:
        await websocket.send_json(
            {"error": "timed out waiting for the opening spec frame"})
        await websocket.close(code=1008)  # policy violation
        return
    except WebSocketDisconnect:
        return

    try:
        spec = load_spec(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        await websocket.send_json({"error": f"invalid spec: {e}"})
        await websocket.close()
        return

    if spec.tables:
        await websocket.send_json(
            {"error": "multi-table specs can't be streamed (one file per table); use the CLI"})
        await websocket.close()
        return

    if spec.entity and spec.entity.observers:
        await websocket.send_json(
            {"error": "observer specs produce one feed per observer; a socket carries one. "
                      "Stream a single observer by removing the others from the spec."})
        await websocket.close()
        return
    try:
        rec_enc = get_record_encoder(spec.output.format)
    except KeyError as e:
        # Whole-file formats (sql, parquet, …) have no per-record framing.
        await websocket.send_json({"error": str(e)})
        await websocket.close()
        return

    try:
        rate, duration, max_records = _parse_stream_params(websocket.query_params)
    except ValueError as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close()
        return

    rec_ceiling, sec_ceiling = _stream_ceilings()
    limit = _resolve_stream_limit(spec, max_records, duration, rec_ceiling)
    # Always bound the socket by the seconds ceiling — even a client that sent
    # no `duration` gets cut at the ceiling, so one held-open connection can't
    # stream to the record cap unattended (the WS had no time bound before).
    duration = min(duration, sec_ceiling) if duration else sec_ceiling
    interval = 1.0 / rate if rate and rate > 0 else 0.0

    start = time.monotonic()
    sent = 0
    try:
        for row in iter_records(spec, limit=limit):
            if duration and (time.monotonic() - start) >= duration:
                break
            if interval and sent:
                await asyncio.sleep(interval)  # async pacing: never blocks the loop
            # Per-record encoders append a trailing newline (ndjson/csv line);
            # one clean record per frame reads better over a socket.
            await websocket.send_text(rec_enc(spec, row).decode("utf-8").rstrip("\n"))
            sent += 1
    except WebSocketDisconnect:
        return  # client hung up mid-stream — stop generating, we're done

    await websocket.close()  # closing the socket marks end-of-stream


# ── Streaming jobs: push to a broker/endpoint, bounded + stoppable ───
# Model 2 of the Stream tab (ADR-0017): the browser can't hold a Kafka/MQTT
# connection, so the server runs the push as a background job the UI drives
# via Start / Status / Stop. Every job is capped (records AND seconds) under a
# hard ceiling — the guardrail. WebSocket "live view" (Model 1, /stream above)
# stays browser-held; this is only for the push sinks.

class StreamJobRequest(BaseModel):
    spec: DatasetSpec
    max_records: int    # required cap (guardrail) — server-clamped to a ceiling
    max_seconds: float  # required cap (guardrail) — server-clamped to a ceiling
    rate: Optional[float] = None  # records/sec pacing; None = as fast as the sink drains


@app.post("/stream/jobs")
def stream_job_start(req: StreamJobRequest):
    from . import stream_jobs
    try:
        job = stream_jobs.start_job(req.spec, max_records=req.max_records,
                                    max_seconds=req.max_seconds, rate=req.rate)
    except stream_jobs.TooManyJobs as e:
        # 429, not 422: the spec is fine, the server is busy. The distinction
        # is what tells a client to retry rather than to edit and resubmit.
        raise HTTPException(status_code=429, detail=str(e))
    except stream_jobs.StreamJobError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return job.public()


@app.get("/stream/jobs")
def stream_jobs_list():
    from . import stream_jobs
    return {"jobs": stream_jobs.list_jobs()}


@app.get("/stream/jobs/{job_id}")
def stream_job_status(job_id: str):
    from . import stream_jobs
    try:
        return stream_jobs.get_job(job_id).public()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no such stream job '{job_id}'")


@app.delete("/stream/jobs/{job_id}")
def stream_job_stop(job_id: str):
    from . import stream_jobs
    try:
        return stream_jobs.stop_job(job_id).public()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no such stream job '{job_id}'")


# ── Spec library (presets + saved schemas) ──────────────────────────

@app.get("/library")
def library_list():
    """Gallery source: preset + saved spec summaries (never hardcoded)."""
    return {"specs": library.list_specs()}


@app.get("/library/{name}")
def library_get(name: str):
    """Full spec for the UI to load into the builder."""
    try:
        return library.load_named(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/library/{name}")
def library_save(name: str, spec: DatasetSpec):
    """Save the current spec under a name (validated before it persists)."""
    try:
        library.save_named(name, spec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"saved": name}


@app.delete("/library/{name}")
def library_delete(name: str):
    """Delete a saved schema (presets are read-only)."""
    try:
        library.delete_named(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"deleted": name}


# ── Natural-language spec drafting (ADR-0010) ────────────────────────

class DraftRequest(BaseModel):
    description: str
    # Optional bring-your-own-key: pasted in the UI, sent per request, never
    # stored server-side or logged. When absent, the server's own key is used.
    api_key: Optional[str] = None
    provider: Optional[str] = None  # anthropic|openai|google; auto-detected if omitted


@app.post("/draft")
def draft(req: DraftRequest, request: Request):
    """Draft a spec from plain English for the user to review/edit (INV-1).

    The key can come from the server env (ANTHROPIC_API_KEY / OPENAI_API_KEY /
    GOOGLE_API_KEY) or be pasted into the UI and sent with the request. A
    request key is used for that call only — never written to disk or logged.

    This is the only route that spends money, so it is also the only one with
    a cost budget (ADR-0030): a prompt ceiling and a per-client rate, checked
    before anything reaches a provider. The limits apply to a pasted key too —
    the server is still acting as the proxy either way.
    """
    from . import draft_budget

    description = req.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
    try:
        draft_budget.check_prompt(description)
        draft_budget.check_rate(request.client.host if request.client else None)
    except draft_budget.DraftRefused as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    from . import nl

    key = (req.api_key or "").strip() or None
    provider = (req.provider or "").strip().lower() or None
    if provider and provider not in nl.PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider '{provider}'; choose one of {list(nl.PROVIDERS)}")
    if key and not provider:
        provider = nl.infer_provider(key)
        if provider is None:
            raise HTTPException(
                status_code=400,
                detail="couldn't tell which service this API key is for — "
                       "pick Anthropic, OpenAI, or Google in the dropdown")
    if not key and nl.active_provider() is None:
        raise HTTPException(
            status_code=503,
            detail="natural-language drafting needs an LLM API key: paste one in the "
                   "UI, or set ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY on "
                   "the server (install the matching extra: chaff[nl] / chaff[nl-openai] "
                   "/ chaff[nl-google])",
        )
    try:
        return nl.draft_spec(description, provider=provider, api_key=key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not draft a valid spec: {e}")


# ── Open-source attribution ──────────────────────────────────────────

def _bundled_dir() -> Path:
    """Root holding bundled data files: _MEIPASS when frozen (ADR-0014), else
    the repo root (parent of api/)."""
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent.parent


def _licenses_page(title: str, intro_html: str, body_html: str) -> str:
    """A tiny self-contained HTML shell for the attribution page. No external
    assets (matches the build-free UI, ADR-0006) and a link back to the app, so
    a non-technical user who clicks the footer link always lands on a readable
    page — never a raw text blob or a bare JSON error that reads as 'empty'."""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — chaff</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.5 system-ui, sans-serif; margin: 0;
          background: #0f1115; color: #e6e6e6; }}
  header {{ padding: 16px 20px; border-bottom: 1px solid #2a2e37; }}
  header a {{ color: #9aa7ff; text-decoration: none; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  main {{ padding: 16px 20px; }}
  p.intro {{ color: #9aa0aa; max-width: 70ch; }}
  pre {{ white-space: pre-wrap; word-wrap: break-word; background: #161a22;
         border: 1px solid #2a2e37; border-radius: 8px; padding: 16px;
         overflow-x: auto; font-size: 12.5px; }}
</style></head>
<body>
  <header><a href="/">← back to chaff</a></header>
  <main>
    <h1>{title}</h1>
    <p class="intro">{intro_html}</p>
    {body_html}
  </main>
</body></html>"""


@app.get("/licenses", response_class=HTMLResponse)
def licenses():
    """Third-party attribution for bundled dependencies (MIT/BSD/Apache-2.0
    NOTICE). chaff's own license is MIT — see the LICENSE file. Rendered as a
    readable page (not a raw text dump) so the footer link never looks broken."""
    path = _bundled_dir() / "THIRD-PARTY-NOTICES.txt"
    if not path.is_file():
        # Missing notices are a packaging gap, not a user error — say so plainly
        # instead of returning a bare JSON 404 that shows up as an empty tab.
        page = _licenses_page(
            "Open-source licenses",
            "Third-party notices aren't bundled in this build. chaff itself is "
            "MIT licensed. If you're seeing this in the packaged app, grab a "
            "current build — the notices are generated and bundled at release "
            "time (<code>make notices</code> regenerates them from source).",
            "")
        return HTMLResponse(page, status_code=404)
    notices = html.escape(path.read_text(encoding="utf-8", errors="replace"))
    return _licenses_page(
        "Open-source licenses",
        "chaff is distributed under the MIT License. Bundled builds include the "
        "open-source dependencies below, each with its license text.",
        f"<pre>{notices}</pre>")


# The web UI is a static, build-free page served by this same process
# (ADR-0006). Mounted last so it doesn't shadow the API routes above.
# When frozen by PyInstaller (the Windows .exe, ADR-0014), pure-Python modules
# live in the PYZ archive so __file__ isn't a real on-disk path; the UI is
# bundled to _MEIPASS/api/static instead. Source runs are unchanged.
if getattr(sys, "frozen", False):
    _STATIC_DIR = Path(sys._MEIPASS) / "api" / "static"
else:
    _STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
