"""chaff API — a thin transport for specs.

The API never contains generation logic; it builds/validates specs and
hands them to the engine (INV-1). Run: uvicorn api.main:app --reload
(requires extras: pip install -e '.[api]').
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chaff import __version__, library
from chaff.engine import effective_row_count, generate_records
from chaff.formats import get_encoder, get_extension, list_formats
from chaff.generators import list_generators
from chaff.sinks import list_sinks
from chaff.spec import DatasetSpec
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
        "generators": list_generators(),
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


# The web UI is a static, build-free page served by this same process
# (ADR-0006). Mounted last so it doesn't shadow the API routes above.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
