"""MISB ST 0903.6 (VMTI) KLV encoder. See formats/AGENTS.md.

VMTI is what a video motion-detection sensor puts on the wire: a binary
Local Set, SMPTE 336 KLV framing, one packet per frame carrying the targets
that frame contained. Where CoT is one entity saying where it is, VMTI is a
sensor reporting what it *found* — priorities, pixel positions, competing
classification hypotheses, each with its own confidence.

Pure function, stdlib only (INV-2), no heavy dep: BER-TLV is arithmetic and
so is ST 1201's IMAPB.

## Read this before changing an encoding

**This is a second implementation of a published binary standard**, and the
first one is a decoder somebody else maintains. When the two disagree it is a
coin flip which is wrong, and the bytes give no hint — a mis-scaled IMAPB
value is a perfectly well-formed number in the right place meaning something
else entirely. Nothing here is safe on inspection; it is safe because a
conformance gate decodes what it produces and scores the result. Change a
range, a length or a tag and that gate is the only thing standing between the
change and a demo that silently lies. (chaff ADR-0034.)

## Absence is absence

Every optional item comes from a column and is **omitted** when the row does
not carry one — never emitted as zero. A consumer can reason about a missing
item; it cannot tell a fabricated zero from a measured one. Same rule the CoT
encoder follows via its sentinel, expressed here by leaving the item out,
which is what a Local Set gives you instead.

options:
  frame      system_name, ls_version (1), source_sensor, frame_width,
             frame_height, horizontal_fov, vertical_fov
  target     target_id_column (auto: track_id/entity_id/uid/id),
             lat_column ('lat'), lon_column ('lon'), hae_column ('hae'),
             sigma_east_column / sigma_north_column / sigma_up_column,
             priority_column, confidence_column, history_column,
             percent_pixels_column, intensity_column,
             pixel_row_column, pixel_col_column, detection_status_column
  track      speed_column, course_column, track_confidence_column
  classes    [{iri, confidence_column}] — one competing hypothesis each. The
             IRIs are the CONSUMER's vocabulary and belong in the spec, never
             in this file: a generator that shipped a program's ontology would
             be carrying that program's domain knowledge (chaff ADR-0034).
  time       time_column, tick_column ('tick'/'t'), base_time, interval_seconds
"""

from __future__ import annotations

import math
from typing import Any

from . import encoder, record_encoder
from ._timing import epoch_micros, event_time, first_key, optional_num
from ..spec import DatasetSpec

# `target_id` leads: it is what ST 0903.6 calls this field, so it is the name a
# spec written for VMTI reaches for first. Leaving it out is not a cosmetic
# miss — every target falls back to its position in the packet, which for a
# one-target frame is always 1, and a consumer sees one entity reported over
# and over instead of several. The packets stay well-formed throughout.
_ID_FALLBACKS = ("target_id", "track_id", "entity_id", "uid", "id")

#: SMPTE 336 Universal Label for a standalone VMTI Local Set.
VMTI_UL = bytes([
    0x06, 0x0E, 0x2B, 0x34, 0x02, 0x0B, 0x01, 0x01,
    0x0E, 0x01, 0x03, 0x03, 0x06, 0x00, 0x00, 0x00,
])

#: ST 0903.6 detection status codes (VTarget item 23).
DETECTION_STATUS = {
    "inactive": 0,
    "active_moving": 1,
    "dropped": 2,
    "active_stopped": 3,
    "active_coasting": 4,
}


# ── KLV primitives ───────────────────────────────────────────────────

def ber_length(n: int) -> bytes:
    """BER length: short form below 0x80, else a count byte then the value."""
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def ber_oid(n: int) -> bytes:
    """BER-OID (base-128, high bit set on every byte but the last)."""
    if n == 0:
        return b"\x00"
    groups = []
    while n > 0:
        groups.append(n & 0x7F)
        n >>= 7
    groups.reverse()
    return bytes([g | 0x80 for g in groups[:-1]] + [groups[-1]])


