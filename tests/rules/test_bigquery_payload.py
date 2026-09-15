import json

from sar_plimsoll.rules.csv_parser import parse_rules_csv
from sar_plimsoll.storage.bigquery import rules_ndjson


def test_ndjson_lines_are_valid_and_complete():
    rows = parse_rules_csv(
        '1,security,"Quotes "" and commas, ok — café"\n2,bug,Second\n'.encode()
    ).rules
    vectors = {"1": [0.1234567891, -1.0, 0.0], "2": [1e-9, 2.5, -0.333333333]}
    lines = rules_ndjson(rows, vectors, "gemini-embedding-001", "2026-09-13T00:00:00+00:00").read()
    records = [json.loads(line) for line in lines.decode().splitlines()]

    assert [r["id"] for r in records] == ["1", "2"]
    assert records[0]["description"] == 'Quotes " and commas, ok — café'
    assert records[0]["embedding"] == [0.123457, -1.0, 0.0]
    assert records[1]["embedding"] == [0.0, 2.5, -0.333333]
    assert records[1]["embedding_model"] == "gemini-embedding-001"
