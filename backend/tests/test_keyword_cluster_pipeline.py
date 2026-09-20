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
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", "a construction payroll SaaS", None)

    landing = {r["cluster"] for r in rows if r["page_category"] == "Landing Page"}
    blog = {r["cluster"] for r in rows if r["page_category"] == "Blog / Guide"}
    assert landing.isdisjoint(blog)
    assert len(landing) == 1 and len(blog) == 1


def test_single_keyword_bucket_gets_theme_labeled_cluster_without_ai_call():
    rows = [{"keyword": "certified payroll compliance audit", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"certified payroll compliance audit": "Payroll Compliance"}), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters") as mock_candidate, \
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
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
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
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
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
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", side_effect=fake_match):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    assert all(r["cannibalization_status"] for r in rows)
    assert rows[0]["cluster"] != rows[1]["cluster"]


def test_no_cannibalization_when_only_one_cluster_matches_a_page():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"construction payroll": "Construction Payroll"}), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value={"url": "https://example.com/payroll", "title": "Payroll", "match_strength": "strong"}):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    assert rows[0]["cannibalization_status"] is None
    assert rows[0]["existing_page_match_strength"] == "strong"


def test_candidate_clustering_is_one_batched_call_not_one_per_bucket():
    # Regression guard (2026-09-19 live incident): a client with many
    # distinct (theme, intent, category) buckets used to fire one
    # sequential AI call per bucket, chaining past Groq's shared rate
    # limit and stalling report generation past the 15-minute timeout.
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "construction payroll services", "search_volume": 800, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "certified payroll", "search_volume": 700, "intent": "Commercial", "page_category": "Comparison / Alternative"},
        {"keyword": "certified payroll vs regular", "search_volume": 600, "intent": "Commercial", "page_category": "Comparison / Alternative"},
        {"keyword": "how construction payroll works", "search_volume": 500, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "construction payroll process explained", "search_volume": 400, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    themes = {r["keyword"]: "Construction Payroll" for r in rows}
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}) as mock_candidate, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    # Three distinct buckets (Landing/Transactional, Comparison/Commercial,
    # Blog/Informational) — must still be exactly one call, not three.
    assert mock_candidate.call_count == 1
    groups = mock_candidate.call_args[0][0]
    assert len(groups) == 3
    assert sum(len(kws) for _label, kws in groups) == 6


def test_catchall_cluster_name_is_rejected_and_left_unclustered():
    # 2026-09-20 spec: a generic/catch-all AI-returned cluster name (e.g.
    # "Overview") must never render — the row falls back to unclustered
    # with cluster_status="Unvalidated" rather than a fake theme-only
    # cluster too, since the AI DID return something (just an invalid
    # name), so this exercises the reject-then-fall-back-to-theme path
    # separately from "AI returned nothing at all."
    rows = [
        {"keyword": "certified payroll basics", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "certified payroll fundamentals", "search_volume": 40, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Certified Payroll" for r in rows}), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={r["keyword"]: "Overview" for r in rows}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    # Rejected AI name falls back to the real theme label, not "Overview".
    assert all(r["cluster"] == "Certified Payroll" for r in rows)
    assert all(r["cluster_status"] == "Validated" for r in rows)


def test_catchall_cluster_name_with_no_theme_fallback_stays_unclustered():
    rows = [{"keyword": "random one", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)
    assert rows[0]["cluster"] == ""
    assert rows[0]["cluster_status"] == "Unvalidated"


def test_core_category_prefers_commercial_and_ranking_over_raw_volume():
    # 2026-09-20 spec steps 18-20: a smaller commercial+already-ranking
    # cluster must outrank a bigger purely-informational one for both
    # core_category and cluster_priority — never sorted by volume alone.
    rows = [
        {"keyword": "construction payroll guide", "search_volume": 9000, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "certified payroll services", "search_volume": 300, "intent": "Transactional", "page_category": "Landing Page", "current_position": 8},
    ]
    themes = {"construction payroll guide": "Construction Payroll Guides", "certified payroll services": "Certified Payroll"}
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    commercial_row = next(r for r in rows if r["cluster"] == "Certified Payroll")
    volume_row = next(r for r in rows if r["cluster"] == "Construction Payroll Guides")
    assert commercial_row["core_category"] == "Certified Payroll"
    assert commercial_row["cluster_priority"] == 1
    assert volume_row["cluster_priority"] == 2


def test_core_category_requires_validation_when_every_theme_unclassified():
    rows = [{"keyword": "random one", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)
    assert rows[0]["core_category"] is None
    assert rows[0]["core_category_status"] == "Requires Validation"


def test_existing_business_theme_is_preserved_not_reclassified():
    # A real Semrush export can already carry its own theme/topic data —
    # generate_business_themes must not be called (and must not overwrite
    # it) when at least one row already has one.
    rows = [{"keyword": "kw", "search_volume": 10, "intent": "Informational", "page_category": "Blog / Guide", "business_theme": "Already Set Theme"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes") as mock_theme, \
         patch("app.services.keyword_cluster_pipeline.generate_batched_candidate_clusters", return_value={}), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_theme.assert_not_called()
    assert rows[0]["business_theme"] == "Already Set Theme"
    assert rows[0]["cluster"] == "Already Set Theme"
