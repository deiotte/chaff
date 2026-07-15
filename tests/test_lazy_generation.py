"""Lazy generation keystone (ADR-0016): `iter_records` yields the same rows
as the eager builders, one at a time, and any prefix is a deterministic
prefix of the same (possibly infinite) sequence — record[i] depends only on
spec + seed + i, never on `limit` or the wall-clock. `time_limited` bounds a
stream by wall-clock without touching content."""

import itertools
import math

import pytest

from chaff.engine import (
    generate_entity_rows,
    generate_rows,
    iter_entity_rows,
    iter_records,
    iter_rows,
)
from chaff.formats import get_record_encoder
from chaff.sinks import time_limited
from chaff.spec import load_spec


def single_spec(rows=5, seed=7):
    return load_spec({
        "name": "events",
        "seed": seed,
        "rows": rows,
        "columns": [
            {"name": "id", "generator": "row_id"},
            {"name": "who", "generator": "full_name"},
            {"name": "amount", "generator": "float_normal", "params": {"mean": 100, "stddev": 10}},
        ],
        "output": {"format": "ndjson"},
    })


def entity_spec(count=3, ticks=4, seed=7):
    return load_spec({
        "name": "tracks",
        "seed": seed,
        "columns": [
            {"name": "lat", "generator": "float_normal", "params": {"mean": 40, "stddev": 1}},
            {"name": "lon", "generator": "float_normal", "params": {"mean": -70, "stddev": 1}},
        ],
        "output": {"format": "ndjson"},
        "entity": {
            "count": count, "ticks": ticks, "id_pattern": "TRK-####",
            "updates": [{"updater": "movement", "params": {"speed": 0.01}}],
        },
    })


# ── Lazy == eager, byte-for-byte ─────────────────────────────────────

def test_iter_rows_matches_eager_exactly():
    spec = single_spec(rows=50)
    assert list(iter_rows(spec)) == generate_rows(spec)


def test_iter_entity_rows_matches_eager_exactly():
    spec = entity_spec(count=5, ticks=6)
    assert list(iter_entity_rows(spec)) == generate_entity_rows(spec)


def test_iter_rows_encodes_byte_identical_to_eager():
    spec = single_spec(rows=20)
    enc = get_record_encoder("ndjson")
    lazy = b"".join(enc(spec, r) for r in iter_rows(spec))
    eager = b"".join(enc(spec, r) for r in generate_rows(spec))
    assert lazy == eager


# ── limit: caps, extends, and stays a deterministic prefix ───────────

def test_limit_caps_below_natural_length():
    spec = single_spec(rows=100)
    capped = list(iter_records(spec, limit=5))
    assert len(capped) == 5
    assert capped == generate_rows(spec)[:5]


def test_limit_extends_past_natural_length():
    spec = single_spec(rows=3)
    extended = list(iter_records(spec, limit=10))
    assert len(extended) == 10
    # The first `rows` records are exactly the natural output...
    assert extended[:3] == generate_rows(spec)
    # ...and the extension keeps the sequential row_id going.
    assert [r["id"] for r in extended] == list(range(1, 11))


def test_unbounded_prefix_is_deterministic_and_matches_finite_limit():
    spec = single_spec(rows=3)
    first20_a = list(itertools.islice(iter_records(spec, limit=math.inf), 20))
    first20_b = list(itertools.islice(iter_records(spec, limit=math.inf), 20))
    assert first20_a == first20_b                       # same prefix twice
    assert first20_a == list(iter_records(spec, limit=20))  # limit doesn't change content


def test_entity_limit_extends_past_natural_ticks():
    spec = entity_spec(count=2, ticks=2)  # natural length 4
    rows = list(iter_records(spec, limit=10))
    assert len(rows) == 10
    assert rows[:4] == generate_entity_rows(spec)
    # Ticks keep advancing: 2 entities/tick -> ticks 0..4 then start of 5.
    assert [r["tick"] for r in rows] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_entity_unbounded_is_a_live_feed():
    spec = entity_spec(count=2, ticks=3)
    feed = iter_records(spec, limit=math.inf)
    head = list(itertools.islice(feed, 12))
    assert [r["tick"] for r in head] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    assert head[:6] == generate_entity_rows(spec)


# ── time_limited: wall-clock bound, content untouched ────────────────

def test_time_limited_cuts_when_clock_expires():
    clock = {"t": 0.0}

    def fake_now():
        clock["t"] += 1.0  # each check advances 1s
        return clock["t"]

    # duration 3s: yields while elapsed < 3, i.e. checks at 1s and 2s pass,
    # the check at 3s stops. (start reads 0->1, so window is [1, 4).)
    out = list(time_limited([b"a", b"b", b"c", b"d", b"e"], 3, now=fake_now))
    assert out == [b"a", b"b"]


def test_time_limited_no_bound_passes_everything():
    items = [b"a", b"b", b"c"]
    assert list(time_limited(items, None)) == items
    assert list(time_limited(items, 0)) == items