def uint(n: int) -> bytes:
    """A big-endian unsigned integer in the fewest bytes that hold it."""
    if n == 0:
        return b"\x00"
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def tlv(tag: int, value: bytes) -> bytes:
    return ber_oid(tag) + ber_length(len(value)) + value


def series(elements: list[bytes]) -> bytes:
    """A Series: each element prefixed with its BER length, no tags."""
    return b"".join(ber_length(len(e)) + e for e in elements)


def checksum_16(data: bytes) -> int:
    """ST 0903.6-119: a 16-bit sum of every byte, wrapping."""
    return sum(data) & 0xFFFF


# ── ST 1201 IMAPB ────────────────────────────────────────────────────
# Ported from the mapping the standard defines. Integer exponents only, so
# Python's exact 2**n is used rather than a loop; every other step mirrors
# the definition operation for operation, because a "tidier" rearrangement
# changes which side of a bucket boundary a value lands on.

def _ceil_log2(x: float) -> int:
    n = 0
    if x > 1.0:
        while 2.0 ** n < x and n < 1024:
            n += 1
    else:
        while 2.0 ** (n - 1) >= x and n > -1074:
            n -= 1
    return n


class Imapb:
    """An IMAPB mapping between an `length`-byte unsigned integer and `[a, b]`."""

    def __init__(self, a: float, b: float, length: int) -> None:
        if not (math.isfinite(a) and math.isfinite(b)) or b <= a:
            raise ValueError(f"IMAPB range must be finite and increasing, got ({a}, {b})")
        if not 1 <= length <= 8:
            raise ValueError(f"IMAPB length must be 1..8, got {length}")
        self.a, self.b, self.length = a, b, length
        b_pow = _ceil_log2(b - a)
        d_pow = 8 * length - 1
        self._s_f = 2.0 ** (d_pow - b_pow)
        # The zero offset applies only when the range straddles zero. Without
        # it a range like (-19.2, 19.2) would not encode 0.0 to an integer
        # that decodes back to exactly 0.0.
        if a < 0.0 < b:
            scaled = self._s_f * a
            self._z = scaled - math.floor(scaled)
        else:
            self._z = 0.0
        self._y_max = int(math.floor(self._s_f * (b - a) + self._z))

    def encode(self, value: float) -> bytes:
        """Encode, saturating to the declared range.

        Saturating rather than raising: a spec asking for a value the standard
        cannot carry should still produce a decodable packet, and the clamp is
        visible in the output where an exception mid-run would not be. Callers
        keep absence separate — an item nothing supplied is omitted, never
        saturated to `a`.
        """
        if not math.isfinite(value):
            value = self.a
        value = min(max(value, self.a), self.b)
        y = int(math.floor(self._s_f * (value - self.a) + self._z))
        y = min(max(y, 0), self._y_max)
        return y.to_bytes(8, "big")[8 - self.length:]


#: The mappings ST 0903.6's tables declare, one per item that uses one.
_LAT = Imapb(-90.0, 90.0, 4)
_LON = Imapb(-180.0, 180.0, 4)
_HAE = Imapb(-900.0, 19000.0, 2)
_SIGMA = Imapb(0.0, 650.0, 2)
_KINEMATIC = Imapb(-900.0, 900.0, 2)
_PERCENT = Imapb(0.0, 100.0, 3)
_FOV = Imapb(0.0, 180.0, 2)


# ── VMTI ─────────────────────────────────────────────────────────────

def _opt(spec: DatasetSpec, key: str, default: Any = None) -> Any:
    return spec.output.options.get(key, default)


def _classes(spec: DatasetSpec) -> list[dict]:
    raw = _opt(spec, "classes", []) or []
    return [c for c in raw if isinstance(c, dict) and c.get("iri")]


