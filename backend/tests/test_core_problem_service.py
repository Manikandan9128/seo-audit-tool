from unittest.mock import patch

from app.services.core_problem_service import generate_core_problem


def test_degenerate_null_response_returns_error_not_crash():
    # 2026-09-15 live crash: Groq returning the literal token "null" (valid
    # JSON, parses to None with no exception) hit data.get("thesis") on a
    # NoneType and surfaced as "'NoneType' object has no attribute 'get'"
    # during report generation instead of falling through to the error path.
    with patch("app.services.core_problem_service.generate_text", return_value=("null", "groq")):
        result = generate_core_problem({"issues": []})
    assert "error" in result


def test_well_formed_response_still_parses():
    raw = '{"thesis": "Site lacks structured data.", "categories": []}'
    with patch("app.services.core_problem_service.generate_text", return_value=(raw, "groq")):
        result = generate_core_problem({"issues": []})
    assert result["thesis"] == "Site lacks structured data."
