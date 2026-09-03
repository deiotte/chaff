"""Credentials must not live in a saved spec (F-05, ADR-0027).

A spec is a shareable document — saved to the library, listed over the API,
committed, attached to a support bundle. The assessment proved `/library`
handing back a stored `Authorization: Bearer …` verbatim, so anyone who could
read the endpoint or the file got a working credential.
"""

from __future__ import annotations

import importlib
import json

import pytest

from chaff.secrets import (
    CredentialInSpec,
    find_credentials,
    is_env_placeholder,
    redact,
    reject_credentials,
    resolve_env,
)

BASE = {"name": "t", "rows": 1, "output": {"format": "ndjson"},
        "columns": [{"name": "a", "generator": "row_id"}]}


def spec(options, sink="http"):
    return {**BASE, "sink": {"sink": sink, "options": options}}


# ── what counts as a secret ──────────────────────────────────────────

@pytest.mark.parametrize("options,sink,expected", [
    ({"url": "https://x/i", "headers": {"Authorization": "Bearer S"}}, "http",
     ["sink.options.headers.Authorization"]),
    ({"url": "https://x/i", "headers": {"X-Api-Key": "k"}}, "http",
     ["sink.options.headers.X-Api-Key"]),
    ({"host": "h", "topic": "t", "password": "pw"}, "mqtt",
     ["sink.options.password"]),
    ({"topic": "t", "config": {"sasl.password": "pw"}}, "kafka",
     ["sink.options.config.sasl.password"]),
    ({"url": "https://joe:pw@host/i"}, "http",
     ["sink.options.url (password embedded in the URL)"]),
])
def test_secrets_are_found(options, sink, expected):
    assert find_credentials(spec(options, sink)) == expected


@pytest.mark.parametrize("options,sink", [
    # Kafka's `key` is the static *message* key. A "contains key" heuristic
    # would flag it, and a check that cries wolf is a check people work
    # around — this is the false positive that mattered most to avoid.
    ({"topic": "t", "key": "k1"}, "kafka"),
    # Half a credential is not one, and blocking it buys nothing.
    ({"host": "h", "topic": "t", "username": "joe"}, "mqtt"),
    ({"topic": "t", "config": {"sasl.username": "joe"}}, "kafka"),
    # A reference, not a secret.
    ({"url": "https://x/i", "headers": {"Authorization": "${MY_TOKEN}"}}, "http"),
    ({"host": "h", "topic": "t", "password": "${MY_PW}"}, "mqtt"),
    # Ordinary options.
    ({"url": "https://example.com/ingest"}, "http"),
    ({"host": "h", "port": 1883, "topic": "t"}, "mqtt"),
    # An empty value isn't a leak.
    ({"host": "h", "topic": "t", "password": ""}, "mqtt"),
])
def test_non_secrets_are_not_flagged(options, sink):
    assert find_credentials(spec(options, sink)) == []


def test_a_sink_with_no_options_is_fine():
    assert find_credentials({**BASE}) == []
    assert find_credentials({**BASE, "sink": {"sink": "file", "options": {}}}) == []


@pytest.mark.parametrize("value,expected", [
    ("${MY_TOKEN}", True), ("  ${MY_TOKEN}  ", True), ("${a_b9}", True),
    ("Bearer x", False), ("$MY_TOKEN", False), ("${}", False),
    ("${9bad}", False), ("prefix ${X}", False), (None, False), (42, False),
])
def test_env_placeholder_recognition(value, expected):
    assert is_env_placeholder(value) is expected


# ── refusing, with a usable message ──────────────────────────────────

def test_refusal_names_the_field_and_the_replacement():
    """'Your spec has a secret' leaves someone guessing. The message has to
    say which field and exactly what to put there instead."""
    with pytest.raises(CredentialInSpec) as excinfo:
        reject_credentials(spec({"url": "https://x/i",
                                 "headers": {"Authorization": "Bearer S"}}))
    message = str(excinfo.value)
    assert "sink.options.headers.Authorization" in message
    assert "${" in message and "}" in message, "no replacement shown"


def test_mqtt_password_suggests_the_real_env_var():
    """chaff already reads CHAFF_MQTT_PASSWORD, so suggest that rather than a
    generic name the operator would have to wire up themselves."""
    with pytest.raises(CredentialInSpec, match="CHAFF_MQTT_PASSWORD"):
        reject_credentials(spec({"host": "h", "topic": "t", "password": "pw"}, "mqtt"))


def test_a_clean_spec_passes():
    reject_credentials(spec({"url": "https://example.com/i"}))


# ── redaction of files written before the rule ───────────────────────

def test_redaction_removes_the_value_and_reports_the_path():
    original = spec({"host": "h", "topic": "t", "username": "joe", "password": "pw"}, "mqtt")
    cleaned, paths = redact(original)
    assert paths == ["sink.options.password"]
    assert "password" not in cleaned["sink"]["options"]
    assert cleaned["sink"]["options"]["username"] == "joe"


def test_redaction_does_not_mutate_the_input():
    original = spec({"host": "h", "topic": "t", "password": "pw"}, "mqtt")
    redact(original)
    assert original["sink"]["options"]["password"] == "pw", "input was mutated"


