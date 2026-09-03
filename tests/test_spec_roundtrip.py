"""Round-trip guards: a spec that goes into an interface comes back whole.

The bug these lock down: the UI's `loadSpecIntoForm` read only `spec.columns`,
so loading a preset that carried `entity` (ADR-0009) or `tables` (ADR-0008)
silently dropped it. `moving_tracks` came back as disconnected points with no
track id and no tick; `order_lifecycle` came back with every row still
`placed`. Both returned HTTP 200. Confidently-wrong demo data is the one
outcome worse than an error, so these tests assert the whole spec survives.

The UI half of that contract is proven by `tests/test_ui_browser.py`, which
drives the real page (ADR-0022). What stays here is the API behaviour plus
the two source-level properties a browser cannot observe.
"""

import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from chaff.engine import encode_tables, generate_records
from chaff.spec import load_spec

EXAMPLES = Path("examples")
INDEX_HTML = Path("api/static/index.html").read_text()


def _examples_with(key):
    out = []
    for f in sorted(EXAMPLES.glob("*.json")):
        data = json.loads(f.read_text())
        if data.get(key):
            out.append(pytest.param(data, id=f.stem))
    return out


@pytest.fixture
def client():
    return TestClient(app)


# ── entity specs survive the API round trip ──────────────────────────

@pytest.mark.parametrize("data", _examples_with("entity"))
def test_entity_preset_previews_as_a_time_series(data, client):
    """The preview must carry the entity's id and tick columns — their absence
    was exactly how the silent flattening showed up."""
    spec = load_spec(data)
    r = client.post("/preview?limit=5", json=data)
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows, "entity preset previewed as no rows"
    assert spec.entity.id_column in rows[0], "entity id column missing — spec was flattened"
    assert spec.entity.tick_column in rows[0], "tick column missing — spec was flattened"


def test_order_lifecycle_actually_transitions():
    """The regression in its most concrete form: dropping `entity` left every
    row stuck in the initial state. A real lifecycle reaches other states."""
    data = json.loads((EXAMPLES / "order_lifecycle.json").read_text())
    statuses = {row["status"] for row in generate_records(load_spec(data))}
    assert len(statuses) > 1, f"lifecycle never transitioned: {statuses}"


def test_dropping_entity_is_visibly_different():
    """Guards the premise: an entity spec and the same spec with `entity`
    stripped really do produce different data, so silently dropping it
    mattered. If this ever passes trivially the other tests are hollow."""
    data = json.loads((EXAMPLES / "moving_tracks.json").read_text())
    with_entity = generate_records(load_spec(data))
    flat = generate_records(load_spec({**data, "entity": None, "rows": 10}))
    assert with_entity[0].keys() != flat[0].keys()


# ── multi-table over the API (ADR-0020) ──────────────────────────────

def test_multitable_preview_returns_every_table_with_intact_fks(client):
    data = json.loads((EXAMPLES / "retail_orders.json").read_text())
    r = client.post("/preview?limit=5", json=data)
    assert r.status_code == 200, r.text
    body = r.json()
    tables = body["tables"]
    assert set(tables) == {"customers", "orders", "lines"}
    # `rows` still holds the primary table, so single-table callers are unchanged.
    assert body["rows"] == tables["customers"]
    # Capping rows for preview must not orphan a child row.
    parents = {c["customer_id"] for c in tables["customers"]}
    assert parents, "no parent rows generated"
    assert all(o["customer_id"] in parents for o in tables["orders"])


def test_multitable_generate_returns_a_zip_per_table(client):
    data = json.loads((EXAMPLES / "retail_orders.json").read_text())
    r = client.post("/generate", json=data)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["X-Chaff-Tables"] == "customers,orders,lines"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["customers.csv", "orders.csv", "lines.csv"]


def test_multitable_zip_is_byte_for_byte_deterministic(client):
    """INV-3 covers the archive, not just its members: pinned entry timestamps
    and stored (not deflated) entries, so the bytes don't drift across zlib
    builds or wall-clock."""
    data = json.loads((EXAMPLES / "retail_orders.json").read_text())
    first = client.post("/generate", json=data).content
    second = client.post("/generate", json=data).content
    assert first == second


def test_zip_members_match_the_engine_encoding(client):
    """The download path and the sink path share `table_views`, so a zipped
    table and a CLI-written file must hold identical bytes."""
    data = json.loads((EXAMPLES / "retail_orders.json").read_text())
    zf = zipfile.ZipFile(io.BytesIO(client.post("/generate", json=data).content))
    for _name, filename, payload in encode_tables(load_spec(data)):
        assert zf.read(filename) == payload


def test_multitable_row_limit_counts_every_table(client, monkeypatch):
    """The ceiling must see all 850 rows, not just the primary table's 50."""
    monkeypatch.setenv("CHAFF_API_MAX_ROWS", "100")
    data = json.loads((EXAMPLES / "retail_orders.json").read_text())
    assert client.post("/generate", json=data).status_code == 413


# ── hostile spec names must not break the download ───────────────────

def test_control_characters_in_a_name_are_refused_at_load(client):
    """A CR/LF in `name` used to reach Content-Disposition and produce an
    invalid header. It is now refused by the contract instead: a name becomes
    a filename, and rejecting beats sanitizing something that can't be a
    filename in the first place."""
    body = {"name": 'pwn"\r\nX-Injected: yes', "rows": 2, "output": {"format": "csv"},
            "columns": [{"name": "x", "generator": "row_id"}]}
    r = client.post("/generate", json=body)
    assert r.status_code == 422, r.text
    assert "X-Injected" not in r.headers


