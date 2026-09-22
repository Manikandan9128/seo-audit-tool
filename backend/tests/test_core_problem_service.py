from unittest.mock import patch

from app.services.core_problem_service import generate_core_problem


def _attempts(*items):
    """items: list of (raw_text, provider) tuples to yield in order."""
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def test_degenerate_null_response_returns_error_not_crash():
    # 2026-09-15 live crash: Groq returning the literal token "null" (valid
    # JSON, parses to None with no exception) hit data.get("thesis") on a
    # NoneType and surfaced as "'NoneType' object has no attribute 'get'"
    # during report generation instead of falling through to the error path.
    with patch("app.services.core_problem_service.iter_text_attempts", side_effect=_attempts(("null", "groq"))):
        result = generate_core_problem({"issues": []})
    assert "error" in result


def test_well_formed_response_still_parses():
    raw = '{"thesis": "Site lacks structured data.", "categories": []}'
    with patch("app.services.core_problem_service.iter_text_attempts", side_effect=_attempts((raw, "groq"))):
        result = generate_core_problem({"issues": []})
    assert result["thesis"] == "Site lacks structured data."


def test_first_provider_invalid_json_falls_through_to_next():
    # 2026-09-22: OpenRouter's auto-router can land on a model that
    # doesn't reliably follow "return ONLY valid JSON" — a bad response
    # from one provider must not kill the section outright, it should
    # fall through to whichever provider comes next.
    good = '{"thesis": "Site lacks structured data.", "categories": []}'
    with patch(
        "app.services.core_problem_service.iter_text_attempts",
        side_effect=_attempts(("not json at all", "openrouter"), (good, "gemini")),
    ):
        result = generate_core_problem({"issues": []})
    assert result["thesis"] == "Site lacks structured data."
