"""Desktop mode: the Quit path for the packaged apps (ADR-0023).

A macOS .app bundle runs windowless, so "close the console window" stops
being a quit story. Desktop builds therefore expose /shutdown and a Quit
button — and the web/Docker deployment must not, because a public /shutdown
is a one-click denial of service.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.main

INDEX_HTML = Path("api/static/index.html").read_text()


def _app(monkeypatch, desktop: bool):
    """Reimport api.main with CHAFF_DESKTOP set, since the flag is read once
    at import (the launcher sets it before importing the app)."""
    monkeypatch.setenv("CHAFF_DESKTOP", "1" if desktop else "0")
    module = importlib.reload(api.main)
    return module


@pytest.fixture(autouse=True)
def _restore_module():
    """Leave the shared module as the rest of the suite expects it."""
    yield
    importlib.reload(api.main)


# ── the web deployment must stay unarmed ─────────────────────────────

def test_shutdown_is_absent_without_desktop_mode(monkeypatch):
    module = _app(monkeypatch, desktop=False)
    assert module.DESKTOP_MODE is False
    assert TestClient(module.app).post("/shutdown").status_code == 404


def test_registry_does_not_advertise_desktop_on_the_web(monkeypatch):
    module = _app(monkeypatch, desktop=False)
    assert TestClient(module.app).get("/registry").json()["desktop"] is False


def test_default_environment_is_not_desktop(monkeypatch):
    """No env var at all — the Docker image's case — must not arm it."""
    monkeypatch.delenv("CHAFF_DESKTOP", raising=False)
    module = importlib.reload(api.main)
    assert module.DESKTOP_MODE is False
    assert TestClient(module.app).post("/shutdown").status_code == 404


# ── desktop mode ─────────────────────────────────────────────────────

def test_desktop_mode_advertises_itself(monkeypatch):
    module = _app(monkeypatch, desktop=True)
    assert module.DESKTOP_MODE is True
    assert TestClient(module.app).get("/registry").json()["desktop"] is True


def _loopback_request():
    """A stand-in Request whose client is loopback, so the origin check passes
    and the test exercises the branch it's actually about."""
    class _Client:
        host = "127.0.0.1"

    class _Req:
        client = _Client()
        base_url = "http://127.0.0.1:8000/"
        headers: dict = {}

    return _Req()


def test_shutdown_without_a_hook_fails_loudly(monkeypatch):
    """Better a 503 than pretending to stop: the launcher registers the hook,
    and if that ever regresses the Quit button must not silently no-op."""
    from fastapi import HTTPException

    module = _app(monkeypatch, desktop=True)
    module._shutdown_hook = None
    with pytest.raises(HTTPException) as excinfo:
        module.shutdown(_loopback_request())
    assert excinfo.value.status_code == 503


def test_shutdown_runs_the_hook_for_a_loopback_caller(monkeypatch):
    module = _app(monkeypatch, desktop=True)
    fired = []
    module.set_shutdown_hook(lambda: fired.append(1))
    assert module.shutdown(_loopback_request()) == {"stopping": True}
    assert fired == [1]


def test_shutdown_refuses_a_non_loopback_caller(monkeypatch):
    """Defense in depth: the launcher binds 127.0.0.1 only, but a desktop
    instance must not be stoppable by anything else on the network.

    The peer address is set explicitly. This test used to pass because
    TestClient's default peer is the literal "testclient", which happens not
    to parse as a loopback IP — an incidental property, not an assertion. It
    now names the remote address it is testing.
    """
    module = _app(monkeypatch, desktop=True)
    fired = []
    module.set_shutdown_hook(lambda: fired.append(1))
    remote = TestClient(module.app, client=("203.0.113.9", 40000))
    r = remote.post("/shutdown")
    assert r.status_code in (401, 403), r.text
    assert not fired, "shutdown ran for a non-loopback client"


def test_shutdown_actually_stops_a_real_server():
    """The real path, against a real server on 127.0.0.1 — the only way to
    exercise the origin check honestly."""
    import http.client
    import json
    import os
    import socket
    import threading
    import time

    import uvicorn

    os.environ["CHAFF_DESKTOP"] = "1"
    module = importlib.reload(api.main)
    try:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server = uvicorn.Server(uvicorn.Config(
            module.app, host="127.0.0.1", port=port, log_level="critical"))
        module.set_shutdown_hook(lambda: setattr(server, "should_exit", True))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 20
        while time.time() < deadline and not getattr(server, "started", False):
            time.sleep(0.05)
        assert getattr(server, "started", False), "test server never started"

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/shutdown")
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read())["stopping"] is True

        thread.join(timeout=15)
        assert not thread.is_alive(), "server did not actually stop"
    finally:
        os.environ.pop("CHAFF_DESKTOP", None)
        importlib.reload(api.main)


# ── the launcher wires it up ─────────────────────────────────────────

def test_launcher_arms_desktop_mode_and_registers_the_hook():
    """Both halves matter: the env var before the app import (the flag is read
    at import time) and the hook, or Quit 503s."""
    src = Path("packaging/chaff_desktop.py").read_text()
    assert 'os.environ["CHAFF_DESKTOP"] = "1"' in src, \
        "launcher must arm desktop mode (setdefault would let a stale env win)"
    assert "set_shutdown_hook" in src
    # The env must be configured before api.main is imported.
    assert src.index("_configure_env") < src.index("from api.main import app")


def test_ui_hides_quit_unless_the_server_says_desktop():
    """A visible Quit button on the Docker UI would just 404."""
    assert re.search(r'id="quitBtn"[^>]*\bhidden\b', INDEX_HTML), \
        "the Quit button must start hidden"
    assert "reg.desktop" in INDEX_HTML, "it must be revealed from the registry"


# ── a page you visit must not be able to kill your app ───────────────

def test_shutdown_refuses_a_cross_site_origin(monkeypatch):
    """Loopback alone isn't enough: any site the user visits can POST to
    127.0.0.1, and that request *is* local. A cross-site POST carries a
    foreign Origin, so it's refused."""
    module = _app(monkeypatch, desktop=True)
    fired = []
    module.set_shutdown_hook(lambda: fired.append(1))

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        base_url = "http://127.0.0.1:8000/"
        headers = {"origin": "https://evil.example"}

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        module.shutdown(_Req())
    assert excinfo.value.status_code == 403
    assert not fired, "a cross-site page stopped the app"


def test_shutdown_accepts_our_own_origin(monkeypatch):
    module = _app(monkeypatch, desktop=True)
    fired = []
    module.set_shutdown_hook(lambda: fired.append(1))

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()
        base_url = "http://127.0.0.1:8000/"
        headers = {"origin": "http://127.0.0.1:8000"}

    assert module.shutdown(_Req()) == {"stopping": True}
    assert fired == [1]


def test_shutdown_allows_a_missing_origin(monkeypatch):
    """curl and the CI smoke test send none. Anything already running on this
    machine can kill the process regardless, so this buys no safety to block."""
    module = _app(monkeypatch, desktop=True)
    fired = []
    module.set_shutdown_hook(lambda: fired.append(1))
    assert module.shutdown(_loopback_request()) == {"stopping": True}
    assert fired == [1]
