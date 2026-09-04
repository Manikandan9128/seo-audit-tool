import datetime

from app.services.semrush_analysis_service import analyze


def _record(rows, own_domain=True):
    return {
        "import_type": "keyword_gap",
        "is_own_site": own_domain,
        "created_at": datetime.datetime.now(),
        "parsed_data": {"rows": rows},
    }


def test_keyword_gap_flags_not_ranking():
    rows = [
        {
            "keyword": "widget insurance",
            "search_volume": 1000,
            "domain_positions": {"client.com": 0, "rival.com": 5},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert len(gap_rows) == 1
    assert gap_rows[0]["gap_type"] == "not_ranking"
    assert gap_rows[0]["competitor_domain"] == "rival.com"
    assert gap_rows[0]["your_position"] is None


def test_keyword_gap_flags_ranking_far_behind_page1_competitor():
    # Regression test: a keyword where you rank (not 0) but so far behind a
    # page-1 competitor it's effectively invisible was previously missed
    # entirely by the old "position 0 only" rule.
    rows = [
        {
            "keyword": "government certified payroll",
            "search_volume": 1600,
            "domain_positions": {"client.com": 56, "rival.com": 7},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert len(gap_rows) == 1
    assert gap_rows[0]["gap_type"] == "ranking_behind"
    assert gap_rows[0]["your_position"] == 56
    assert gap_rows[0]["competitor_position"] == 7


def test_keyword_gap_excludes_competitive_positions():
    # You rank #15, competitor ranks #12 — both roughly page 1-2, not a
    # real gap under either rule.
    rows = [
        {
            "keyword": "close race keyword",
            "search_volume": 500,
            "domain_positions": {"client.com": 15, "rival.com": 12},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    assert result["keyword_gap_rows"] == []


def test_keyword_gap_excludes_keywords_nobody_ranks_for():
    rows = [
        {
            "keyword": "nobody ranks this",
            "search_volume": 200,
            "domain_positions": {"client.com": 0, "rival.com": 0},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    assert result["keyword_gap_rows"] == []


def test_keyword_gap_picks_best_ranking_competitor_when_multiple():
    rows = [
        {
            "keyword": "multi competitor keyword",
            "search_volume": 300,
            "domain_positions": {"client.com": 0, "rival.com": 40, "leader.com": 3},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert len(gap_rows) == 1
    assert gap_rows[0]["competitor_domain"] == "leader.com"
    assert gap_rows[0]["competitor_position"] == 3


def test_keyword_gap_sorted_by_search_volume_descending():
    rows = [
        {"keyword": "low volume", "search_volume": 100, "domain_positions": {"client.com": 0, "rival.com": 5}},
        {"keyword": "high volume", "search_volume": 5000, "domain_positions": {"client.com": 0, "rival.com": 5}},
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert [r["keyword"] for r in gap_rows] == ["high volume", "low volume"]
