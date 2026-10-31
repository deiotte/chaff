"""Which sinks can run in this install, and how to fix the ones that can't
(ADR-0047).

Covers four things:
- the packaging split: http/mqtt clients in core, Kafka opt-in, `all` complete;
- the registry's declared requirements, checked against what each sink
  actually imports lazily, so a new sink can't skip declaring its client;
- `sink_status()` when a client is missing;
- every place that status reaches a person: the CLI, `/registry`, and
  `/stream/jobs`.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import chaff.sinks as sinks
from chaff.cli import app as cli_app

ROOT = Path(__file__).resolve().parent.parent
SINK_DIR = ROOT / "src" / "chaff" / "sinks"


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib")  # stdlib on 3.11+
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _names(reqs: list[str]) -> set[str]:
    from packaging.requirements import Requirement
    return {Requirement(r).name.lower() for r in reqs}


# ── Packaging: what a plain install gets ─────────────────────────────

def test_pure_python_sink_clients_are_core_dependencies():
    core = _names(_pyproject()["project"]["dependencies"])
    assert {"httpx", "paho-mqtt"} <= core, "http/mqtt must work after a plain install"


def test_kafka_never_enters_the_core_install():
    """A compiled wheel with no build for some platforms. In core it could
    break the whole install, CLI included (ADR-0047)."""
    proj = _pyproject()["project"]
    assert "confluent-kafka" not in _names(proj["dependencies"])
    assert _names(proj["optional-dependencies"]["kafka"]) == {"confluent-kafka"}


def test_streaming_alias_still_installs_what_it_always_did():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert _names(extras["streaming"]) == {"httpx", "paho-mqtt", "confluent-kafka"}


def test_all_extra_is_every_user_facing_extra():
    """`all` is a hand-written list (pip can't reference sibling extras
    portably), so this keeps it from drifting."""
    extras = _pyproject()["project"]["optional-dependencies"]
    user_facing = ("api", "formats-extra", "kafka", "nl", "nl-openai", "nl-google")
    want = set().union(*(_names(extras[e]) for e in user_facing))
    assert _names(extras["all"]) == want


# ── Registry: declared requirements match real imports ───────────────

def _lazy_third_party_imports() -> dict[str, set[str]]:
    """sink_id -> top-level third-party modules imported inside that sink's
    registered function, read from source."""
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, set[str]] = {}
    for path in SINK_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            ids = [d.args[0].value for d in fn.decorator_list
                   if isinstance(d, ast.Call) and getattr(d.func, "id", "") == "stream_sink"
                   and d.args and isinstance(d.args[0], ast.Constant)]
            if not ids:
                continue
            mods = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods.add(node.module.split(".")[0])
            found[ids[0]] = {m for m in mods if m not in stdlib}
    return found


def test_every_lazily_imported_client_is_declared():
    """Add a sink that imports a client and forget `requires=`, and the UI
    would offer it on installs where it can't run."""
    lazy = _lazy_third_party_imports()
    assert {"http", "kafka", "mqtt"} <= set(lazy), "scanner missed the known sinks"
    for sid, mods in lazy.items():
        declared = {m.split(".")[0] for m in sinks._REQUIREMENTS.get(sid, ((), None))[0]}
        assert mods <= declared, f"{sid} imports {mods - declared} without declaring it"


def test_stdlib_sinks_need_nothing():
    for sid in ("file", "tcp", "udp"):
        assert sinks.sink_status(sid) == {"available": True, "missing": [], "install": None}


def test_requires_without_an_extra_is_refused():
    with pytest.raises(ValueError, match="extra"):
        sinks.stream_sink("never_registered", requires=("x",))


def test_unknown_sink_status_is_a_keyerror():
    with pytest.raises(KeyError):
        sinks.sink_status("no_such_sink")


# ── Status when a client is missing ──────────────────────────────────

@pytest.fixture
def no_kafka(monkeypatch):
    """Pretend confluent-kafka isn't installed, whatever this env has."""
    real = sinks._module_present
    monkeypatch.setattr(sinks, "_module_present",
                        lambda m: False if m == "confluent_kafka" else real(m))


def test_missing_client_reports_the_extra_to_install(no_kafka):
    st = sinks.sink_status("kafka")
    assert st == {"available": False, "missing": ["confluent_kafka"],
                  "install": "pip install 'chaff[kafka]'"}


def test_missing_parent_package_reads_as_missing(monkeypatch):
    """find_spec('paho.mqtt') raises (not returns None) when `paho` is absent."""
    def boom(name):
        raise ModuleNotFoundError(name)
    monkeypatch.setattr(sinks.importlib.util, "find_spec", boom)
    assert sinks._module_present("paho.mqtt") is False


def test_missing_dependency_error_names_the_right_command():
    err = sinks.missing_dependency("kafka", "confluent-kafka")
    assert "pip install 'chaff[kafka]'" in str(err)
    assert "confluent-kafka" in str(err)


def test_cli_registry_flags_an_uninstalled_sink(no_kafka):
    out = CliRunner().invoke(cli_app, ["registry"]).output
    sinks_line = next(line for line in out.splitlines() if line.startswith("sinks:"))
    assert "kafka (not installed: pip install 'chaff[kafka]')" in sinks_line
    assert "mqtt," in sinks_line and "mqtt (" not in sinks_line


# ── API surfaces ─────────────────────────────────────────────────────

@pytest.fixture
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_registry_endpoint_reports_sink_status(client, no_kafka):
    reg = client.get("/registry").json()
    assert reg["sinks"] == sinks.list_sinks()  # unchanged, for older UIs
    assert reg["sink_status"]["kafka"]["available"] is False
    assert reg["sink_status"]["kafka"]["install"] == "pip install 'chaff[kafka]'"
    assert reg["sink_status"]["tcp"]["available"] is True


def test_stream_job_refuses_an_uninstalled_sink_up_front(client, no_kafka):
    spec = {"name": "k", "seed": 1, "rows": 5,
            "columns": [{"name": "id", "generator": "row_id"}],
            "output": {"format": "ndjson"},
            "sink": {"sink": "kafka", "options": {"topic": "t",
                                                   "bootstrap_servers": "localhost:9092"}}}
    r = client.post("/stream/jobs", json={"spec": spec, "max_records": 5, "max_seconds": 5})
    assert r.status_code == 422
    assert "pip install 'chaff[kafka]'" in r.json()["detail"]
