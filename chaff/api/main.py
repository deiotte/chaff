"""chaff API (Phase 1 skeleton — Claude Code builds this out).

The API is a thin transport for specs. It must never contain generation
logic; if you find yourself writing generation code here, it belongs in
src/chaff. Run: uvicorn api.main:app --reload  (requires extras: pip
install -e '.[api]')
"""

from fastapi import FastAPI, HTTPException

from chaff import __version__
from chaff.engine import generate_rows
from chaff.formats import get_encoder, list_formats
from chaff.generators import list_generators
from chaff.sinks import list_sinks
from chaff.spec import DatasetSpec

app = FastAPI(title="chaff", version=__version__)


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
    capped = spec.model_copy(update={"rows": min(spec.rows, limit)})
    return {"rows": generate_rows(capped)}


@app.post("/generate")
def generate(spec: DatasetSpec):
    """Generate and return encoded payload inline (small datasets).
    TODO(claude-code): streaming download for large datasets; job queue
    for sinks that run over time (kafka/http with rate control)."""
    try:
        rows = generate_rows(spec)
        payload = get_encoder(spec.output.format)(spec, rows)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"bytes": len(payload), "payload": payload.decode("utf-8", errors="replace")}
