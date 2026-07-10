"""API transport tests. Skipped when the `api` extra isn't installed so
`make check` on a core-only checkout stays green; CI installs the extras."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from chaff.engine import generate_rows  # noqa: E402
from chaff.formats import get_encoder  # noqa: E402
from chaff.spec import load_spec  # noqa: E402

client = TestClient(app)


def spec_dict(**over):
    d = {
        "name": "widgets",
        "seed": 7,
        "rows": 25,
        "columns": [
            {"name": "id", "generator": "row_id"},
            {"name": "who", "generator": "full_name"},
        ],
        "output": {"format": "csv"},
    }
    d.update(over)
    return d


def test_registry_lists_plugins():
    body = client.get("/registry").json()
    assert "full_name" in body["generators"]
    assert "xlsx" in body["formats"]
    assert "file" in body["sinks"]


def test_preview_caps_rows():
    body = client.post("/preview?limit=3", json=spec_dict(rows=1000)).json()
    assert len(body["rows"]) == 3


def test_generate_streams_a_file_download():
    d = spec_dict()
    resp = client.post("/generate", json=d)
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="widgets.csv"'
    assert resp.headers["content-type"].startswith("text/csv")
    # Body is byte-identical to what the encoder produces for the same spec.
    spec = load_spec(d)
    assert resp.content == get_encoder("csv")(spec, generate_rows(spec))


def test_generate_xlsx_download_media_type():
    resp = client.post("/generate", json=spec_dict(output={"format": "xlsx"}))
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="widgets.xlsx"'
    assert resp.content[:2] == b"PK"


def test_generate_rejects_oversized_request(monkeypatch):
    monkeypatch.setenv("CHAFF_API_MAX_ROWS", "5")
    resp = client.post("/generate", json=spec_dict(rows=10))
    assert resp.status_code == 413
    assert "caps inline generation" in resp.json()["detail"]


def test_generate_unknown_format_is_400():
    resp = client.post("/generate", json=spec_dict(output={"format": "nope"}))
    assert resp.status_code == 400
