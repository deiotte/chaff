"""MISB ST 0601 (UAS Datalink) parent frames carrying embedded VMTI.

A VMTI Local Set can travel two ways. `klv` writes the standalone one: its own
Universal Label, its own framing, targets at absolute coordinates. This writes
the other — **ST 0601 Item 74**, where the VMTI set is one item inside a UAS
Datalink frame and the targets are *offsets* from the frame centre the parent
declares.

That is how the standard is most often actually deployed, and it is a
different wire format rather than a dialect of the first: the Universal Label
differs, so a decoder built for one refuses the other outright. Sharing an
encoder id between them would mean a `.klv` file whose format nobody can name
without reading its first sixteen bytes (chaff ADR-0036).

## Why offsets are the whole point

ST 0903.6 §10.2.2.11 says a target's `targetLocationOffsetLat` *"adds to"*
Frame Center Latitude — ST 0601 Item 23 — to give the target's real latitude.
So an embedded child is only half a position. The other half lives in the
parent, and a consumer that cannot pair them has targets it can describe in
every respect except *where they are*.

Everything this module exists to exercise follows from that split:

- **The parent supplies the origin.** Items 23 and 24 are Required parent
  metadata (ST 0903.6 Table 6).
- **A parent may fail to.** `withhold_frame_center_column` writes exactly that
  frame — deliberately non-conforming, because a consumer's handling of a
  non-conforming parent is not testable with conforming input, and "the parent
  stopped sending a frame centre" is one of the two faults the unresolved-
  position signal exists to name.
- **Nothing is malformed either way.** Both sets parse. The fault, when there
  is one, lives in the relationship between them — which is invisible in
  either one alone, and is why this format is worth generating at all.

## Read this before changing an encoding

Same warning as `klv`, doubled. This is a second implementation of a second
binary standard, and ST 0601's normative text is *not* held by the consumer
either — its item encodings are reconstructed from the published convention.
A wrong scale here produces a frame centre that is a perfectly good number in
the right item, and every target in the frame lands somewhere plausible and
wrong. Only a conformance gate that decodes these bytes and scores the
resulting positions can tell.

options:
  centre     frame_center_lat / frame_center_lon (degrees, required), or
             frame_center_lat_column / frame_center_lon_column,
             frame_center_elevation (metres above MSL, item 25),
             withhold_frame_center_column (truthy: omit items 23/24 for
             that frame — a parent that is not conforming, on purpose)
  parent     uas_ls_version (item 65, default 1)
  plus every `klv` option: the child is the same Local Set, and its frames,
  targets, tracks, classes and timing all come from there.
"""

from __future__ import annotations

import math
from typing import Any

from . import encoder, record_encoder
from ._timing import epoch_micros, event_time
from ..spec import DatasetSpec
from .klv import ber_length, checksum_16, frames, local_set

#: SMPTE 336 Universal Label for the UAS Datalink Local Set.
UAS_LS_UL = bytes([
    0x06, 0x0E, 0x2B, 0x34, 0x02, 0x0B, 0x01, 0x01,
    0x0E, 0x01, 0x03, 0x01, 0x01, 0x00, 0x00, 0x00,
])

#: Item 25 maps [-900, 19000] metres onto a uint16.
_ELEVATION_MIN = -900.0
_ELEVATION_SPAN = 19000.0 - _ELEVATION_MIN


def _round_half_away_from_zero(x: float) -> float:
    """Round like the standard's reference implementations do.

    Python's built-in `round` is banker's rounding: `round(0.5)` is 0 and
    `round(1.5)` is 2. Every published ST 0601 mapping — and the decoder these
    bytes are checked against — rounds halves *away* from zero. The two differ
    on exactly one integer in every million, which is precisely the kind of
    disagreement that produces a well-formed packet a reviewer cannot fault
    and a gate reports as a position a metre from where it belongs.
    """
    return math.floor(x + 0.5) if x >= 0.0 else math.ceil(x - 0.5)


def mapped_int32(degrees: float, full_scale: float) -> bytes:
    """ST 0601's signed-int32 angle mapping, over ±`full_scale` degrees.

    Saturating rather than wrapping: a latitude past the pole is a spec error,
    and clamping it keeps the packet decodable so the gate can say so. The
    reserved `0x80000000` "error" encoding is never produced here — that value
    means *no data*, and emitting it for an out-of-range number would turn a
    bad coordinate into a claim of absence.
    """
    if not math.isfinite(degrees):
        degrees = 0.0
    scaled = _round_half_away_from_zero(degrees * 2_147_483_647.0 / full_scale)
    clamped = int(min(max(scaled, -2_147_483_647.0), 2_147_483_647.0))
    return clamped.to_bytes(4, "big", signed=True)


def _elevation(metres: float) -> bytes:
    """Item 25 — frame centre elevation, metres above MSL, as a uint16."""
    if not math.isfinite(metres):
        metres = 0.0
    raw = _round_half_away_from_zero((metres - _ELEVATION_MIN) * 65535.0 / _ELEVATION_SPAN)
    return int(min(max(raw, 0.0), 65535.0)).to_bytes(2, "big")


def tlv(tag: int, value: bytes) -> bytes:
    """An ST 0601 item.

    The tag is a single byte, **not** the BER-OID `klv.tlv` writes: ST 0601
    numbers its items into a one-byte space over the range anything here
    touches, and borrowing VMTI's encoder would produce identical bytes for
    small tags and diverge silently above 127.
    """
    return bytes([tag]) + ber_length(len(value)) + value


