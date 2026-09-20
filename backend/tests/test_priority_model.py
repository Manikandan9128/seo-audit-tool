"""Universal SEO Audit Engine spec (2026-09-20) section 40: priority must
combine multiple factors, never search volume/demand alone."""

from app.services.priority_model import compute_priority_score, evidence_confidence_to_score


def test_score_is_zero_with_no_evidence_at_all():
    result = compute_priority_score()
    assert result["score"] < 10  # only the effort=0.5-default weight contributes anything


def test_high_demand_alone_never_dominates_the_score():
    # Search demand's weight is capped — a maxed-out demand signal with
    # every other factor at zero must never produce a near-max score.
    result = compute_priority_score(search_demand=1.0)
    assert result["score"] < 20


def test_full_evidence_produces_high_score():
    result = compute_priority_score(
        business_relevance=1.0, search_demand=1.0, current_visibility=1.0, ranking_opportunity=1.0,
        intent_strength=1.0, commercial_value=1.0, conversion_potential=1.0, technical_severity=1.0,
        effort=0.0, evidence_confidence=1.0,
    )
    assert result["score"] == 100.0


def test_higher_effort_lowers_score_all_else_equal():
    low_effort = compute_priority_score(business_relevance=1.0, effort=0.0)
    high_effort = compute_priority_score(business_relevance=1.0, effort=1.0)
    assert low_effort["score"] > high_effort["score"]


def test_factors_breakdown_always_returned():
    result = compute_priority_score(business_relevance=0.7)
    assert result["factors"]["business_relevance"] == 0.7
    assert "effort" in result["factors"]


def test_out_of_range_inputs_are_clamped():
    result = compute_priority_score(business_relevance=5.0, search_demand=-3.0)
    assert result["factors"]["business_relevance"] == 1.0
    assert result["factors"]["search_demand"] == 0.0


def test_evidence_confidence_to_score_mapping():
    assert evidence_confidence_to_score("High") == 1.0
    assert evidence_confidence_to_score("Medium") == 0.5
    assert evidence_confidence_to_score("Low") == 0.0
    assert evidence_confidence_to_score(None) == 0.0
    assert evidence_confidence_to_score("garbage") == 0.0
