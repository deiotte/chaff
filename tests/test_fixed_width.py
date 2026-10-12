"""Fixed-width extracts and their copybook layout (ADR-0044)."""

import pytest

from chaff.engine import encode_view, generate_rows
from chaff.formats import get_encoder
from chaff.spec import load_spec


def spec(**options):
    return load_spec({
        "name": "legacy cases", "seed": 4, "rows": 5,
        "columns": [
            {"name": "case_no", "generator": "pattern", "params": {"pattern": "CV-####"}},
            {"name": "amount", "generator": "int_range", "params": {"min": 1, "max": 99999}},
            {"name": "name", "generator": "full_name"},
        ],
        "output": {"format": "fixed_width", "options": options},
    })


def encode(s, fmt="fixed_width"):
    return get_encoder(fmt)(encode_view(s), generate_rows(s))


def test_every_record_is_the_same_length_and_fields_line_up():
    s = spec(pad={"amount": "0"})
    lines = encode(s).decode().splitlines()
    rows = generate_rows(s)
    assert len({len(line) for line in lines}) == 1
    for line, r in zip(lines, rows):
        assert line[:7] == r["case_no"]
        assert int(line[7:12]) == r["amount"]          # zero-padded, right-aligned
        assert line[12:].rstrip() == r["name"]         # left-aligned, space-padded


def test_layout_copybook_matches_the_data_file():
    s = spec(pad={"amount": "0"})
    cpy = encode(s, "fixed_width_layout").decode()
    line_len = len(encode(s).decode().splitlines()[0])
    assert f"(record length {line_len})" in cpy
    assert "CASE-NO" in cpy and "PIC 9(5)." in cpy and "*> 1-7" in cpy


def test_explicit_widths_overflow_fail_loud_unless_truncating():
    with pytest.raises(ValueError, match="widen it or set overflow"):
        encode(spec(widths={"name": 3}))
    out = encode(spec(widths={"name": 3}, overflow="truncate")).decode()
    assert all(len(line) == 7 + 5 + 3 for line in out.splitlines())


def test_header_crlf_and_code_page():
    out = encode(spec(header=True, eol="crlf", encoding="cp1252"))
    text = out.decode("cp1252")
    assert text.startswith("case_no") and text.count("\r\n") == 6


def test_unencodable_characters_fail_unless_replaced():
    s = load_spec({"name": "n", "seed": 1, "rows": 1,
                   "columns": [{"name": "v", "generator": "choice", "params": {"values": ["Nguyễn"]}}],
                   "output": {"format": "fixed_width", "options": {"encoding": "latin-1"}}})
    with pytest.raises(UnicodeEncodeError):
        encode(s)
    s.output.options["errors"] = "replace"
    assert encode(s) == b"Nguy?n\n"


def test_formula_leads_are_guarded():
    s = load_spec({"name": "n", "seed": 1, "rows": 1,
                   "columns": [{"name": "v", "generator": "choice",
                                "params": {"values": ["=HYPERLINK(\"http://x\")"]}}],
                   "output": {"format": "fixed_width"}})
    assert not encode(s).decode().startswith("=")


def test_deterministic():
    assert encode(spec()) == encode(spec())


@pytest.mark.parametrize("opts, msg", [
    ({"align": {"name": "middle"}}, "align"), ({"pad": {"name": "ab"}}, "one character"),
    ({"eol": "cr"}, "eol"), ({"overflow": "wrap"}, "overflow"), ({"widths": {"name": 0}}, "at least 1"),
])
def test_bad_options_are_explained(opts, msg):
    with pytest.raises(ValueError, match=msg):
        encode(spec(**opts))
