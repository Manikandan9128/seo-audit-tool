import json
from unittest.mock import patch

from app.services.keyword_relevance_service import classify_keywords


def _attempts(*items):
    """items: list of (raw_text, provider) tuples to yield in order."""
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def test_comparison_keyword_mentioning_competitor_brand_reaches_ai_not_auto_excluded():
    # Universal SEO Audit Engine spec (2026-09-20) section 22: a
    # comparison/alternative-intent query mentioning a competitor's brand
    # ("mailchimp vs constant contact") is a real content opportunity, not
    # automatically irrelevant noise — must reach the AI relevance
    # judgment instead of being mechanically excluded as "brand".
    brand_tokens = {"mailchimp"}
    ai_response = json.dumps({"classifications": {
        "mailchimp vs constant contact": {"status": "competitor_comparison_opportunity", "reason": "vs.-shaped query"},
    }})
    with patch(
        "app.services.keyword_relevance_service.iter_text_attempts", side_effect=_attempts((ai_response, "gemini"))
    ) as mock_ai:
        result = classify_keywords("Constant Contact", "constantcontact.com", brand_tokens, ["mailchimp vs constant contact"])
    mock_ai.assert_called_once()
    assert result["mailchimp vs constant contact"]["label"] == "potentially_relevant"
    assert result["mailchimp vs constant contact"]["status"] == "Competitor Comparison Opportunity"


def test_plain_brand_mention_without_comparison_intent_still_excluded_by_rule():
    # No comparison signal word — still a mechanical, free exclude, same
    # as before this fix.
    brand_tokens = {"mailchimp"}
    with patch("app.services.keyword_relevance_service.iter_text_attempts") as mock_ai:
        result = classify_keywords("Constant Contact", "constantcontact.com", brand_tokens, ["mailchimp login"])
    mock_ai.assert_not_called()
    assert result["mailchimp login"]["label"] == "exclude"
    assert result["mailchimp login"]["status"] == "Competitor Brand Search"


def test_alternative_signal_word_also_bypasses_brand_exclude():
    brand_tokens = {"mailchimp"}
    ai_response = json.dumps({"classifications": {
        "mailchimp alternative": {"status": "competitor_comparison_opportunity", "reason": "alternative-shaped query"},
    }})
    with patch(
        "app.services.keyword_relevance_service.iter_text_attempts", side_effect=_attempts((ai_response, "gemini"))
    ) as mock_ai:
        result = classify_keywords("Constant Contact", "constantcontact.com", brand_tokens, ["mailchimp alternative"])
    mock_ai.assert_called_once()
    assert result["mailchimp alternative"]["label"] == "potentially_relevant"
