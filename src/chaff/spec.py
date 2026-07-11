"""chaff spec contract.

The spec IS the product. The UI builds specs, the CLI consumes specs,
the API transports specs. The engine only ever sees a validated DatasetSpec.

Design rules (see docs/adr/0001, 0002, 0003):
- Columns declare a *semantic generator*, not a physical type. Physical
  types are the format encoder's problem.
- `format` (how bytes are encoded) and `sink` (where bytes go) are
  independent axes. Any format may pair with any compatible sink.
- `seed` makes generation deterministic: same spec + same seed = same bytes.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SPEC_VERSION = "1.0"


def _reject_duplicate_columns(cols: list["ColumnSpec"]) -> list["ColumnSpec"]:
    names = [c.name for c in cols]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate column names: {sorted(dupes)}")
    return cols


class ColumnSpec(BaseModel):
    """One column: a name, a semantic generator, and its parameters."""

    name: str = Field(..., min_length=1, description="Column/field name in the output.")
    generator: str = Field(..., description="Registered generator id, e.g. 'full_name', 'choice_weighted'.")
    params: dict[str, Any] = Field(default_factory=dict, description="Generator-specific parameters.")
    null_rate: float = Field(0.0, ge=0.0, le=1.0, description="Probability [0,1] a value is null/empty.")

    @field_validator("name")
    @classmethod
    def name_is_identifier_friendly(cls, v: str) -> str:
        # Permissive on purpose (office Joe types spaces); encoders quote as needed.
        if v.strip() != v or not v:
            raise ValueError("column name must be non-empty with no leading/trailing whitespace")
        return v


class OutputSpec(BaseModel):
    """Format axis: how the rows are encoded."""

    format: str = Field(..., description="Registered format id: csv, tsv, json, ndjson, sql, ...")
    options: dict[str, Any] = Field(default_factory=dict, description="Format-specific options (e.g. sql dialect).")


class SinkSpec(BaseModel):
    """Sink axis: where the encoded bytes terminate."""

    sink: str = Field("file", description="Registered sink id: file, http, kafka, tcp, udp, ...")
    options: dict[str, Any] = Field(default_factory=dict, description="Sink-specific options (path, url, topic, rate...).")


class TableSpec(BaseModel):
    """One additional related table in a multi-table spec (Phase 3, ADR-0008).

    Same generation unit as the top-level table — a name, a row count, and
    columns — but no output/sink of its own: format and delivery are shared
    across all tables in the spec. Use the `fk` generator in a column to
    reference another table's column for foreign-key integrity.
    """

    name: str = Field(..., min_length=1, description="Table name; used for its filename and SQL table name.")
    rows: int = Field(..., ge=1, le=10_000_000, description="Record count for this table.")
    columns: list[ColumnSpec] = Field(..., min_length=1)

    @field_validator("columns")
    @classmethod
    def _cols_unique(cls, v: list[ColumnSpec]) -> list[ColumnSpec]:
        return _reject_duplicate_columns(v)


class DatasetSpec(BaseModel):
    """The whole contract. Serialize to JSON, save it, share it, version it."""

    spec_version: str = Field(SPEC_VERSION, description="Contract version for forward migration.")
    name: str = Field(..., min_length=1, description="Dataset/table name; used by SQL encoder and filenames.")
    description: Optional[str] = Field(None, description="Human context; shown in the preset library UI.")
    seed: Optional[int] = Field(None, description="RNG seed. Set it and the dataset is reproducible byte-for-byte.")
    rows: int = Field(..., ge=1, le=10_000_000, description="Record count.")
    columns: list[ColumnSpec] = Field(..., min_length=1)
    output: OutputSpec
    sink: SinkSpec = Field(default_factory=SinkSpec)

    # Additional related tables (Phase 3, ADR-0008). The top-level name/
    # columns/rows are the primary table; `tables` holds the rest. Absent =>
    # a single-table spec, byte-for-byte identical to before.
    tables: Optional[list[TableSpec]] = Field(
        None, description="Additional related tables; use `fk` columns for referential integrity."
    )

    # Reserved for Phase 3 — stateful entities that evolve over time.
    # Presence of `entity` will switch the engine from independent-row
    # generation to per-entity update loops (tracks, lifecycles, sensors).
    entity: Optional[dict[str, Any]] = Field(None, description="RESERVED (Phase 3): stateful entity config.")

    @field_validator("columns")
    @classmethod
    def column_names_unique(cls, v: list[ColumnSpec]) -> list[ColumnSpec]:
        return _reject_duplicate_columns(v)

    @model_validator(mode="after")
    def _table_names_unique(self) -> "DatasetSpec":
        if self.tables:
            names = [self.name] + [t.name for t in self.tables]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise ValueError(f"duplicate table names: {sorted(dupes)}")
        return self


def load_spec(data: dict[str, Any] | str) -> DatasetSpec:
    """Parse and validate a spec from a dict or JSON string."""
    if isinstance(data, str):
        return DatasetSpec.model_validate_json(data)
    return DatasetSpec.model_validate(data)
