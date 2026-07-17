"""chaff API — a thin transport for specs.

The API never contains generation logic; it builds/validates specs and
hands them to the engine (INV-1). Run: uvicorn api.main:app --reload
(requires extras: pip install -e '.[api]').
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import auth

from chaff import __version__, library
from chaff.engine import effective_row_count, generate_records, iter_records
from chaff.formats import get_encoder, get_extension, get_record_encoder, list_formats
from chaff.generators import (
    list_generator_examples,
    list_generator_groups,
    list_generators,
)
from chaff.sinks import list_sinks
from chaff.spec import DatasetSpec, load_spec
from chaff.updaters import list_updaters

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
    }


def _reject_multitable(spec: DatasetSpec) -> None:
    if spec.tables:
        raise HTTPException(
            status_code=400,
            detail="multi-table specs aren't supported over the API yet "
                   "(one request = one file); generate them with the CLI: chaff generate",
        )


@app.post("/preview")
def preview(spec: DatasetSpec, limit: int = 10):
    """First N rows for the UI's live preview pane."""
    _reject_multitable(spec)
    limit = max(1, min(limit, PREVIEW_MAX_ROWS))
    if spec.entity:
        # Bound preview work for entity specs: a few entities over a few ticks.
        capped_entity = spec.entity.model_copy(update={
            "count": min(spec.entity.count, limit), "ticks": min(spec.entity.ticks, limit)})
        capped = spec.model_copy(update={"entity": capped_entity})
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
    _reject_multitable(spec)
    _enforce_row_limit(effective_row_count(spec))
    try:
        rows = generate_records(spec)
        payload = get_encoder(spec.output.format)(spec, rows)
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
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
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
    # Auth gate (ADR-0018): a no-op unless CHAFF_API_TOKEN is set. Checked after
    # accept so we can hand the client a readable error frame, not a bare close.
    if not auth.ws_token_ok(websocket):
        await websocket.send_json({"error": "missing or invalid API token"})
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


@app.post("/stream/jobs", dependencies=[Depends(auth.require_token)])
def stream_job_start(req: StreamJobRequest):
    from . import stream_jobs
    try:
        job = stream_jobs.start_job(req.spec, max_records=req.max_records,
                                    max_seconds=req.max_seconds, rate=req.rate)
    except stream_jobs.StreamJobError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return job.public()


@app.get("/stream/jobs", dependencies=[Depends(auth.require_token)])
def stream_jobs_list():
    from . import stream_jobs
    return {"jobs": stream_jobs.list_jobs()}


@app.get("/stream/jobs/{job_id}", dependencies=[Depends(auth.require_token)])
def stream_job_status(job_id: str):
    from . import stream_jobs
    try:
        return stream_jobs.get_job(job_id).public()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no such stream job '{job_id}'")


@app.delete("/stream/jobs/{job_id}", dependencies=[Depends(auth.require_token)])
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
def draft(req: DraftRequest):
    """Draft a spec from plain English for the user to review/edit (INV-1).

    The key can come from the server env (ANTHROPIC_API_KEY / OPENAI_API_KEY /
    GOOGLE_API_KEY) or be pasted into the UI and sent with the request. A
    request key is used for that call only — never written to disk or logged.
    """
    description = req.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="description is required")
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
