"""Updater param examples (ADR-0021).

The UI fills an update rule's params box from the updater's own registered
example, so a stale or wrong example is a broken form field for every user.
These run each example through its updater for real.
"""

import random

import pytest
from faker import Faker

from chaff.updaters import (
    EntityContext,
    get_updater,
    list_updater_examples,
    list_updaters,
)


def _ctx():
    return EntityContext(rng=random.Random(1), faker=Faker(), entity_index=0, tick=1)


def test_every_updater_registers_an_example():
    """A missing example leaves the UI with an empty params box and no hint
    about what the rule needs."""
    assert set(list_updater_examples()) == set(list_updaters())


@pytest.mark.parametrize("updater_id", list_updaters())
def test_example_params_actually_drive_the_updater(updater_id):
    """The example must be usable as-is: the UI offers it as a working
    default, so it has to run without a KeyError on a missing param."""
    params = list_updater_examples()[updater_id]
    state = {"lat": 34.0, "lon": -118.0, "status": "placed", "reading": 20.0}
    before = dict(state)
    get_updater(updater_id)(_ctx(), state, params)
    assert state != before, f"{updater_id} example changed nothing"


@pytest.mark.parametrize("updater_id", list_updaters())
def test_example_is_json_safe(updater_id):
    """The UI serializes the example into a textarea with JSON.stringify."""
    import json
    assert json.loads(json.dumps(list_updater_examples()[updater_id]))


def test_registry_endpoint_exposes_examples():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from api.main import app
    reg = TestClient(app).get("/registry").json()
    assert set(reg["updater_examples"]) == set(reg["updaters"])
