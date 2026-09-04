"""One scene, several accounts of it (Phase 9, ADR-0033)."""

import json
import math

import pytest

from chaff.engine import (
    effective_row_count,
    encode_observers,
    encode_view,
    generate_entity_rows,
    observer_views,
    run,
    scene_truth,
)
from chaff.formats import get_encoder
from chaff.spec import load_spec

_M_PER_DEG = 111_320.0


def scene(observers, **entity):
    ent = {"count": 4, "ticks": 6, "id_column": "uid", "tick_column": "t",
           "id_pattern": "TRUTH-####",
           "updates": [{"updater": "movement", "params": {"speed": 0.0002}}],
           "observers": observers}
    ent.update(entity)
    return load_spec({
        "name": "scene", "seed": 11,
        "columns": [
            {"name": "lat", "generator": "lat", "params": {"min": 34.0, "max": 34.05}},
            {"name": "lon", "generator": "lon", "params": {"min": -118.3, "max": -118.25}},
            # The movement updater writes `heading` into state, so it is a real
            # column in every row; a column-oriented encoder refuses a row that
            # carries one the spec never declared.
            {"name": "heading", "generator": "int_range", "params": {"min": 0, "max": 359}},
        ],
        "output": {"format": "ndjson"},
        "entity": ent,
    })


def metres(a, b):
    dlat = (a["lat"] - b["lat"]) * _M_PER_DEG
    dlon = (a["lon"] - b["lon"]) * _M_PER_DEG * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlon)


# ── The scene is the invariant ───────────────────────────────────────

def test_observers_never_change_the_scene_behind_them():
    """The load-bearing property. Observers draw from their own derived rng,
    so a thing's trajectory cannot depend on how many sensors watched it —
    which would be both wrong and invisible until someone diffed two runs."""
    watched = generate_entity_rows(scene([{"name": "a", "position_error_m": 50.0},
                                          {"name": "b", "position_error_m": 50.0}]))
    unwatched = generate_entity_rows(scene([]))
    assert watched == unwatched


def test_a_scene_renders_identically_twice():
    a = {n: rows for n, _, rows in observer_views(scene([{"name": "x", "id_pattern": "X-##",
                                                          "position_error_m": 9.0}]))}
    b = {n: rows for n, _, rows in observer_views(scene([{"name": "x", "id_pattern": "X-##",
                                                          "position_error_m": 9.0}]))}
    assert a == b  # INV-3


def test_each_observer_draws_independently_of_the_others():
    """Removing an observer must not move the ones that remain, or a spec's
    feeds would be coupled through a shared draw order."""
    both = dict((n, r) for n, _, r in observer_views(
        scene([{"name": "a", "position_error_m": 5.0}, {"name": "b", "position_error_m": 5.0}])))
    alone = dict((n, r) for n, _, r in observer_views(
        scene([{"name": "b", "position_error_m": 5.0}])))
    assert both["b"] == alone["b"]


# ── What an observer changes ─────────────────────────────────────────

def test_observers_name_the_same_thing_differently():
    """The whole point: a correlating consumer must *work out* that two
    differently-named tracks are one object."""
    feeds = dict((n, r) for n, _, r in observer_views(
        scene([{"name": "a", "id_pattern": "A-####"}, {"name": "b", "id_pattern": "B-####"}])))
    a_ids = {r["uid"] for r in feeds["a"]}
    b_ids = {r["uid"] for r in feeds["b"]}
    assert a_ids.isdisjoint(b_ids)
    assert all(i.startswith("A-") for i in a_ids)
    assert len(a_ids) == 4  # one alias per entity, not one per row


def test_an_observers_id_for_a_thing_is_stable_across_ticks():
    rows = next(r for n, _, r in observer_views(scene([{"name": "a", "id_pattern": "A-####"}])))
    by_tick = {}
    for r in rows:
        by_tick.setdefault(r["t"], set()).add(r["uid"])
    assert all(ids == by_tick[0] for ids in by_tick.values())


def test_position_error_stays_inside_its_radius():
    """Bounded, not Gaussian (ADR-0033): a tail would make a consumer's gate
    radius clear on most runs rather than every run."""
    spec = scene([{"name": "a", "position_error_m": 10.0}])
    truth = generate_entity_rows(spec)
    seen = next(r for n, _, r in observer_views(spec))
    for t, o in zip(truth, seen):
        assert metres(t, o) <= 10.0 + 1e-6


def test_a_perfect_observer_reports_the_truth():
    spec = scene([{"name": "a", "position_error_m": 0.0}])
    truth = generate_entity_rows(spec)
    seen = next(r for n, _, r in observer_views(spec))
    assert [r["lat"] for r in seen] == [r["lat"] for r in truth]


def test_reports_are_what_the_sensor_says_about_itself():
    feeds = dict((n, r) for n, _, r in observer_views(
        scene([{"name": "a", "reports": {"ce": 6.0}}, {"name": "b", "reports": {"ce": 12.0}}])))
    assert {r["ce"] for r in feeds["a"]} == {6.0}
    assert {r["ce"] for r in feeds["b"]} == {12.0}


