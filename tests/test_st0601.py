"""MISB ST 0601 parent frames carrying embedded VMTI (Phase 9, ADR-0036)."""

import pytest

from chaff.formats import get_encoder, get_record_encoder
from chaff.formats.klv import ber_length, checksum_16, local_set
from chaff.formats.st0601 import (
    UAS_LS_UL,
    _elevation,
    _round_half_away_from_zero,
    frame_centre,
    mapped_int32,
    parent_packet,
    tlv,
)
from chaff.spec import load_spec

# Reference vectors printed by the SHIPPED `st0601::build::UasBuilder` in the
# consuming repository — the decoder's own encoder, run against these exact
# inputs. This is the load-bearing test in this file for the same reason the
# IMAPB table is load-bearing in `test_klv.py`: a wrong angle scale produces a
# frame centre that is a perfectly well-formed signed integer in the right
# item, every target in the frame lands somewhere plausible, and nothing about
# the bytes says otherwise.
LAT_VECTORS = [(34.07, "30748229"), (0.0, "00000000"), (-0.0000001, "fffffffe"),
               (90.0, "7fffffff"), (-90.0, "80000001")]
LON_VECTORS = [(-118.26, "abe76c8c"), (179.9999999, "7ffffffe")]
ELEVATION_VECTORS = [(210.0, "0e47"), (-900.0, "0000"), (19000.0, "ffff"),
                     (0.0, "0b94"), (100.5, "0cdf")]

#: A whole packet from the same builder: timestamp, frame centre, elevation,
#: version, a two-byte Item 74 stand-in, and the trailing checksum.
REFERENCE_PACKET = (
    "060e2b34020b01010e0103010100000025020800064748462040001704307482291804abe76c8c"
    "19020e474101014a02aabb01020877"
)


def embedded_spec(rows=4, **opts):
    options = {
        "base_time": "2026-01-01T00:00:00Z", "tick_column": "t",
        "frame_center_lat": 34.07, "frame_center_lon": -118.26,
    }
    options.update(opts)
    return load_spec({
        "name": "embedded", "seed": 5, "rows": rows,
        "columns": [
            {"name": "target_id", "generator": "row_id"},
            {"name": "lat", "generator": "lat", "params": {"min": 34.06, "max": 34.08}},
            {"name": "lon", "generator": "lon", "params": {"min": -118.27, "max": -118.25}},
        ],
        "output": {"format": "klv0601", "options": options},
    })


def rows_in(spec):
    from chaff.engine import generate_rows
    return generate_rows(spec)


def items(body: bytes) -> dict[int, bytes]:
    """Walk an ST 0601 Local Set body into {tag: value}.

    A real walker rather than a substring search: an item's value can contain
    any byte, so `b"\\x17" in body` is a coin flip, and a test that passed that
    way would keep passing after the item stopped being emitted.
    """
    out, i = {}, 0
    while i < len(body):
        tag = body[i]
        first = body[i + 1]
        if first & 0x80:
            count = first & 0x7F
            length = int.from_bytes(body[i + 2:i + 2 + count], "big")
            head = i + 2 + count
        else:
            length, head = first, i + 2
        out[tag] = body[head:head + length]
        i = head + length
    return out


def parent_body(packet: bytes) -> bytes:
    """The Local Set body, past the universal label and its BER length."""
    first = packet[len(UAS_LS_UL)]
    offset = len(UAS_LS_UL) + 1 + (first & 0x7F if first & 0x80 else 0)
    return packet[offset:]


def parents(payload: bytes) -> list[bytes]:
    starts = [i for i in range(len(payload) - len(UAS_LS_UL) + 1)
              if payload[i:i + len(UAS_LS_UL)] == UAS_LS_UL]
    return [payload[s:e] for s, e in zip(starts, starts[1:] + [len(payload)])]


# ── Against the other implementation ─────────────────────────────────

@pytest.mark.parametrize("degrees,expected", LAT_VECTORS)
def test_frame_centre_latitude_matches_the_reference_implementation(degrees, expected):
    assert mapped_int32(degrees, 90.0).hex() == expected


