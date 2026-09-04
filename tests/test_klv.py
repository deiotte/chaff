"""MISB ST 0903.6 (VMTI) KLV encoder (Phase 9, ADR-0034)."""

import math

import pytest

from chaff.engine import generate_entity_rows, generate_rows
from chaff.formats import get_encoder, get_record_encoder, list_record_formats
from chaff.formats.klv import (
    VMTI_UL,
    Imapb,
    ber_length,
    ber_oid,
    checksum_16,
    series,
    tlv,
    uint,
)
from chaff.spec import load_spec

# Reference vectors produced by the ST 1201 implementation the consuming
# decoder uses, keyed by `(a, b, length)`. This is the load-bearing test in
# this file: an IMAPB scaling error does not produce a malformed packet, it
# produces a well-formed number meaning something else, and no amount of
# reading the bytes reveals it. Pinning against the other implementation is
# the only local check that can catch one (ADR-0034).
IMAPB_VECTORS = {
    (-90.0, 90.0, 4): [(0.0, "2d000000"), (34.075577, "3e09ac81"), (-33.9, "1c0ccccc"),
                       (89.9999, "59fffcb9"), (-90.0, "00000000"), (90.0, "5a000000")],
    (-180.0, 180.0, 4): [(0.0, "2d000000"), (-118.279, "0f6e24dd"), (179.9999, "59fffe5c"),
                         (-180.0, "00000000"), (180.0, "5a000000")],
    (-900.0, 19000.0, 2): [(-900.0, "0000"), (0.0, "0384"), (82.0, "03d6"),
                           (140.5, "0410"), (19000.0, "4dbc")],
    (0.0, 650.0, 2): [(0.0, "0000"), (7.13, "00e4"), (18.94, "025e"),
                      (28.04, "0381"), (650.0, "5140")],
    (-900.0, 900.0, 2): [(-900.0, "0000"), (-4.06, "37ff"), (0.0, "3840"),
                         (2.8206, "386d"), (900.0, "7080")],
    (0.0, 100.0, 3): [(0.0, "000000"), (38.05, "260ccc"), (68.64, "44a3d7"),
                      (79.64, "4fa3d7"), (100.0, "640000")],
    (0.0, 180.0, 2): [(0.0, "0000"), (13.5, "06c0"), (24.0, "0c00"), (180.0, "5a00")],
    (-19.2, 19.2, 3): [(-19.2, "000000"), (0.0, "266667"), (1.5, "296667"),
                       (19.2, "4ccccd")],
}


def target_spec(rows=3, **opts):
    options = {"base_time": "2026-01-01T00:00:00Z", "tick_column": "t"}
    options.update(opts)
    return load_spec({
        "name": "vmti", "seed": 5, "rows": rows,
        "columns": [
            {"name": "target_id", "generator": "row_id"},
            {"name": "lat", "generator": "lat", "params": {"min": 34.0, "max": 34.1}},
            {"name": "lon", "generator": "lon", "params": {"min": -118.3, "max": -118.2}},
        ],
        "output": {"format": "klv", "options": options},
    })


def body_of(packet: bytes) -> bytes:
    """The Local Set body: past the universal label and its BER length.

    Parsed rather than assumed — the length is short form for a small packet
    and long form for a large one, and a test that hardcoded either would pass
    for the wrong reason on the other.
    """
    first = packet[len(VMTI_UL)]
    offset = len(VMTI_UL) + 1 + (first & 0x7F if first & 0x80 else 0)
    return packet[offset:]


def packets(payload: bytes) -> list[bytes]:
    """Split a concatenated stream on the universal label."""
    starts = [i for i in range(len(payload) - len(VMTI_UL) + 1)
              if payload[i:i + len(VMTI_UL)] == VMTI_UL]
    return [payload[s:e] for s, e in zip(starts, starts[1:] + [len(payload)])]


# ── ST 1201 IMAPB, against the other implementation ──────────────────

@pytest.mark.parametrize("mapping", sorted(IMAPB_VECTORS))
def test_imapb_matches_the_reference_implementation(mapping):
    a, b, length = mapping
    imapb = Imapb(a, b, length)
    for value, expected in IMAPB_VECTORS[mapping]:
        assert imapb.encode(value).hex() == expected, f"IMAPB{mapping} of {value}"


def test_imapb_saturates_rather_than_raising():
    """A spec asking for a value the standard cannot carry still produces a
    decodable packet; the clamp is visible in the output where an exception
    mid-run would not be."""
    imapb = Imapb(0.0, 100.0, 3)
    assert imapb.encode(1e9) == imapb.encode(100.0)
    assert imapb.encode(-1e9) == imapb.encode(0.0)
    assert imapb.encode(float("nan")) == imapb.encode(0.0)


