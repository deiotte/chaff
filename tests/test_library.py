"""Spec library tests: storage module + API endpoints."""

import json

import pytest

from chaff import library


@pytest.fixture
def libdirs(tmp_path, monkeypatch):
    presets, saved = tmp_path / "presets", tmp_path / "saved"
    presets.mkdir()
    saved.mkdir()
    monkeypatch.setenv("CHAFF_PRESETS_DIR", str(presets))
    monkeypatch.setenv("CHAFF_LIBRARY_DIR", str(saved))
    (presets / "demo.json").write_text(json.dumps({
        "name": "demo", "description": "a preset", "rows": 10,
        "columns": [{"name": "id", "generator": "row_id"}], "output": {"format": "csv"}}))
    return presets, saved


def _spec(name="mine", rows=5):
    return {"name": name, "rows": rows,
            "columns": [{"name": "a", "generator": "uuid"}], "output": {"format": "json"}}


# ── storage module ───────────────────────────────────────────────────

def test_lists_presets_with_summaries(libdirs):
    specs = library.list_specs()
    demo = next(s for s in specs if s["name"] == "demo")
    assert demo["source"] == "preset"
    assert demo["rows"] == 10 and demo["columns"] == 1 and demo["format"] == "csv"


def test_save_load_delete_roundtrip(libdirs):
    library.save_named("mine", _spec())
    assert library.load_named("mine")["rows"] == 5
    assert any(s["name"] == "mine" and s["source"] == "saved" for s in library.list_specs())
    library.delete_named("mine")
    with pytest.raises(KeyError):
        library.load_named("mine")


def test_saved_shadows_preset_on_load(libdirs):
    library.save_named("demo", _spec("demo", rows=99))
    assert library.load_named("demo")["rows"] == 99


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "foo.json", "", "x y"])
def test_rejects_unsafe_names(libdirs, bad):
    with pytest.raises(ValueError):
        library.load_named(bad)


def test_save_rejects_invalid_spec(libdirs):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        library.save_named("bad", {"name": "x"})  # missing rows/columns/output
    assert not (libdirs[1] / "bad.json").exists()  # nothing persisted


def test_delete_missing_raises(libdirs):
    with pytest.raises(KeyError):
        library.delete_named("nope")


# ── API endpoints ────────────────────────────────────────────────────

def test_library_api_roundtrip(libdirs):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from api.main import app
    client = TestClient(app)

    listed = client.get("/library").json()["specs"]
    assert any(s["name"] == "demo" for s in listed)

    assert client.get("/library/demo").json()["rows"] == 10
    assert client.get("/library/missing").status_code == 404

    assert client.post("/library/saved_one", json=_spec("saved_one")).status_code == 200
    assert client.get("/library/saved_one").json()["rows"] == 5
    assert client.delete("/library/saved_one").json() == {"deleted": "saved_one"}
    assert client.get("/library/saved_one").status_code == 404
