from unittest.mock import patch

from app.services.business_theme_service import UNCLASSIFIED_THEME
from app.services.keyword_cluster_pipeline import (
    _CAREER_ROUTE_CLUSTER_LABEL,
    _COMPETITOR_ROUTE_CLUSTER_LABEL,
    _GEO_ROUTE_CLUSTER_LABEL,
    _NEEDS_REVIEW_CLUSTER_LABEL,
    _final_cluster_acceptance_check,
    _strip_modifiers,
    build_final_keyword_clusters,
)

_PHASE2_PATH = "app.services.keyword_cluster_pipeline.generate_phase2_candidate_clusters"
_PHASE3_PATH = "app.services.keyword_cluster_pipeline.generate_phase3_validated_clusters"


def _rows():
    return [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "construction payroll services", "search_volume": 500, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "how to do construction payroll", "search_volume": 300, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "construction payroll process", "search_volume": 100, "intent": "Informational", "page_category": "Blog / Guide"},
    ]


def _clustered(cluster_map: dict[str, list[str]], needs_review: set[str] | None = None):
    """Builds matching (phase2_return_value, phase3_return_value) for a
    desired {cluster_name: [keywords]} outcome — phase2 emits one candidate
    cluster per name, phase3 echoes it straight back as Validated (or
    Needs Review for names listed in `needs_review`)."""
    needs_review = needs_review or set()
    candidate_clusters = {
        name: {"main_entity": None, "semantic_topic": name, "justification": "", "keywords": list(kws)}
        for name, kws in cluster_map.items()
    }
    final_clusters = [
        {
            "cluster_name": name, "primary_keyword": kws[0], "member_keywords": list(kws),
            "cluster_status": "Needs Review" if name in needs_review else "Validated",
        }
        for name, kws in cluster_map.items()
    ]
    return candidate_clusters, final_clusters


def test_empty_rows_returns_as_is():
    assert build_final_keyword_clusters([], "Acme", None, None) == []


def test_full_pipeline_assigns_cluster_and_primary_from_phase3_output():
    rows = _rows()
    phase2_return = ({}, [])  # unused directly; only phase3's echo matters here
    candidate_clusters, final_clusters = _clustered({
        "Construction Payroll Landing": [r["keyword"] for r in rows if r["page_category"] == "Landing Page"],
        "Construction Payroll Guide": [r["keyword"] for r in rows if r["page_category"] == "Blog / Guide"],
    })
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Construction Payroll" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", "a construction payroll SaaS", None)

    landing = {r["cluster"] for r in rows if r["page_category"] == "Landing Page"}
    blog = {r["cluster"] for r in rows if r["page_category"] == "Blog / Guide"}
    assert landing == {"Construction Payroll Landing"}
    assert blog == {"Construction Payroll Guide"}
    primary = next(r for r in rows if r["keyword"] == "construction payroll")
    assert primary["primary_or_secondary"] == "Primary"