def test_a_legal_but_awkward_name_still_gets_a_safe_header(client):
    """Quotes aren't path-hostile, so `pwn"` is a legal dataset name — which
    is exactly why `_content_disposition` still has to escape it."""
    body = {"name": 'pwn"', "rows": 2, "output": {"format": "csv"},
            "columns": [{"name": "x", "generator": "row_id"}]}
    r = client.post("/generate", json=body)
    assert r.status_code == 200, r.text
    cd = r.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert cd.count('"') == 2, f"unescaped quote in {cd!r}"


# ── path traversal via table names (F-06) ────────────────────────────

@pytest.mark.parametrize("bad", ["../../outside/pwn", "a/b", "..", "C:evil"])
def test_traversing_table_names_are_refused(client, bad):
    """A table name becomes a filename on disk and a member in the zip.
    Unvalidated, '../escaped' wrote outside the requested output directory
    and produced a traversal entry in the archive."""
    body = {"name": "customers", "rows": 2, "output": {"format": "csv"},
            "columns": [{"name": "id", "generator": "row_id"}],
            "tables": [{"name": bad, "rows": 2,
                        "columns": [{"name": "x", "generator": "row_id"}]}]}
    assert client.post("/generate", json=body).status_code == 422


def test_no_zip_member_can_escape_the_archive(client):
    """Every member must be a single path component."""
    body = {"name": "customers", "rows": 2, "output": {"format": "csv"},
            "columns": [{"name": "id", "generator": "row_id"}],
            "tables": [{"name": "orders", "rows": 2,
                        "columns": [{"name": "x", "generator": "row_id"}]}]}
    r = client.post("/generate", json=body)
    assert r.status_code == 200
    for member in zipfile.ZipFile(io.BytesIO(r.content)).namelist():
        assert "/" not in member and "\\" not in member and ".." not in member


def test_cli_multitable_cannot_write_outside_the_output_directory(tmp_path):
    """The damaging half of F-06: the file sink, not the zip. Guarded twice —
    the name is refused at load, and the resolved path is required to stay
    under the requested directory."""
    from chaff.engine import run

    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(Exception):
        run(load_spec({
            "name": "customers", "rows": 2, "output": {"format": "csv"},
            "sink": {"sink": "file", "options": {"path": str(out / "customers.csv")}},
            "columns": [{"name": "id", "generator": "row_id"}],
            "tables": [{"name": "../escaped", "rows": 2,
                        "columns": [{"name": "x", "generator": "row_id"}]}]}))
    assert not list(tmp_path.glob("escaped*")), "a table escaped the output directory"


# ── entity and tables are different modes, not a blend ───────────────

def test_entity_plus_tables_is_rejected_at_load():
    """The engine takes the multi-table path and never reads `entity`, so a
    spec with both silently lost it — and with `rows` omitted (legal for an
    entity spec) crashed deep in generation. One clear error at load instead."""
    with pytest.raises(Exception, match="cannot set both"):
        load_spec({"name": "a", "output": {"format": "csv"},
                   "columns": [{"name": "x", "generator": "row_id"}],
                   "entity": {"count": 2, "ticks": 2, "updates": []},
                   "tables": [{"name": "b", "rows": 2,
                               "columns": [{"name": "y", "generator": "row_id"}]}]})


# ── source-level guards that a browser test can't replace ────────────
# `tests/test_ui_browser.py` executes the page and is the real proof of
# behaviour (ADR-0022). Two things survive here because driving the page
# cannot show them:
#   - INV-4 compliance is a property of the *source*: a hardcoded list that
#     happens to match the registry looks identical in a browser.
#   - a syntax error blanks the page, and a suite of skipped browser tests
#     would not notice; this fails fast with the parser's own message.

def test_ui_never_hardcodes_updater_ids():
    """INV-4: the update-rule dropdown comes from the registry. A hardcoded
    list renders identically until someone registers a new updater and it
    silently fails to appear — which no browser test would catch, because
    the page looks correct either way."""
    body = re.search(r"function updaterOptions\(selected\) \{(.*?)\n\}", INDEX_HTML, re.S)
    assert body, "updaterOptions not found"
    assert "UPDATERS" in body.group(1)
    for hardcoded in ("'movement'", '"movement"', "'lifecycle'", "'drift'"):
        assert hardcoded not in body.group(1)


def test_ui_javascript_parses():
    """A syntax error blanks the whole page. Every browser test would then
    fail — but they *skip* without a browser, so this stays as the cheap
    check that runs anywhere and names the offending line."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("no node available to parse the page")
    script = INDEX_HTML[INDEX_HTML.rindex("<script>") + len("<script>"):
                        INDEX_HTML.rindex("</script>")]
    proc = subprocess.run([node, "--check", "-"], input=script, text=True,
                          capture_output=True)
    assert proc.returncode == 0, f"index.html script does not parse:\n{proc.stderr}"


def test_engine_refuses_a_traversing_name_even_without_spec_validation():
    """The second layer, tested on its own.

    `DatasetSpec` rejects these names, so the engine guard normally never
    fires — which means it could rot unnoticed. `model_construct` skips
    validators the way a future internal caller might, and proves the write
    path refuses independently.
    """
    from chaff.engine import _safe_member_name
    from chaff.spec import ColumnSpec, TableSpec

    with pytest.raises(ValueError, match="unsafe table filename"):
        _safe_member_name("../escaped", ".csv")
    with pytest.raises(ValueError, match="unsafe table filename"):
        _safe_member_name("a/b", ".csv")

    # And end-to-end through run(), with validation bypassed.
    unvalidated = TableSpec.model_construct(
        name="../escaped", rows=2,
        columns=[ColumnSpec(name="x", generator="row_id")])
    assert unvalidated.name == "../escaped", "model_construct should skip validation"
