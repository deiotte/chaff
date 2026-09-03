"""Cost controls for the natural-language drafting endpoint (ADR-0030).

`/draft` is the one route that spends real money. Everything else in chaff
costs CPU on a machine the operator already owns; this one reaches a paid API
whose bill arrives later, so the failure mode is a surprise invoice rather
than a slow server.

ADR-0025 closed the authentication half of the red team's F-10 — with a token
set, `/draft` is refused like every other route. What it did not close is the
*cost* half, which was measured on the merged tree:

* a 5,000,000-character description reached the provider verbatim;
* 40 requests in a row made 40 provider calls, and since `draft_spec` retries
  once on an invalid draft, each one can be two.

Three limits, all cheap, none of which change the zero-config local demo:

1. **A prompt ceiling.** A spec description is a sentence, not a document.
2. **A request rate.** Per client address, since that is the only identity
   the server has — there are no accounts, and a shared token cannot tell two
   callers apart.
3. **A wall-clock timeout**, so a provider that hangs doesn't hold a worker.

Deliberately *not* here: rate limiting for the rest of the API. That is a
different change with a different shape (every route, a shared bucket) and
pretending this covers it would be worse than saying so.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

#: Longest description accepted. Roughly a thousand tokens — far past any
#: real "describe your dataset" sentence, far below a pasted document.
DEFAULT_MAX_CHARS = 4000

#: Draft requests allowed per client per minute. Drafting is interactive and
#: iterative, so this has to fit a person tweaking a sentence several times;
#: it does not have to fit a loop. Zero turns the endpoint off entirely.
DEFAULT_RATE_PER_MINUTE = 10

#: Seconds to wait on a provider before giving up. Long enough for a slow
#: model, short enough that a hung connection doesn't pin a worker.
DEFAULT_TIMEOUT_SECONDS = 60.0

_WINDOW = 60.0


class DraftRefused(Exception):
    """A draft request the server declines to pay for.

    `status` is the HTTP code the route should answer with, so the reason for
    each refusal is decided here rather than guessed at the call site.
    """

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value >= 0 else default


def max_chars() -> int:
    return _int_env("CHAFF_DRAFT_MAX_CHARS", DEFAULT_MAX_CHARS) or DEFAULT_MAX_CHARS


def rate_per_minute() -> int:
    return _int_env("CHAFF_DRAFT_RATE_PER_MINUTE", DEFAULT_RATE_PER_MINUTE)


def timeout_seconds() -> float:
    try:
        value = float(os.environ.get("CHAFF_DRAFT_TIMEOUT_SECONDS",
                                     DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


# Per-client request times, newest last. In-memory and per-process, matching
# the stream-job registry (ADR-0017): chaff is a single-process tool, and a
# multi-worker deploy would need shared state for both.
_HITS: dict[str, deque[float]] = {}
_LOCK = threading.Lock()


def check_prompt(description: str) -> None:
    """Refuse a description too large to be a description."""
    limit = max_chars()
    if len(description) > limit:
        raise DraftRefused(
            f"description is {len(description):,} characters, over the "
            f"{limit:,} limit. Describe the dataset in a sentence or two — "
            "the model drafts a spec, it doesn't read documents.",
            status=413)


def check_rate(client: str | None) -> None:
    """Refuse a request that would exceed this client's share of the budget.

    Counting and recording share one lock hold: counting first and recording
    after would let simultaneous requests all read the same under-cap count
    and all be admitted, which is the same defect the stream-job cap had.
    """
    allowed = rate_per_minute()
    if allowed == 0:
        raise DraftRefused(
            "natural-language drafting is turned off on this server "
            "(CHAFF_DRAFT_RATE_PER_MINUTE=0).",
            status=503)

    key = client or "unknown"
    now = time.monotonic()
    with _LOCK:
        hits = _HITS.setdefault(key, deque())
        while hits and now - hits[0] >= _WINDOW:
            hits.popleft()
        if len(hits) >= allowed:
            wait = int(_WINDOW - (now - hits[0])) + 1
            raise DraftRefused(
                f"too many draft requests — the limit is {allowed} per minute "
                f"and each one costs an LLM call. Try again in {wait}s, or "
                "raise CHAFF_DRAFT_RATE_PER_MINUTE.",
                status=429)
        hits.append(now)
        _prune_locked(now)


def _prune_locked(now: float) -> None:
    """Drop clients with no recent requests, so the map can't grow unbounded.

    Without this, one request each from many addresses would leave an entry
    per address forever — a slow leak with the same shape as the finding this
    module exists to fix.
    """
    stale = [k for k, v in _HITS.items() if not v or now - v[-1] >= _WINDOW]
    for k in stale:
        _HITS.pop(k, None)


def reset() -> None:
    """Forget all recorded requests. For tests; never called by the app."""
    with _LOCK:
        _HITS.clear()
