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


def test_keyword_gap_rows_carry_url_mapping_from_gsc():
    rows = [
        {
            "keyword": "widget insurance",
            "search_volume": 1000,
            "domain_positions": {"client.com": 0, "rival.com": 5},
        }
    ]
    result = analyze(
        [_record(rows)],
        own_domain="client.com",
        gsc_query_page_rows=[
            {"query": "widget insurance", "page": "https://client.com/widget-insurance", "position": 18, "impressions": 40}
        ],
    )
    gap_row = result["keyword_gap_rows"][0]
    assert gap_row["target_url"] == "https://client.com/widget-insurance"
    assert gap_row["url_action"] == "OPTIMIZE_EXISTING"
    assert gap_row["current_position"] == 18


def test_technical_issues_carry_page_scoped_url_mapping():
    site_audit_rows = [
        {"page_url": "https://client.com/broken", "http_status_code": "404", "issues": 0, "canonicalization": "Self-referencing", "in_sitemap": 1, "schema_jsonld": 1},
        {"page_url": "https://client.com/fine", "http_status_code": "200", "issues": 0, "canonicalization": "Self-referencing", "in_sitemap": 1, "schema_jsonld": 1},
    ]
    record = {
        "import_type": "site_audit_pages",
        "is_own_site": True,
        "created_at": __import__("datetime").datetime.now(),
        "parsed_data": {"rows": site_audit_rows},
    }
    result = analyze([record], own_domain="client.com")
    bad_status_issue = next(i for i in result["issues"] if "non-200" in i["summary"])
    assert bad_status_issue["target_url"] == "https://client.com/broken"
    assert bad_status_issue["url_action"] == "REDIRECT"


def test_backlink_gap_issue_is_sitewide_with_no_single_url():
    own_backlinks = {
        "import_type": "backlinks",
        "is_own_site": True,
        "created_at": __import__("datetime").datetime.now(),
        "parsed_data": {"row_count": 2, "rows": [{"source_url": "https://a.com/x"}, {"source_url": "https://b.com/y"}]},
    }
    comp_backlinks = {
        "import_type": "backlinks",
        "is_own_site": False,
        "domain_label": "rival.com",
        "created_at": __import__("datetime").datetime.now(),
        "parsed_data": {"row_count": 10, "rows": [{"source_url": f"https://c{i}.com/z"} for i in range(10)]},
    }
    result = analyze([own_backlinks, comp_backlinks], own_domain="client.com")
    gap_issue = next(i for i in result["issues"] if "referring domains" in i["summary"])
    assert gap_issue["target_url"] is None
    assert gap_issue["url_action"] == "SITEWIDE"