def test_imapb_rejects_a_range_it_cannot_map():
    for bad in ((1.0, 1.0, 2), (5.0, 1.0, 2), (0.0, 1.0, 0), (0.0, 1.0, 9)):
        with pytest.raises(ValueError):
            Imapb(*bad)


# ── KLV primitives ───────────────────────────────────────────────────

def test_ber_length_short_and_long_form():
    assert ber_length(0) == b"\x00"
    assert ber_length(127) == b"\x7f"
    assert ber_length(128) == b"\x81\x80"       # long form begins at 0x80
    assert ber_length(291) == b"\x82\x01\x23"


def test_ber_oid_is_base_128_with_continuation_bits():
    assert ber_oid(0) == b"\x00"
    assert ber_oid(1) == b"\x01"
    assert ber_oid(127) == b"\x7f"
    assert ber_oid(128) == b"\x81\x00"
    assert ber_oid(300) == b"\x82\x2c"


def test_uint_is_the_fewest_bytes_that_hold_it():
    assert uint(0) == b"\x00"
    assert uint(255) == b"\xff"
    assert uint(256) == b"\x01\x00"


def test_tlv_and_series_framing():
    assert tlv(2, b"\x01\x02") == b"\x02\x02\x01\x02"
    assert series([b"ab", b"cde"]) == b"\x02ab\x03cde"


def test_checksum_is_a_wrapping_16_bit_sum():
    assert checksum_16(b"\x01\x02\x03") == 6
    assert checksum_16(b"\xff" * 300) == (255 * 300) & 0xFFFF


# ── Packet shape ─────────────────────────────────────────────────────

def test_klv_is_record_capable():
    assert "klv" in list_record_formats()


def test_a_packet_is_a_label_a_length_a_body_and_a_checksum():
    spec = target_spec(rows=1)
    payload = get_encoder("klv")(spec, generate_rows(spec))
    assert payload.startswith(VMTI_UL)
    # The declared length must cover exactly the bytes that follow it.
    first = payload[len(VMTI_UL)]
    width = first & 0x7F if first & 0x80 else 0
    declared = (int.from_bytes(payload[len(VMTI_UL) + 1:len(VMTI_UL) + 1 + width], "big")
                if width else first)
    assert declared == len(body_of(payload))
    # The trailing checksum item, and a checksum that verifies.
    assert payload[-4:-2] == b"\x01\x02"
    assert checksum_16(payload[:-2]) == int.from_bytes(payload[-2:], "big")


def test_the_timestamp_leads_the_local_set():
    """ST 0903.6 §10.1.2 requires it, and a consumer that finds no timestamp
    refuses the packet outright."""
    spec = target_spec(rows=1)
    body = body_of(get_encoder("klv")(spec, generate_rows(spec)))
    assert body[0] == 0x02 and body[1] == 0x08


def test_time_comes_from_the_data_not_the_clock():
    spec = load_spec({
        "name": "vmti", "seed": 1,
        "columns": [{"name": "lat", "generator": "lat"}, {"name": "lon", "generator": "lon"}],
        "output": {"format": "klv", "options": {
            "tick_column": "t", "base_time": "2026-01-01T00:00:00Z", "interval_seconds": 10}},
        "entity": {"count": 1, "ticks": 3, "tick_column": "t",
                   "updates": [{"updater": "movement", "params": {"speed": 0.0001}}]},
    })
    rows = generate_entity_rows(spec)
    stamps = []
    for packet in packets(get_encoder("klv")(spec, rows)):
        body = body_of(packet)
        assert body[0] == 0x02
        stamps.append(int.from_bytes(body[2:10], "big"))
    assert stamps[1] - stamps[0] == 10_000_000       # microseconds, from the tick
    assert stamps[2] - stamps[1] == 10_000_000


def test_record_and_blob_agree():
    spec = target_spec(rows=4)
    rows = generate_rows(spec)
    assert get_encoder("klv")(spec, rows) == b"".join(
        get_record_encoder("klv")(spec, r) for r in rows)


def test_encoding_is_deterministic():
    spec = target_spec(rows=4)
    assert get_encoder("klv")(spec, generate_rows(spec)) == \
        get_encoder("klv")(spec, generate_rows(spec))


# ── Absence stays absence ────────────────────────────────────────────

