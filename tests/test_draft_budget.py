"""Draft-endpoint cost budget: F-10.

ADR-0025 already closed the authentication half of this finding — with a token
set, `/draft` is refused like every other route. What it did not close is the
cost half, measured on the merged tree as a local operator:

    description of 5,000,000 chars -> 200, provider received 5,000,000 chars
    40 rapid requests -> [200], provider calls made: 40

`/draft` is the one route that spends money rather than CPU, so the bill for
getting it wrong arrives later and belongs to someone else.
"""

from __future__ import annotations

import sys
import types

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import draft_budget, main, nl  # noqa: E402

VALID_DRAFT = (
    '{"name":"t","rows":1,"seed":1,'
    '"columns":[{"name":"v","generator":"row_id"}],'
    '"output":{"format":"csv"},"sink":{"sink":"file","options":{"path":"out/x"}}}'
)

client = TestClient(main.app)


@pytest.fixture
def provider(monkeypatch):
    """A recording stand-in for the LLM, so no test can reach a real provider.

    Yields the list of prompt lengths the provider was handed — the thing that
    actually costs money, and therefore the thing worth asserting on.
    """
    seen: list[int] = []

    def fake_call(description, error=None, **kwargs):
        seen.append(len(str(description)))
        return VALID_DRAFT

    real = nl.draft_spec
    monkeypatch.setattr(nl, "draft_spec",
                        lambda d, **kw: real(d, _caller=fake_call))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    draft_budget.reset()
    yield seen
    draft_budget.reset()


def post(description="500 customers with name and email"):
    return client.post("/draft", json={"description": description})


# ── the prompt ceiling ───────────────────────────────────────────────

@pytest.mark.parametrize("size", [10_000, 1_000_000, 5_000_000])
def test_an_oversized_description_never_reaches_the_provider(provider, size):
    r = post("x" * size)
    assert r.status_code == 413
    assert provider == [], "the provider was called anyway — the bill is the damage"


def test_the_refusal_says_the_limit_and_what_to_do(provider):
    detail = post("x" * 10_000).json()["detail"]
    assert f"{draft_budget.max_chars():,}" in detail
    assert "sentence" in detail


def test_a_normal_description_is_untouched(provider):
    assert post().status_code == 200
    assert len(provider) == 1


def test_the_ceiling_boundary_is_exact(provider):
    limit = draft_budget.max_chars()
    assert post("x" * limit).status_code == 200
    draft_budget.reset()
    assert post("x" * (limit + 1)).status_code == 413


def test_the_ceiling_is_configurable(provider, monkeypatch):
    monkeypatch.setenv("CHAFF_DRAFT_MAX_CHARS", "50")
    assert post("x" * 51).status_code == 413


def test_a_nonsense_ceiling_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CHAFF_DRAFT_MAX_CHARS", "lots")
    assert draft_budget.max_chars() == draft_budget.DEFAULT_MAX_CHARS


# ── the request rate ─────────────────────────────────────────────────

def test_forty_rapid_requests_do_not_buy_forty_llm_calls(provider):
    """The report's proof."""
    allowed = draft_budget.rate_per_minute()
    codes = [post().status_code for _ in range(40)]
    assert codes.count(200) == allowed
    assert codes.count(429) == 40 - allowed
    assert len(provider) == allowed, "a refused request still called the provider"


def test_the_rate_refusal_explains_the_cost_and_the_wait(provider):
    for _ in range(draft_budget.rate_per_minute()):
        post()
    detail = post().json()["detail"]
    assert "costs an LLM call" in detail
    assert "CHAFF_DRAFT_RATE_PER_MINUTE" in detail


def test_the_rate_is_per_client_not_global(provider):
    """One noisy caller must not lock everyone else out of the feature."""
    noisy = TestClient(main.app, client=("127.0.0.1", 51000))
    for _ in range(draft_budget.rate_per_minute()):
        noisy.post("/draft", json={"description": "hello"})
    assert noisy.post("/draft", json={"description": "hello"}).status_code == 429

    other = TestClient(main.app, client=("127.0.0.2", 51001))
    assert other.post("/draft", json={"description": "hello"}).status_code == 200


def test_the_rate_is_configurable(provider, monkeypatch):
    monkeypatch.setenv("CHAFF_DRAFT_RATE_PER_MINUTE", "2")
    assert [post().status_code for _ in range(4)] == [200, 200, 429, 429]