@pytest.mark.parametrize("degrees,expected", LON_VECTORS)
def test_frame_centre_longitude_matches_the_reference_implementation(degrees, expected):
    assert mapped_int32(degrees, 180.0).hex() == expected


@pytest.mark.parametrize("metres,expected", ELEVATION_VECTORS)
def test_frame_centre_elevation_matches_the_reference_implementation(metres, expected):
    assert _elevation(metres).hex() == expected


def test_a_whole_packet_matches_the_reference_implementation():
    """Framing as well as values: label, BER length, item order, and the
    checksum's coverage — which spans the checksum item's own tag and length
    byte but not its value."""
    body = (tlv(2, (1_767_225_600_000_000).to_bytes(8, "big"))
            + tlv(23, mapped_int32(34.07, 90.0))
            + tlv(24, mapped_int32(-118.26, 180.0))
            + tlv(25, _elevation(210.0))
            + tlv(65, bytes([1]))
            + tlv(74, bytes([0xAA, 0xBB])))
    packet = UAS_LS_UL + ber_length(len(body) + 4) + body + b"\x01\x02"
    packet += checksum_16(packet).to_bytes(2, "big")
    assert packet.hex() == REFERENCE_PACKET


def test_the_universal_label_is_the_uas_datalink_one():
    """Not VMTI's. The label is the only thing distinguishing the two framings
    on the wire, and a decoder for one refuses the other on the strength of it
    — which is exactly why these are separate formats (ADR-0036)."""
    from chaff.formats.klv import VMTI_UL
    assert UAS_LS_UL.hex() == "060e2b34020b01010e01030101000000"
    assert UAS_LS_UL != VMTI_UL


# ── The rounding trap ────────────────────────────────────────────────

def test_halves_round_away_from_zero_not_to_even():
    """Python's built-in `round` is banker's rounding and the standard's
    reference implementations are not. Using the built-in would agree with the
    decoder on every value except the exact halves, which is the worst possible
    place to disagree: rare enough to survive every test written by hand, and
    silent when it happens."""
    for value, away in [(0.5, 1), (1.5, 2), (2.5, 3), (-0.5, -1), (-2.5, -3)]:
        assert _round_half_away_from_zero(value) == away
    # The behaviour actually differs — otherwise this test proves nothing.
    assert round(0.5) == 0 and round(2.5) == 2


def test_the_reserved_error_encoding_is_never_produced():
    """`0x80000000` means *no data* in ST 0601. Saturating an out-of-range
    coordinate onto it would turn a bad number into a claim of absence, which
    a consumer would believe."""
    for degrees in (-90.0, -1e9, 90.0, 1e9, float("nan"), float("-inf")):
        assert mapped_int32(degrees, 90.0) != b"\x80\x00\x00\x00"


# ── Tags ─────────────────────────────────────────────────────────────

def test_item_tags_are_single_bytes_not_ber_oid():
    """ST 0601 numbers its items into a one-byte space. VMTI's BER-OID encoder
    agrees for every tag below 128 and diverges silently above, so the two are
    interchangeable exactly until they are not."""
    from chaff.formats.klv import tlv as vmti_tlv
    assert tlv(74, b"\xaa") == b"\x4a\x01\xaa"
    assert tlv(127, b"\xaa") == vmti_tlv(127, b"\xaa")
    assert tlv(128, b"\xaa") != vmti_tlv(128, b"\xaa")


# ── The parent/child split ───────────────────────────────────────────

def test_the_parent_carries_the_child_as_item_74():
    spec = embedded_spec()
    rows = rows_in(spec)
    body = items(parent_body(parent_packet(spec, rows)))
    # Tag 1 is the checksum, which lives inside the declared length.
    assert set(body) == {1, 2, 23, 24, 65, 74}
    assert body[74] == local_set(spec, rows, (34.07, -118.26))


def test_targets_carry_offsets_and_not_an_absolute_location():
    """Never both. A consumer that sees item 17 prefers it, so emitting both
    would leave the offsets dead weight — and the embedded path untested by
    the very packets written to exercise it."""
    spec = embedded_spec()
    child = items(parent_body(parent_packet(spec, rows_in(spec))))[74]
    targets = items(child)[101]
    # First VTarget in the series: a BER length, a BER-OID id, then its items.
    first = targets[1:targets[0] + 1]
    target_items = items(first[1:])
    assert 10 in target_items and 11 in target_items
    assert 17 not in target_items


