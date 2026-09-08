import json

from app.services.geopulse_parser import parse_geopulse_file


def test_parses_csv():
    content = b"query,mentions\nbest payroll software,12\n"
    result = parse_geopulse_file("export.csv", content)
    assert result["row_count"] == 1
    assert "best payroll software" in result["rows"][0]["raw_text"]


def test_parses_json():
    content = json.dumps({"query": "best payroll software", "mentions": 12}).encode()
    result = parse_geopulse_file("export.json", content)
    assert "best payroll software" in result["rows"][0]["raw_text"]


def test_falls_back_to_raw_text_for_unknown_format():
    content = b"some plain text export from geopulse"
    result = parse_geopulse_file("export.txt", content)
    assert result["rows"][0]["raw_text"] == "some plain text export from geopulse"


def test_truncates_very_long_text():
    content = b"a" * 100_000
    result = parse_geopulse_file("export.txt", content)
    assert len(result["rows"][0]["raw_text"]) == 40_000
