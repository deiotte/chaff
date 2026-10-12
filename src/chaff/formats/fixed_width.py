"""Fixed-width text: the mainframe extract (ADR-0044).

Every field sits at a fixed character offset, padded to its width, one record
per line, usually no header. This is what legacy case, payroll and benefits
systems hand a migration team. Pure (INV-2): rows in, bytes out.

Options (`output.options`), all optional:
  widths    {"col": 12}      chars per field; default = widest value (or header)
  align     {"col": "right"} default right for all-numeric columns, else left
  pad       {"col": "0"}     fill character; default space
  header    false            emit a header line of padded column names
  overflow  "error"          or "truncate" (cut the value to fit, as old systems did)
  eol       "lf"             or "crlf"
  encoding  "utf-8"          e.g. "cp1252", "latin-1" for a legacy code page
  errors    "strict"         or "replace" for characters the code page lacks
  null      ""               what a null renders as before padding

Widths count characters, not bytes. `fixed_width_layout` renders the same
layout as a COBOL-style copybook, so the file and its layout always agree.
"""

from __future__ import annotations

import re
from typing import Any

from ..spec import DatasetSpec
from . import _formula, encoder


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _layout(spec: DatasetSpec, rows: list[dict]) -> list[dict[str, Any]]:
    """[{name, start, width, align, pad, numeric}] for every column, in spec
    order. Data-sized widths are a pure function of the rows, so the encoder
    and the copybook compute identical layouts from the same input."""
    opts = spec.output.options
    widths, aligns, pads = opts.get("widths") or {}, opts.get("align") or {}, opts.get("pad") or {}
    mode = _formula.guard_mode(opts)
    null = str(opts.get("null", ""))
    header = bool(opts.get("header", False))
    out, start = [], 1
    for c in spec.columns:
        values = [r.get(c.name) for r in rows]
        present = [v for v in values if v is not None]
        numeric = bool(present) and all(_is_number(v) for v in present)
        if c.name in widths:
            width = int(widths[c.name])
            if width < 1:
                raise ValueError(f"fixed_width: width for '{c.name}' must be at least 1")
        else:
            texts = [_text(v, null, mode) for v in values]
            width = max([len(t) for t in texts] + [len(_text(c.name, '', mode)) if header else 0, 1])
        align = aligns.get(c.name, "right" if numeric else "left")
        if align not in ("left", "right"):
            raise ValueError(f"fixed_width: align for '{c.name}' must be 'left' or 'right'")
        pad = str(pads.get(c.name, " "))
        if len(pad) != 1:
            raise ValueError(f"fixed_width: pad for '{c.name}' must be one character")
        out.append({"name": c.name, "start": start, "width": width, "align": align,
                    "pad": pad, "numeric": numeric})
        start += width
    return out


def _text(v: Any, null: str, mode: str) -> str:
    return null if v is None else str(_formula.neutralize(v, mode))


def _fit(text: str, f: dict[str, Any], overflow: str) -> str:
    if len(text) > f["width"]:
        if overflow != "truncate":
            raise ValueError(
                f"fixed_width: value {text!r} is {len(text)} characters but '{f['name']}' "
                f"is {f['width']} wide — widen it or set overflow: \"truncate\"")
        text = text[: f["width"]]
    fill = f["pad"] * (f["width"] - len(text))
    return fill + text if f["align"] == "right" else text + fill


@encoder("fixed_width", ".txt")
def to_fixed_width(spec: DatasetSpec, rows: list[dict]) -> bytes:
    opts = spec.output.options
    mode = _formula.guard_mode(opts)
    null = str(opts.get("null", ""))
    overflow = opts.get("overflow", "error")
    if overflow not in ("error", "truncate"):
        raise ValueError("fixed_width: overflow must be 'error' or 'truncate'")
    eol = {"lf": "\n", "crlf": "\r\n"}.get(opts.get("eol", "lf"))
    if eol is None:
        raise ValueError("fixed_width: eol must be 'lf' or 'crlf'")
    layout = _layout(spec, rows)
    lines = []
    if opts.get("header", False):
        lines.append("".join(_fit(_text(f["name"], "", mode), {**f, "align": "left", "pad": " "},
                                  overflow) for f in layout))
    known = {f["name"] for f in layout}
    for r in rows:
        extra = r.keys() - known
        if extra:  # same tripwire as csv: engine and spec disagree
            raise ValueError(f"row contains columns not declared in the spec: {sorted(extra)}")
        lines.append("".join(_fit(_text(r.get(f["name"]), null, mode), f, overflow) for f in layout))
    text = "".join(line + eol for line in lines)
    return text.encode(opts.get("encoding", "utf-8"), errors=opts.get("errors", "strict"))


def _cobol_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:30] or "FIELD"


@encoder("fixed_width_layout", ".cpy")
def to_fixed_width_layout(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """The `fixed_width` layout for the same spec and rows, as a COBOL-style
    copybook with 1-based start positions. Generate it with the same seed and
    options as the data file and the two match exactly."""
    layout = _layout(spec, rows)
    total = sum(f["width"] for f in layout)
    lines = [f"      * chaff fixed-width layout: {spec.name} (record length {total})",
             f"       01  {_cobol_name(spec.name)}-REC."]
    for f in layout:
        pic = f"9({f['width']})" if f["numeric"] and f["pad"] == "0" else f"X({f['width']})"
        end = f["start"] + f["width"] - 1
        lines.append(f"           05  {_cobol_name(f['name']):<30} PIC {pic + '.':<10}"
                     f"*> {f['start']}-{end}")
    return ("\n".join(lines) + "\n").encode("utf-8")
