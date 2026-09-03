"""Shared fixtures.

The browser fixtures here back `tests/test_ui_browser.py`. They are built to
**skip, never fail**, when Playwright or a Chromium build isn't present:
`make check` is the definition of green for every contributor (AGENTS.md §3),
so it must stay runnable on a machine with no browser. CI installs the
browser and runs them for real — see .github/workflows/ci.yml.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest


# A skip is the right default locally and a silent hole in CI: if the browser
# install ever breaks, every UI test would skip and `make check` would still
# go green with zero coverage of the page. CI sets this to turn any skip in
# the browser fixtures into a failure.
REQUIRE_BROWSER = os.environ.get("CHAFF_REQUIRE_BROWSER_TESTS") == "1"


def _unavailable(reason: str):
    """Fail when the environment promised a browser, else skip."""
    if REQUIRE_BROWSER:
        pytest.fail(
            f"{reason} — CHAFF_REQUIRE_BROWSER_TESTS=1 says these must run. "
            "Install the 'dev-browser' extra and a chromium build.")
    pytest.skip(reason)


# ── Tests present as a local operator (ADR-0025) ─────────────────────
# chaff serves localhost without a token and refuses remote callers that don't
# have one. Starlette's TestClient defaults its peer address to the literal
# "testclient", which is correctly classified as *remote* — so without this
# every existing test would exercise the refused path and 401.
#
# Applied at conftest import, not as a fixture, because several test modules
# build their client at module scope (`client = TestClient(app)`), which runs
# before any fixture could patch it.
#
# The suite is overwhelmingly about what a local operator can do, so that is
# the default. The remote and token-configured paths are not left to chance:
# tests/test_access_control.py sets the peer address explicitly and asserts
# the refusals.
def _default_testclient_to_loopback() -> None:
    import starlette.testclient as testclient_module

    original_init = testclient_module.TestClient.__init__
    if getattr(original_init, "_chaff_loopback_default", False):
        return

    def local_init(self, *args, **kwargs):
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        return original_init(self, *args, **kwargs)

    local_init._chaff_loopback_default = True
    testclient_module.TestClient.__init__ = local_init


_default_testclient_to_loopback()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def token_server():
    """A live server that requires an access token, for the UI's refusal path.

    Function-scoped and separate from `live_server` (which is session-scoped
    and open) because arming auth is a different app instance: CHAFF_API_TOKEN
    is read at import time.
    """
    import importlib

    try:
        import uvicorn

        import api.auth
        import api.main
    except ImportError as e:
        _unavailable(f"can't start the app ({e}); needs the 'api' extra")

    os.environ["CHAFF_API_TOKEN"] = "s3cret"
    importlib.reload(api.auth)
    module = importlib.reload(api.main)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        module.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while time.time() < deadline and not getattr(server, "started", False):
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", "s3cret"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        os.environ.pop("CHAFF_API_TOKEN", None)
        importlib.reload(api.auth)
        importlib.reload(api.main)


@pytest.fixture(scope="session")
def live_server():
    """The real FastAPI app on a real port, in a background thread.

    TestClient isn't enough here: the page under test is served as static
    files and talks to the API over fetch(), so the browser needs a genuine
    HTTP origin.
    """
    try:
        import uvicorn

        from api.main import app
    except ImportError as e:
        _unavailable(f"can't start the app ({e}); needs the 'api' extra")

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if getattr(server, "started", False):
            break
        time.sleep(0.05)
    else:
        server.should_exit = True
        pytest.fail("live server did not start within 30s")

    yield base

    server.should_exit = True
    thread.join(timeout=10)


def _chromium_executable() -> str | None:
    """An explicit Chromium path when Playwright's bundled one won't do.

    Playwright pins an exact browser build per version; an image that ships a
    different build (as some CI/dev containers do) fails to launch with the
    default. `CHAFF_TEST_CHROMIUM` overrides it, and we otherwise probe
    PLAYWRIGHT_BROWSERS_PATH for any chromium build present.
    """
    explicit = os.environ.get("CHAFF_TEST_CHROMIUM")
    if explicit and os.path.exists(explicit):
        return explicit

    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root or not os.path.isdir(root):
        return None
    from pathlib import Path
    for entry in sorted(Path(root).glob("chromium-*/chrome-linux/chrome")):
        return str(entry)
    return None


@pytest.fixture(scope="session")
def browser():
    try:
        from playwright import sync_api as playwright
    except ImportError:
        _unavailable("playwright is not installed (needs the 'dev-browser' extra)")
    with playwright.sync_playwright() as pw:
        launch_kwargs = {"args": ["--no-sandbox"]}
        exe = _chromium_executable()
        if exe:
            launch_kwargs["executable_path"] = exe
        try:
            b = pw.chromium.launch(**launch_kwargs)
        except Exception as e:  # no browser binary installed
            _unavailable(f"no usable chromium for Playwright: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server):
    """A page on the live app, with JS errors turned into test failures.

    A silent `pageerror` is exactly how the ADR-0020 bug hid, so no test here
    is allowed to pass while the console is throwing.
    """
    ctx = browser.new_context()
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live_server, wait_until="networkidle")
    yield pg
    ctx.close()
    assert not errors, f"JavaScript errors on the page: {errors}"