def test_phase2_and_phase3_each_called_exactly_once_for_the_whole_report():
    # Regression guard (2026-09-19 live incident, still applies): clustering
    # must never fire once per bucket/topic — now it's simpler still, since
    # there's no bucket concept at all: exactly one Phase 2 call and one
    # Phase 3 call for the entire candidate pool, regardless of how many
    # distinct intents/page-categories/themes are present.
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "certified payroll vs regular", "search_volume": 600, "intent": "Commercial", "page_category": "Comparison / Alternative"},
        {"keyword": "how construction payroll works", "search_volume": 500, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    themes = {r["keyword"]: "Construction Payroll" for r in rows}
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch(_PHASE2_PATH, return_value=({}, [r["keyword"] for r in rows])) as mock_p2, \
         patch(_PHASE3_PATH, return_value=[]) as mock_p3, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    assert mock_p2.call_count == 1
    keyword_meta = mock_p2.call_args[0][0]
    assert len(keyword_meta) == 3
    mock_p3.assert_not_called()  # phase2 returned no candidate clusters, nothing to validate


def test_phase2_failure_falls_back_to_rule_based_cluster_never_ungrouped():
    # Universal SEO Keyword engine (2026-09-23): an AI failure no longer
    # leaves keywords in an "Other / Ungrouped" dump — every keyword still
    # gets its deterministic same-page group, visibly marked not AI-validated.
    rows = [{"keyword": "certified payroll compliance audit", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"certified payroll compliance audit": "Payroll Compliance"}), \
         patch(_PHASE2_PATH, return_value=({}, ["certified payroll compliance audit"])), \
         patch(_PHASE3_PATH) as mock_p3, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_p3.assert_not_called()
    assert rows[0]["cluster"] == "Certified Payroll Compliance Audit — Guides"
    assert rows[0]["cluster_source"] == "rule"
    assert rows[0]["cluster_status"] == "Rule-based (not AI-validated)"
    assert rows[0]["primary_or_secondary"] == "Primary"


def test_unclassified_theme_without_any_candidate_cluster_gets_rule_based_groups():
    rows = [
        {"keyword": "random one", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "random two", "search_volume": 40, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={}), \
         patch(_PHASE2_PATH, return_value=({}, [r["keyword"] for r in rows])), \
         patch(_PHASE3_PATH) as mock_p3, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_p3.assert_not_called()
    assert all(r["business_theme"] == UNCLASSIFIED_THEME for r in rows)
    # Different core entities ("random one" vs "random two") stay separate
    # pages — never merged just because the AI didn't run.
    assert rows[0]["cluster"] != rows[1]["cluster"]
    assert all(r["cluster_source"] == "rule" for r in rows)
    assert all(r["cluster_confidence_level"] in ("Low", "Medium") for r in rows)


def test_phase3_primary_keyword_selection_is_respected_not_recomputed():
    # Phase 3 Step 5 explicitly says primary selection is NOT automatically
    # highest volume — this pipeline must respect whatever Phase 3 chose,
    # not silently recompute its own answer downstream.
    rows = [
        {"keyword": "construction payroll software", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "construction payroll provider", "search_volume": 300, "intent": "Transactional", "page_category": "Landing Page", "current_position": 8},
    ]
    candidate_clusters, final_clusters = _clustered({
        "Construction Payroll": ["construction payroll software", "construction payroll provider"],
    })
    # Force Phase 3's pick to the LOWER-volume keyword, deliberately
    # contradicting what the old volume/ranking heuristic would choose.
    final_clusters[0]["primary_keyword"] = "construction payroll provider"
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Construction Payroll" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    primary = next(r for r in rows if r["primary_or_secondary"] == "Primary")
    assert primary["keyword"] == "construction payroll provider"


def test_existing_page_action_maps_from_match_strength():
    for strength, expected_action in [
        ("strong", "Optimize Existing Page"),
        ("partial", "Expand Existing Page"),
        ("weak", "Differentiate"),
        (None, "Create New Page"),
    ]:
        rows = [{"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"}]
        match = None if strength is None else {"url": "https://example.com/payroll", "title": "Payroll", "match_strength": strength}
        candidate_clusters, final_clusters = _clustered({"Construction Payroll": ["construction payroll"]})
        with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"construction payroll": "Construction Payroll"}), \
             patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
             patch(_PHASE3_PATH, return_value=final_clusters), \
             patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=match):
            build_final_keyword_clusters(rows, "Acme", None, None)
        assert rows[0]["existing_page_action"] == expected_action, f"strength={strength}"


def test_cannibalization_overrides_action_with_primary_and_differentiate():
    # 2026-09-20 spec section 21: the stronger/higher-volume cluster is the
    # URL's Primary owner (keeps Consolidate); the other cluster sharing
    # the same URL is told to differentiate/redirect instead of both
    # clusters independently reading "Optimize Existing Page".
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "certified payroll", "search_volume": 200, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    themes = {"construction payroll": "Construction Payroll", "certified payroll": "Certified Payroll"}
    candidate_clusters, final_clusters = _clustered({
        "Construction Payroll": ["construction payroll"], "Certified Payroll": ["certified payroll"],
    })

    def fake_match(keywords, pages, page_category=None, **_kwargs):
        return {"url": "https://example.com/payroll", "title": "Payroll", "match_strength": "strong"}

    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", side_effect=fake_match):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    primary_row = next(r for r in rows if r["cluster"] == "Construction Payroll")  # higher volume
    other_row = next(r for r in rows if r["cluster"] == "Certified Payroll")
    assert "Consolidate" in primary_row["existing_page_action"]
    assert "Primary URL" in primary_row["existing_page_action"]
    assert "Differentiate or Redirect / Merge into \"Construction Payroll\"" == other_row["existing_page_action"]


