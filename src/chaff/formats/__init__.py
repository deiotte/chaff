"""Format encoders (Axis 1: how bytes are encoded). See formats/AGENTS.md.

An encoder is a callable: (spec: DatasetSpec, rows: list[dict]) -> bytes.
It must be pure: no I/O, no network, no filesystem. Delivery is the
sink's job (ADR-0002). Keep encoders pure and every format works with
every sink for free.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from typing import Any, Callable

from ..spec import DatasetSpec
from . import _formula

EncoderFn = Callable[[DatasetSpec, list[dict[str, Any]]], bytes]
RecordEncoderFn = Callable[[DatasetSpec, dict[str, Any]], bytes]

_REGISTRY: dict[str, EncoderFn] = {}
_EXTENSIONS: dict[str, str] = {}
# Per-record encoders for streaming sinks (ADR-0007). Only record-oriented
# formats register one; whole-file formats (xlsx, parquet, sql, ...) can't
# be framed per record and simply don't appear here.
_RECORD_REGISTRY: dict[str, RecordEncoderFn] = {}


def encoder(fmt_id: str, extension: str) -> Callable[[EncoderFn], EncoderFn]:
    def deco(fn: EncoderFn) -> EncoderFn:
        if fmt_id in _REGISTRY:
            raise ValueError(f"format '{fmt_id}' already registered")
        _REGISTRY[fmt_id] = fn
        _EXTENSIONS[fmt_id] = extension
        return fn
    return deco


def record_encoder(fmt_id: str) -> Callable[[RecordEncoderFn], RecordEncoderFn]:
    """Register a `(spec, record) -> bytes` encoder for streaming delivery."""
    def deco(fn: RecordEncoderFn) -> RecordEncoderFn:
        if fmt_id in _RECORD_REGISTRY:
            raise ValueError(f"record encoder '{fmt_id}' already registered")
        _RECORD_REGISTRY[fmt_id] = fn
        return fn
    return deco


def get_encoder(fmt_id: str) -> EncoderFn:
    try:
        return _REGISTRY[fmt_id]
    except KeyError:
        raise KeyError(f"unknown format '{fmt_id}'. Registered: {sorted(_REGISTRY)}") from None


def get_record_encoder(fmt_id: str) -> RecordEncoderFn:
    try:
        return _RECORD_REGISTRY[fmt_id]
    except KeyError:
        raise KeyError(
            f"format '{fmt_id}' has no per-record encoder (required for streaming "
            f"sinks). Streaming-capable formats: {sorted(_RECORD_REGISTRY)}"
        ) from None


def get_extension(fmt_id: str) -> str:
    return _EXTENSIONS[fmt_id]


def list_formats() -> list[str]:
    return sorted(_REGISTRY)


def list_record_formats() -> list[str]:
    return sorted(_RECORD_REGISTRY)


# ── Delimited ────────────────────────────────────────────────────────

def _delimited(spec: DatasetSpec, rows: list[dict], delimiter: str) -> bytes:
    """Header row plus one row per record, in spec-column order.

    Column order comes from the spec, not from each row's key order, so a
    row that is missing a key or carries a stray one can't shift a column.
    Values are run through the formula guard (see `_formula`) because a
    delimited file opened in a spreadsheet evaluates formula-leading cells.
    """
    mode = _formula.guard_mode(spec.output.options)
    cols = [c.name for c in spec.columns]
    known = set(cols)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    writer.writerow([_formula.neutralize(c, mode) for c in cols])
    for r in rows:
        # csv.DictWriter used to raise on a key the spec doesn't declare.
        # Keep that tripwire: a row carrying an undeclared column means the
        # engine and the spec disagree, and silently dropping it would hide
        # the disagreement in a file that looks complete.
        extra = r.keys() - known
        if extra:
            raise ValueError(
                f"row contains columns not declared in the spec: {sorted(extra)}")
        writer.writerow([
            "" if r.get(c) is None else _formula.neutralize(r.get(c), mode)
            for c in cols
        ])
    return buf.getvalue().encode("utf-8")


@encoder("csv", ".csv")
def to_csv(spec: DatasetSpec, rows: list[dict]) -> bytes:
    return _delimited(spec, rows, spec.output.options.get("delimiter", ","))


@encoder("tsv", ".tsv")
def to_tsv(spec: DatasetSpec, rows: list[dict]) -> bytes:
    return _delimited(spec, rows, "\t")


# ── JSON family ──────────────────────────────────────────────────────

@encoder("json", ".json")
def to_json(spec: DatasetSpec, rows: list[dict]) -> bytes:
    indent = spec.output.options.get("indent", 2)
    return json.dumps(rows, indent=indent, default=str).encode("utf-8")


@encoder("ndjson", ".ndjson")
def to_ndjson(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """One JSON object per line. The streaming-native format: this is what
    the kafka/http sinks frame into per-record messages."""
    return ("\n".join(json.dumps(r, default=str) for r in rows) + "\n").encode("utf-8")


# ── Per-record encoders (streaming sinks, ADR-0007) ──────────────────
# Record-oriented formats expose a `(spec, record) -> bytes` framing. The
# engine uses these when a streaming sink is selected; whole-file formats
# (sql, xlsx, parquet, avro, xml) deliberately register none.

@record_encoder("ndjson")
def _ndjson_record(spec: DatasetSpec, record: dict) -> bytes:
    return (json.dumps(record, default=str) + "\n").encode("utf-8")


@record_encoder("json")
def _json_record(spec: DatasetSpec, record: dict) -> bytes:
    return json.dumps(record, default=str).encode("utf-8")


@record_encoder("csv")
def _csv_record(spec: DatasetSpec, record: dict) -> bytes:
    """One CSV row (no header) in spec-column order, properly quoted.

    Guarded like the whole-file encoder: a streamed CSV record is just as
    likely to end up pasted into a spreadsheet as a downloaded one.
    """
    mode = _formula.guard_mode(spec.output.options)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "" if record[c.name] is None else _formula.neutralize(record[c.name], mode)
        for c in spec.columns
    ])
    return buf.getvalue().encode("utf-8")


# ── SQL ──────────────────────────────────────────────────────────────

_SQL_TYPE_MAP = {  # python type -> portable SQL type
    int: "INTEGER",
    float: "REAL",
    bool: "BOOLEAN",
}


def _sql_ident(name: str, dialect: str) -> str:
    """Quote an identifier for `dialect`, escaping the closing delimiter.

    Column and dataset names are deliberately permissive (office Joe types
    freely), so the delimiter is the only thing standing between a name and
    the surrounding statement. Doubling it is the escape both dialect
    families define: `]]` inside brackets, `""` inside double quotes.
    Without it, a name of `x]; DROP TABLE audit;--` closes the bracket and
    everything after it becomes live SQL in the file a consumer runs.
    """
    if dialect == "tsql":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(v: Any, dialect: str) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        if dialect == "tsql":
            return "1" if v else "0"
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


@encoder("sql", ".sql")
def to_sql(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """CREATE TABLE + batched INSERTs.

    options: dialect ('postgres'|'sqlite'|'tsql', default 'postgres'),
             batch_size (default 500), create_table (default True).
    """
    opts = spec.output.options
    dialect = opts.get("dialect", "postgres")
    batch = int(opts.get("batch_size", 500))
    table = _sql_ident(spec.name, dialect)
    cols = [c.name for c in spec.columns]
    out: list[str] = [f"-- generated by chaff (seed={spec.seed}, rows={spec.rows})"]

    if opts.get("create_table", True):
        # Infer portable types from the first non-null value per column.
        types = {}
        for c in cols:
            sample = next((r[c] for r in rows if r[c] is not None), "")
            types[c] = _SQL_TYPE_MAP.get(type(sample), "TEXT")
        col_defs = ",\n  ".join(f"{_sql_ident(c, dialect)} {types[c]}" for c in cols)
        out.append(f"CREATE TABLE {table} (\n  {col_defs}\n);")

    col_list = ", ".join(_sql_ident(c, dialect) for c in cols)
    for i in range(0, len(rows), batch):
        values = ",\n".join(
            "(" + ", ".join(_sql_literal(r[c], dialect) for c in cols) + ")"
            for r in rows[i:i + batch]
        )
        out.append(f"INSERT INTO {table} ({col_list}) VALUES\n{values};")

    return ("\n\n".join(out) + "\n").encode("utf-8")


# ── XML ──────────────────────────────────────────────────────────────

@encoder("xml", ".xml")
def to_xml(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """Row/field XML. Column names ride in a `name` attribute rather than
    the element tag, so any column name (spaces, punctuation — office Joe
    types freely) is represented exactly with no tag-sanitizing guesswork.

    options: root_tag (default 'dataset'), row_tag (default 'row').

    <dataset name="...">
      <row><field name="case_id">DEA-1234-ABCDE</field>...</row>
    </dataset>
    """
    opts = spec.output.options
    root = ET.Element(str(opts.get("root_tag", "dataset")), {"name": spec.name})
    row_tag = str(opts.get("row_tag", "row"))
    cols = [c.name for c in spec.columns]
    for r in rows:
        row_el = ET.SubElement(root, row_tag)
        for c in cols:
            field = ET.SubElement(row_el, "field", {"name": c})
            v = r[c]
            if v is not None:
                field.text = str(v)
    ET.indent(root)  # stable, deterministic pretty-print
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


# ── Sensor wire formats (stdlib, core) ───────────────────────────────
# Cursor-on-Target (XML) and MISB ST 0903.6 VMTI (KLV). Both are binary- or
# text-exact renderings of published standards and carry no heavy dep.
from . import cot, klv  # noqa: E402,F401


# ── Heavy formats (optional deps under the `formats-extra` extra) ─────
# Imported for their registration side-effect. Each module registers its
# encoder at import time WITHOUT importing its heavy dep (that import is
# lazy, inside the encoder), so core `import chaff.formats` stays dep-free.
from . import avro, excel, parquet  # noqa: E402,F401
