from unittest.mock import patch

from app.services.geopulse_ai_service import _find_disputed_metrics, generate_aeo_geo_content


def _attempts(*items):
    """items: list of (raw_text, provider) tuples to yield in order."""
    def _gen(prompt, max_tokens, errors):
        for raw, provider in items:
            yield raw, provider
    return _gen


def test_returns_aeo_and_geo_items():
    fake_response = """{
      "aeo_items": ["Create a pricing explainer answering the missed pricing prompts — GeoPulse shows 0 citations there."],
      "geo_items": ["Publish a comparison page — GeoPulse shows competitor X cited 12 times for this query."]
    }"""
    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_attempts((fake_response, "gemini"))):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result == {
        "aeo_items": ["Create a pricing explainer answering the missed pricing prompts — GeoPulse shows 0 citations there."],
        "geo_items": ["Publish a comparison page — GeoPulse shows competitor X cited 12 times for this query."],
    }


def test_returns_provider_error_when_ai_unavailable():
    from app.integrations.text_ai_client import NoAIProviderConfigured

    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=NoAIProviderConfigured("no key")):
        result = generate_aeo_geo_content("some text")
    assert result == {"error": "no key"}


def test_returns_error_on_invalid_json():
    with patch(
        "app.services.geopulse_ai_service.iter_text_attempts", side_effect=_attempts(("not json at all", "gemini"))
    ):
        result = generate_aeo_geo_content("some text")
    assert "error" in result and "aeo_items" not in result


def test_returns_empty_dict_for_empty_input():
    assert generate_aeo_geo_content("") == {}
    assert generate_aeo_geo_content("   ") == {}


def test_returns_error_when_both_lists_empty():
    with patch(
        "app.services.geopulse_ai_service.iter_text_attempts",
        side_effect=_attempts(('{"aeo_items": [], "geo_items": []}', "gemini")),
    ):
        result = generate_aeo_geo_content("some text")
    assert "error" in result and "aeo_items" not in result


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

    def _fake_iter_text_attempts(prompt, max_tokens, errors):
        captured["prompt"] = prompt
        yield '{"aeo_items": ["x"], "geo_items": ["y"]}', "gemini"

    raw_text = _LUMBER_CONSISTENCY_BANNER + "\nlumberfi appears in only 1 of 90 unbranded answers (1.1% organic discovery)."
    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_fake_iter_text_attempts):
        generate_aeo_geo_content(raw_text)

    prompt = captured["prompt"]
    assert "0.0%" in prompt
    assert "Use 0.0%, never 1.1%" in prompt


def test_drops_items_with_unsupported_causal_claims():
    fake_response = """{
      "aeo_items": [
        "Implement FAQ schema on the pricing page — this will increase AI answer-box visibility.",
        "Answer the missed product-comparison prompts on the product page to address the observed visibility gap."
      ],
      "geo_items": [
        "Publish a comparison page — this will improve AI parsing of the brand's offerings.",
        "Create comparison-friendly content to strengthen competitive presence in AI responses."
      ]
    }"""
    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_attempts((fake_response, "gemini"))):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result["aeo_items"] == ["Answer the missed product-comparison prompts on the product page to address the observed visibility gap."]
    assert result["geo_items"] == ["Create comparison-friendly content to strengthen competitive presence in AI responses."]


def test_bare_guarantee_claim_dropped_but_explicit_disclaimer_kept():
    fake_response = """{
      "aeo_items": [
        "Answering the missed setup questions guarantees inclusion in the AI answer box.",
        "Answer the missed setup questions directly — a prerequisite for eligibility, not a guarantee of inclusion."
      ],
      "geo_items": ["Some geo item with no issues at all."]
    }"""
    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_attempts((fake_response, "gemini"))):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert result["aeo_items"] == ["Answer the missed setup questions directly — a prerequisite for eligibility, not a guarantee of inclusion."]


def test_returns_error_when_all_items_fail_causal_guard():
    fake_response = """{
      "aeo_items": ["This will increase AI visibility for the brand."],
      "geo_items": ["This will improve ranking in AI engines."]
    }"""
    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_attempts((fake_response, "gemini"))):
        result = generate_aeo_geo_content("some raw geopulse export text")
    assert "none were backed by the visibility report" in result["error"]


def test_prompt_has_no_disputed_metrics_note_when_no_banner():
    captured = {}

    def _fake_iter_text_attempts(prompt, max_tokens, errors):
        captured["prompt"] = prompt
        yield '{"aeo_items": ["x"], "geo_items": ["y"]}', "gemini"

    with patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_fake_iter_text_attempts):
        generate_aeo_geo_content("lumberfi is mentioned in 52 of 150 AI answers.")

    assert "flagged these specific metrics as disputed" not in captured["prompt"]


def test_groq_pin_condenses_oversized_export_before_the_final_call():
    # 2026-09-28: a full export (~10k tokens) never fit Groq's 7,500-token
    # per-minute budget, so a Groq pin always ended in "check unavailable".
    import app.integrations.text_ai_client as text_ai_client

    raw_text = "GeoPulse finding. " * 2500  # ~45k chars
    seen_prompts = []

    def _gen(prompt, max_tokens, errors):
        seen_prompts.append(prompt)
        yield '{"aeo_items": ["Answer the pricing prompt GeoPulse shows 0 citations for."], "geo_items": []}', "groq"

    text_ai_client.set_preferred_provider("groq")
    try:
        with patch("app.services.geopulse_ai_service.generate_text", return_value=("- condensed fact", "groq")) as mock_gen, \
                patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_gen):
            result = generate_aeo_geo_content(raw_text)
    finally:
        text_ai_client.set_preferred_provider(None)
    assert mock_gen.call_count == 4
    assert "- condensed fact" in seen_prompts[0] and raw_text not in seen_prompts[0]
    assert text_ai_client.groq_prompt_fits(seen_prompts[0], 4096)
    assert result["aeo_items"] == ["Answer the pricing prompt GeoPulse shows 0 citations for."]


def test_non_groq_pin_sends_full_export():
    raw_text = "GeoPulse finding. " * 2500
    seen_prompts = []

    def _gen(prompt, max_tokens, errors):
        seen_prompts.append(prompt)
        yield '{"aeo_items": [], "geo_items": []}', "claude"

    with patch("app.services.geopulse_ai_service.generate_text") as mock_gen, \
            patch("app.services.geopulse_ai_service.iter_text_attempts", side_effect=_gen):
        generate_aeo_geo_content(raw_text)
    mock_gen.assert_not_called()
    assert raw_text in seen_prompts[0]
