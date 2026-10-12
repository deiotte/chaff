"""Hidden columns (ADR-0044): generated, readable by later columns and child
tables, never emitted. The point is an answer key that can be built from the
clean values without those values leaking into the dirty file it scores, so
every exit from the engine is checked: rows, encoders, multi-table views,
entity snapshots, the API preview and the live WebSocket."""

import io
import json
import zipfile

from fastapi.testclient import TestClient

from api.main import app
from chaff.engine import encode_tables, encode_view, generate_rows, generate_tables, iter_entity_rows
from chaff.formats import get_encoder
from chaff.spec import load_spec


def plain(fmt="csv"):
    return {
        "name": "p", "seed": 5, "rows": 6,
        "columns": [
            {"name": "id", "generator": "row_id"},
            {"name": "secret", "generator": "full_name", "hidden": True},
            {"name": "shout", "generator": "derived", "params": {"expr": "secret + '!'"}},
        ],
        "output": {"format": fmt},
    }


def test_hidden_cells_feed_later_columns_but_never_leave():
    rows = generate_rows(load_spec(plain()))
    assert all(set(r) == {"id", "shout"} and r["shout"].endswith("!") for r in rows)


def test_encoders_never_name_a_hidden_column():
    spec = load_spec(plain())
    csv = get_encoder("csv")(encode_view(spec), generate_rows(spec)).decode()
    assert csv.splitlines()[0] == "id,shout" and "secret" not in csv


def test_child_tables_read_a_parents_hidden_cells():
    d = plain()
    d["tables"] = [{"name": "kids", "rows": 6, "columns": [
        {"name": "p_id", "generator": "fk", "params": {"table": "p", "column": "id", "each": True}},
        {"name": "parent_secret", "generator": "parent", "params": {"via": "p_id", "column": "secret"}},
    ]}]
    spec = load_spec(d)
    t = generate_tables(spec)
    assert all("secret" not in r for r in t["p"])
    assert [k["parent_secret"] + "!" for k in t["kids"]] == [p["shout"] for p in t["p"]]
    files = {name: payload.decode() for name, _, payload in encode_tables(spec)}
    assert "secret" not in files["p"].splitlines()[0]


def test_entity_snapshots_drop_hidden_state():
    spec = load_spec({
        "name": "e", "seed": 1,
        "entity": {"count": 2, "ticks": 2},
        "columns": [
            {"name": "x", "generator": "int_range", "hidden": True},
            {"name": "y", "generator": "derived", "params": {"expr": "x * 2"}},
        ],
        "output": {"format": "csv"},
    })
    rows = list(iter_entity_rows(spec))
    assert rows and all("x" not in r and "y" in r for r in rows)
    assert "x" not in [c.name for c in encode_view(spec).columns]


def test_api_preview_and_download_hide_it():
    client = TestClient(app)
    rows = client.post("/preview?limit=3", json=plain()).json()["rows"]
    assert rows and all("secret" not in r for r in rows)
    body = client.post("/generate", json=plain()).content.decode()
    assert "secret" not in body.splitlines()[0]
    d = plain()
    d["tables"] = [{"name": "kids", "rows": 2, "columns": [
        {"name": "p_id", "generator": "fk", "params": {"table": "p", "column": "id"}}]}]
    z = zipfile.ZipFile(io.BytesIO(client.post("/generate", json=d).content))
    assert "secret" not in z.read("p.csv").decode().splitlines()[0]


def test_websocket_stream_hides_it():
    """The /stream socket used to encode with the raw spec; a hidden column
    would have shown up there as an empty CSV field."""
    client = TestClient(app)
    with client.websocket_connect("/stream?max_records=3") as ws:
        ws.send_text(json.dumps(plain()))
        lines = [ws.receive_text() for _ in range(3)]
    assert all(line.count(",") == 1 for line in lines), lines


def test_no_hidden_columns_means_rows_pass_through_untouched():
    spec = load_spec({**plain(), "columns": plain()["columns"][:1]})
    assert encode_view(spec) is spec
