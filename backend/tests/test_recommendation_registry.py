"""Universal SEO Audit Engine spec (2026-09-20) section 38: every
recommendation carries a stable ID/Category/Evidence/URL/Priority/Action,
and the same recommendation must never repeat across modules."""

from app.services.recommendation_registry import (
    build_keyword_strategy_recommendations, deduplicate_recommendations,
)


def _rec(category, url, action, priority=1, **extra):
    return {"category": category, "url": url, "action": action, "priority": priority, **extra}


def test_cross_module_duplicate_is_merged():
    recs = [
        _rec("Technical", "https://x.com/trucks", "optimize the existing page (https://x.com/trucks)", priority=2),
        _rec("Keyword Strategy", "https://x.com/trucks", "optimize the existing page (https://x.com/trucks)", priority=1),
    ]
    result = deduplicate_recommendations(recs)
    assert len(result) == 1
    assert result[0]["category"] == "Keyword Strategy"  # kept the higher-priority (lower number) instance
    assert result[0]["also_raised_in"] == ["Technical"]


def test_same_category_same_url_same_action_not_merged():
    # Two distinct issues from the SAME module that happen to share a
    # canonical action are not the cross-module duplicate this targets.
    recs = [
        _rec("Technical", "https://x.com/trucks", "optimize the existing page", priority=1, issue="Missing title"),
        _rec("Technical", "https://x.com/trucks", "optimize the existing page", priority=2, issue="No H1"),
    ]
    result = deduplicate_recommendations(recs)
    assert len(result) == 2


def test_no_url_or_no_canonical_action_never_merged():
    recs = [
        _rec("Technical", None, "some free-text fix with no canonical phrase"),
        _rec("Keyword Strategy", "https://x.com/trucks", "some other unrelated wording"),
    ]
    result = deduplicate_recommendations(recs)
    assert len(result) == 2


def test_different_urls_never_merged():
    recs = [
        _rec("Technical", "https://x.com/a", "optimize the existing page"),
        _rec("Keyword Strategy", "https://x.com/b", "optimize the existing page"),
    ]
    result = deduplicate_recommendations(recs)
    assert len(result) == 2


def test_every_recommendation_gets_a_stable_recommendation_id():
    recs = [_rec("Technical", "https://x.com/a", "create a new page")]
    result = deduplicate_recommendations(recs)
    assert result[0]["recommendation_id"]
    # Same inputs -> same id, deterministic.
    again = deduplicate_recommendations([_rec("Technical", "https://x.com/a", "create a new page")])
    assert again[0]["recommendation_id"] == result[0]["recommendation_id"]


def test_build_keyword_strategy_recommendations_one_per_cluster():
    rows = [
        {"cluster": "Insurance", "cluster_priority": 1, "existing_page_action": "Optimize Existing Page", "existing_page_url": "https://x.com/insurance"},
        {"cluster": "Insurance", "cluster_priority": 1, "existing_page_action": "Optimize Existing Page", "existing_page_url": "https://x.com/insurance"},
        {"cluster": "Warranty", "cluster_priority": 2, "existing_page_action": "Create New Page", "existing_page_url": None},
    ]
    recs = build_keyword_strategy_recommendations(rows)
    assert len(recs) == 2
    insurance = next(r for r in recs if "Insurance" in r["issue"])
    assert insurance["evidence"] == "2 keyword(s) tracked in this cluster"
    assert insurance["url"] == "https://x.com/insurance"
    assert "optimize the existing page" in insurance["action"]


def test_build_keyword_strategy_recommendations_skips_rows_with_no_action():
    rows = [{"cluster": "Insurance", "existing_page_action": None}]
    assert build_keyword_strategy_recommendations(rows) == []


def test_build_keyword_strategy_recommendations_empty_input():
    assert build_keyword_strategy_recommendations(None) == []
    assert build_keyword_strategy_recommendations([]) == []
