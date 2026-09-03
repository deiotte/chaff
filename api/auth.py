"""Shared-secret auth for the API surface (ADR-0018, widened by ADR-0025).

chaff's product target is a single operator on localhost, so the zero-config
demo (Build DNA §0) is unchanged: no token, `http://localhost:8000` works.
Setting `CHAFF_API_TOKEN` is what turns "reachable from the network" from
*anyone on the segment can generate, read the library and drive the sinks*
into *only the token holder can*.

Scope (ADR-0025): the token gates **every** API route, not just streaming.
ADR-0018 scoped it to the streaming surface and said "the same dependency can
be hung on other routes later"; nobody did, so an external assessment found
`/registry`, `/preview`, `/generate`, `/library` and `/draft` answering
unauthenticated *with a token configured* — the operator who set one believed
they were protected and were not.

Two rules, and they compose into "fail closed without breaking localhost":

1. **A token is set** → it is required on every API route, from every client,
   loopback included. Exempting loopback would silently disable auth for
   anyone behind a reverse proxy, where every request appears to come from
   127.0.0.1.
2. **No token is set** → only loopback is served. That is chaff's documented
   product target (a single operator on their own machine) and keeps the
   zero-config demo intact, while a remote caller gets a 401 that says what to
   do instead of a dataset.

It is a single operator secret, not a user/tenant model — chaff has no users,
so per-job ownership would invent a concept the product doesn't have.
"""

from __future__ import annotations

import hmac
import ipaddress
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


def is_loopback(host: Optional[str]) -> bool:
    """True when a client address is this machine.

    Uses `ipaddress` rather than a literal set so 127.0.0.2, ::1 and the
    IPv4-mapped ::ffff:127.0.0.1 are all recognised — a set of three strings
    would quietly treat those as remote.
    """
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped.is_loopback
    return addr.is_loopback


def access_denied_reason(
    client_host: Optional[str],
    provided_token: Optional[str],
) -> Optional[str]:
    """None when the caller may proceed, else a message explaining why not.

    The single place both the HTTP middleware and the WebSocket handshake ask
    the question, so the two can't drift.
    """
    expected = configured_token()
    if expected is not None:
        if _matches(provided_token, expected):
            return None
        return "missing or invalid API token"
    if not is_loopback(client_host):
        return ("this chaff instance serves localhost only. Set CHAFF_API_TOKEN "
                "on the server and send it as 'X-Chaff-Token' to allow remote use.")
    return None


def token_from_headers(headers) -> Optional[str]:
    """Read the token from a mapping of request headers (case-insensitive)."""
    return _extract(headers.get("authorization"), headers.get("x-chaff-token"))


def ws_denied_reason(websocket) -> Optional[str]:
    """Access check for the WebSocket, using the same rule as HTTP.

    Returns the same message the HTTP path would, so the two can't drift and
    the client is told the real reason. Browsers can't set headers on a `new
    WebSocket`, so the token also rides the `token` query param; the header
    forms stay for non-browser clients.
    """
    provided = (
        token_from_headers(websocket.headers)
        or websocket.query_params.get("token")
    )
    client = websocket.client.host if websocket.client else None
    return access_denied_reason(client, provided)


def ws_token_ok(websocket) -> bool:
    """Back-compat boolean wrapper around `ws_denied_reason`."""
    return ws_denied_reason(websocket) is None
