from pathlib import Path

from sar_plimsoll.rules.csv_parser import parse_rules_csv

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_spec_example_without_header():
    data = (
        "1, formatting, Avoid single-character variable names — they hurt readability\n"
        "2, performance, Cache repeated database lookups inside the request loop\n"
        "3, security, Never interpolate raw user input directly into SQL queries\n"
    ).encode()
    result = parse_rules_csv(data)
    assert [r.id for r in result.rules] == ["1", "2", "3"]
    assert result.rules[0].description.endswith("— they hurt readability")
    assert result.rules[2].dimension == "security"
    assert not result.header_detected
    assert result.rejected == []


def test_messy_file_accepts_good_rows_and_reports_bad_ones():
    result = parse_rules_csv((FIXTURES / "rules_messy.csv").read_bytes())
    by_id = {r.id: r for r in result.rules}

    assert result.header_detected
    assert set(by_id) == {"1", "2", "3", "4", "5", "6", "9"}
    assert by_id["4"].description == "Don't store secrets, tokens or passwords in source code"
    # Unquoted commas inside a description are rejoined rather than rejected.
    assert (
        by_id["5"].description
        == "Prefer batch APIs over per-item calls, especially over the network"
    )
    assert by_id["5"].dimension == "performance"
    assert by_id["6"].type == "witchcraft" and by_id["6"].dimension is None
    assert "日本語" in by_id["6"].description
    # The first occurrence of a duplicate ID wins.
    assert by_id["3"].description.startswith("Never interpolate")

    reasons = sorted(r.reason.split(" ")[0] for r in result.rejected)
    assert reasons == ["duplicate", "expected", "missing", "missing"]


def test_bom_crlf_and_cp1252_are_handled():
    utf8 = "﻿id,type,description\r\n1,security,Validate input\r\n".encode()
    assert [r.id for r in parse_rules_csv(utf8).rules] == ["1"]

    legacy = "1,style,Use “smart” names – café\n".encode("cp1252")
    result = parse_rules_csv(legacy)
    assert result.encoding == "cp1252"
    assert result.rules[0].description == "Use “smart” names – café"


def test_nul_bytes_do_not_abort_the_file():
    result = parse_rules_csv(b"1,security,Bad\x00row\n2,security,Good row\n")
    assert [r.id for r in result.rules] == ["1", "2"]


def test_thirty_thousand_rows():
    data = "".join(f'{i},performance,"Rule {i}, with a comma"\n' for i in range(30_000)).encode()
    result = parse_rules_csv(data)
    assert len(result.rules) == 30_000
    assert result.rejected == []


def test_content_hash_changes_with_description():
    a = parse_rules_csv(b"1,security,Text A\n").rules[0]
    b = parse_rules_csv(b"1,security,Text B\n").rules[0]
    assert a.content_hash != b.content_hash
