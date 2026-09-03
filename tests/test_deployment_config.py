"""The supplied deployment must actually apply its documented settings (F-01).

`docker-compose.yml` published 0.0.0.0:8000 and forwarded only the three AI
provider keys. Every security setting in `.env.example` — the API token, the
egress allowlist, the stream ceilings — was read from the *container's*
environment, so a value in the host `.env` reached Compose and never the
process. Someone following the documented hardening got none of it, with no
error to tell them.

This is the same failure mode as ADR-0020: a thing that looks configured and
silently isn't. These tests are cheap; discovering it in production is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = yaml.safe_load(Path("docker-compose.yml").read_text())
SERVICE = COMPOSE["services"]["chaff"]
FORWARDED = set(SERVICE.get("environment") or {})

# Settings the app reads but which must NOT come from the host environment,
# each with the reason. Anything else the code reads has to be forwarded, or
# it is silently inert in the container.
DELIBERATELY_NOT_FORWARDED = {
    "CHAFF_PRESETS_DIR": "container-internal path, fixed by the image",
    "CHAFF_LIBRARY_DIR": "container-internal path, backed by a volume",
    "CHAFF_DESKTOP": "desktop builds only; never the server deployment",
    "CHAFF_DESKTOP_PORT": "desktop builds only",
    "CHAFF_NO_BROWSER": "desktop builds only",
}


def _settings_read_by_the_app() -> set[str]:
    found: set[str] = set()
    for root in ("api", "src", "packaging"):
        for path in Path(root).rglob("*.py"):
            found.update(re.findall(r"CHAFF_[A-Z_]+", path.read_text()))
    return found


def test_every_setting_the_app_reads_is_forwarded_or_explicitly_excluded():
    """The regression guard for F-01: adding a setting to the code without
    adding it to Compose makes it silently inert in the shipped deployment."""
    missing = _settings_read_by_the_app() - FORWARDED - set(DELIBERATELY_NOT_FORWARDED)
    assert not missing, (
        f"these settings are read by the app but never reach the container: "
        f"{sorted(missing)} — add them to docker-compose.yml, or to "
        f"DELIBERATELY_NOT_FORWARDED with a reason")


def test_documented_security_settings_reach_the_container():
    """Named explicitly, because these are the ones .env.example tells the
    user to set in order to be safe."""
    for setting in ("CHAFF_API_TOKEN", "CHAFF_STREAM_ALLOWED_HOSTS",
                    "CHAFF_STREAM_MAX_RECORDS", "CHAFF_STREAM_MAX_SECONDS"):
        assert setting in FORWARDED, f"{setting} is documented but never forwarded"


def test_compose_does_not_forward_a_setting_nothing_reads():
    """The other direction: a forwarded name that no longer exists in the code
    is a setting the user can set with no effect."""
    known = _settings_read_by_the_app()
    stale = {k for k in FORWARDED if k.startswith("CHAFF_")} - known
    assert not stale, f"compose forwards settings nothing reads: {sorted(stale)}"


def test_compose_binds_to_loopback_by_default():
    """chaff's defaults assume one operator on localhost — no auth on the
    generation routes. Publishing on 0.0.0.0 handed that to the network."""
    ports = SERVICE["ports"]
    assert len(ports) == 1
    assert ports[0].startswith("${CHAFF_BIND:-127.0.0.1}:"), (
        f"expected a loopback-by-default bind, got {ports[0]!r}")


def test_exposing_on_a_network_stays_possible():
    """Fail-closed must not mean 'impossible' — a deliberate operator sets
    CHAFF_BIND. If this stops being overridable the default is a wall."""
    assert "CHAFF_BIND" in SERVICE["ports"][0]


def test_env_example_documents_the_bind_switch():
    text = Path(".env.example").read_text()
    assert "CHAFF_BIND" in text, "the escape hatch must be documented where the rest are"
