"""Browser-driven UI tests (ADR-0022).

These execute the page. Everything else that guards the UI parses it as
text — which cannot tell whether the form actually round-trips a spec, only
whether the source still looks the way it did when the guard was written.

Each test asserts on **the spec the page puts on the wire**, because that is
the product (INV-1): the UI's only job is to build a `DatasetSpec`. The bug
in ADR-0020 was precisely a page that looked fine and sent the wrong spec.

Skips (never fails) without Playwright or a Chromium build — see conftest.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def sent(page):
    """Captures the JSON body of each POST the page makes, by endpoint."""
    bodies: dict[str, dict] = {}

    def record(req):
        if req.method == "POST" and req.post_data:
            try:
                bodies[req.url.split("?")[0].rstrip("/").split("/")[-1]] = json.loads(req.post_data)
            except json.JSONDecodeError:
                pass

    page.on("request", record)
    return bodies


def _load_preset(page, name):
    page.get_by_text(name, exact=False).first.click()
    page.wait_for_timeout(400)


def _preview(page):
    page.locator("#previewBtn").click()
    page.wait_for_timeout(1500)


# ── the ADR-0020 regression, executed rather than pattern-matched ─────

def test_entity_preset_round_trips_through_the_form(page, sent):
    """The original bug: loading `moving_tracks` dropped `entity`, and the
    page cheerfully previewed disconnected points with no track id or tick.
    HTTP 200, wrong data, no warning."""
    _load_preset(page, "moving_tracks")
    _preview(page)

    spec = sent["preview"]
    assert spec["entity"], "entity dropped between the preset and the request"
    assert spec["entity"]["count"] == 10 and spec["entity"]["ticks"] == 30
    assert spec["entity"]["id_column"] == "track_id"
    # `rows` is meaningless under an entity spec and must not be sent.
    assert "rows" not in spec

    headers = page.locator("#preview th").all_inner_texts()
    assert "track_id" in headers and "t" in headers


def test_multitable_preset_round_trips_and_previews_every_table(page, sent):
    _load_preset(page, "retail_orders")
    _preview(page)

    spec = sent["preview"]
    assert [t["name"] for t in spec["tables"]] == ["orders", "lines"]
    assert [t["rows"] for t in spec["tables"]] == [200, 600]
    sections = page.locator(".preview-table-name").all_inner_texts()
    assert len(sections) == 3
    assert sections[0].lower().startswith("customers")


def test_plain_preset_sends_no_entity_or_tables(page, sent):
    """The common case must stay exactly as it was."""
    _load_preset(page, "crm_contacts")
    _preview(page)
    spec = sent["preview"]
    assert "entity" not in spec and "tables" not in spec
    assert spec["rows"] == 500


# ── the ADR-0021 editors, executed ───────────────────────────────────

def test_editing_an_entity_changes_the_spec_that_is_sent(page, sent):
    """Without the editor read-back, the request would carry the values the
    editor was *opened* with and silently discard every edit."""
    _load_preset(page, "moving_tracks")
    page.locator("#entCount").fill("4")
    page.locator("#entTicks").fill("5")
    page.locator("#entIdCol").fill("unit")
    _preview(page)

    entity = sent["preview"]["entity"]
    assert entity["count"] == 4 and entity["ticks"] == 5
    assert entity["id_column"] == "unit"
    assert "unit" in page.locator("#preview th").all_inner_texts()


def test_entity_row_total_tracks_the_inputs(page):
    """The count × ticks relationship is the thing people get wrong, so the
    page states the result rather than leaving it as mental arithmetic."""
    _load_preset(page, "moving_tracks")
    assert "300" in page.locator("#entTotal").inner_text()
    page.locator("#entCount").fill("7")
    page.locator("#entCount").dispatch_event("input")
    assert "210" in page.locator("#entTotal").inner_text()


def test_build_a_time_series_from_nothing(page, sent):
    """The whole point of ADR-0021: authoring, not just carrying."""
    page.locator("#addEntityBtn").click()
    page.wait_for_timeout(300)
    page.locator("#entCount").fill("3")
    page.locator("#entTicks").fill("4")
    page.locator("#entIdCol").fill("sensor_id")
    page.locator("#entTickCol").fill("t")
    page.locator("#addUpdate").click()
    page.wait_for_timeout(200)

    rule = page.locator("#entUpdates .u-id").first
    rule.select_option("drift")
    page.wait_for_timeout(200)
    # Params must be pre-filled from the registry's example, not left blank.
    params = json.loads(page.locator("#entUpdates .u-params").first.input_value())
    assert params["column"] == "reading"

    _preview(page)
    entity = sent["preview"]["entity"]
    assert entity["count"] == 3 and entity["ticks"] == 4
    assert entity["updates"] == [{"updater": "drift", "params": params}]
    headers = page.locator("#preview th").all_inner_texts()
    assert headers[:2] == ["sensor_id", "t"]


def test_build_a_multitable_spec_from_nothing(page, sent):
    """Including a working fk link — the reason multi-table exists."""
    page.locator("#name").fill("shop")
    page.locator("#rows").fill("5")
    page.locator("#addTableBtn").click()
    page.wait_for_timeout(300)

    card = page.locator("#tablesEditor .tbl").first
    card.locator(".t-name").fill("orders")
    card.locator(".t-rows").fill("9")
    card.locator(".t-addcol").click()
    page.wait_for_timeout(200)
    card.locator(".col").nth(0).locator(".c-name").fill("order_id")
    card.locator(".col").nth(0).locator(".c-gen").select_option("row_id")
    card.locator(".col").nth(1).locator(".c-name").fill("shop_id")
    card.locator(".col").nth(1).locator(".c-gen").select_option("fk")
    page.wait_for_timeout(200)
    card.locator(".col").nth(1).locator(".c-params").fill('{"table":"shop","column":"id"}')

    _preview(page)
    tables = sent["preview"]["tables"]
    assert len(tables) == 1 and tables[0]["name"] == "orders" and tables[0]["rows"] == 9

    body = page.request.post(page.url.rstrip("/") + "/preview?limit=50",
                             data=json.dumps(sent["preview"]),
                             headers={"Content-Type": "application/json"}).json()
    parents = {r["id"] for r in body["tables"]["shop"]}
    assert parents, "primary table generated no rows"
    assert all(r["shop_id"] in parents for r in body["tables"]["orders"]), \
        "fk values don't resolve to the parent table"


def test_removing_an_entity_returns_a_flat_table(page, sent):
    _load_preset(page, "moving_tracks")
    assert page.locator("#rows").is_disabled()
    page.locator("#dropEntity").click()
    page.wait_for_timeout(300)

    assert not page.locator("#rows").is_disabled()
    _preview(page)
    assert "entity" not in sent["preview"]


# ── invariants the page has to hold on its own ───────────────────────

def test_the_two_modes_are_mutually_exclusive(page):
    """`DatasetSpec` rejects a spec carrying both, so the page must not let
    one be built — a disabled button beats a 422 after the work is done."""
    _load_preset(page, "moving_tracks")
    assert page.locator("#addTableBtn").is_disabled()
    assert page.locator("#advHint").inner_text().strip()

    _load_preset(page, "retail_orders")
    assert page.locator("#addEntityBtn").is_disabled()
    assert page.locator("#advHint").inner_text().strip()


def test_incomplete_related_table_is_refused_with_a_useful_message(page, sent):
    page.locator("#addTableBtn").click()
    page.wait_for_timeout(300)
    _preview(page)
    msg = page.locator("#msg").inner_text()
    assert "name" in msg.lower(), msg
    assert "preview" not in sent, "an invalid spec was sent to the server"


def test_editing_stays_usable_while_a_table_is_incomplete(page):
    """Validation runs on build only. Running it on re-render broke the
    Remove button whenever another card was half-filled."""
    page.locator("#addTableBtn").click()
    page.wait_for_timeout(300)
    page.locator("#addTableBtn").click()
    page.wait_for_timeout(300)
    assert page.locator("#tablesEditor .tbl").count() == 2

    page.locator("#tablesEditor .tbl").nth(1).locator(".t-drop").click()
    page.wait_for_timeout(300)
    assert page.locator("#tablesEditor .tbl").count() == 1


def test_derived_column_picker_does_not_leak_across_tables(page):
    """`columnsBefore` feeds this list. Scanning the whole page would offer
    the primary table's columns to a related table's derived column, which
    the engine then rejects at load (spec.py validates per-table)."""
    page.locator("#name").fill("shop")
    page.locator("#addTableBtn").click()
    page.wait_for_timeout(300)

    card = page.locator("#tablesEditor .tbl").first
    card.locator(".t-name").fill("orders")
    card.locator(".col").nth(0).locator(".c-name").fill("qty")
    card.locator(".t-addcol").click()
    page.wait_for_timeout(200)
    derived = card.locator(".col").nth(1)
    derived.locator(".c-name").fill("total")
    derived.locator(".c-gen").select_option("derived")
    page.wait_for_timeout(400)

    # The list is filled on focus, not on render — without this the datalist
    # is empty and any subset assertion below would pass for the wrong reason.
    derived.locator(".fxpanel .op").first.focus()
    page.wait_for_timeout(200)

    list_id = derived.locator(".fxpanel .op").first.get_attribute("list")
    offered = {o.get_attribute("value")
               for o in page.locator(f"#{list_id} option").all()}
    assert offered == {"qty"}, (
        f"related table's derived column was offered {offered}; it may only "
        "reference columns of its own table")


def test_derived_picker_still_offers_the_primary_table_its_own_columns(page):
    """The guard above must not pass by offering nothing at all."""
    page.locator("#cols .col").nth(0).locator(".c-name").fill("price")
    page.locator("#cols .col").nth(1).locator(".c-name").fill("qty")
    page.locator("#addCol").click()
    page.wait_for_timeout(200)

    derived = page.locator("#cols .col").nth(2)
    derived.locator(".c-name").fill("total")
    derived.locator(".c-gen").select_option("derived")
    page.wait_for_timeout(400)
    derived.locator(".fxpanel .op").first.focus()
    page.wait_for_timeout(200)

    list_id = derived.locator(".fxpanel .op").first.get_attribute("list")
    offered = {o.get_attribute("value")
               for o in page.locator(f"#{list_id} option").all()}
    assert offered == {"price", "qty"}, offered


def test_gallery_cards_state_the_real_shape(page):
    cards = {}
    for card in page.locator(".spec-card").all():
        lines = [l for l in card.inner_text().split("\n") if l.strip()]
        cards[lines[0].split()[0]] = lines[-2] if len(lines) > 2 else lines[-1]

    assert "3 tables" in cards["retail_orders"] and "850" in cards["retail_orders"]
    assert "10×30" in cards["moving_tracks"] and "300" in cards["moving_tracks"]
    assert "9 cols" in cards["crm_contacts"]  # plain specs unchanged


# ── the token-protected server (ADR-0025) ────────────────────────────

def test_a_protected_server_tells_you_what_to_do(browser, token_server):
    """A page that silently fails is the failure this project keeps fixing.

    On a server that requires a token the page must still load, say what is
    wrong, and recover completely once the token is supplied — not sit on
    "Loading…" forever with a console full of errors, which is what it did
    before the refusal path existed.
    """
    base, token = token_server
    ctx = browser.new_context()
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base, wait_until="networkidle")

        # The page itself is reachable — you must be able to load it to type
        # the token into it.
        assert page.locator("h1").inner_text() == "chaff"
        assert page.locator("#apiToken").is_visible()

        banner = page.locator("#authBanner")
        assert banner.is_visible(), "no explanation shown for a refused page"
        assert "access token" in banner.inner_text().lower()
        assert page.locator(".spec-card").count() == 0

        # A wrong token gets a different, accurate message.
        page.locator("#apiToken").fill("wrong")
        page.locator("#apiToken").dispatch_event("change")
        page.wait_for_timeout(1200)
        assert "refused" in banner.inner_text().lower()

        # The right one recovers everything, not just the registry.
        page.locator("#apiToken").fill(token)
        page.locator("#apiToken").dispatch_event("change")
        page.wait_for_timeout(2000)
        assert banner.is_hidden(), banner.inner_text()
        assert page.locator(".spec-card").count() > 0, "gallery never recovered"

        page.locator("#previewBtn").click()
        page.wait_for_timeout(1500)
        assert page.locator("#preview th").count() > 0, "preview failed with a valid token"
    finally:
        ctx.close()
    assert not errors, f"JavaScript errors on the page: {errors}"
