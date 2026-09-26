import json
from unittest.mock import patch

from app.services.keyword_semantic_cluster_service import (
    generate_phase2_candidate_clusters,
    generate_phase3_validated_clusters,
)

_MOD = "app.services.keyword_semantic_cluster_service"


def _attempts(*items):
    """items: list of (raw_text, provider) tuples to yield in order."""
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def _keyword_meta(keywords):
    return [{"keyword": kw, "search_volume": 100, "keyword_difficulty": 20, "source_cluster": None} for kw in keywords]


def test_phase2_empty_input_returns_empty():
    assert generate_phase2_candidate_clusters([], None) == ({}, [])


def test_phase2_parses_valid_response_and_keeps_only_real_keywords():
    response = json.dumps({
        "clusters": {
            "c1": {"main_entity": "truck", "semantic_topic": "6x4 truck", "justification": "same config", "keywords": ["6x4 truck", "6x4 truck price"]},
        },
        "unmapped": ["random noise"],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        clusters, unmapped = generate_phase2_candidate_clusters(
            _keyword_meta(["6x4 truck", "6x4 truck price", "random noise"]), "a truck manufacturer",
        )
    assert clusters["c1"]["keywords"] == ["6x4 truck", "6x4 truck price"]
    assert unmapped == ["random noise"]


def test_phase2_never_trusts_a_hallucinated_keyword():
    response = json.dumps({
        "clusters": {"c1": {"main_entity": None, "semantic_topic": "t", "justification": "", "keywords": ["6x4 truck", "invented keyword"]}},
        "unmapped": [],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        clusters, unmapped = generate_phase2_candidate_clusters(_keyword_meta(["6x4 truck"]), None)
    assert clusters["c1"]["keywords"] == ["6x4 truck"]
    assert "invented keyword" not in clusters["c1"]["keywords"]


def test_phase2_keyword_dropped_by_ai_is_reported_as_unmapped_not_silently_lost():
    response = json.dumps({"clusters": {}, "unmapped": []})
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        clusters, unmapped = generate_phase2_candidate_clusters(_keyword_meta(["orphan keyword"]), None)
    assert clusters == {}
    assert unmapped == ["orphan keyword"]


def test_phase2_fails_open_on_malformed_json_after_retry():
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts(("not json at all", "groq"))):
        clusters, unmapped = generate_phase2_candidate_clusters(_keyword_meta(["6x4 truck"]), None)
    assert clusters == {}
    assert unmapped == ["6x4 truck"]


def test_phase2_first_provider_invalid_json_falls_through_to_next():
    # 2026-09-22: OpenRouter's auto-router can land on a model that
    # doesn't reliably follow "return ONLY valid JSON" — a bad response
    # from one provider must not kill the section outright.
    response = json.dumps({
        "clusters": {
            "c1": {"main_entity": "truck", "semantic_topic": "6x4 truck", "justification": "same config", "keywords": ["6x4 truck"]},
        },
        "unmapped": [],
    })
    with patch(
        f"{_MOD}.iter_text_attempts",
        side_effect=_attempts(("not json at all", "openrouter"), (response, "gemini")),
    ):
        clusters, unmapped = generate_phase2_candidate_clusters(_keyword_meta(["6x4 truck"]), None)
    assert clusters["c1"]["keywords"] == ["6x4 truck"]


def test_phase3_empty_candidate_clusters_returns_empty():
    assert generate_phase3_validated_clusters({}) == []


def test_phase3_parses_valid_response():
    candidate_clusters = {"c1": {"keywords": [{"keyword": "6x4 truck", "search_volume": 100}, {"keyword": "6x4 truck price", "search_volume": 50}]}}
    response = json.dumps({
        "final_clusters": [
            {"cluster_name": "6x4 Truck", "primary_keyword": "6x4 truck", "member_keywords": ["6x4 truck", "6x4 truck price"],
             "cluster_status": "Validated", "cluster_purity": 0.9, "conflict_signals": [], "page_purpose": "product"},
        ],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        result = generate_phase3_validated_clusters(candidate_clusters)
    assert result == [{
        "cluster_name": "6x4 Truck", "primary_keyword": "6x4 truck", "member_keywords": ["6x4 truck", "6x4 truck price"],
        "cluster_status": "Validated", "cluster_purity": 0.9, "conflict_signals": [], "page_purpose": "product",
    }]


def test_phase3_cluster_purity_is_clamped_and_defaults_to_pure_when_missing():
    # A missing/malformed cluster_purity must never silently read as "the
    # AI flagged a conflict" — default is 1.0 (pure), never 0.0.
    candidate_clusters = {"c1": {"keywords": [{"keyword": "6x4 truck", "search_volume": 100}]}}
    response = json.dumps({
        "final_clusters": [
            {"cluster_name": "6x4 Truck", "primary_keyword": "6x4 truck", "member_keywords": ["6x4 truck"],
             "cluster_status": "Validated", "cluster_purity": 4.2},
        ],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        result = generate_phase3_validated_clusters(candidate_clusters)
    assert result[0]["cluster_purity"] == 1.0

    response2 = json.dumps({
        "final_clusters": [
            {"cluster_name": "6x4 Truck", "primary_keyword": "6x4 truck", "member_keywords": ["6x4 truck"], "cluster_status": "Validated"},
        ],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response2, "groq"))):
        result2 = generate_phase3_validated_clusters(candidate_clusters)
    assert result2[0]["cluster_purity"] == 1.0
    assert result2[0]["conflict_signals"] == []
    assert result2[0]["page_purpose"] is None


def test_phase3_low_purity_cluster_carries_its_conflict_signals():
    candidate_clusters = {"c1": {"keywords": [
        {"keyword": "database support", "search_volume": 100},
        {"keyword": "oracle dba support", "search_volume": 80},
    ]}}
    response = json.dumps({
        "final_clusters": [
            {"cluster_name": "Database Support", "primary_keyword": "database support",
             "member_keywords": ["database support", "oracle dba support"], "cluster_status": "Validated",
             "cluster_purity": 0.4, "conflict_signals": ["entity conflict: generic database vs Oracle-specific"],
             "page_purpose": "service"},
        ],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        result = generate_phase3_validated_clusters(candidate_clusters)
    assert result[0]["cluster_purity"] == 0.4
    assert "entity conflict" in result[0]["conflict_signals"][0]
    assert result[0]["page_purpose"] == "service"


def test_phase3_never_trusts_a_hallucinated_keyword():
    candidate_clusters = {"c1": {"keywords": [{"keyword": "6x4 truck", "search_volume": 100}]}}
    response = json.dumps({
        "final_clusters": [
            {"cluster_name": "6x4 Truck", "primary_keyword": "6x4 truck", "member_keywords": ["6x4 truck", "invented keyword"], "cluster_status": "Validated"},
        ],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        result = generate_phase3_validated_clusters(candidate_clusters)
    assert result[0]["member_keywords"] == ["6x4 truck"]


def test_phase3_keyword_dropped_by_ai_becomes_its_own_needs_review_singleton():
    candidate_clusters = {"c1": {"keywords": [{"keyword": "6x4 truck", "search_volume": 100}, {"keyword": "6x4 truck price", "search_volume": 50}]}}
    response = json.dumps({
        "final_clusters": [
            {"cluster_name": "6x4 Truck", "primary_keyword": "6x4 truck", "member_keywords": ["6x4 truck"], "cluster_status": "Validated"},
        ],
    })
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts((response, "groq"))):
        result = generate_phase3_validated_clusters(candidate_clusters)
    dropped = next(c for c in result if c["member_keywords"] == ["6x4 truck price"])
    assert dropped["cluster_status"] == "Needs Review"


def test_phase3_fails_open_on_malformed_json_after_retry():
    candidate_clusters = {"c1": {"keywords": [{"keyword": "6x4 truck", "search_volume": 100}]}}
    with patch(f"{_MOD}.iter_text_attempts", side_effect=_attempts(("not json", "groq"))):
        result = generate_phase3_validated_clusters(candidate_clusters)
    assert result == []


def test_phase3_prompt_states_final_authority_override_confidence_and_serp_guardrails():
    # 2026-09-26: the user pasted an external "Prompt 3" spec naming this
    # module's Phase 3 call as its intended target — comparing it against
    # the existing _PHASE3_PROMPT_TEMPLATE found 4 real gaps (no explicit
    # override-rule framing, no confidence-rule tied to unresolved
    # conflicts, no SERP-invention guardrail, no "don't keep merely
    # because of X" list). User chose the minimal fix: strengthen the
    # prompt text only, no output-schema change. This pins that the
    # strengthened language actually ships in the real prompt sent to
    # every provider, not just in a comment.
    from app.services.keyword_semantic_cluster_service import _PHASE3_PROMPT_TEMPLATE

    assert "FINAL AUTHORITY" in _PHASE3_PROMPT_TEMPLATE
    assert "OVERRIDE RULE" in _PHASE3_PROMPT_TEMPLATE
    assert "CONFIDENCE RULE" in _PHASE3_PROMPT_TEMPLATE
    assert "SERP LIMITATION" in _PHASE3_PROMPT_TEMPLATE
    assert "semantic similarity" in _PHASE3_PROMPT_TEMPLATE
    assert "previous cluster assignment" in _PHASE3_PROMPT_TEMPLATE