def test_reported_columns_survive_a_column_oriented_encoder():
    """`reports` keys are real columns the spec never declares — the exact
    shape of the bug ADR-0028 found in entity id and tick."""
    spec = scene([{"name": "a", "reports": {"ce": 6.0}}])
    spec = spec.model_copy(update={"output": spec.output.model_copy(update={"format": "csv"})})
    name, view, rows = next(iter(observer_views(spec)))
    header = get_encoder("csv")(encode_view(view), rows).decode().splitlines()[0]
    assert "ce" in header.split(",")


def test_observer_options_overlay_the_shared_output_options():
    spec = load_spec({
        "name": "s", "seed": 3,
        "columns": [{"name": "lat", "generator": "lat"}, {"name": "lon", "generator": "lon"}],
        "output": {"format": "cot", "options": {"stale_seconds": 30, "type": "a-f-G"}},
        "entity": {"count": 1, "ticks": 1, "observers": [
            {"name": "a"}, {"name": "b", "options": {"type": "a-h-G"}}]},
    })
    views = {n: v for n, v, _ in observer_views(spec)}
    assert views["a"].output.options["type"] == "a-f-G"
    assert views["b"].output.options["type"] == "a-h-G"
    assert views["b"].output.options["stale_seconds"] == 30  # overlay, not replace


# ── Truth ────────────────────────────────────────────────────────────

def test_truth_names_every_entity_under_every_observer():
    spec = scene([{"name": "a", "id_pattern": "A-####"}, {"name": "b", "id_pattern": "B-####"}])
    truth = scene_truth(spec)
    assert len(truth["identities"]) == 4
    assert all(set(m) == {"a", "b"} for m in truth["identities"].values())


def test_truth_agrees_with_what_the_feeds_actually_carry():
    """A truth file that drifted from its feeds is worse than none: it would
    score a consumer against ids nothing emitted."""
    spec = scene([{"name": "a", "id_pattern": "A-####"}, {"name": "b", "id_pattern": "B-####"}])
    truth = scene_truth(spec)
    feeds = dict((n, r) for n, _, r in observer_views(spec))
    for observer, rows in feeds.items():
        claimed = {m[observer] for m in truth["identities"].values()}
        assert claimed == {r["uid"] for r in rows}


def test_truth_is_never_a_feed():
    """It ships beside the feeds as its own member, and no feed contains it."""
    members = encode_observers(scene([{"name": "a", "id_pattern": "A-####"}]))
    names = [filename for _, filename, _ in members]
    assert names[-1].endswith("-truth.json")
    truth = json.loads(members[-1][2])
    feed = members[0][2].decode()
    assert "identities" in truth
    assert "identities" not in feed


# ── Wiring ───────────────────────────────────────────────────────────

def test_row_count_counts_every_observer():
    assert effective_row_count(scene([{"name": "a"}, {"name": "b"}])) == 4 * 6 * 2
    assert effective_row_count(scene([])) == 4 * 6


def test_streaming_a_multi_observer_spec_is_refused():
    spec = scene([{"name": "a"}, {"name": "b"}])
    spec = spec.model_copy(update={
        "output": spec.output.model_copy(update={"format": "ndjson"}),
        "sink": spec.sink.model_copy(update={"sink": "tcp", "options": {"host": "127.0.0.1",
                                                                       "port": 9}}),
    })
    with pytest.raises(ValueError, match="one feed per observer|one whole file per observer"):
        run(spec)


def test_duplicate_observer_names_are_refused():
    with pytest.raises(ValueError, match="duplicate observer names"):
        scene([{"name": "a"}, {"name": "a"}])


def test_run_writes_one_file_per_observer_plus_truth(tmp_path):
    spec = scene([{"name": "cot-01", "id_pattern": "A-####"},
                  {"name": "cot-02", "id_pattern": "B-####"}])
    spec = spec.model_copy(update={
        "sink": spec.sink.model_copy(update={
            "sink": "file", "options": {"path": str(tmp_path / "scene.ndjson")}}),
    })
    run(spec)
    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == ["scene-cot-01.ndjson", "scene-cot-02.ndjson", "scene-truth.json"]


def test_an_id_pattern_that_collides_is_refused_not_merged():
    """A pattern with too few placeholders gives two entities one name. The
    feeds look fine; only the answer key quietly merges them, and scoring a
    consumer against that marks correct refusals wrong."""
    spec = scene([{"name": "a", "id_pattern": "A-###"}], count=60, id_pattern="T-#")
    with pytest.raises(ValueError, match="same id to more than one entity"):
        scene_truth(spec)


def test_a_colliding_observer_pattern_is_refused_too():
    spec = scene([{"name": "a", "id_pattern": "A-#"}], count=40)
    with pytest.raises(ValueError, match="observer 'a'.s id_pattern"):
        scene_truth(spec)
