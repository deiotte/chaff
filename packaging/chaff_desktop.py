"""Desktop launcher for chaff: double-click the .exe -> browser opens to the UI.

This is the single entry point in the PyInstaller onefile build (see
packaging/chaff.spec). It exists only to make the existing web app runnable by
someone who has never seen a terminal: it points chaff at bundled presets and a
per-user writable library, starts uvicorn on localhost, and opens the default
browser once the port is live.

It adds no generation logic (INV-1) — it just launches the same API that
produces specs for the engine.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path


def _base_dir() -> Path:
    """Root of bundled resources: sys._MEIPASS when frozen, else the repo root
    (this file lives in packaging/, so the repo root is one level up)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else Path(__file__).resolve().parent.parent


def _user_library_dir() -> Path:
    """Per-user writable dir for saved schemas — survives across launches and
    doesn't depend on where the exe was double-clicked."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "chaff" / "spec-library"
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return Path(xdg) / "chaff" / "spec-library"


def _configure_env() -> None:
    """Point chaff at bundled presets and a writable library. MUST run before
    importing api.main, which resolves the static UI dir at import time.
    setdefault so a power user can still override via the shell."""
    base = _base_dir()
    os.environ.setdefault("CHAFF_PRESETS_DIR", str(base / "examples"))
    # Turns on the Quit button and the /shutdown route (ADR-0023). Set, not
    # setdefault: this process IS the desktop app, and a macOS .app has no
    # console window to close instead.
    os.environ["CHAFF_DESKTOP"] = "1"
    lib = _user_library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CHAFF_LIBRARY_DIR", str(lib))


def _ensure_importable() -> None:
    """Source runs only: put the repo root (for `api`) and src/ (for `chaff`) on
    sys.path, so `python packaging/chaff_desktop.py` works from a fresh checkout.
    Frozen builds resolve these via the spec's pathex, so this is a no-op there."""
    if getattr(sys, "frozen", False):
        return
    root = _base_dir()
    for p in (root, root / "src"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)


def _preferred_port() -> int:
    """Port to try first. `CHAFF_DESKTOP_PORT` pins it — for someone whose
    8000 is permanently busy, and so the CI smoke test knows where to look
    instead of guessing at a fallback port."""
    raw = os.environ.get("CHAFF_DESKTOP_PORT", "").strip()
    if raw.isdigit() and 1 <= int(raw) <= 65535:
        return int(raw)
    return 8000


def _free_port(preferred: int = 8000) -> int:
    """The preferred port if free, else an OS-assigned one — so a busy 8000
    (or a second instance) doesn't crash the launch."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("could not bind a local port")


def _open_browser_when_up(url: str, host: str, port: int) -> None:
    """Open the browser once the server actually accepts connections. A poll
    thread avoids the race where a startup hook fires before the socket binds."""
    if os.environ.get("CHAFF_NO_BROWSER") == "1":
        return  # headless: CI smoke tests, or a user who'd rather open it themselves

    def _wait() -> None:
        for _ in range(200):  # ~20s cap (200 * 0.1s)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.25)
                if s.connect_ex((host, port)) == 0:
                    webbrowser.open(url)
                    return
            threading.Event().wait(0.1)
    threading.Thread(target=_wait, daemon=True).start()


def main() -> None:
    _configure_env()
    _ensure_importable()

    # Import AFTER env is configured so the static mount + presets resolve.
    import uvicorn
    from api import main as api_main
    from api.main import app

    host = "127.0.0.1"
    port = _free_port(_preferred_port())
    url = f"http://{host}:{port}"

    print("=" * 60)
    print(f"  chaff is running at  {url}")
    if os.environ.get("CHAFF_NO_BROWSER") == "1":
        print("  Open that address in your browser.")
    else:
        print("  Your browser should open automatically.")
    print('  Close this window, or click "Quit chaff" in the page, to stop.')
    print("=" * 60)

    _open_browser_when_up(url, host, port)

    # asyncio + h11 are pure Python: dodges uvloop (no Windows wheel) and
    # httptools (native), so the frozen build stays simple and portable.
    config = uvicorn.Config(app, host=host, port=port, loop="asyncio",
                            http="h11", log_level="warning")
    server = uvicorn.Server(config)

    # The launcher owns stopping, since it holds the Server. Signals would be
    # the alternative and behave differently on Windows (ADR-0023).
    api_main.set_shutdown_hook(lambda: setattr(server, "should_exit", True))

    server.run()
    print("chaff stopped.")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # frozen Windows apps must call this first
    main()
