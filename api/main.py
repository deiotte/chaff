"""chaff API — a thin transport for specs.

The API never contains generation logic; it builds/validates specs and
hands them to the engine (INV-1). Run: uvicorn api.main:app --reload
(requires extras: pip install -e '.[api]').
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from chaff import __version__
from chaff.engine import generate_rows
from chaff.formats import get_encoder, get_extension, list_formats
from chaff.generators import list_generators
from chaff.sinks import list_sinks
from chaff.spec import DatasetSpec

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
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    }


@app.post("/preview")
def preview(spec: DatasetSpec, limit: int = 10):
    """First N rows for the UI's live preview pane."""
    limit = max(1, min(limit, PREVIEW_MAX_ROWS))
    capped = spec.model_copy(update={"rows": min(spec.rows, limit)})
    try:
        return {"rows": generate_rows(capped)}
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
    _enforce_row_limit(spec.rows)
    try:
        rows = generate_rows(spec)
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


# The web UI is a static, build-free page served by this same process
# (ADR-0006). Mounted last so it doesn't shadow the API routes above.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")
