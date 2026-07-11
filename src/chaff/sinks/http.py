"""HTTP POST streaming sink. See sinks/AGENTS.md.

Delivers encoded records to a developer's endpoint, one record per request
or in batches, with a retry policy and auth-header passthrough. httpx is
heavy/optional, so it lives under the `streaming` extra and is imported
lazily — core `import chaff.sinks` stays dep-free.

INV-2: this sink groups and delivers record-bytes; it never parses or
transforms them. Secrets (auth headers) come from options/env and are
never echoed into the receipt (sinks/AGENTS.md).

options:
  url (required), method (default POST), batch_size (default 1),
  headers (dict — e.g. {"Authorization": "Bearer ..."}),
  content_type (default application/x-ndjson),
  timeout (s, default 30), max_retries (default 3), backoff (s, default 0.5).
Network/5xx failures retry with linear backoff; 4xx fails loudly (a
misconfigured endpoint should not look like a silent success).
"""

from __future__ import annotations

import time
from typing import Iterator

from . import stream_sink
from ..spec import DatasetSpec


def _batched(records: Iterator[bytes], n: int) -> Iterator[list[bytes]]:
    batch: list[bytes] = []
    for rec in records:
        batch.append(rec)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


@stream_sink("http")
def http_sink(spec: DatasetSpec, records: Iterator[bytes]) -> str:
    try:
        import httpx
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "http sink needs httpx. Install it with: pip install 'chaff[streaming]'"
        ) from e

    opts = spec.sink.options
    url = opts.get("url")
    if not url:
        raise ValueError("http sink requires options.url")
    method = str(opts.get("method", "POST")).upper()
    batch_size = max(1, int(opts.get("batch_size", 1)))
    headers = dict(opts.get("headers", {}))
    headers.setdefault("Content-Type", str(opts.get("content_type", "application/x-ndjson")))
    timeout = float(opts.get("timeout", 30))
    max_retries = int(opts.get("max_retries", 3))
    backoff = float(opts.get("backoff", 0.5))

    sent = 0
    requests = 0
    with httpx.Client(timeout=timeout) as client:
        for batch in _batched(records, batch_size):
            body = b"".join(batch)
            _send_with_retry(httpx, client, method, url, body, headers, max_retries, backoff)
            sent += len(batch)
            requests += 1

    return f"delivered {sent} record(s) in {requests} request(s) -> {url}"


def _send_with_retry(httpx, client, method, url, body, headers, max_retries, backoff) -> None:
    """POST with retries on transport errors and 5xx; raise loudly on 4xx."""
    attempt = 0
    while True:
        try:
            resp = client.request(method, url, content=body, headers=headers)
        except httpx.TransportError as e:
            if attempt >= max_retries:
                raise RuntimeError(f"http sink: {url} unreachable after {attempt + 1} attempts ({e})") from e
            time.sleep(backoff * (attempt + 1))
            attempt += 1
            continue

        if 400 <= resp.status_code < 500:
            # Client error: our request is wrong; retrying won't help. Fail loud.
            raise RuntimeError(f"http sink: {url} returned {resp.status_code} (not retrying 4xx)")
        if resp.status_code >= 500:
            if attempt >= max_retries:
                raise RuntimeError(f"http sink: {url} returned {resp.status_code} after {attempt + 1} attempts")
            time.sleep(backoff * (attempt + 1))
            attempt += 1
            continue
        return  # 2xx/3xx: delivered
