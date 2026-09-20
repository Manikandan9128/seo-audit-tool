import json
from unittest.mock import patch

from app.integrations.text_ai_client import NoAIProviderConfigured
from app.services.structured_data_insights_service import _deterministic_schema_insights, generate_structured_data_insights

_PART1 = [{"page_type": "Blog / Article", "recommended_schema": "Article", "pages": 13, "why_it_applies": "x"}]
_PART2 = [{"schema_type": "Article", "applicable": 13, "present": 0, "valid": 0, "invalid": 0, "missing": 13, "coverage_pct": 0}]


def _attempts(*items):
    """items: list of (raw_text, provider) tuples to yield in order."""
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def test_retries_next_provider_when_first_returns_empty_insights():
    # 2026-09-20 regression: Groq answering with a syntactically valid but
    # semantically empty {"insights": []} must not be treated as final —
    # Gemini (or whichever provider is next) should get a real try.
    groq_empty = json.dumps({"insights": []})
    gemini_real = json.dumps({"insights": ["Article schema gap — 13 applicable pages have no Article structured data detected. Fix: implement it."]})
    with patch(
        "app.services.structured_data_insights_service.iter_text_attempts",
        side_effect=_attempts((groq_empty, "groq"), (gemini_real, "gemini")),
    ):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    assert "error" not in result
    assert len(result["insights"]) == 1
    assert "Article schema gap" in result["insights"][0]


def test_falls_back_to_deterministic_insights_when_every_provider_returns_empty():
    # 2026-09-20 spec section 30: Key Insights must never fully disappear.
    # _PART2 has a real Missing gap, so the deterministic pass must fill
    # in when every AI provider comes back semantically empty.
    groq_empty = json.dumps({"insights": []})
    gemini_empty = json.dumps({"insights": []})
    with patch(
        "app.services.structured_data_insights_service.iter_text_attempts",
        side_effect=_attempts((groq_empty, "groq"), (gemini_empty, "gemini")),
    ):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    assert "error" not in result
    assert result.get("fallback") is True
    assert any("Article schema gap" in i for i in result["insights"])


def test_error_when_ai_empty_and_deterministic_pass_also_finds_nothing():
    # Every part2 row Not Applicable (applicable=0) — genuinely nothing
    # meaningful to say, the one case where an error is still correct.
    part2_nothing = [{"schema_type": "Article", "applicable": 0, "present": 0, "valid": 0, "invalid": 0, "missing": 0, "coverage_pct": 0}]
    groq_empty = json.dumps({"insights": []})
    with patch(
        "app.services.structured_data_insights_service.iter_text_attempts",
        side_effect=_attempts((groq_empty, "groq")),
    ):
        result = generate_structured_data_insights(_PART1, part2_nothing, {}, {})
    assert "error" in result
    assert "groq" in result["error"]


def test_invalid_json_from_one_provider_falls_through_to_next():
    with patch(
        "app.services.structured_data_insights_service.iter_text_attempts",
        side_effect=_attempts(("not json at all", "groq"), (json.dumps({"insights": ["real insight"]}), "gemini")),
    ):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    assert result["insights"] == ["real insight"]


def test_no_provider_configured_still_falls_back_to_deterministic_insights():
    def _gen(prompt, max_tokens, errors):
        raise NoAIProviderConfigured("No Gemini, Groq, or Claude API key configured — add one in Settings")
        yield  # pragma: no cover - unreachable, makes this a generator function

    with patch("app.services.structured_data_insights_service.iter_text_attempts", side_effect=_gen):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    # 2026-09-20 spec section 30: Key Insights must never fully disappear,
    # even with zero AI keys configured — the deterministic pass covers
    # this too.
    assert "error" not in result
    assert result.get("fallback") is True


def test_no_schema_data_short_circuits_without_calling_ai():
    with patch("app.services.structured_data_insights_service.iter_text_attempts") as mock_iter:
        result = generate_structured_data_insights([], [], {}, {})
    mock_iter.assert_not_called()
    assert result["error"] == "No schema data to analyze"


def test_deterministic_insights_missing_gap():
    part2 = [{"schema_type": "Article", "applicable": 13, "present": 0, "valid": 0, "invalid": 0, "missing": 13, "coverage_pct": 0, "site_level": False}]
    insights = _deterministic_schema_insights(part2, {})
    assert len(insights) == 1
    assert "Article schema gap" in insights[0]
    assert "13 applicable page(s)" in insights[0]
    assert "Fix:" in insights[0]


def test_deterministic_insights_invalid_schema():
    part2 = [{"schema_type": "Product", "applicable": 18, "present": 18, "valid": 12, "invalid": 6, "missing": 0, "coverage_pct": 67, "site_level": False}]
    insights = _deterministic_schema_insights(part2, {})
    assert len(insights) == 1
    assert "6 of 18" in insights[0]
    assert "validation errors" in insights[0]


def test_deterministic_insights_confirmed_win():
    part2 = [{"schema_type": "FAQPage", "applicable": 5, "present": 5, "valid": 5, "invalid": 0, "missing": 0, "coverage_pct": 100, "site_level": False}]
    insights = _deterministic_schema_insights(part2, {})
    assert len(insights) == 1
    assert "confirmed valid" in insights[0]
    assert "No fix needed" in insights[0]


def test_deterministic_insights_site_level_missing():
    part2 = [{"schema_type": "WebSite", "applicable": "Site-level", "present": "No", "valid": "—", "invalid": "—", "missing": "—", "coverage_pct": None, "site_level": True}]
    insights = _deterministic_schema_insights(part2, {})
    assert len(insights) == 1
    assert "site level" in insights[0]
    assert "823" not in insights[0]  # never a page count for a site-level fact


def test_deterministic_insights_not_applicable_row_produces_nothing():
    part2 = [{"schema_type": "Article", "applicable": 0, "present": 0, "valid": 0, "invalid": 0, "missing": 0, "coverage_pct": 0, "site_level": False}]
    assert _deterministic_schema_insights(part2, {}) == []


def test_deterministic_insights_respects_eligibility_notes():
    part2 = [{"schema_type": "FAQPage", "applicable": 13, "present": 0, "valid": 0, "invalid": 0, "missing": 13, "coverage_pct": 0, "site_level": False}]
    notes = {"FAQPage": "Google retired the classic FAQ rich-result SERP dropdown in May 2026 — this is content/AI-citation value only, not a SERP visual."}
    insights = _deterministic_schema_insights(part2, notes)
    assert "Google retired" in insights[0]


def test_deterministic_insights_capped_at_four_and_gaps_ranked_by_size():
    part2 = [
        {"schema_type": "A", "applicable": 5, "present": 0, "valid": 0, "invalid": 0, "missing": 5, "coverage_pct": 0, "site_level": False},
        {"schema_type": "B", "applicable": 50, "present": 0, "valid": 0, "invalid": 0, "missing": 50, "coverage_pct": 0, "site_level": False},
        {"schema_type": "C", "applicable": 20, "present": 0, "valid": 0, "invalid": 0, "missing": 20, "coverage_pct": 0, "site_level": False},
        {"schema_type": "D", "applicable": 30, "present": 0, "valid": 0, "invalid": 0, "missing": 30, "coverage_pct": 0, "site_level": False},
        {"schema_type": "E", "applicable": 40, "present": 0, "valid": 0, "invalid": 0, "missing": 40, "coverage_pct": 0, "site_level": False},
    ]
    insights = _deterministic_schema_insights(part2, {})
    assert len(insights) == 4
    assert insights[0].startswith("B schema gap")  # largest gap (50) first
