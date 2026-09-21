from unittest.mock import patch

from app.services.geopulse_ai_service import _find_disputed_metrics, generate_aeo_geo_content


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


# 2026-09-10 regression: a real Lumber GeoPulse export flagged its own
# headline "organic discovery (1.1%)" as disputed against the dashboard's
# 0.0%, but the AEO/GEO slide cited 1.1% as plain fact with no caveat.
_LUMBER_CONSISTENCY_BANNER = (
    "Automated consistency check failed - verify these numbers before sharing this report:\n"
    "Report mention rate (34.7%) doesn't match the dashboard's independently computed rate (34.0%).\n"
    "Report organic discovery (1.1%) doesn't match the dashboard's (0.0%).\n"
)


def test_finds_disputed_metrics_in_real_geopulse_banner_wording():
    found = _find_disputed_metrics(_LUMBER_CONSISTENCY_BANNER)
    assert found == [
        ("Report mention rate", "34.7%", "34.0%"),
        ("Report organic discovery", "1.1%", "0.0%"),
    ]


def test_finds_no_disputed_metrics_when_no_banner_present():
    assert _find_disputed_metrics("lumberfi is mentioned in 52 of 150 AI answers (34.7% overall).") == []


def test_prompt_names_disputed_metric_and_correction_when_banner_present():
    captured = {}

    def _fake_generate_text(prompt):
        captured["prompt"] = prompt
        return '{"aeo_items": ["x"], "geo_items": ["y"]}', "gemini"

    raw_text = _LUMBER_CONSISTENCY_BANNER + "\nlumberfi appears in only 1 of 90 unbranded answers (1.1% organic discovery)."
    with patch("app.services.geopulse_ai_service.generate_text", side_effect=_fake_generate_text):
        generate_aeo_geo_content(raw_text)

    prompt = captured["prompt"]
    assert "0.0%" in prompt
    assert "Use 0.0%, never 1.1%" in prompt


def test_drops_items_with_unsupported_causal_claims():
    fake_response = """{
      "aeo_items": [
        "Implement FAQ schema on the pricing page — this will increase AI answer-box visibility.",
        "Add structured data to the product page to address eligibility for AI Overview inclusion."
      ],
      "geo_items": [
        "Publish a comparison page — this will improve AI parsing of the brand's offerings.",
        "Create comparison-friendly content to strengthen competitive presence in AI responses."
      ]
    }"""
    with patch("app.services.geopulse_ai_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result["aeo_items"] == ["Add structured data to the product page to address eligibility for AI Overview inclusion."]
    assert result["geo_items"] == ["Create comparison-friendly content to strengthen competitive presence in AI responses."]


def test_bare_guarantee_claim_dropped_but_explicit_disclaimer_kept():
    fake_response = """{
      "aeo_items": [
        "Implementing FAQPage schema guarantees inclusion in the AI answer box.",
        "FAQPage structured data is a prerequisite for eligibility, not a guarantee of inclusion."
      ],
      "geo_items": ["Some geo item with no issues at all."]
    }"""
    with patch("app.services.geopulse_ai_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result["aeo_items"] == ["FAQPage structured data is a prerequisite for eligibility, not a guarantee of inclusion."]


def test_returns_empty_dict_when_all_items_fail_causal_guard():
    fake_response = """{
      "aeo_items": ["This will increase AI visibility for the brand."],
      "geo_items": ["This will improve ranking in AI engines."]
    }"""
    with patch("app.services.geopulse_ai_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result == {}


def test_prompt_has_no_disputed_metrics_note_when_no_banner():
    captured = {}

    def _fake_generate_text(prompt):
        captured["prompt"] = prompt
        return '{"aeo_items": ["x"], "geo_items": ["y"]}', "gemini"

    with patch("app.services.geopulse_ai_service.generate_text", side_effect=_fake_generate_text):
        generate_aeo_geo_content("lumberfi is mentioned in 52 of 150 AI answers.")

    assert "flagged these specific metrics as disputed" not in captured["prompt"]