def _target_pack(spec: DatasetSpec, row: dict, target_id: int) -> bytes:
    """One VTarget Pack: a BER-OID target id, then its items in tag order."""
    items: list[bytes] = []

    def num(option: str, fallbacks: tuple[str, ...] = ()) -> float | None:
        return optional_num(spec, row, option, fallbacks)

    def add_int(tag: int, option: str, fallbacks: tuple[str, ...] = (),
                single_byte: bool = False) -> None:
        v = num(option, fallbacks)
        if v is None:
            return
        n = max(int(v), 0)
        items.append(tlv(tag, bytes([min(n, 255)]) if single_byte else uint(n)))

    add_int(4, "priority_column", ("priority",), single_byte=True)
    add_int(5, "confidence_column", ("confidence",), single_byte=True)
    add_int(6, "history_column", ("history",))
    add_int(7, "percent_pixels_column", ("percent_pixels",), single_byte=True)
    add_int(9, "intensity_column", ("intensity",))

    # Item 17 — absolute location. The 16-byte pack when the row states its
    # positional sigmas, the 10-byte truncation when it does not: reporting
    # sigmas nobody measured would be inventing the one number a consumer
    # turns into position confidence.
    lat, lon = num("lat_column", ("lat",)), num("lon_column", ("lon",))
    if lat is not None and lon is not None:
        hae = num("hae_column", ("hae",))
        value = _LAT.encode(lat) + _LON.encode(lon) + _HAE.encode(hae or 0.0)
        sigmas = [num(f"sigma_{d}_column", (f"sigma_{d}",)) for d in ("east", "north", "up")]
        if all(s is not None for s in sigmas):
            value += b"".join(_SIGMA.encode(s) for s in sigmas)
        items.append(tlv(17, value))

    add_int(19, "pixel_row_column", ("pixel_row",))
    add_int(20, "pixel_col_column", ("pixel_col",))

    status = row.get(_opt(spec, "detection_status_column", "detection_status"))
    if status is not None:
        code = DETECTION_STATUS.get(str(status).strip().lower())
        if code is None and isinstance(status, (int, float)) and float(status).is_integer():
            code = int(status)
        if code is not None and 0 <= code <= 255:
            items.append(tlv(23, bytes([code])))

    tracker = _tracker_ls(spec, row)
    if tracker:
        items.append(tlv(104, tracker))

    objects = _object_series(spec, row)
    if objects:
        items.append(tlv(107, objects))

    return ber_oid(target_id) + b"".join(items)


def _tracker_ls(spec: DatasetSpec, row: dict) -> bytes:
    """VTracker LS — track confidence and velocity.

    The standard carries velocity as east/north/up components; a spec states a
    speed and a course, which is what a scene naturally has, and the two are
    the same claim in different coordinates. A consumer takes the magnitude
    back, so the round trip is the identity up to quantisation.
    """
    items: list[bytes] = []
    confidence = optional_num(spec, row, "track_confidence_column", ("track_confidence",))
    if confidence is not None:
        items.append(tlv(7, bytes([min(max(int(confidence), 0), 100)])))

    speed = optional_num(spec, row, "speed_column", ("speed",))
    if speed is not None:
        course = optional_num(spec, row, "course_column", ("course", "heading")) or 0.0
        radians = math.radians(course)
        items.append(tlv(10, _KINEMATIC.encode(speed * math.sin(radians))
                         + _KINEMATIC.encode(speed * math.cos(radians))
                         + _KINEMATIC.encode(0.0)))
    return b"".join(items)


def _object_series(spec: DatasetSpec, row: dict) -> bytes:
    """VObject series — one entry per classification hypothesis this row states.

    Each names an ontology entry and carries **its own** confidence. A target
    that is 90% a vehicle and 70% a car says both; collapsing them to the
    winner is the loss a consumer cannot undo, so a hypothesis whose column is
    absent is simply not asserted rather than defaulted.
    """
    elements: list[bytes] = []
    for index, entry in enumerate(_classes(spec), start=1):
        raw = row.get(entry.get("confidence_column"))
        try:
            value = None if raw is None else float(raw)
        except (TypeError, ValueError):
            value = None
        if value is None or not math.isfinite(value):
            continue
        elements.append(tlv(3, uint(index)) + tlv(4, _PERCENT.encode(value)))
    return series(elements)


