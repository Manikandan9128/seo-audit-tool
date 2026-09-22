from unittest.mock import patch

from app.services.seo_issues_insights_service import generate_seo_issues_insights


_ERRORS = [{"issue": "Missing meta description", "pages": 12}]
_WARNINGS = [{"issue": "Title too long", "pages": 5}]


def _attempts(*items):
    """items: list of (raw_text, provider) tuples to yield in order."""
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def test_degenerate_null_response_returns_error_not_crash():
    # Same class of crash as core_problem_service (2026-09-15 live report):
    # a literal "null" completion parses to None with no exception, and
    # data.get("headline") on a NoneType crashed instead of erroring out.
    with patch(
        "app.services.seo_issues_insights_service.iter_text_attempts", side_effect=_attempts(("null", "groq"))
    ):
        result = generate_seo_issues_insights(_ERRORS, _WARNINGS, 100, 17)
    assert "error" in result


def test_well_formed_response_still_parses():
    raw = '{"headline": "Structured data is the biggest gap.", "supporting": [], "takeaway": "Fix it."}'
    with patch(
        "app.services.seo_issues_insights_service.iter_text_attempts", side_effect=_attempts((raw, "groq"))
    ):
        result = generate_seo_issues_insights(_ERRORS, _WARNINGS, 100, 17)
    assert result["headline"] == "Structured data is the biggest gap."


def test_first_provider_invalid_json_falls_through_to_next():
    # 2026-09-22: OpenRouter's auto-router can land on a model that
    # doesn't reliably follow "return ONLY valid JSON" — a bad response
    # from one provider must not kill the section outright, it should
    # fall through to whichever provider comes next.
    good = '{"headline": "Structured data is the biggest gap.", "supporting": [], "takeaway": "Fix it."}'
    with patch(
        "app.services.seo_issues_insights_service.iter_text_attempts",
        side_effect=_attempts(("not json at all", "openrouter"), (good, "gemini")),
    ):
        result = generate_seo_issues_insights(_ERRORS, _WARNINGS, 100, 17)
    assert result["headline"] == "Structured data is the biggest gap."