def test_a_redacted_spec_can_be_saved_again():
    """The value is removed, not replaced with a placeholder: a
    '***REDACTED***' string under a `password` key would itself be refused,
    which would strand the spec."""
    cleaned, _ = redact(spec({"host": "h", "topic": "t", "password": "pw"}, "mqtt"))
    reject_credentials(cleaned)


# ── ${VAR} resolution at run time ────────────────────────────────────

def test_placeholders_resolve_from_the_environment(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "Bearer live")
    out = resolve_env({"headers": {"Authorization": "${MY_TOKEN}"}, "url": "https://x"})
    assert out["headers"]["Authorization"] == "Bearer live"
    assert out["url"] == "https://x"


def test_an_unset_placeholder_is_left_alone(monkeypatch):
    """Left as-is on purpose: the sink then fails naming the destination,
    instead of silently sending an empty credential and getting an opaque
    401 that looks like a server problem."""
    monkeypatch.delenv("MY_TOKEN", raising=False)
    out = resolve_env({"headers": {"Authorization": "${MY_TOKEN}"}})
    assert out["headers"]["Authorization"] == "${MY_TOKEN}"


def test_resolution_reaches_nested_structures(monkeypatch):
    monkeypatch.setenv("PW", "s3cret")
    out = resolve_env({"config": {"sasl.password": "${PW}"}, "list": ["${PW}", 1]})
    assert out["config"]["sasl.password"] == "s3cret"
    assert out["list"] == ["s3cret", 1]


def test_the_sink_actually_receives_the_resolved_value(monkeypatch):
    """Through `run()`, with a recording sink — not by calling the resolver
    directly.

    The first version of this test called `_with_resolved_sink_options`
    itself, so it passed even with the call removed from `run()`: the helper
    worked and nothing proved the pipeline used it. A sink that records what
    it was handed is the only thing that shows the wiring.
    """
    monkeypatch.setenv("MY_TOKEN", "Bearer live")
    from chaff.engine import run
    from chaff.sinks import _REGISTRY
    from chaff.spec import load_spec

    seen = {}

    def recorder(spec_arg, payload):
        seen.update(spec_arg.sink.options)
        return "recorded"

    monkeypatch.setitem(_REGISTRY, "recording", recorder)
    run(load_spec(spec({"headers": {"Authorization": "${MY_TOKEN}"}}, "recording")))
    assert seen["headers"]["Authorization"] == "Bearer live", (
        "the sink was handed the unresolved placeholder — run() isn't resolving")


# ── the library, end to end ──────────────────────────────────────────

@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAFF_LIBRARY_DIR", str(tmp_path / "saved"))
    monkeypatch.setenv("CHAFF_PRESETS_DIR", str(tmp_path / "presets"))
    import chaff.library
    return importlib.reload(chaff.library), tmp_path / "saved"


def test_saving_a_spec_with_a_credential_is_refused(library):
    lib, _ = library
    with pytest.raises(CredentialInSpec):
        lib.save_named("leaky", spec({"url": "https://x/i",
                                      "headers": {"Authorization": "Bearer S"}}))


def test_nothing_is_written_when_a_save_is_refused(library):
    """Validation happens before any bytes hit disk."""
    lib, saved_dir = library
    with pytest.raises(CredentialInSpec):
        lib.save_named("leaky", spec({"host": "h", "topic": "t", "password": "pw"}, "mqtt"))
    assert not (saved_dir / "leaky.json").exists()


def test_the_env_reference_form_is_saveable(library):
    lib, saved_dir = library
    lib.save_named("ok", spec({"url": "https://x/i",
                               "headers": {"Authorization": "${MY_TOKEN}"}}))
    assert "${MY_TOKEN}" in (saved_dir / "ok.json").read_text()


def test_reading_a_legacy_file_strips_the_secret(library):
    """The exact F-05 proof: a stored bearer token came back verbatim."""
    lib, saved_dir = library
    saved_dir.mkdir(parents=True, exist_ok=True)
    (saved_dir / "legacy.json").write_text(json.dumps(
        spec({"url": "https://x/i", "headers": {"Authorization": "Bearer SECRET"}})))

    back = lib.load_named("legacy")
    assert "Authorization" not in back["sink"]["options"]["headers"]
    assert "SECRET" not in json.dumps(back)
    assert back["_redacted"] == ["sink.options.headers.Authorization"]


def test_the_gallery_listing_does_not_leak(library):
    lib, saved_dir = library
    saved_dir.mkdir(parents=True, exist_ok=True)
    (saved_dir / "legacy.json").write_text(json.dumps(
        spec({"host": "h", "topic": "t", "password": "hunter2"}, "mqtt")))
    assert "hunter2" not in json.dumps(lib.list_specs())


def test_redaction_on_read_does_not_rewrite_the_file(library):
    """Stated plainly because it's the residual risk: the secret is still on
    disk. Reading is protected; a backup of the library directory is not.
    Rotation is the only fix for an already-stored credential."""
    lib, saved_dir = library
    saved_dir.mkdir(parents=True, exist_ok=True)
    path = saved_dir / "legacy.json"
    path.write_text(json.dumps(spec({"host": "h", "topic": "t", "password": "hunter2"}, "mqtt")))
    lib.load_named("legacy")
    assert "hunter2" in path.read_text(), "the file was rewritten unexpectedly"
