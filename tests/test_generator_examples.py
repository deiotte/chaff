"""The params examples the registry publishes (shown as the UI's params-field
placeholder) must be *true*: every example names a real generator and, fed to
that generator as-is, produces a value. This is the tripwire that stopped the
"pick choice, get a bare `values` KeyError" trap — if a placeholder shape drifts
from what the generator reads, this fails before a user ever hits it."""

from chaff.engine import generate_rows
from chaff.generators import list_generator_examples, list_generators
from chaff.spec import load_spec


def test_examples_reference_real_generators():
    registered = set(list_generators())
    unknown = set(list_generator_examples()) - registered
    assert not unknown, f"examples for unregistered generators: {unknown}"


def test_examples_are_json_object_shaped():
    for gen_id, ex in list_generator_examples().items():
        assert isinstance(ex, dict), f"{gen_id} example must be a params object"


# `fk` only resolves inside a multi-table spec (it draws from a parent table),
# so it can't be exercised as a lone single-table column here.
_NEEDS_MULTITABLE = {"fk"}


def test_every_example_actually_generates():
    # Feed each published example to its generator exactly as a user would paste
    # it into the params field. It must yield a value without raising (e.g.
    # `choice` needs a `values` key — a numeric-range example would KeyError).
    for gen_id, ex in list_generator_examples().items():
        if gen_id in _NEEDS_MULTITABLE:
            continue
        spec = load_spec({
            "name": "t", "seed": 7, "rows": 5,
            "columns": [{"name": "v", "generator": gen_id, "params": ex}],
            "output": {"format": "csv"},
        })
        rows = list(generate_rows(spec))
        assert len(rows) == 5, f"{gen_id} example produced no rows"


def test_choice_example_has_values_key():
    # The reported bug: choice needs {"values": [...]}. Guard it explicitly so a
    # future edit can't quietly revert the placeholder to a shape choice rejects.
    ex = list_generator_examples()["choice"]
    assert "values" in ex and isinstance(ex["values"], list) and ex["values"]