def test_an_absent_column_omits_its_item_rather_than_sending_zero():
    """A Local Set expresses absence by leaving the item out. Sending a zero
    would be a measurement a consumer cannot distinguish from a real one."""
    bare = get_encoder("klv")(target_spec(rows=1), generate_rows(target_spec(rows=1)))
    spec = load_spec({
        "name": "vmti", "seed": 5, "rows": 1,
        "columns": [
            {"name": "target_id", "generator": "row_id"},
            {"name": "lat", "generator": "lat", "params": {"min": 34.0, "max": 34.1}},
            {"name": "lon", "generator": "lon", "params": {"min": -118.3, "max": -118.2}},
            {"name": "priority", "generator": "int_range", "params": {"min": 7, "max": 7}},
        ],
        "output": {"format": "klv", "options": {"base_time": "2026-01-01T00:00:00Z"}},
    })
    with_priority = get_encoder("klv")(spec, generate_rows(spec))
    assert len(with_priority) > len(bare)


def test_a_non_finite_value_never_reaches_the_wire():
    spec = target_spec(rows=1)
    row = {"target_id": 1, "lat": float("nan"), "lon": -118.25}
    # A NaN latitude saturates to the mapping's floor rather than emitting
    # a value no consumer can read; the packet still verifies.
    payload = get_record_encoder("klv")(spec, row)
    assert checksum_16(payload[:-2]) == int.from_bytes(payload[-2:], "big")


def test_sigmas_are_all_or_nothing():
    """The 16-byte location pack carries three sigmas; a partial set would
    have to invent the missing ones, so the 10-byte truncation is used."""
    spec = target_spec(rows=1)
    base = {"target_id": 1, "lat": 34.05, "lon": -118.25, "hae": 80.0}
    short = get_record_encoder("klv")(spec, base)
    partial = get_record_encoder("klv")(spec, {**base, "sigma_east": 5.0})
    full = get_record_encoder("klv")(
        spec, {**base, "sigma_east": 5.0, "sigma_north": 6.0, "sigma_up": 7.0})
    assert len(short) == len(partial)
    assert len(full) == len(short) + 6


def test_classes_carry_one_confidence_each():
    """ST 0903.6 §7.3 lets one target hold several competing labels. Collapsing
    them to a winner is the loss a consumer cannot undo."""
    spec = target_spec(rows=1, classes=[
        {"iri": "https://example.invalid/ontology#Vehicle", "confidence_column": "cv"},
        {"iri": "https://example.invalid/ontology#Car", "confidence_column": "cc"},
    ])
    row = {"target_id": 1, "lat": 34.05, "lon": -118.25, "cv": 90.0, "cc": 70.0}
    both = get_record_encoder("klv")(spec, row)
    one = get_record_encoder("klv")(spec, {**row, "cc": None})
    assert len(both) > len(one)
    assert b"ontology#Vehicle" in both and b"ontology#Car" in both


def test_the_ontology_vocabulary_comes_from_the_spec():
    """The IRIs are the consumer's vocabulary. A generator that shipped a
    program's ontology would be carrying that program's domain knowledge."""
    import chaff.formats.klv as klv_module
    source = open(klv_module.__file__).read()
    assert "example.invalid" not in source
    assert "ontology#" not in source


def test_a_numeric_string_id_is_read_not_discarded():
    """`id_pattern: "#####"` is how a spec expresses a sensor-local numeric id.
    Discarding it would give every target in a scene the same id, and a
    consumer would see one entity where there are several."""
    spec = target_spec(rows=1)
    base = {"lat": 34.05, "lon": -118.25}
    a = get_record_encoder("klv")(spec, {**base, "target_id": "48213"})
    b = get_record_encoder("klv")(spec, {**base, "target_id": "48214"})
    assert a != b
    same = get_record_encoder("klv")(spec, {**base, "target_id": 48213})
    assert a == same


def test_a_non_numeric_id_falls_back_rather_than_being_invented():
    """A target id is source-local and numeric in ST 0903.6. Hashing a string
    into one would be an invented identity that looked stable."""
    spec = target_spec(rows=1)
    base = {"lat": 34.05, "lon": -118.25}
    named = get_record_encoder("klv")(spec, {**base, "target_id": "RAVEN-7"})
    positional = get_record_encoder("klv")(spec, {**base, "target_id": None})
    assert named == positional


def test_distinct_targets_get_distinct_ids():
    """The regression guard for a bug that produced perfectly well-formed
    packets in which every target claimed to be the same entity: `target_id`
    was missing from the id fallbacks, so each one-target frame numbered its
    target by position, which is always 1."""
    spec = target_spec(rows=6)
    rows = generate_rows(spec)
    ids = set()
    for packet in packets(get_encoder("klv")(spec, rows)):
        body = body_of(packet)
        marker = body.index(0x65)                      # item 101, the target series
        # tag, length, then the series element's own length, then the BER-OID id
        ids.add(body[marker + 3])
    assert len(ids) == len(rows), f"{len(ids)} distinct ids across {len(rows)} targets"
