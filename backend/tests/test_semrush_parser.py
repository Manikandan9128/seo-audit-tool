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
