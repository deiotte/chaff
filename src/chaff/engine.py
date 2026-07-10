"""The chaff engine: spec -> generate -> encode -> sink.

Deliberately boring. All the extensibility lives in the registries;
the engine just walks the pipeline. If you're tempted to add cleverness
here, it probably belongs in a generator, encoder, or sink instead.
"""

from __future__ import annotations

import random
from typing import Any

from faker import Faker

from .formats import get_encoder
from .generators import GenContext, get_generator
from .sinks import get_sink
from .spec import DatasetSpec


def generate_rows(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Generate all rows for a spec. Deterministic when spec.seed is set."""
    rng = random.Random(spec.seed)
    faker = Faker()
    if spec.seed is not None:
        faker.seed_instance(spec.seed)

    # Resolve generators once, fail fast on unknown ids before generating.
    resolved = [(col, get_generator(col.generator)) for col in spec.columns]

    rows: list[dict[str, Any]] = []
    for i in range(spec.rows):
        ctx = GenContext(rng=rng, faker=faker, row_index=i)
        row: dict[str, Any] = {}
        for col, fn in resolved:
            if col.null_rate > 0 and rng.random() < col.null_rate:
                row[col.name] = None
            else:
                row[col.name] = fn(ctx, col.params)
        rows.append(row)
    return rows


# Fun clause (AGENTS.md §5): seed 8675309 tips its hat to Jenny. Receipt
# only — the payload bytes are untouched, so seed determinism is unharmed.
_JENNY = 8675309


def run(spec: DatasetSpec) -> str:
    """Full pipeline. Returns the sink's human-readable receipt."""
    rows = generate_rows(spec)
    payload = get_encoder(spec.output.format)(spec, rows)
    receipt = get_sink(spec.sink.sink)(spec, payload)
    if spec.seed == _JENNY:
        receipt += "\n☎  867-5309 — thanks for the seed, Jenny."
    return receipt
