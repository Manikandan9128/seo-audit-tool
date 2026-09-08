import io

from app.services.semrush_parser import parse_semrush_file


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def test_keyword_gap_detects_real_domain_matrix():
    # Shape confirmed against a real Semrush Keyword Gap export (Keyword |
    # Intents | Volume | KD | CPC | Competition Density | <domain columns>
    # | <domain> (pages) columns).
    csv = (
        "Keyword,Intents,Volume,Keyword Difficulty,CPC,Competition Density,"
        "client.com,rival.com,client.com (pages),rival.com (pages),Results\n"
        "widget insurance,Commercial,1000,35,12.5,0.4,0,7,,https://rival.com/widgets,50000\n"
    )
    import_type, parsed = parse_semrush_file("gap.keywords_test.csv", _csv_bytes(csv))
    assert import_type == "keyword_gap"
    row = parsed["rows"][0]
    assert row["domain_positions"] == {"client.com": 0, "rival.com": 7}
    assert row["cpc"] == 12.5
    assert row["intent"] == "Commercial"


def test_keyword_gap_without_domain_columns_still_parses_basic_fields():
    csv = "Keyword,Volume,Keyword Difficulty,CPC\nsimple keyword,500,20,3.0\n"
    import_type, parsed = parse_semrush_file("gap.keywords_simple.csv", _csv_bytes(csv))
    assert import_type == "keyword_gap"
    row = parsed["rows"][0]
    assert row["keyword"] == "simple keyword"
    assert "domain_positions" not in row or not row["domain_positions"]


def test_organic_positions_not_capped_at_500():
    # Regression guard: a competitor's Google Sheet (see
    # google_sheets_service.py) is supposed to hold their FULL keyword
    # list — capping this import at 500 rows silently understated it
    # (confirmed real: a competitor's Sheet showed exactly "500 keywords
    # tracked" as an upload-cap artifact, not their true total).
    header = "Keyword,Search Volume,Keyword Difficulty,Position,Previous Position,URL\n"
    body = "".join(f"keyword {i},100,20,{i % 50 + 1},{i % 50 + 2},https://example.com/{i}\n" for i in range(600))
    import_type, parsed = parse_semrush_file("positions_test.csv", _csv_bytes(header + body))
    assert import_type == "organic_positions"
    assert parsed["row_count"] == 600
    assert len(parsed["rows"]) == 600
