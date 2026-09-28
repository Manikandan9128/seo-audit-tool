import json
from unittest.mock import patch

from app.integrations.text_ai_client import NoAIProviderConfigured
from app.services.structured_data_insights_service import _deterministic_schema_impact, generate_schema_implementation_impact

_PART1 = [{"page_type": "Blog / Article", "recommended_schema": "Article, BreadcrumbList", "pages": 13, "why_it_applies": "x"}]


def _attempts(*items):
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def test_no_schema_data_short_circuits_without_calling_ai():
    with patch("app.services.structured_data_insights_service.iter_text_attempts") as mock_iter:
        result = generate_schema_implementation_impact([], [])
    mock_iter.assert_not_called()
    assert result["error"] == "No schema data to analyze"


def test_ai_response_used_when_present():
    part2 = [{"schema_type": "Article", "applicable": 13, "present": 0, "valid": 0, "invalid": 0, "missing": 13, "coverage_pct": 0, "site_level": False}]
    raw = json.dumps({"impact": [{"label": "Editorial Content", "text": "Clarifies the site's editorial content and its relationship to the topics covered."}]})
    with patch("app.services.structured_data_insights_service.iter_text_attempts", side_effect=_attempts((raw, "groq"))):
        result = generate_schema_implementation_impact(_PART1, part2)
    assert "error" not in result
    assert result["impact"] == [{"label": "Editorial Content", "text": "Clarifies the site's editorial content and its relationship to the topics covered."}]


def test_falls_back_to_deterministic_when_every_provider_empty():
    part2 = [{"schema_type": "Article", "applicable": 13, "present": 0, "valid": 0, "invalid": 0, "missing": 13, "coverage_pct": 0, "site_level": False}]
    empty = json.dumps({"impact": []})
    with patch("app.services.structured_data_insights_service.iter_text_attempts", side_effect=_attempts((empty, "groq"), (empty, "gemini"))):
        result = generate_schema_implementation_impact(_PART1, part2)
    assert "error" not in result
    assert result.get("fallback") is True
    assert result["impact"][0]["label"] == "Editorial Content"


def test_no_provider_configured_still_falls_back():
    def _gen(prompt, max_tokens, errors):
        raise NoAIProviderConfigured("no keys")
        yield  # pragma: no cover
    part2 = [{"schema_type": "Product", "applicable": 10, "present": 2, "valid": 1, "invalid": 1, "missing": 8, "coverage_pct": 10, "site_level": False}]
    with patch("app.services.structured_data_insights_service.iter_text_attempts", side_effect=_gen):
        result = generate_schema_implementation_impact(_PART1, part2)
    assert "error" not in result
    assert result.get("fallback") is True


def test_error_when_ai_empty_and_no_real_gap_or_win_exists():
    # applicable=0 everywhere -> not a real gap, not a meaningful win either.
    part2 = [{"schema_type": "Article", "applicable": 0, "present": 0, "valid": 0, "invalid": 0, "missing": 0, "coverage_pct": 0, "site_level": False}]
    empty = json.dumps({"impact": []})
    with patch("app.services.structured_data_insights_service.iter_text_attempts", side_effect=_attempts((empty, "groq"))):
        result = generate_schema_implementation_impact(_PART1, part2)
    assert "error" in result


def test_deterministic_impact_only_covers_types_actually_present():
    # Dynamic per spec section 15/27 -- JobPosting must never appear when
    # there's no JobPosting row in part2, regardless of what other sites'
    # reports might show.
    part2 = [{"schema_type": "Product", "applicable": 10, "present": 0, "valid": 0, "invalid": 0, "missing": 10, "coverage_pct": 0, "site_level": False}]
    impact = _deterministic_schema_impact(part2)
    assert len(impact) == 1
    assert impact[0]["label"] == "Product Pages"
    assert "JobPosting" not in json.dumps(impact)
    assert "Job Listings" not in json.dumps(impact)


def test_deterministic_impact_has_no_numbers_or_directive_verbs():
    part2 = [
        {"schema_type": "Article", "applicable": 13, "present": 0, "valid": 0, "invalid": 0, "missing": 13, "coverage_pct": 0, "site_level": False},
        {"schema_type": "JobPosting", "applicable": 3, "present": 1, "valid": 0, "invalid": 1, "missing": 2, "coverage_pct": 0, "site_level": False},
        {"schema_type": "WebSite", "applicable": "Site-level", "present": "No", "valid": "—", "invalid": "—", "missing": "—", "coverage_pct": None, "site_level": True},
    ]
    impact = _deterministic_schema_impact(part2)
    assert len(impact) == 3
    banned_verbs = {"add", "implement", "create", "fix", "deploy", "update", "optimize", "configure"}
    for item in impact:
        words = {w.strip(".,").lower() for w in item["text"].split()}
        assert not words & banned_verbs, item
        assert not any(ch.isdigit() for ch in item["text"]), item


def test_deterministic_impact_never_pads_and_caps_at_four():
    part2 = [
        {"schema_type": t, "applicable": 5, "present": 0, "valid": 0, "invalid": 0, "missing": 5, "coverage_pct": 0, "site_level": False}
        for t in ["Article", "Product", "JobPosting", "LocalBusiness", "Event", "FAQPage"]
    ]
    impact = _deterministic_schema_impact(part2)
    assert len(impact) == 4  # capped, never all 6


def test_deterministic_impact_valid_schema_not_framed_as_gap():
    # A confirmed win only gets surfaced when there are fewer than 2 real
    # gaps -- never described as something to fix.
    part2 = [{"schema_type": "FAQPage", "applicable": 5, "present": 5, "valid": 5, "invalid": 0, "missing": 0, "coverage_pct": 100, "site_level": False}]
    impact = _deterministic_schema_impact(part2)
    assert len(impact) == 1
    assert impact[0]["label"] == "FAQ Content"


def test_deterministic_impact_win_not_shown_when_two_real_gaps_exist():
    part2 = [
        {"schema_type": "Article", "applicable": 5, "present": 0, "valid": 0, "invalid": 0, "missing": 5, "coverage_pct": 0, "site_level": False},
        {"schema_type": "Product", "applicable": 5, "present": 0, "valid": 0, "invalid": 0, "missing": 5, "coverage_pct": 0, "site_level": False},
        {"schema_type": "FAQPage", "applicable": 5, "present": 5, "valid": 5, "invalid": 0, "missing": 0, "coverage_pct": 100, "site_level": False},
    ]
    impact = _deterministic_schema_impact(part2)
    labels = [i["label"] for i in impact]
    assert "FAQ Content" not in labels
    assert set(labels) == {"Editorial Content", "Product Pages"}


def test_deterministic_impact_empty_when_nothing_meaningful():
    part2 = [{"schema_type": "Article", "applicable": 0, "present": 0, "valid": 0, "invalid": 0, "missing": 0, "coverage_pct": 0, "site_level": False}]
    assert _deterministic_schema_impact(part2) == []
