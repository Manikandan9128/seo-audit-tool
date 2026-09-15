from unittest.mock import patch

from app.services.seo_issues_insights_service import generate_seo_issues_insights


_ERRORS = [{"issue": "Missing meta description", "pages": 12}]
_WARNINGS = [{"issue": "Title too long", "pages": 5}]


def test_degenerate_null_response_returns_error_not_crash():
    # Same class of crash as core_problem_service (2026-09-15 live report):
    # a literal "null" completion parses to None with no exception, and
    # data.get("headline") on a NoneType crashed instead of erroring out.
    with patch("app.services.seo_issues_insights_service.generate_text", return_value=("null", "groq")):
        result = generate_seo_issues_insights(_ERRORS, _WARNINGS, 100, 17)
    assert "error" in result


def test_well_formed_response_still_parses():
    raw = '{"headline": "Structured data is the biggest gap.", "supporting": [], "takeaway": "Fix it."}'
    with patch("app.services.seo_issues_insights_service.generate_text", return_value=(raw, "groq")):
        result = generate_seo_issues_insights(_ERRORS, _WARNINGS, 100, 17)
    assert result["headline"] == "Structured data is the biggest gap."
