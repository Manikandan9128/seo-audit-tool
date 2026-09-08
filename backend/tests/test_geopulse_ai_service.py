from unittest.mock import patch

from app.services.geopulse_ai_service import generate_aeo_geo_content


def test_returns_aeo_and_geo_items():
    fake_response = """{
      "aeo_items": ["Add FAQ schema to the pricing page — GeoPulse shows 0 citations there."],
      "geo_items": ["Publish a comparison page — GeoPulse shows competitor X cited 12 times for this query."]
    }"""
    with patch("app.services.geopulse_ai_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result == {
        "aeo_items": ["Add FAQ schema to the pricing page — GeoPulse shows 0 citations there."],
        "geo_items": ["Publish a comparison page — GeoPulse shows competitor X cited 12 times for this query."],
    }


def test_returns_empty_dict_when_ai_unavailable():
    from app.integrations.text_ai_client import NoAIProviderConfigured

    with patch("app.services.geopulse_ai_service.generate_text", side_effect=NoAIProviderConfigured("no key")):
        result = generate_aeo_geo_content("some text")
    assert result == {}


def test_returns_empty_dict_on_invalid_json():
    with patch("app.services.geopulse_ai_service.generate_text", return_value=("not json at all", "gemini")):
        result = generate_aeo_geo_content("some text")
    assert result == {}


def test_returns_empty_dict_for_empty_input():
    assert generate_aeo_geo_content("") == {}
    assert generate_aeo_geo_content("   ") == {}


def test_returns_empty_dict_when_both_lists_empty():
    with patch("app.services.geopulse_ai_service.generate_text", return_value=('{"aeo_items": [], "geo_items": []}', "gemini")):
        result = generate_aeo_geo_content("some text")
    assert result == {}
