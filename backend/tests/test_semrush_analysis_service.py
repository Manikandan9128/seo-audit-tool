import datetime

from app.services.semrush_analysis_service import analyze


def _record(rows, own_domain=True):
    return {
        "import_type": "keyword_gap",
        "is_own_site": own_domain,
        "created_at": datetime.datetime.now(),
        "parsed_data": {"rows": rows},
    }


def test_keyword_gap_flags_missing():
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
    assert gap_rows[0]["gap_category"] == "Missing"
    assert gap_rows[0]["competitor_positions"] == [{"competitor": "rival.com", "position": 5, "ranking_url": None}]
    assert gap_rows[0]["your_position"] is None


def test_keyword_gap_flags_shared_even_when_close_race():
    # You rank #15, competitor ranks #12 — both roughly page 1-2. Per the
    # 2026-09-18 spec, existence of a ranking on both sides makes this
    # "Shared", not excluded — how close the race is doesn't matter here.
    rows = [
        {
            "keyword": "close race keyword",
            "search_volume": 500,
            "domain_positions": {"client.com": 15, "rival.com": 12},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert len(gap_rows) == 1
    assert gap_rows[0]["gap_category"] == "Shared"
    assert gap_rows[0]["your_position"] == 15
    assert gap_rows[0]["competitor_positions"][0]["position"] == 12


def test_keyword_gap_flags_untapped_when_nobody_ranks():
    rows = [
        {
            "keyword": "nobody ranks this",
            "search_volume": 200,
            "domain_positions": {"client.com": 0, "rival.com": 0},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert len(gap_rows) == 1
    assert gap_rows[0]["gap_category"] == "Untapped"
    assert gap_rows[0]["competitor_positions"] == []


def test_keyword_gap_excludes_when_only_you_rank():
    # You rank, nobody else compared does — not a gap at all.
    rows = [
        {
            "keyword": "your own turf",
            "search_volume": 500,
            "domain_positions": {"client.com": 3, "rival.com": 0},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    assert result["keyword_gap_rows"] == []


def test_keyword_gap_keeps_every_ranking_competitor_not_just_best():
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
    competitors = gap_rows[0]["competitor_positions"]
    assert [c["competitor"] for c in competitors] == ["leader.com", "rival.com"]
    assert [c["position"] for c in competitors] == [3, 40]


def test_keyword_gap_carries_ranking_urls():
    rows = [
        {
            "keyword": "url keyword",
            "search_volume": 400,
            "domain_positions": {"client.com": 5, "rival.com": 2},
            "domain_ranking_urls": {"client.com": "client.com/page", "rival.com": "rival.com/page"},
        }
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert gap_rows[0]["your_url"] == "client.com/page"
    assert gap_rows[0]["competitor_positions"][0]["ranking_url"] == "rival.com/page"


def test_keyword_gap_sorted_by_search_volume_descending():
    rows = [
        {"keyword": "low volume", "search_volume": 100, "domain_positions": {"client.com": 0, "rival.com": 5}},
        {"keyword": "high volume", "search_volume": 5000, "domain_positions": {"client.com": 0, "rival.com": 5}},
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    gap_rows = result["keyword_gap_rows"]
    assert [r["keyword"] for r in gap_rows] == ["high volume", "low volume"]


def test_keyword_gap_issue_entry_is_marked_for_replacement():
    # 2026-09-24 single-source-of-truth spec: site_audit.py replaces this
    # entry with one built from the fully relevance/KD/volume-filtered
    # dataset before Core Problem ever sees it — it can only find and drop
    # the right entry if this one carries the marker.
    rows = [{"keyword": "widget insurance", "search_volume": 1000, "domain_positions": {"client.com": 0, "rival.com": 5}}]
    result = analyze([_record(rows)], own_domain="client.com")
    keyword_gap_issues = [i for i in result["issues"] if i.get("type") == "keyword_gap"]
    assert len(keyword_gap_issues) == 1
    assert "keyword gap" in keyword_gap_issues[0]["summary"].lower()


def test_keyword_gap_fallback_path_issue_entry_is_also_marked():
    # No own_domain / no domain_positions matrix -> the simple fallback
    # path (a different code branch, its own issues.append call).
    rows = [{"keyword": "some keyword", "search_volume": 500}]
    result = analyze([_record(rows, own_domain=False)], own_domain=None)
    keyword_gap_issues = [i for i in result["issues"] if i.get("type") == "keyword_gap"]
    assert len(keyword_gap_issues) == 1


def test_keyword_gap_dedupes_same_keyword_across_casing():
    # 2026-09-21 spec rule 11: a raw export repeating the same keyword under
    # different casing must collapse to one row, not two.
    rows = [
        {"keyword": "Dumper Lorry", "search_volume": 1000, "domain_positions": {"client.com": 0, "rival.com": 5}},
        {"keyword": "dumper lorry", "search_volume": 1000, "domain_positions": {"client.com": 0, "rival.com": 5}},
    ]
    result = analyze([_record(rows)], own_domain="client.com")
    assert len(result["keyword_gap_rows"]) == 1
