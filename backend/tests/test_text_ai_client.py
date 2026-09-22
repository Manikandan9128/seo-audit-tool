from unittest.mock import MagicMock, patch

import httpx
import pytest

import app.integrations.text_ai_client as text_ai_client
from app.integrations.text_ai_client import NoAIProviderConfigured, _reserve_gemini_slot, generate_text


@pytest.fixture(autouse=True)
def _reset_gemini_pacer_windows():
    # The pacer's rolling windows are plain module-level lists, shared
    # (and mutated) across every test in the whole suite that ends up
    # calling _attempt_gemini/_reserve_gemini_slot — without resetting
    # them, an earlier test's Gemini calls could push a later, unrelated
    # test right up against the RPM/RPD budget and make it block or fail
    # for reasons that have nothing to do with what that test is checking.
    text_ai_client._gemini_minute_window.clear()
    text_ai_client._gemini_day_window.clear()
    yield
    text_ai_client._gemini_minute_window.clear()
    text_ai_client._gemini_day_window.clear()


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


def test_gemini_slot_reserved_immediately_when_windows_are_empty():
    assert _reserve_gemini_slot() is True
    assert len(text_ai_client._gemini_minute_window) == 1
    assert len(text_ai_client._gemini_day_window) == 1


def test_gemini_slot_denied_once_daily_budget_is_spent():
    # Fill the daily window directly (RPD budget) without touching the
    # per-minute one — a spent DAY budget must refuse immediately, no
    # sleep, since waiting can't free it up the way a per-minute cap does.
    # Timestamps must be "now", not 0.0 — the function prunes anything
    # older than the 24h window before checking length, so a stale
    # timestamp would just get pruned away instead of testing the denial.
    import time as _time
    now = _time.monotonic()
    text_ai_client._gemini_day_window.extend([now] * text_ai_client._GEMINI_RPD_BUDGET)
    with patch("app.integrations.text_ai_client.time.sleep") as mock_sleep:
        assert _reserve_gemini_slot() is False
    mock_sleep.assert_not_called()


def test_gemini_slot_blocks_then_succeeds_when_only_the_minute_window_is_full():
    # RPM budget full, but the day budget has room — must wait (sleep),
    # not refuse outright, since a per-minute window does free up.
    now = 1000.0
    text_ai_client._gemini_minute_window.extend([now] * text_ai_client._GEMINI_RPM_BUDGET)
    text_ai_client._gemini_day_window.extend([now] * text_ai_client._GEMINI_RPM_BUDGET)

    # First time.monotonic() call is inside the loop's first iteration
    # (still full); second is after a simulated 61s sleep, by which time
    # the minute window has aged out and a slot is free.
    with patch("app.integrations.text_ai_client.time.monotonic", side_effect=[now, now + 61]), \
         patch("app.integrations.text_ai_client.time.sleep") as mock_sleep:
        assert _reserve_gemini_slot() is True
    mock_sleep.assert_called_once()


def test_attempt_gemini_skips_the_real_call_when_daily_budget_is_spent():
    import time as _time
    now = _time.monotonic()
    text_ai_client._gemini_day_window.extend([now] * text_ai_client._GEMINI_RPD_BUDGET)
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.gemini_api_key = "test"
        with patch("app.integrations.text_ai_client._try_gemini") as mock_try_gemini:
            errors: list[str] = []
            result = text_ai_client._attempt_gemini("prompt", 1024, errors)
    assert result is None
    mock_try_gemini.assert_not_called()
    assert any("daily request-count budget" in e for e in errors)