def test_zero_turns_the_endpoint_off(provider, monkeypatch):
    monkeypatch.setenv("CHAFF_DRAFT_RATE_PER_MINUTE", "0")
    r = post()
    assert r.status_code == 503
    assert "turned off" in r.json()["detail"]
    assert provider == []


def test_the_window_expires_so_the_limit_is_a_rate_not_a_quota(provider, monkeypatch):
    monkeypatch.setattr(draft_budget, "_WINDOW", 0.0)  # every request is a new window
    assert all(post().status_code == 200 for _ in range(20))


def test_the_client_map_does_not_grow_without_bound(provider, monkeypatch):
    # One request each from many addresses would otherwise leave an entry per
    # address forever — the same slow leak this module exists to prevent.
    #
    # The addresses must be loopback: a 10.x peer is refused by the access
    # middleware (ADR-0025) before the route runs, so this test would pass
    # without ever reaching the rate limiter. It did, until mutation testing
    # said otherwise.
    monkeypatch.setattr(draft_budget, "_WINDOW", 0.0)
    codes = [
        TestClient(main.app, client=(f"127.0.0.{i}", 5000)).post(
            "/draft", json={"description": "hello"}).status_code
        for i in range(1, 51)
    ]
    assert codes == [200] * 50, "the requests never reached the rate limiter"
    assert len(draft_budget._HITS) <= 1


# ── the provider timeout ─────────────────────────────────────────────

def _fake_module(**attrs):
    mod = types.ModuleType("fake")
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


def test_the_anthropic_call_is_given_a_timeout(monkeypatch):
    """A hung provider connection pins a worker thread for as long as the
    socket stays open, which costs the caller nothing.

    Asserted by driving a stand-in SDK rather than by reading the source: a
    source check passes on the *comment* that explains the timeout, which is
    how this test first passed with the timeout removed.
    """
    seen = {}

    class FakeAnthropic:
        def __init__(self, **kw):
            seen.update(kw)
            self.messages = types.SimpleNamespace(
                create=lambda **k: types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text="{}")]))

    monkeypatch.setitem(sys.modules, "anthropic", _fake_module(Anthropic=FakeAnthropic))
    nl._call_anthropic("sys", "user", api_key="k")
    assert seen.get("timeout") == draft_budget.timeout_seconds()


def test_the_openai_call_is_given_a_timeout(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            seen.update(kw)
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(
                create=lambda **k: types.SimpleNamespace(choices=[
                    types.SimpleNamespace(message=types.SimpleNamespace(content="{}"))])))

    monkeypatch.setitem(sys.modules, "openai", _fake_module(OpenAI=FakeOpenAI))
    nl._call_openai("sys", "user", api_key="k")
    assert seen.get("timeout") == draft_budget.timeout_seconds()


def test_the_google_call_is_given_a_timeout(monkeypatch):
    seen = {}

    class FakeModel:
        def __init__(self, *a, **kw):
            pass

        def generate_content(self, user, **kw):
            seen.update(kw.get("request_options") or {})
            return types.SimpleNamespace(text="{}")

    genai = _fake_module(configure=lambda **kw: None, GenerativeModel=FakeModel)
    google = _fake_module(generativeai=genai)
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.generativeai", genai)
    nl._call_google("sys", "user", api_key="k")
    assert seen.get("timeout") == draft_budget.timeout_seconds()


def test_a_nonsense_timeout_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CHAFF_DRAFT_TIMEOUT_SECONDS", "soon")
    assert draft_budget.timeout_seconds() == draft_budget.DEFAULT_TIMEOUT_SECONDS


def test_a_zero_timeout_falls_back_rather_than_disabling_it(monkeypatch):
    # 0 would mean "no timeout" to some SDKs — the opposite of the intent.
    monkeypatch.setenv("CHAFF_DRAFT_TIMEOUT_SECONDS", "0")
    assert draft_budget.timeout_seconds() == draft_budget.DEFAULT_TIMEOUT_SECONDS


# ── the budget applies to a pasted key too ───────────────────────────

def test_a_pasted_key_is_still_rate_limited(provider):
    """Bring-your-own-key spends someone else's quota, but the server is still
    the proxy — which is the shape of abuse the finding describes."""
    body = {"description": "hello", "api_key": "sk-ant-pasted", "provider": "anthropic"}
    codes = [client.post("/draft", json=body).status_code
             for _ in range(draft_budget.rate_per_minute() + 3)]
    assert 429 in codes
