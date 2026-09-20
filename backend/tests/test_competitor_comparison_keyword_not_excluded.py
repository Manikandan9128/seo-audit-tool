import json
from unittest.mock import patch

from app.services.keyword_relevance_service import classify_keywords


def test_comparison_keyword_mentioning_competitor_brand_reaches_ai_not_auto_excluded():
    # Universal SEO Audit Engine spec (2026-09-20) section 22: a
    # comparison/alternative-intent query mentioning a competitor's brand
    # ("mailchimp vs constant contact") is a real content opportunity, not
    # automatically irrelevant noise — must reach the AI relevance
    # judgment instead of being mechanically excluded as "brand".
    brand_tokens = {"mailchimp"}
    ai_response = json.dumps({"classifications": {"mailchimp vs constant contact": "highly_relevant"}})
    with patch("app.services.keyword_relevance_service.generate_text", return_value=(ai_response, "gemini")) as mock_ai:
        result = classify_keywords("Constant Contact", "constantcontact.com", brand_tokens, ["mailchimp vs constant contact"])
    mock_ai.assert_called_once()
    assert result["mailchimp vs constant contact"] == "highly_relevant"


def test_plain_brand_mention_without_comparison_intent_still_excluded_by_rule():
    # No comparison signal word — still a mechanical, free exclude, same
    # as before this fix.
    brand_tokens = {"mailchimp"}
    with patch("app.services.keyword_relevance_service.generate_text") as mock_ai:
        result = classify_keywords("Constant Contact", "constantcontact.com", brand_tokens, ["mailchimp login"])
    mock_ai.assert_not_called()
    assert result["mailchimp login"] == "exclude"


def test_alternative_signal_word_also_bypasses_brand_exclude():
    brand_tokens = {"mailchimp"}
    ai_response = json.dumps({"classifications": {"mailchimp alternative": "highly_relevant"}})
    with patch("app.services.keyword_relevance_service.generate_text", return_value=(ai_response, "gemini")) as mock_ai:
        result = classify_keywords("Constant Contact", "constantcontact.com", brand_tokens, ["mailchimp alternative"])
    mock_ai.assert_called_once()
    assert result["mailchimp alternative"] == "highly_relevant"
