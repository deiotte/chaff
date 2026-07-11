"""Natural-language spec drafting (ADR-0010, multi-provider per ADR-0011).

An interface that turns a plain-English description into a *draft*
`DatasetSpec` the user reviews and edits — the UI's "describe it in English"
box. It lives in the API layer, not the engine: like the UI/CLI/API, it
*produces* a spec (INV-1); the engine still generates the data
deterministically. This does NOT make chaff an AI/ML dataset pipeline
(INV-5) — the LLM drafts a spec, nothing more.

The provider is chosen by whichever API key is present in the server
environment (the browser never sees it): Anthropic, then OpenAI, then
Google. Each SDK is optional and imported lazily. Whatever the provider,
the model's JSON is validated with `load_spec` and one correction
round-trip is attempted before giving up, so a malformed draft never
reaches the caller.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from chaff.formats import list_formats
from chaff.generators import list_generators
from chaff.spec import load_spec

# Per-provider default models, each overridable by env var.
_MODELS = {
    "anthropic": ("CHAFF_ANTHROPIC_MODEL", "claude-opus-4-8"),
    "openai": ("CHAFF_OPENAI_MODEL", "gpt-4o"),
    "google": ("CHAFF_GOOGLE_MODEL", "gemini-1.5-flash"),
}
MAX_TOKENS = 8192


def active_provider() -> str | None:
    """Which LLM provider a key is configured for, or None. Anthropic wins
    ties, then OpenAI, then Google."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "google"
    return None


def _model(provider: str) -> str:
    env, default = _MODELS[provider]
    return os.environ.get(env, default)


def _system_prompt() -> str:
    return (
        "You draft datasets for chaff, a synthetic demo-data engine. Given a "
        "plain-English description, return ONE JSON object — a chaff DatasetSpec — "
        "and nothing else (no prose, no markdown fences).\n\n"
        "Schema:\n"
        '{\n'
        '  "name": "snake_case_name",\n'
        '  "description": "one line of human context",\n'
        '  "seed": 1337,\n'
        '  "rows": 500,\n'
        '  "columns": [\n'
        '    {"name": "col", "generator": "<one of the generators below>",\n'
        '     "params": { ... generator-specific ... }, "null_rate": 0.0}\n'
        '  ],\n'
        '  "output": {"format": "<one of the formats below>"}\n'
        "}\n\n"
        f"Available generators: {', '.join(list_generators())}.\n"
        f"Available formats: {', '.join(list_formats())}.\n\n"
        "Guidance: choose semantic generators that fit each column (e.g. full_name, "
        "email, city, choice_weighted with values+weights, money, date_between with "
        "start+end, pattern with a '#'=digit/'?'=A-Z template, lognormal/poisson for "
        "skewed numbers, ipv4/user_agent/http_status for logs). For a column computed "
        "from other columns in the same row, use the 'derived' generator with "
        "params {\"expr\": \"...\"} where the formula uses other column names — e.g. "
        "{\"name\": \"total\", \"generator\": \"derived\", \"params\": {\"expr\": "
        "\"price * qty\", \"precision\": 2}} — and declare it AFTER the columns it "
        "references. Give every generator sensible params. Pick a reasonable row count "
        "and a format that suits the data. Return only the JSON object."
    )


def _user_prompt(description: str, error: str | None) -> str:
    if error:
        return (description +
                f"\n\nYour previous attempt did not validate: {error}\n"
                "Return a corrected JSON object only.")
    return description


# ── Provider callers (each lazily imports its SDK) ───────────────────

def _missing(provider: str, extra: str) -> RuntimeError:
    return RuntimeError(f"{provider} drafting needs its SDK: pip install 'chaff[{extra}]'")


def _call_anthropic(system: str, user: str) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise _missing("anthropic", "nl") from e
    resp = anthropic.Anthropic().messages.create(
        model=_model("anthropic"), max_tokens=MAX_TOKENS,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def _call_openai(system: str, user: str) -> str:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise _missing("openai", "nl-openai") from e
    resp = OpenAI().chat.completions.create(
        model=_model("openai"),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},  # forces valid JSON
    )
    return resp.choices[0].message.content or ""


def _call_google(system: str, user: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise _missing("google", "nl-google") from e
    genai.configure(api_key=os.environ.get("GOOGLE_API_KEY") or os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(_model("google"), system_instruction=system)
    resp = model.generate_content(
        user, generation_config={"response_mime_type": "application/json"})
    return resp.text


_CALLERS = {"anthropic": _call_anthropic, "openai": _call_openai, "google": _call_google}


def _call_llm(description: str, error: str | None = None) -> str:
    provider = active_provider()
    if provider is None:
        raise RuntimeError(
            "no LLM API key configured "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY)")
    return _CALLERS[provider](_system_prompt(), _user_prompt(description, error))


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply, tolerating stray prose/fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("model returned no JSON object")
    return json.loads(text[start:end + 1])


def draft_spec(description: str, *, _caller: Callable[..., str] = _call_llm) -> dict[str, Any]:
    """Draft a validated spec dict from a description, with one retry.

    `_caller` is injectable so tests can drive the flow without the network.
    Raises if a valid spec can't be produced in two attempts.
    """
    raw = _caller(description)
    spec = _extract_json(raw)
    try:
        load_spec(spec)  # validate; the engine only ever sees valid specs
        return spec
    except Exception as first_error:
        raw = _caller(description, error=str(first_error))
        spec = _extract_json(raw)
        load_spec(spec)  # raise if still invalid
        return spec
