from unittest.mock import patch

from app.services.business_theme_service import UNCLASSIFIED_THEME
from app.services.keyword_cluster_pipeline import build_final_keyword_clusters


def _rows():
    return [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "construction payroll services", "search_volume": 500, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "how to do construction payroll", "search_volume": 300, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "construction payroll process", "search_volume": 100, "intent": "Informational", "page_category": "Blog / Guide"},
    ]


def test_empty_rows_returns_as_is():
    assert build_final_keyword_clusters([], "Acme", None, None) == []


def test_mixed_intent_and_page_category_never_share_a_cluster():
    rows = _rows()
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Construction Payroll" for r in rows}), \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", "a construction payroll SaaS", None)

    landing = {r["cluster"] for r in rows if r["page_category"] == "Landing Page"}
    blog = {r["cluster"] for r in rows if r["page_category"] == "Blog / Guide"}
    assert landing.isdisjoint(blog)
    assert len(landing) == 1 and len(blog) == 1


def test_single_keyword_bucket_gets_theme_labeled_cluster_without_ai_call():
    rows = [{"keyword": "certified payroll compliance audit", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"certified payroll compliance audit": "Payroll Compliance"}), \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters") as mock_candidate, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_candidate.assert_not_called()
    assert rows[0]["cluster"] == "Payroll Compliance"
    assert rows[0]["primary_or_secondary"] == "Primary"


def test_unclassified_theme_without_ai_evidence_stays_unclustered():
    rows = [
        {"keyword": "random one", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "random two", "search_volume": 40, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    assert all(r["business_theme"] == UNCLASSIFIED_THEME for r in rows)
    assert all(r["cluster"] == "" for r in rows)
    assert all("primary_or_secondary" not in r for r in rows)


def test_primary_selection_prefers_ranking_signal_over_raw_volume():
    rows = [
        {"keyword": "construction payroll software", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "construction payroll provider", "search_volume": 300, "intent": "Transactional", "page_category": "Landing Page", "current_position": 8},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Construction Payroll" for r in rows}), \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    primary = next(r for r in rows if r["primary_or_secondary"] == "Primary")
    assert primary["keyword"] == "construction payroll provider"


def test_cannibalization_flags_two_clusters_sharing_a_strong_existing_page_match():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "certified payroll", "search_volume": 500, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    themes = {"construction payroll": "Construction Payroll", "certified payroll": "Certified Payroll"}

    def fake_match(keywords, pages):
        return {"url": "https://example.com/payroll", "title": "Payroll", "match_strength": "strong"}

    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", side_effect=fake_match):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    assert all(r["cannibalization_status"] for r in rows)
    assert rows[0]["cluster"] != rows[1]["cluster"]


def test_no_cannibalization_when_only_one_cluster_matches_a_page():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"construction payroll": "Construction Payroll"}), \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value={"url": "https://example.com/payroll", "title": "Payroll", "match_strength": "strong"}):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    assert rows[0]["cannibalization_status"] is None
    assert rows[0]["existing_page_match_strength"] == "strong"


def test_existing_business_theme_is_preserved_not_reclassified():
    # A real Semrush export can already carry its own theme/topic data —
    # generate_business_themes must not be called (and must not overwrite
    # it) when at least one row already has one.
    rows = [{"keyword": "kw", "search_volume": 10, "intent": "Informational", "page_category": "Blog / Guide", "business_theme": "Already Set Theme"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes") as mock_theme, \
         patch("app.services.keyword_cluster_pipeline.generate_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_theme.assert_not_called()
    assert rows[0]["business_theme"] == "Already Set Theme"
    assert rows[0]["cluster"] == "Already Set Theme"
