"""Avro (object container file) encoder. See formats/AGENTS.md.

Pure function `(spec, rows) -> bytes` (INV-2): builds the container in
memory, no filesystem. fastavro is heavy, so it lives under the
`formats-extra` extra and is imported lazily inside the encoder — core
`import chaff.formats` stays dep-free.

Two determinism points (INV-3):
  1. fastavro generates a RANDOM 16-byte sync marker per file unless one
     is supplied — we pin a fixed marker.
  2. Avro field/record names must match [A-Za-z_][A-Za-z0-9_]* (no
     spaces), so column names are sanitized to valid Avro names; the
     original name is preserved in each field's `doc`. Every field is a
     nullable union so null_rate columns encode cleanly.
"""

from __future__ import annotations

from typing import Any

from . import encoder
from ..spec import DatasetSpec

# Fixed sync marker (exactly 16 bytes) so output is byte-for-byte stable.
_SYNC_MARKER = b"chaff-avro-sync!"

_AVRO_TYPE = {int: "long", float: "double", bool: "boolean", str: "string"}


def _avro_name(name: str, used: set[str]) -> str:
    """Coerce an arbitrary column name into a unique valid Avro name."""
    chars = [ch if (ch.isascii() and ch.isalnum()) or ch == "_" else "_" for ch in name]
    s = "".join(chars) or "field"
    if not (s[0].isascii() and s[0].isalpha() or s[0] == "_"):
        s = "_" + s
    candidate, n = s, 1
    while candidate in used:
        candidate, n = f"{s}_{n}", n + 1
    used.add(candidate)
    return candidate


def _field_type(col_name: str, rows: list[dict]) -> str:
    sample = next((r[col_name] for r in rows if r[col_name] is not None), "")
    return _AVRO_TYPE.get(type(sample), "string")


@encoder("avro", ".avro")
def to_avro(spec: DatasetSpec, rows: list[dict]) -> bytes:
    try:
        import fastavro
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "avro format needs fastavro. Install it with: pip install 'chaff[formats-extra]'"
        ) from e

    used: set[str] = set()
    # Preserve column order; map each spec column to its (valid) Avro name.
    fields = []
    mapping: list[tuple[str, str]] = []  # (original, avro_name)
    for c in spec.columns:
        aname = _avro_name(c.name, used)
        mapping.append((c.name, aname))
        fields.append({
            "name": aname,
            "type": ["null", _field_type(c.name, rows)],
            "default": None,
            "doc": c.name,
        })

    schema = fastavro.parse_schema({
        "type": "record",
        "name": _avro_name(spec.name, set()),
        "fields": fields,
    })
    records = [{aname: r[orig] for orig, aname in mapping} for r in rows]

    import io
    buf = io.BytesIO()
    fastavro.writer(buf, schema, records, codec="null", sync_marker=_SYNC_MARKER)
    return buf.getvalue()
