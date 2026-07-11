"""The dropdown groups (list_generator_groups) must stay in lockstep with the
registry: every generator grouped exactly once, no phantom ids, no 'Other'
catch-all. This is the tripwire that keeps a newly-added generator from
silently falling out of (or duplicating in) the UI dropdown."""

from chaff.generators import _GROUP_ORDER, list_generator_groups, list_generators


def test_every_generator_is_grouped_exactly_once():
    registered = set(list_generators())
    placed = [g for _, ids in _GROUP_ORDER for g in ids]
    assert sorted(placed) == sorted(registered), (
        "generator groups out of sync with the registry — "
        f"missing: {registered - set(placed)}, extra: {set(placed) - registered}")
    assert len(placed) == len(set(placed)), "a generator is listed in two groups"


def test_no_other_catch_all_group():
    # If this fails, a generator wasn't placed in _GROUP_ORDER and got dumped
    # into the trailing 'Other' group — add it to a real group instead.
    assert "Other" not in {g["group"] for g in list_generator_groups()}


def test_groups_preserve_order_and_shape():
    groups = list_generator_groups()
    assert [g["group"] for g in groups] == [name for name, _ in _GROUP_ORDER]
    for g in groups:
        assert g["generators"] and all(isinstance(x, str) for x in g["generators"])