def _opt(spec: DatasetSpec, key: str, default: Any = None) -> Any:
    return spec.output.options.get(key, default)


def _number(spec: DatasetSpec, row: dict, option: str, column_option: str) -> float | None:
    """A frame-level number: a fixed option value, or a column read per frame."""
    column = _opt(spec, column_option)
    raw = row.get(column) if column else _opt(spec, option)
    try:
        value = None if raw is None else float(raw)
    except (TypeError, ValueError):
        return None
    return value if value is not None and math.isfinite(value) else None


def frame_centre(spec: DatasetSpec, rows: list[dict]) -> tuple[float, float]:
    """Where the camera is looking for this frame, in degrees.

    Required. A spec that names this format without a frame centre is asking
    for offsets from an origin it never states, and every target in the file
    would be a displacement from nothing. Refused here rather than defaulted
    to (0, 0), which would put an entire scene in the Gulf of Guinea and look
    like data.
    """
    lat = _number(spec, rows[0], "frame_center_lat", "frame_center_lat_column")
    lon = _number(spec, rows[0], "frame_center_lon", "frame_center_lon_column")
    if lat is None or lon is None:
        raise ValueError(
            "a klv0601 spec needs a frame centre: set `frame_center_lat` and "
            "`frame_center_lon` (or the `_column` forms). Embedded VMTI targets "
            "are offsets from it, so without one they are displacements from an "
            "origin the packet never states."
        )
    return lat, lon


#: Column values that mean "this frame's parent DID declare its frame centre".
#:
#: Plain truthiness is wrong here, and quietly so. The natural way to vary this
#: per frame is a `lifecycle` updater, which holds *state names* — and every
#: non-empty string is truthy, so a frame in the `declared` state would read as
#: withheld and the file would carry no frame centre anywhere. Naming the false
#: values is the fix; guessing at them is the bug.
_DECLARED = frozenset({"", "0", "false", "no", "off", "declared", "none"})


def _withheld(spec: DatasetSpec, row: dict) -> bool:
    """Whether this frame's parent declines to declare its frame centre.

    ST 0903.6 Table 6 makes Frame Center Latitude and Longitude **Required**
    parent metadata, so a frame this returns `True` for is deliberately
    non-conforming. That is the point: a consumer's handling of a parent that
    stops supplying an origin cannot be exercised with conforming input, and
    "the parent is not sending a frame centre" is one of exactly two faults
    that leave a well-formed child with nowhere to be.
    """
    column = _opt(spec, "withhold_frame_center_column")
    if not column:
        return False
    value = row.get(column)
    if isinstance(value, str):
        return value.strip().lower() not in _DECLARED
    return bool(value)


def parent_packet(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """One UAS Datalink packet carrying one VMTI Local Set as Item 74."""
    centre = frame_centre(spec, rows)
    items: list[bytes] = []

    # Item 2 — Precision Time Stamp. Required parent metadata (Table 6), and
    # the fallback a child with no timestamp of its own inherits.
    items.append(tlv(2, epoch_micros(event_time(spec, rows[0])).to_bytes(8, "big")))

    # Items 23, 24 — the frame centre, unless this frame withholds it. Both or
    # neither: a latitude with no longitude is not an origin, and a consumer
    # pairing it with an implicit zero would put the whole frame on the
    # Greenwich meridian rather than declining to place it.
    if not _withheld(spec, rows[0]):
        items.append(tlv(23, mapped_int32(centre[0], 90.0)))
        items.append(tlv(24, mapped_int32(centre[1], 180.0)))
        elevation = _number(spec, rows[0], "frame_center_elevation",
                            "frame_center_elevation_column")
        if elevation is not None:
            items.append(tlv(25, _elevation(elevation)))

    items.append(tlv(65, bytes([min(max(int(_opt(spec, "uas_ls_version", 1)), 0), 255)])))

    # Item 74 — the child, offsets computed against the frame centre whether or
    # not the parent above declared it. That is deliberate: withholding must
    # change the parent alone, so the child bytes of a resolvable frame and an
    # unresolvable one are identical and the difference is provably the parent's.
    items.append(tlv(74, local_set(spec, rows, centre)))

    body = b"".join(items)
    # The checksum item is `01 02 hi lo` and lives inside the declared length.
    declared = len(body) + 4
    packet = UAS_LS_UL + ber_length(declared) + body + b"\x01\x02"
    return packet + checksum_16(packet).to_bytes(2, "big")


@record_encoder("klv0601")
def _st0601_record(spec: DatasetSpec, record: dict) -> bytes:
    """One parent frame carrying one target.

    Refused for a framed spec for the same reason `klv` refuses it: a frame is
    several records and the per-record seam delivers one, so streaming would
    quietly emit one-target frames that contradict the spec's own framing.
    """
    if _opt(spec, "frame_column"):
        raise ValueError(
            "a klv0601 spec with `frame_column` groups rows into multi-target frames, "
            "which a per-record streaming sink cannot express: it delivers one record "
            "at a time. Drop `frame_column` to stream one target per frame, or use a "
            "file sink to keep the framing."
        )
    return parent_packet(spec, [record])


@encoder("klv0601", ".klv")
def to_st0601(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """A UAS Datalink stream: parent frames, concatenated, each carrying VMTI."""
    return b"".join(parent_packet(spec, frame) for frame in frames(spec, rows))
