"""Parquet encoder. See formats/AGENTS.md.

Pure function `(spec, rows) -> bytes` (INV-2): builds an Arrow table in
memory and writes a Parquet buffer, no filesystem. pyarrow is heavy, so
it lives under the `formats-extra` extra and is imported lazily inside
the encoder — core `import chaff.formats` stays dep-free.

Determinism (INV-3): pyarrow's Parquet writer is byte-stable for a given
input and library version (its only stamp, `created_by`, is the fixed
version string — no wall-clock data unless you write timestamp columns,
which chaff does not: dates/times are ISO strings). Covered by a
determinism test.
"""

from __future__ import annotations

import io

from . import encoder
from ..spec import DatasetSpec


@encoder("parquet", ".parquet")
def to_parquet(spec: DatasetSpec, rows: list[dict]) -> bytes:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "parquet format needs pyarrow. Install it with: pip install 'chaff[formats-extra]'"
        ) from e

    # Column order follows the spec; a dict preserves insertion order, and
    # pyarrow infers each column's type independently (all-null -> null type).
    cols = [c.name for c in spec.columns]
    table = pa.table({c: [r[c] for r in rows] for c in cols})

    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()
