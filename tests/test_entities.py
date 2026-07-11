"""Stateful entity tests (ADR-0009): entity-tick generation + updaters."""

import pytest

from chaff.engine import effective_row_count, generate_entity_rows, generate_records
from chaff.spec import load_spec
from chaff.updaters import EntityContext, get_updater, list_updaters


def tracks(**entity_over):
    ent = {"count": 3, "ticks": 4, "id_pattern": "UNIT-##",
           "updates": [{"updater": "movement", "params": {"speed": 0.02}}]}
    ent.update(entity_over)
    return load_spec({
        "name": "tracks", "seed": 7,
        "columns": [{"name": "lat", "generator": "lat", "params": {"min": 34.0, "max": 34.1}},
                    {"name": "lon", "generator": "lon", "params": {"min": -118.5, "max": -118.4}}],
        "output": {"format": "ndjson"},
        "entity": ent,
    })


def test_row_count_is_entities_times_ticks():
    rows = generate_entity_rows(tracks())
    assert len(rows) == 3 * 4
    assert effective_row_count(tracks()) == 12


def test_output_is_tick_major_time_ordered():
    rows = generate_entity_rows(tracks())
    ticks = [r["tick"] for r in rows]
    # all of tick 0 before any tick 1, etc.
    assert ticks == sorted(ticks)
    assert ticks[:3] == [0, 0, 0] and ticks[3:6] == [1, 1, 1]


def test_id_and_tick_columns_present():
    r = generate_entity_rows(tracks())[0]
    assert r["entity_id"].startswith("UNIT-") and r["tick"] == 0


def test_sequential_ids_without_pattern():
    rows = generate_entity_rows(tracks(id_pattern=None))
    ids_at_tick0 = [r["entity_id"] for r in rows if r["tick"] == 0]
    assert ids_at_tick0 == [1, 2, 3]


def test_movement_changes_position_over_ticks():
    rows = generate_entity_rows(tracks())
    first_id = rows[0]["entity_id"]
    path = [(r["lat"], r["lon"]) for r in rows if r["entity_id"] == first_id]
    assert path[0] != path[-1]  # it moved
    assert "heading" in rows[-1]  # movement writes heading back into state


def test_lifecycle_transitions_and_terminates():
    spec = load_spec({
        "name": "orders", "seed": 3,
        "columns": [{"name": "status", "generator": "choice", "params": {"values": ["placed"]}}],
        "output": {"format": "csv"},
        "entity": {"count": 4, "ticks": 5, "updates": [
            {"updater": "lifecycle", "params": {"column": "status", "transitions": {
                "placed": {"shipped": 1.0},
                "shipped": {"delivered": 1.0},
                "delivered": {}}}}]},
    })
    e1 = [r["status"] for r in generate_entity_rows(spec) if r["entity_id"] == 1]
    assert e1[:3] == ["placed", "shipped", "delivered"]
    assert e1[-1] == "delivered"  # terminal: no outgoing transition


def test_entity_generation_is_deterministic():
    assert generate_entity_rows(tracks()) == generate_entity_rows(tracks())
    a = generate_entity_rows(tracks())
    b = generate_entity_rows(load_spec({**tracks().model_dump(), "seed": 99}))
    assert a != b


def test_generate_records_dispatches_entity_vs_single():
    assert len(generate_records(tracks())) == 12
    single = load_spec({"name": "s", "seed": 1, "rows": 5,
                        "columns": [{"name": "id", "generator": "row_id"}],
                        "output": {"format": "csv"}})
    assert len(generate_records(single)) == 5


def test_unknown_updater_fails_fast():
    spec = load_spec({
        "name": "x", "columns": [{"name": "a", "generator": "uuid"}],
        "output": {"format": "csv"},
        "entity": {"count": 2, "ticks": 2, "updates": [{"updater": "nope"}]},
    })
    with pytest.raises(KeyError, match="unknown updater"):
        generate_entity_rows(spec)


def test_rows_optional_only_for_entity_specs():
    from pydantic import ValidationError
    # entity spec without rows is fine
    load_spec({"name": "e", "columns": [{"name": "a", "generator": "uuid"}],
               "output": {"format": "csv"}, "entity": {"count": 1, "ticks": 1}})
    # non-entity spec still requires rows
    with pytest.raises(ValidationError, match="rows is required"):
        load_spec({"name": "n", "columns": [{"name": "a", "generator": "uuid"}],
                   "output": {"format": "csv"}})


def test_updaters_registered():
    assert {"movement", "lifecycle", "drift"} <= set(list_updaters())


def test_drift_clamps_and_random_walks():
    import random
    ctx = EntityContext(rng=random.Random(1), faker=None, entity_index=0, tick=1)
    state = {"temp": 20.0}
    for _ in range(50):
        get_updater("drift")(ctx, state, {"column": "temp", "stddev": 5, "min": 0, "max": 100})
        assert 0 <= state["temp"] <= 100
