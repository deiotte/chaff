"""Optional shared-secret auth for the streaming control surface (ADR-0018).

chaff's product target is a single operator on localhost, so auth is **off by
default** — the zero-config demo (Build DNA §0) is unchanged. Set
`CHAFF_API_TOKEN` to require a matching token on the streaming endpoints
(Start / Status / Stop and the `/stream` WebSocket). That is what turns
"exposed on 0.0.0.0" from *anyone on the segment can list/kill jobs and drive
the sinks* into *only the token holder can*.

Scope: the token gates the **streaming** surface — the new network-facing
attack surface in the findings (`/stream/jobs*` and the `/stream` WebSocket).
It is a single operator secret, not a user/tenant model — chaff has no users
(so per-job ownership would be inventing a concept the product doesn't have;
gating the whole surface is the proportionate fix). The same dependency can be
hung on other routes later if the API's exposure model changes.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException


def configured_token() -> Optional[str]:
    """The server's expected token, or None when auth is disabled (unset/blank)."""
    tok = os.environ.get("CHAFF_API_TOKEN", "").strip()
    return tok or None


def _matches(provided: Optional[str], expected: str) -> bool:
    # Constant-time compare so a wrong token can't be recovered by timing.
    return bool(provided) and hmac.compare_digest(provided, expected)


def _extract(authorization: Optional[str], x_chaff_token: Optional[str]) -> Optional[str]:
    """Pull the token from either `X-Chaff-Token` or a `Bearer` auth header."""
    if x_chaff_token:
        return x_chaff_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_token(
    authorization: Optional[str] = Header(default=None),
    x_chaff_token: Optional[str] = Header(default=None),
) -> None:
    """FastAPI dependency: enforce `CHAFF_API_TOKEN` when set; a no-op when
    unset (the localhost default). Raises 401 on a missing/wrong token."""
    expected = configured_token()
    if expected is None:
        return
    if not _matches(_extract(authorization, x_chaff_token), expected):
        raise HTTPException(status_code=401, detail="missing or invalid API token")


def ws_token_ok(websocket) -> bool:
    """Token check for the WebSocket. Browsers can't set headers on a `new
    WebSocket`, so the token also rides the `token` query param; the header
    forms are still accepted for non-browser clients. Returns True when auth
    is disabled."""
    expected = configured_token()
    if expected is None:
        return True
    provided = (
        _extract(websocket.headers.get("authorization"),
                 websocket.headers.get("x-chaff-token"))
        or websocket.query_params.get("token")
    )
    return _matches(provided, expected)
