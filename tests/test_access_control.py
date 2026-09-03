"""Access control across the whole API surface (F-02, ADR-0025).

ADR-0018 gated the streaming routes and noted the dependency "can be hung on
other routes later". Nobody did, so an external assessment found `/registry`,
`/preview`, `/generate`, `/library` and `/draft` answering unauthenticated
*with CHAFF_API_TOKEN configured* — the operator who set one believed they
were protected and were not.

These tests set the peer address explicitly, unlike the rest of the suite
(see conftest), because who is calling is the whole question here.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

import api.auth
import api.main

REMOTE = ("203.0.113.9", 40000)
LOCAL = ("127.0.0.1", 50000)

SPEC = {"name": "t", "rows": 1, "output": {"format": "csv"},
        "columns": [{"name": "a", "generator": "row_id"}]}

# Every route that returns data or changes state. Deliberately spelled out:
# a new route missing from this list is caught by
# test_no_route_is_unprotected_by_accident below.
GUARDED = [
    ("GET", "/registry", None),
    ("GET", "/library", None),
    ("GET", "/library/crm_contacts", None),
    ("POST", "/library/tmpname", SPEC),
    ("DELETE", "/library/tmpname", None),
    ("POST", "/preview", SPEC),
    ("POST", "/generate", SPEC),
    ("POST", "/draft", {"description": "x"}),
    ("POST", "/stream/jobs", SPEC),
    ("GET", "/stream/jobs", None),
]

# Reachable without a token on purpose: you must be able to load the page to
# type the token into it, and licence text has to stay readable.
OPEN = [("GET", "/"), ("GET", "/licenses")]


@pytest.fixture
def app(monkeypatch):
    def _build(token: str | None):
        if token is None:
            monkeypatch.delenv("CHAFF_API_TOKEN", raising=False)
        else:
            monkeypatch.setenv("CHAFF_API_TOKEN", token)
        importlib.reload(api.auth)
        return importlib.reload(api.main)
    yield _build
    importlib.reload(api.auth)
    importlib.reload(api.main)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(api.auth)
    importlib.reload(api.main)


# ── no token configured: localhost only ──────────────────────────────

@pytest.mark.parametrize("method,path,body", GUARDED)
def test_remote_caller_is_refused_when_no_token_is_set(app, method, path, body):
    """The default deployment. A remote caller gets a 401 that tells them what
    to do, not a dataset."""
    client = TestClient(app(None).app, client=REMOTE)
    r = client.request(method, path, json=body)
    assert r.status_code == 401, f"{method} {path} answered {r.status_code}"
    assert "localhost" in r.json()["detail"]


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_local_caller_is_served_when_no_token_is_set(app, method, path, body):
    """The zero-config demo must be completely unchanged — this is the whole
    reason the rule is 'localhost open' rather than 'token always'."""
    client = TestClient(app(None).app, client=LOCAL)
    r = client.request(method, path, json=body)
    assert r.status_code != 401, f"{method} {path} refused a local caller"


# ── token configured: required from everyone ─────────────────────────

@pytest.mark.parametrize("method,path,body", GUARDED)
def test_token_is_required_on_every_route(app, method, path, body):
    """The actual F-02 regression: with a token set, these all returned 200."""
    client = TestClient(app("s3cret").app, client=REMOTE)
    assert client.request(method, path, json=body).status_code == 401


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_a_valid_token_is_accepted(app, method, path, body):
    client = TestClient(app("s3cret").app, client=REMOTE)
    r = client.request(method, path, json=body, headers={"X-Chaff-Token": "s3cret"})
    assert r.status_code != 401, f"{method} {path} refused a valid token"


def test_a_configured_token_is_required_from_localhost_too(app):
    """Not exempting loopback is the point: behind a reverse proxy every
    request appears to come from 127.0.0.1, so a loopback exemption would
    silently disable auth for exactly the deployment that needs it."""
    client = TestClient(app("s3cret").app, client=LOCAL)
    assert client.get("/registry").status_code == 401
    assert client.get("/registry", headers={"X-Chaff-Token": "s3cret"}).status_code == 200


def test_a_wrong_token_is_refused(app):
    client = TestClient(app("s3cret").app, client=LOCAL)
    assert client.get("/registry", headers={"X-Chaff-Token": "wrong"}).status_code == 401


def test_bearer_form_is_accepted(app):
    client = TestClient(app("s3cret").app, client=REMOTE)
    r = client.get("/registry", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


# ── the page itself stays reachable ──────────────────────────────────

@pytest.mark.parametrize("method,path", OPEN)
def test_bootstrap_routes_stay_open(app, method, path):
    """You cannot type a token into a page you can't load."""
    client = TestClient(app("s3cret").app, client=REMOTE)
    assert client.request(method, path).status_code == 200


# ── the structural guard ─────────────────────────────────────────────

def test_no_route_is_unprotected_by_accident(app):
    """F-02 happened because routes were added and the auth dependency wasn't.

    Enumerate the app's real routes and require each to be either guarded or
    deliberately open — so adding a route without thinking about access fails
    the build rather than shipping a hole.
    """
    module = app("s3cret")
    known = {p for _m, p, _b in GUARDED} | {p for _m, p in OPEN}
    # Templated paths are listed with a placeholder name in GUARDED.
    known |= {"/library/{name}", "/stream/jobs/{job_id}", "/shutdown", "/stream"}

    unaccounted = []
    for route in module.app.routes:
        path = getattr(route, "path", None)
        if not path or path.startswith("/static"):
            continue
        if path in known or path in ("/openapi.json", "/docs", "/redoc",
                                     "/docs/oauth2-redirect"):
            continue
        unaccounted.append(path)

    assert not unaccounted, (
        f"routes not covered by an access-control test: {unaccounted} — add "
        "them to GUARDED or OPEN in this file")


def test_websocket_uses_the_same_rule(app):
    """The WS handshake and the HTTP middleware must not drift; both ask
    `access_denied_reason`.

    The socket accepts and then sends one error frame before closing, rather
    than refusing the handshake — a browser can read the reason that way. What
    matters is that no record is ever served.
    """
    import json

    module = app("s3cret")
    with TestClient(module.app, client=REMOTE).websocket_connect("/stream") as ws:
        first = json.loads(ws.receive_text())
    assert "error" in first, first
    assert "token" in first["error"]


def test_websocket_serves_a_local_caller_when_no_token_is_set(app):
    """The other half: the live-view demo on localhost still works."""
    import json

    module = app(None)
    with TestClient(module.app, client=LOCAL).websocket_connect(
            "/stream?max_records=2") as ws:
        ws.send_text(json.dumps({
            "name": "t", "rows": 2, "output": {"format": "ndjson"},
            "columns": [{"name": "a", "generator": "row_id"}]}))
        first = ws.receive_text()
    assert "error" not in first.lower(), first


def test_websocket_refuses_a_remote_caller_with_no_token_configured(app):
    import json

    module = app(None)
    with TestClient(module.app, client=REMOTE).websocket_connect("/stream") as ws:
        first = json.loads(ws.receive_text())
    assert "localhost" in first.get("error", "")
