from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text


def _groq_429(retry_after_seconds: int) -> httpx.HTTPStatusError:
    # Mirrors _try_groq's own real message construction (status + reason +
    # url + response body) exactly, since this fixture patches _try_groq
    # itself — a bare httpx.HTTPStatusError's str() would just be
    # "429 Too Many Requests", not the org-id-bearing body this regression
    # is actually about.
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    body = (
        '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization '
        '`org_01m1nfvvxbe998jw8y33xx4qce` on tokens per minute (TPM): Limit 8000, Used 7999"}}'
    )
    response = httpx.Response(429, request=request, headers={"retry-after": str(retry_after_seconds)}, text=body)
    return httpx.HTTPStatusError(
        f"429 Too Many Requests for url '{request.url}': {body}", request=request, response=response,
    )


def test_both_providers_failing_are_clearly_separated_not_run_together():
    # Regression (confirmed real, 2026-09-19 live report): joining errors
    # with " / " let a truncated Groq error (which legitimately contains
    # " / " inside its own org-id text) run straight into Gemini's separate
    # error with no readable boundary — looked, on a real downloaded
    # report, like Gemini's quota message was somehow embedded INSIDE
    # Groq's own error body.
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.groq_api_key = "gsk_test"
        mock_settings.gemini_api_key = "test"
        mock_settings.claude_api_key = None
        with patch("app.integrations.text_ai_client._try_groq", side_effect=_groq_429(1550)), \
             patch("app.integrations.text_ai_client._try_gemini", side_effect=Exception("503 UNAVAILABLE: servers are overloaded")):
            with pytest.raises(NoAIProviderConfigured) as exc_info:
                generate_text("some prompt")

    message = str(exc_info.value)
    assert " | " in message
    groq_part, gemini_part = message.split(" | ")
    assert groq_part.startswith("Groq rate-limited, not retrying (Retry-After 1550s)")
    assert gemini_part == "Gemini's servers are temporarily unavailable. Try again shortly."
    # The org-id text that caused the original confusion must stay inside
    # Groq's own segment, never bleed into Gemini's.
    assert "org_01m1nfvvxbe998jw8y33xx4qce" in groq_part
    assert "org_01m1nfvvxbe998jw8y33xx4qce" not in gemini_part


def test_no_key_configured_raises_immediately():
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.groq_api_key = None
        mock_settings.gemini_api_key = None
        mock_settings.claude_api_key = None
        with pytest.raises(NoAIProviderConfigured):
            generate_text("some prompt")


def test_first_successful_provider_short_circuits_the_rest():
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.groq_api_key = "gsk_test"
        mock_settings.gemini_api_key = "test"
        mock_settings.claude_api_key = "sk-ant-test"
        with patch("app.integrations.text_ai_client._try_groq", return_value="groq answer") as mock_groq, \
             patch("app.integrations.text_ai_client._try_gemini") as mock_gemini:
            text, provider = generate_text("some prompt")
    assert text == "groq answer"
    assert provider == "groq"
    mock_groq.assert_called_once()
    mock_gemini.assert_not_called()
