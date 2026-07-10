"""Excel (.xlsx) encoder. See formats/AGENTS.md.

Pure function `(spec, rows) -> bytes` (INV-2): builds the workbook in
memory and returns bytes, no filesystem. openpyxl is a heavy dep, so it
lives under the `formats-extra` extra and is imported lazily inside the
encoder — importing `chaff.formats` stays dep-free (registration below
does not need openpyxl), and a missing install fails with an actionable
message instead of an ImportError at startup.

INV-3 (byte-for-byte determinism) is the hard part here. An .xlsx is a
zip, and two entropy sources leak wall-clock time into the bytes:
  1. Workbook doc-properties default to datetime.now().
  2. Python's zip writer stamps each member with the current time.
Both are pinned to fixed constants so same spec + same seed = same bytes.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from typing import Any

from . import encoder
from ..spec import DatasetSpec

# Fixed instants so nothing wall-clock-derived reaches the payload.
_FIXED_DT = datetime(2020, 1, 1, 0, 0, 0)
_FIXED_W3CDTF = b"2020-01-01T00:00:00Z"
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)  # zip's minimum representable date_time

# openpyxl forces dcterms:modified to the current time during save(),
# ignoring the property we set, so pin it in the core-properties member.
_CORE_PROPS = "docProps/core.xml"
_MODIFIED_RE = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


def _normalize_zip(raw: bytes) -> bytes:
    """Re-pack an xlsx archive so the bytes are reproducible.

    Two entropy sources leak wall-clock time and must be pinned:
      - the zip stamps each member with the current time -> fixed epoch;
      - docProps/core.xml's dcterms:modified is set to now() at save time
        regardless of the workbook property -> rewritten to a fixed value.
    Members are rewritten in their original order.
    """
    src, dst = io.BytesIO(raw), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == _CORE_PROPS:
                data = _MODIFIED_RE.sub(rb"\g<1>" + _FIXED_W3CDTF + rb"\g<2>", data)
            zi = zipfile.ZipInfo(info.filename, date_time=_ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            zout.writestr(zi, data)
    return dst.getvalue()


@encoder("xlsx", ".xlsx")
def to_xlsx(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """One worksheet: header row of column names, then the data rows.

    options: sheet_name (default the dataset name, truncated to Excel's
             31-char limit).
    """
    try:
        from openpyxl import Workbook
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "xlsx format needs openpyxl. Install it with: pip install 'chaff[formats-extra]'"
        ) from e

    cols = [c.name for c in spec.columns]
    wb = Workbook()
    ws = wb.active
    ws.title = str(spec.output.options.get("sheet_name", spec.name))[:31]

    # Pin doc-properties: their defaults are datetime.now() (INV-3).
    props = wb.properties
    props.creator = "chaff"
    props.created = _FIXED_DT
    props.modified = _FIXED_DT
    props.lastModifiedBy = "chaff"

    ws.append(cols)
    for r in rows:
        ws.append([_cell(r.get(c)) for c in cols])

    buf = io.BytesIO()
    wb.save(buf)
    return _normalize_zip(buf.getvalue())


def _cell(v: Any) -> Any:
    """openpyxl accepts str/int/float/bool/None natively; coerce anything
    else (e.g. a stray dict/list) to str so encoding never blows up."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