def test_the_standalone_encoder_still_writes_absolute_locations():
    """The refactor that gave `klv` a reusable body must not have changed what
    `klv` emits."""
    from chaff.formats.klv import VMTI_UL, _packet
    spec = embedded_spec()
    packet = _packet(spec, rows_in(spec))
    first = packet[len(VMTI_UL)]
    start = len(VMTI_UL) + 1 + (first & 0x7F if first & 0x80 else 0)
    targets = items(packet[start:])[101]
    first = targets[1:targets[0] + 1]
    target_items = items(first[1:])
    assert 17 in target_items
    assert 10 not in target_items and 11 not in target_items


def test_withholding_the_frame_centre_changes_the_parent_and_only_the_parent():
    """The decisive property of this format, and the thing a standalone feed
    cannot express at all: the same child bytes are a set of positions or no
    positions at all depending on a *different* set's items."""
    rows = rows_in(embedded_spec())
    declared = embedded_spec(withhold_frame_center_column="hide")
    withheld = embedded_spec(withhold_frame_center_column="hide")
    open_rows = [dict(r, hide="declared") for r in rows]
    shut_rows = [dict(r, hide="withheld") for r in rows]

    open_body = items(parent_body(parent_packet(declared, open_rows)))
    shut_body = items(parent_body(parent_packet(withheld, shut_rows)))

    assert 23 in open_body and 24 in open_body
    assert 23 not in shut_body and 24 not in shut_body
    assert open_body[74] == shut_body[74], "the child must be byte-identical"


def test_a_state_name_is_not_read_as_truthiness():
    """`lifecycle` is the natural way to vary this per frame and it holds state
    *names*. Every non-empty string is truthy, so a naive `bool()` would read
    the `declared` state as withheld and the file would carry no frame centre
    anywhere — while still decoding perfectly."""
    spec = embedded_spec(withhold_frame_center_column="hide")
    rows = rows_in(spec)
    for value in ("declared", "", "no", "false", "0", "OFF", None, False, 0):
        body = items(parent_body(parent_packet(spec, [dict(r, hide=value) for r in rows])))
        assert 23 in body, f"{value!r} should mean the centre IS declared"
    for value in ("withheld", "yes", "true", 1, True):
        body = items(parent_body(parent_packet(spec, [dict(r, hide=value) for r in rows])))
        assert 23 not in body, f"{value!r} should mean the centre is withheld"


# ── Refusals ─────────────────────────────────────────────────────────

def test_a_spec_without_a_frame_centre_is_refused():
    """Defaulting to (0, 0) would put an entire scene in the Gulf of Guinea and
    look exactly like data."""
    spec = load_spec({
        "name": "no-centre", "seed": 1, "rows": 2,
        "columns": [{"name": "lat", "generator": "lat"}],
        "output": {"format": "klv0601", "options": {}},
    })
    with pytest.raises(ValueError, match="frame centre"):
        frame_centre(spec, rows_in(spec))


def test_a_framed_spec_cannot_stream():
    spec = embedded_spec(frame_column="t")
    with pytest.raises(ValueError, match="frame_column"):
        get_record_encoder("klv0601")(spec, rows_in(spec)[0])


# ── Stream shape and determinism ─────────────────────────────────────

def test_one_parent_frame_per_frame():
    spec = embedded_spec(rows=6, frame_column="t")
    rows = rows_in(spec)
    for index, row in enumerate(rows):
        row["t"] = index // 3          # two frames of three targets
    payload = get_encoder("klv0601")(spec, rows)
    assert len(parents(payload)) == 2


def test_seed_determinism_is_byte_for_byte():
    spec = embedded_spec()
    first = get_encoder("klv0601")(spec, rows_in(spec))
    second = get_encoder("klv0601")(embedded_spec(), rows_in(embedded_spec()))
    assert first == second
