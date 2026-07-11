"""Natural-language spec drafting (Phase 3, ADR-0010).

An interface that turns a plain-English description into a *draft*
`DatasetSpec` the user reviews and edits — the UI's "describe it in English"
box. It lives in the API layer, not the engine: like the UI/CLI/API, it
*produces* a spec (INV-1); the engine still generates the data
deterministically from that spec. This does NOT make chaff an AI/ML
dataset pipeline (INV-5) — the LLM drafts a spec, nothing more.

Uses the Anthropic SDK with a server-side key (`ANTHROPIC_API_KEY`); the
browser never sees it. The model's JSON is validated with `load_spec` and
one correction round-trip is attempted before giving up, so a malformed
draft never reaches the caller.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from chaff.formats import list_formats
from chaff.generators import list_generators
from chaff.spec import load_spec

MODEL = "claude-opus-4-8"
MAX_TOKENS = 8192


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
        "start+end, pattern with a '#'=digit/'?'=A-Z template). Give every generator "
        "sensible params. Pick a reasonable row count and a format that suits the data. "
        "Return only the JSON object."
    )


def _call_claude(description: str, error: str | None = None) -> str:
    import anthropic  # lazy: only needed when actually drafting

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    user = description
    if error:
        user += (
            f"\n\nYour previous attempt did not validate: {error}\n"
            "Return a corrected JSON object only."
        )
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS,
        system=_system_prompt(),
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply, tolerating stray prose/fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("model returned no JSON object")
    return json.loads(text[start:end + 1])


def draft_spec(description: str, *, _caller: Callable[..., str] = _call_claude) -> dict[str, Any]:
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