def test_cannibalization_flags_two_clusters_sharing_a_strong_existing_page_match():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
        {"keyword": "certified payroll", "search_volume": 500, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    themes = {"construction payroll": "Construction Payroll", "certified payroll": "Certified Payroll"}
    candidate_clusters, final_clusters = _clustered({
        "Construction Payroll": ["construction payroll"], "Certified Payroll": ["certified payroll"],
    })

    def fake_match(keywords, pages, page_category=None, **_kwargs):
        return {"url": "https://example.com/payroll", "title": "Payroll", "match_strength": "strong"}

    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", side_effect=fake_match):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    assert all(r["cannibalization_status"] for r in rows)
    assert rows[0]["cluster"] != rows[1]["cluster"]


def test_no_cannibalization_when_only_one_cluster_matches_a_page():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    candidate_clusters, final_clusters = _clustered({"Construction Payroll": ["construction payroll"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"construction payroll": "Construction Payroll"}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value={"url": "https://example.com/payroll", "title": "Payroll", "match_strength": "strong"}):
        build_final_keyword_clusters(rows, "Acme", None, [{"page_url": "https://example.com/payroll", "page_title": "Payroll"}])

    assert rows[0]["cannibalization_status"] is None
    assert rows[0]["existing_page_match_strength"] == "strong"


def test_catchall_cluster_name_is_rejected_and_falls_back_to_rule_name():
    # 2026-09-21 spec Phase 3 Step 3: a generic/catch-all name means "set
    # cluster_status = Needs Review instead of forcing a name" — no theme
    # fallback name exists in the literal spec, unlike the old engine's
    # bucket-theme fallback.
    rows = [
        {"keyword": "certified payroll compliance", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "certified payroll audit trail", "search_volume": 40, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    candidate_clusters, final_clusters = _clustered({
        "Overview": ["certified payroll compliance", "certified payroll audit trail"],
    })
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Certified Payroll" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    # The AI's catch-all "Overview" name never reaches a slide; each
    # keyword keeps its own evidence-based rule group instead.
    assert all(r["cluster"] and r["cluster"] != "Overview" for r in rows)
    assert all(r["cluster_source"] == "rule" for r in rows)


def test_duplicate_cluster_names_from_phase3_are_disambiguated():
    rows = [
        {"keyword": "widget pricing", "search_volume": 500, "intent": "Commercial", "page_category": "Landing Page"},
        {"keyword": "gadget pricing", "search_volume": 400, "intent": "Commercial", "page_category": "Landing Page"},
    ]
    candidate_clusters, final_clusters = _clustered({
        "duplicate-name-a": ["widget pricing"], "duplicate-name-b": ["gadget pricing"],
    })
    # Force both finalized clusters to the exact same name — a real
    # coincidence Phase 3 could produce since it has no cross-cluster
    # bucket boundary anymore.
    for f in final_clusters:
        f["cluster_name"] = "Pricing"
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Widgets" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    clusters = {r["cluster"] for r in rows}
    assert clusters == {"Pricing", "Pricing (2)"}


def test_core_category_prefers_commercial_and_ranking_over_raw_volume():
    # 2026-09-20 spec steps 18-20: a smaller commercial+already-ranking
    # cluster must outrank a bigger purely-informational one for both
    # core_category and cluster_priority — never sorted by volume alone.
    rows = [
        {"keyword": "construction payroll guide", "search_volume": 9000, "intent": "Informational", "page_category": "Blog / Guide"},
        {"keyword": "certified payroll services", "search_volume": 300, "intent": "Transactional", "page_category": "Landing Page", "current_position": 8},
    ]
    themes = {"construction payroll guide": "Construction Payroll Guides", "certified payroll services": "Certified Payroll"}
    candidate_clusters, final_clusters = _clustered({
        "Construction Payroll Guides": ["construction payroll guide"], "Certified Payroll": ["certified payroll services"],
    })
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value=themes), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
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
         patch(_PHASE2_PATH, return_value=({}, ["random one"])), \
         patch(_PHASE3_PATH, return_value=[]), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)
    assert rows[0]["core_category"] is None
    assert rows[0]["core_category_status"] == "Requires Validation"


def test_strip_modifiers_separates_core_topic_from_attribute():
    assert _strip_modifiers("product pricing") == ("product", ["pricing"])
    assert _strip_modifiers("product pricing plans") == ("product", ["pricing", "plans"])
    assert _strip_modifiers("product features") == ("product", ["features"])
    assert _strip_modifiers("product") == ("product", [])


def test_strip_modifiers_falls_back_to_full_keyword_when_all_words_are_modifiers():
    core, modifiers = _strip_modifiers("pricing")
    assert core == "pricing"
    assert modifiers == ["pricing"]


def test_semantic_topic_and_modifier_fields_computed_before_any_ai_call():
    # Universal SEO Audit Engine spec (2026-09-20) section 46's architecture
    # rule: semantic_topic/modifier must be real fields the engine already
    # has BEFORE any AI call runs, regardless of what Phase 2/3 do with them.
    rows = [{"keyword": "product pricing plans", "search_volume": 300, "intent": "Commercial", "page_category": "Product"}]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"product pricing plans": "Widget Product"}), \
         patch(_PHASE2_PATH, return_value=({}, ["product pricing plans"])), \
         patch(_PHASE3_PATH, return_value=[]), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    assert rows[0]["semantic_topic"] == "Product"
    assert "pricing" in rows[0]["modifier"] and "plans" in rows[0]["modifier"]