def _ontology_series(spec: DatasetSpec) -> bytes:
    """Ontology series — the vocabulary the VObjects point into.

    Ids are the list position, so a VObject's item 3 and this series stay in
    step by construction rather than by a lookup that could go stale.
    """
    elements = [
        tlv(1, uint(index)) + tlv(4, str(entry["iri"]).encode("utf-8"))
        for index, entry in enumerate(_classes(spec), start=1)
    ]
    return series(elements)


def _packet(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """One standalone VMTI packet: universal label, length, items, checksum."""
    items: list[bytes] = []

    # Item 2 first — ST 0903.6 §10.1.2 requires the timestamp lead the set.
    items.append(tlv(2, epoch_micros(event_time(spec, rows[0])).to_bytes(8, "big")))

    for tag, option in ((3, "system_name"), (10, "source_sensor")):
        value = _opt(spec, option)
        if value:
            items.append(tlv(tag, str(value).encode("utf-8")))
    items.append(tlv(4, uint(int(_opt(spec, "ls_version", 1)))))
    # Detected and reported are equal here: this encoder culls nothing, and
    # claiming otherwise would fake the ratio a consumer reads as feed health.
    items.append(tlv(5, uint(len(rows))))
    items.append(tlv(6, uint(len(rows))))
    for tag, option in ((8, "frame_width"), (9, "frame_height")):
        value = _opt(spec, option)
        if value:
            items.append(tlv(tag, uint(int(value))))
    for tag, option in ((11, "horizontal_fov"), (12, "vertical_fov")):
        value = _opt(spec, option)
        if value is not None:
            items.append(tlv(tag, _FOV.encode(float(value))))

    ids = _opt(spec, "target_id_column")
    packs = []
    for offset, row in enumerate(rows, start=1):
        key = first_key(row, ids, _ID_FALLBACKS)
        raw = row.get(key) if key else None
        packs.append(_target_pack(spec, row, _stable_target_id(raw, offset)))
    items.append(tlv(101, series(packs)))

    ontologies = _ontology_series(spec)
    if ontologies:
        items.append(tlv(103, ontologies))

    body = b"".join(items)
    # The checksum item is `01 02 hi lo` and lives inside the declared length.
    declared = len(body) + 4
    packet = VMTI_UL + ber_length(declared) + body + b"\x01\x02\x00\x00"
    # Summed from the first byte of the key through the checksum item's length
    # byte — everything but the two value bytes just reserved.
    covered = len(packet) - 2
    return packet[:covered] + checksum_16(packet[:covered]).to_bytes(2, "big")


def _stable_target_id(raw: Any, fallback: int) -> int:
    """A VTarget id: the row's own if it is a positive integer, else its position.

    A target id is **source-local and numeric** in ST 0903.6, so a string uid
    cannot be carried here and is deliberately not hashed into one — an
    invented id that looked stable would be a worse lie than an obvious
    positional one. Identity a consumer can rely on rides in the VTracker's
    track id, which is a UUID and says so.
    """
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, float) and raw.is_integer() and raw > 0:
        return int(raw)
    # A digit-only string is how a spec expresses a numeric sensor-local id
    # (`id_pattern: "#####"`), so read it rather than discarding it — falling
    # back would give every target in a scene the same id, and a consumer
    # would see one entity where there are several. Leading zeros are lost,
    # which can collide two ids the emitter kept distinct; a scene wanting
    # that many targets should widen the pattern rather than pad it.
    if isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
        if value > 0:
            return value
    return fallback


@record_encoder("klv")
def _klv_record(spec: DatasetSpec, record: dict) -> bytes:
    """One standalone VMTI packet carrying one target.

    A frame with a single target is ordinary VMTI, and it keeps the streamed
    bytes identical to the whole-file ones. Batching several rows into one
    frame is a real thing a sensor does and is not expressible on the
    per-record seam; see the ADR.
    """
    return _packet(spec, [record])


@encoder("klv", ".klv")
def to_klv(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """A VMTI stream: one standalone packet per row, concatenated — identical
    framing to the per-record encoder so file and stream match."""
    return b"".join(_klv_record(spec, r) for r in rows)
