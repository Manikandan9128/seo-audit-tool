from types import SimpleNamespace
from unittest.mock import patch

from app.api.routes import site_audit


def _client():
    return SimpleNamespace(name="Geopits", website_url="https://www.geopits.com/", id=1)


def _run(data):
    with patch.object(site_audit, "fetch_homepage_text", return_value=None), \
         patch.object(site_audit, "capture_homepage_screenshots", return_value={}), \
         patch.object(site_audit, "generate_competitor_narratives_batch") as mock_batch:
        mock_batch.side_effect = lambda client_name, client_domain, facts: {
            domain: {"best_at": ["x"]} for domain in facts
        }
        return site_audit._generate_competitor_narratives(_client(), data)


def test_www_and_bare_domain_dedupe_to_one_competitor():
    # competitor_positions and competitor_rows are two independently-sourced
    # per-competitor lists that can disagree on the "www." prefix for the
    # same competitor (confirmed live on a Geopits report: competitor_positions
    # had "www.navisite.com", the Semrush export row had "navisite.com").
    # Before the fix, the old membership check compared raw strings and
    # treated these as two different competitors — doubling that
    # competitor's narrative slides.
    data = {
        "competitor_positions": {
            "www.navisite.com": [{"keyword": "cloud migration", "position": 5, "search_volume": 100}],
        },
        "competitor_rows": [
            {"domain": "navisite.com", "organic_traffic": 1100, "dr": 66},
            {"domain": "mydbops.com", "organic_traffic": 349, "dr": 43},
        ],
        "competitor_analysis": {"issues": []},
    }
    result = _run(data)
    assert sorted(result.keys()) == ["mydbops.com", "navisite.com"]


def test_canonical_domain_prefers_competitor_row_spelling():
    # The Competitor Analysis table (add_competitor_table_slide) renders
    # competitor_rows' domain spelling — narrative slides should refer to
    # the same string, not whichever source happened to be scanned first.
    data = {
        "competitor_positions": {
            "www.rival.com": [{"keyword": "widgets", "position": 3, "search_volume": 50}],
        },
        "competitor_rows": [{"domain": "rival.com", "organic_traffic": 500, "dr": 40}],
        "competitor_analysis": {"issues": []},
    }
    result = _run(data)
    assert list(result.keys()) == ["rival.com"]


def test_own_domain_still_excluded_regardless_of_www():
    data = {
        "competitor_positions": {
            "www.geopits.com": [{"keyword": "self", "position": 1, "search_volume": 10}],
            "www.rival.com": [{"keyword": "widgets", "position": 3, "search_volume": 50}],
        },
        "competitor_rows": [],
        "competitor_analysis": {"issues": []},
    }
    result = _run(data)
    assert "geopits.com" not in result and "www.geopits.com" not in result
    # No competitor_rows entry for rival.com here, so positions' own
    # spelling ("www.rival.com") is the only canonical form available.
    assert list(result.keys()) == ["www.rival.com"]