def test_evidence_confidence_low_for_rule_only_rows_with_no_evidence():
    rows = [
        {"keyword": "random one", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={}), \
         patch(_PHASE2_PATH, return_value=({}, ["random one"])), \
         patch(_PHASE3_PATH, return_value=[]), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)
    assert rows[0]["cluster_source"] == "rule"
    assert rows[0]["evidence_confidence"] == "Low"


def test_evidence_confidence_high_with_theme_and_ranking_signal():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional",
         "page_category": "Landing Page", "current_position": 5},
    ]
    candidate_clusters, final_clusters = _clustered({"Construction Payroll": ["construction payroll"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"construction payroll": "Construction Payroll"}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)
    assert rows[0]["evidence_confidence"] == "High"


def test_evidence_confidence_medium_with_theme_only():
    rows = [
        {"keyword": "construction payroll", "search_volume": 900, "intent": "Transactional", "page_category": "Landing Page"},
    ]
    candidate_clusters, final_clusters = _clustered({"Construction Payroll": ["construction payroll"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"construction payroll": "Construction Payroll"}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)
    assert rows[0]["evidence_confidence"] == "Medium"


def test_existing_business_theme_is_preserved_not_reclassified():
    # A real Semrush export can already carry its own theme/topic data —
    # generate_business_themes must not be called (and must not overwrite
    # it) when at least one row already has one.
    rows = [{"keyword": "kw", "search_volume": 10, "intent": "Informational", "page_category": "Blog / Guide", "business_theme": "Already Set Theme"}]
    candidate_clusters, final_clusters = _clustered({"Already Set Theme": ["kw"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes") as mock_theme, \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_theme.assert_not_called()
    assert rows[0]["business_theme"] == "Already Set Theme"
    assert rows[0]["cluster"] == "Already Set Theme"


def _valid_cluster_row(**overrides) -> dict:
    row = {
        "keyword": "kw", "cluster": "Real Topic", "cluster_status": "Validated",
        "primary_or_secondary": "Primary", "existing_page_action": "Optimize Existing Page",
        "cannibalization_status": None,
    }
    row.update(overrides)
    return row


def test_final_acceptance_check_leaves_a_fully_valid_cluster_untouched():
    rows = [_valid_cluster_row()]
    _final_cluster_acceptance_check(rows)
    assert rows[0]["cluster"] == "Real Topic"
    assert rows[0]["cluster_status"] == "Validated"


def test_final_acceptance_check_demotes_cluster_with_no_primary_keyword():
    # Spec section 44 — a real pipeline defect (e.g. Phase 3 never assigned
    # a primary for this cluster) must never silently reach the renderer.
    rows = [_valid_cluster_row(primary_or_secondary="Secondary")]
    _final_cluster_acceptance_check(rows)
    assert rows[0]["cluster"] == ""
    assert "no primary keyword" in rows[0]["cluster_status"]
    assert rows[0]["primary_or_secondary"] is None


def test_final_acceptance_check_demotes_cluster_missing_existing_page_evaluation():
    rows = [_valid_cluster_row(existing_page_action="")]
    _final_cluster_acceptance_check(rows)
    assert rows[0]["cluster"] == ""
    assert "existing-page match never evaluated" in rows[0]["cluster_status"]


def test_final_acceptance_check_demotes_catchall_name_defensively():
    rows = [_valid_cluster_row(cluster="Miscellaneous")]
    _final_cluster_acceptance_check(rows)
    assert rows[0]["cluster"] == ""
    assert "catch-all" in rows[0]["cluster_status"]


def test_priority_score_attached_to_every_row_without_replacing_cluster_priority():
    rows = [
        {"keyword": "widget insurance", "search_volume": 500, "intent": "Commercial", "page_category": "Landing Page"},
    ]
    candidate_clusters, final_clusters = _clustered({"Insurance": ["widget insurance"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"widget insurance": "Insurance"}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    row = rows[0]
    assert 0 <= row["priority_score"] <= 100
    assert "business_relevance" in row["priority_factors"]
    assert "cluster_priority" in row  # existing field, untouched by the additive priority_score


def test_final_acceptance_check_strips_disambiguation_suffix_before_judging_name():
    # "Pricing (2)" is a disambiguation suffix from duplicate-name handling,
    # not itself a catch-all name — only its bare "Pricing" part (not a
    # catch-all word) should be judged.
    rows = [_valid_cluster_row(cluster="Pricing (2)")]
    _final_cluster_acceptance_check(rows)
    assert rows[0]["cluster"] == "Pricing (2)"


def test_ambiguous_and_competitor_rows_never_blend_into_a_business_cluster():
    # Reproduces the real BharatBenz bug: an AI-fail-open "Unknown / Needs
    # Review" row (e.g. "jaguar land rover new") and a kept
    # competitor-comparison row must each land in their own fixed bucket,
    # never inside a normal-looking, AI-named business-theme cluster
    # alongside genuinely confident rows.
    rows = [
        {"keyword": "heavy truck dealer near me", "search_volume": 900, "intent": "Commercial", "page_category": "Landing Page"},
        {"keyword": "heavy truck financing options", "search_volume": 400, "intent": "Commercial", "page_category": "Landing Page"},
        {
            "keyword": "jaguar land rover new", "search_volume": 300, "intent": "Informational", "page_category": "Blog / Guide",
            "relevance_status": "Unknown / Needs Review", "relevance_reason": "AI classification unavailable — needs manual review.",
        },
        {
            "keyword": "tatamotors vs bharatbenz trucks", "search_volume": 200, "intent": "Commercial", "page_category": "Landing Page",
            "competitor_status": "Competitor Comparison Opportunity",
        },
    ]
    candidate_clusters, final_clusters = _clustered({
        "Commercial Trucks": ["heavy truck dealer near me", "heavy truck financing options"],
    })
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Commercial Trucks" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "BharatBenz", "a commercial truck manufacturer", None)

    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["jaguar land rover new"]["cluster"] == _NEEDS_REVIEW_CLUSTER_LABEL
    assert by_kw["tatamotors vs bharatbenz trucks"]["cluster"] == _COMPETITOR_ROUTE_CLUSTER_LABEL
    assert by_kw["heavy truck dealer near me"]["cluster"] == "Commercial Trucks"
    assert by_kw["heavy truck financing options"]["cluster"] == "Commercial Trucks"


def test_row_outside_classified_candidate_pool_still_clusters_normally():
    # "Unknown / Needs Review" with the pool-exclusion reason means the
    # classifier never actually judged this row at all (it was simply
    # outside the top-N-by-volume candidate cap) — a deliberate "leave as
    # is" case, not real ambiguity evidence, so it must NOT be routed away
    # from normal clustering.
    rows = [
        {
            "keyword": "heavy truck dealer near me", "search_volume": 900, "intent": "Commercial", "page_category": "Landing Page",
            "relevance_status": "Unknown / Needs Review", "relevance_reason": "Keyword outside the classified candidate pool.",
        },
    ]
    candidate_clusters, final_clusters = _clustered({"Commercial Trucks": ["heavy truck dealer near me"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={"heavy truck dealer near me": "Commercial Trucks"}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "BharatBenz", "a commercial truck manufacturer", None)

    assert rows[0]["cluster"] not in (_NEEDS_REVIEW_CLUSTER_LABEL, _COMPETITOR_ROUTE_CLUSTER_LABEL)
    assert rows[0]["cluster"] == "Commercial Trucks"


def test_career_status_row_routes_to_jobs_careers_cluster():
    rows = [
        {"keyword": "heavy truck dealer near me", "search_volume": 900, "intent": "Commercial", "page_category": "Landing Page"},
        {
            "keyword": "bharatbenz careers", "search_volume": 150, "intent": "Navigational", "page_category": "Landing Page",
            "relevance_status": "Career / Recruitment Query",
        },
    ]
    candidate_clusters, final_clusters = _clustered({"Commercial Trucks": ["heavy truck dealer near me"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Commercial Trucks" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "BharatBenz", "a commercial truck manufacturer", None)

    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["bharatbenz careers"]["cluster"] == _CAREER_ROUTE_CLUSTER_LABEL
    assert by_kw["heavy truck dealer near me"]["cluster"] != _CAREER_ROUTE_CLUSTER_LABEL


def test_geographic_mismatch_row_routes_to_out_of_market_cluster():
    rows = [
        {"keyword": "heavy truck dealer near me", "search_volume": 900, "intent": "Commercial", "page_category": "Landing Page"},
        {
            "keyword": "heavy truck dealer usa", "search_volume": 150, "intent": "Commercial", "page_category": "Landing Page",
            "geo_status": "Geographic Mismatch",
        },
    ]
    candidate_clusters, final_clusters = _clustered({"Commercial Trucks": ["heavy truck dealer near me"]})
    with patch("app.services.keyword_cluster_pipeline.generate_business_themes", return_value={r["keyword"]: "Commercial Trucks" for r in rows}), \
         patch(_PHASE2_PATH, return_value=(candidate_clusters, [])), \
         patch(_PHASE3_PATH, return_value=final_clusters), \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "BharatBenz", "a commercial truck manufacturer", None)

    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["heavy truck dealer usa"]["cluster"] == _GEO_ROUTE_CLUSTER_LABEL
    assert by_kw["heavy truck dealer near me"]["cluster"] != _GEO_ROUTE_CLUSTER_LABEL


def test_native_semrush_cluster_column_still_gets_full_enrichment():
    # Regression (confirmed real, Geopits report, 2026-09-24): a Semrush
    # export that already carries a Cluster/Topic column used to skip this
    # whole function at the site_audit.py call site — every row kept its
    # native cluster label, but with no cluster_confidence, no primary-
    # keyword scoring, and no existing-page matching, because the guard
    # predated everything this pipeline does beyond clustering itself.
    # Semrush's own grouping should still be trusted (no AI re-clustering,
    # same as before), but every enrichment step below it must still run.
    rows = [
        {"keyword": "database support services", "cluster": "Database Support Services",
         "search_volume": 260, "intent": "Commercial Investigation", "page_category": "Service Page"},
        {"keyword": "remote database support", "cluster": "Database Support Services",
         "search_volume": 140, "intent": "Commercial", "page_category": "Service Page"},
    ]
    with patch(_PHASE2_PATH) as mock_p2, \
         patch(_PHASE3_PATH) as mock_p3, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    # No AI re-clustering — Semrush's own grouping is kept as-is.
    mock_p2.assert_not_called()
    mock_p3.assert_not_called()
    assert {r["cluster"] for r in rows} == {"Database Support Services"}
    # But every downstream enrichment step still ran, unlike before the fix.
    assert all(r.get("cluster_confidence") is not None for r in rows)
    assert all(r.get("primary_or_secondary") for r in rows)
    assert all(r.get("detected_intent") for r in rows)


def test_native_semrush_cluster_routes_junk_and_competitor_rows_like_any_report():
    rows = [
        {"keyword": "database support services", "cluster": "Database Support Services",
         "search_volume": 260, "intent": "Commercial", "page_category": "Service Page"},
        {"keyword": "job openings dba", "cluster": "Database Support Services", "search_volume": 40,
         "intent": "Informational", "page_category": "Blog / Guide", "relevance_status": "Career / Recruitment Query"},
    ]
    with patch(_PHASE2_PATH) as mock_p2, patch(_PHASE3_PATH) as mock_p3, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None)

    mock_p2.assert_not_called()
    mock_p3.assert_not_called()
    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["database support services"]["cluster"] == "Database Support Services"
    assert by_kw["job openings dba"]["cluster"] == _CAREER_ROUTE_CLUSTER_LABEL


def test_manual_cluster_map_is_used_and_ai_pipeline_never_runs():
    # User's explicit instruction (2026-09-21): manual clustering is first
    # preference — when a manual_cluster_map is supplied, the AI Phase 2/3
    # pipeline must never be called at all, not even as a second opinion.
    rows = [
        {"keyword": "6x4 truck", "search_volume": 900, "intent": "Commercial", "page_category": "Landing Page"},
        {"keyword": "6x4 truck price", "search_volume": 400, "intent": "Commercial", "page_category": "Landing Page"},
        {"keyword": "unlisted keyword", "search_volume": 50, "intent": "Informational", "page_category": "Blog / Guide"},
    ]
    manual_cluster_map = {
        "6x4 truck": {"cluster": "6x4 Truck Configuration", "primary_or_secondary": "Primary"},
        "6x4 truck price": {"cluster": "6x4 Truck Configuration", "primary_or_secondary": "Secondary"},
    }
    with patch(_PHASE2_PATH) as mock_p2, \
         patch(_PHASE3_PATH) as mock_p3, \
         patch("app.services.keyword_cluster_pipeline.generate_business_themes") as mock_theme, \
         patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None, manual_cluster_map=manual_cluster_map)

    mock_p2.assert_not_called()
    mock_p3.assert_not_called()
    mock_theme.assert_not_called()
    by_kw = {r["keyword"]: r for r in rows}
    assert by_kw["6x4 truck"]["cluster"] == "6x4 Truck Configuration"
    assert by_kw["6x4 truck"]["primary_or_secondary"] == "Primary"
    assert by_kw["6x4 truck price"]["primary_or_secondary"] == "Secondary"
    assert by_kw["unlisted keyword"]["cluster"] == ""  # not covered by the manual file — left unclustered, never guessed


def test_manual_cluster_map_respects_final_acceptance_check_like_any_other_cluster():
    rows = [{"keyword": "6x4 truck", "cluster": "", "search_volume": 900}]
    manual_cluster_map = {"6x4 truck": {"cluster": "6x4 Truck Configuration", "primary_or_secondary": "Primary"}}
    with patch("app.services.keyword_cluster_pipeline.match_existing_page_for_cluster", return_value=None):
        build_final_keyword_clusters(rows, "Acme", None, None, manual_cluster_map=manual_cluster_map)
    assert rows[0]["cluster"] == "6x4 Truck Configuration"
    assert rows[0]["cluster_status"] == "Validated (Manual)"
    assert rows[0]["primary_or_secondary"] == "Primary"
