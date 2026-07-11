"""Natural-language spec-drafting tests (Phase 3E). The Anthropic call is
seamed out (`_caller`) / monkeypatched, so nothing here touches the network
or needs a key — the live call is verified separately with a real key."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from api import nl  # noqa: E402

VALID = ('{"name":"widgets","seed":1,"rows":10,'
         '"columns":[{"name":"id","generator":"row_id"},'
         '{"name":"who","generator":"full_name"}],"output":{"format":"csv"}}')


# ── draft_spec ───────────────────────────────────────────────────────

def test_draft_returns_validated_spec():
    out = nl.draft_spec("ten widgets", _caller=lambda desc, error=None: VALID)
    assert out["name"] == "widgets" and len(out["columns"]) == 2


def test_draft_retries_once_on_invalid_then_succeeds():
    calls = []

    def caller(desc, error=None):
        calls.append(error)
        return '{"name":"x"}' if error is None else VALID  # invalid, then valid

    out = nl.draft_spec("desc", _caller=caller)
    assert out["name"] == "widgets"
    assert len(calls) == 2 and calls[0] is None and calls[1] is not None  # error fed back


def test_draft_gives_up_after_second_invalid():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        nl.draft_spec("desc", _caller=lambda desc, error=None: '{"name":"x"}')


def test_extract_json_tolerates_fences_and_prose():
    text = "Here you go:\n```json\n" + VALID + "\n```\nHope that helps!"
    assert nl._extract_json(text)["name"] == "widgets"


def test_extract_json_raises_without_object():
    with pytest.raises(ValueError, match="no JSON object"):
        nl._extract_json("sorry, I can't do that")


# ── provider selection ───────────────────────────────────────────────

_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY")


def _clear_keys(monkeypatch):
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)


def test_active_provider_none_without_keys(monkeypatch):
    _clear_keys(monkeypatch)
    assert nl.active_provider() is None


def test_active_provider_detects_each(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    assert nl.active_provider() == "google"
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert nl.active_provider() == "openai"   # openai wins over google
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert nl.active_provider() == "anthropic"  # anthropic wins over all


def test_model_defaults_are_overridable(monkeypatch):
    assert nl._model("openai") == "gpt-4o"
    monkeypatch.setenv("CHAFF_OPENAI_MODEL", "gpt-4o-mini")
    assert nl._model("openai") == "gpt-4o-mini"


def test_call_llm_errors_without_any_key(monkeypatch):
    _clear_keys(monkeypatch)
    with pytest.raises(RuntimeError, match="no LLM API key"):
        nl._call_llm("anything")


# ── /draft endpoint ──────────────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def test_draft_endpoint_requires_key(monkeypatch):
    _clear_keys(monkeypatch)  # no provider configured
    assert _client().post("/draft", json={"description": "x"}).status_code == 503


def test_draft_endpoint_works_with_any_provider(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # OpenAI alone is enough
    monkeypatch.setattr(nl, "draft_spec", lambda d: {
        "name": "via_openai", "rows": 3,
        "columns": [{"name": "id", "generator": "row_id"}], "output": {"format": "csv"}})
    r = _client().post("/draft", json={"description": "x"})
    assert r.status_code == 200 and r.json()["name"] == "via_openai"


def test_draft_endpoint_rejects_empty(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert _client().post("/draft", json={"description": "   "}).status_code == 400


def test_draft_endpoint_returns_spec(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(nl, "draft_spec", lambda d: {
        "name": "drafted", "rows": 5,
        "columns": [{"name": "id", "generator": "row_id"}], "output": {"format": "csv"}})
    r = _client().post("/draft", json={"description": "five ids"})
    assert r.status_code == 200 and r.json()["name"] == "drafted"


def test_draft_endpoint_502_when_undraftable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def boom(_):
        raise RuntimeError("model gave nonsense")
    monkeypatch.setattr(nl, "draft_spec", boom)
    assert _client().post("/draft", json={"description": "x"}).status_code == 502
