from unittest.mock import patch

from app.services.search_intent_service import generate_search_intents


def test_maps_keywords_to_intent_labels():
    fake_response = """[
      {"keyword": "certified payroll software", "intent": "Commercial"},
      {"keyword": "what is certified payroll", "intent": "Informational"},
      {"keyword": "buy certified payroll software", "intent": "Transactional"}
    ]"""
    with patch("app.services.search_intent_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_search_intents(
            ["certified payroll software", "what is certified payroll", "buy certified payroll software"]
        )
    assert result == {
        "certified payroll software": "Commercial",
        "what is certified payroll": "Informational",
        "buy certified payroll software": "Transactional",
    }


def test_drops_hallucinated_keywords_not_in_input():
    fake_response = '[{"keyword": "real keyword", "intent": "Commercial"}, {"keyword": "invented keyword", "intent": "Transactional"}]'
    with patch("app.services.search_intent_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_search_intents(["real keyword"])
    assert result == {"real keyword": "Commercial"}
    assert "invented keyword" not in result


def test_drops_invalid_intent_labels():
    # Regression guard: only Semrush's own 4 real intent labels are ever
    # trusted — anything else (a hallucinated or malformed label) is
    # dropped rather than written into a report as fact.
    fake_response = '[{"keyword": "some keyword", "intent": "Sponsored"}]'
    with patch("app.services.search_intent_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_search_intents(["some keyword"])
    assert result == {}


def test_returns_empty_dict_when_ai_unavailable():
    from app.integrations.text_ai_client import NoAIProviderConfigured

    with patch("app.services.search_intent_service.generate_text", side_effect=NoAIProviderConfigured("no key")):
        result = generate_search_intents(["some keyword"])
    assert result == {}


def test_returns_empty_dict_on_invalid_json():
    with patch("app.services.search_intent_service.generate_text", return_value=("not json at all", "gemini")):
        result = generate_search_intents(["some keyword"])
    assert result == {}


def test_returns_empty_dict_for_empty_input():
    assert generate_search_intents([]) == {}
