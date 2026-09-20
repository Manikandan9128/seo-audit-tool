import json
from unittest.mock import patch

from app.integrations.text_ai_client import NoAIProviderConfigured
from app.services.structured_data_insights_service import generate_structured_data_insights

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


def test_error_names_every_provider_that_returned_empty():
    groq_empty = json.dumps({"insights": []})
    gemini_empty = json.dumps({"insights": []})
    with patch(
        "app.services.structured_data_insights_service.iter_text_attempts",
        side_effect=_attempts((groq_empty, "groq"), (gemini_empty, "gemini")),
    ):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    assert "error" in result
    assert "groq" in result["error"] and "gemini" in result["error"]


def test_invalid_json_from_one_provider_falls_through_to_next():
    with patch(
        "app.services.structured_data_insights_service.iter_text_attempts",
        side_effect=_attempts(("not json at all", "groq"), (json.dumps({"insights": ["real insight"]}), "gemini")),
    ):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    assert result["insights"] == ["real insight"]


def test_no_provider_configured_propagates_as_error():
    def _gen(prompt, max_tokens, errors):
        raise NoAIProviderConfigured("No Gemini, Groq, or Claude API key configured — add one in Settings")
        yield  # pragma: no cover - unreachable, makes this a generator function

    with patch("app.services.structured_data_insights_service.iter_text_attempts", side_effect=_gen):
        result = generate_structured_data_insights(_PART1, _PART2, {}, {})
    assert "No Gemini, Groq, or Claude API key configured" in result["error"]


def test_no_schema_data_short_circuits_without_calling_ai():
    with patch("app.services.structured_data_insights_service.iter_text_attempts") as mock_iter:
        result = generate_structured_data_insights([], [], {}, {})
    mock_iter.assert_not_called()
    assert result["error"] == "No schema data to analyze"
