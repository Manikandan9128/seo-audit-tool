from unittest.mock import MagicMock, patch

import httpx
import pytest

import app.integrations.text_ai_client as text_ai_client
from app.integrations.text_ai_client import (
    NoAIProviderConfigured, _cap_image_dimensions, _reserve_gemini_slot, generate_text, generate_text_with_image,
    generate_text_with_images,
)


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


def test_attempt_claude_retries_once_on_empty_response_then_succeeds():
    # Regression (confirmed real, Geopits Core Problem slide, 2026-09-24):
    # a paid Claude key can still answer with a 200 and zero text (safety
    # stop or stopping before writing anything) — a per-request roll, not
    # an outage, so one immediate retry on the same prompt should recover.
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.claude_api_key = "sk-ant-test"
        with patch(
            "app.integrations.text_ai_client._try_claude",
            side_effect=[RuntimeError("empty content, stop_reason=end_turn"), "claude answer"],
        ) as mock_claude:
            text = text_ai_client._attempt_claude("some prompt", 1024, [])
    assert text == "claude answer"
    assert mock_claude.call_count == 2


def test_attempt_claude_reports_stop_reason_when_retry_also_comes_back_empty():
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.claude_api_key = "sk-ant-test"
        with patch(
            "app.integrations.text_ai_client._try_claude",
            side_effect=RuntimeError("empty content, stop_reason=refusal"),
        ):
            errors: list[str] = []
            text = text_ai_client._attempt_claude("some prompt", 1024, errors)
    assert text is None
    assert "stop_reason=refusal" in errors[0]
    assert "retried once" in errors[0]


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


def _fake_claude_response(text: str, input_tokens: int, output_tokens: int):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


def test_claude_token_usage_tracked_across_calls_and_reset_between_jobs():
    # Real per-report Claude spend (2026-09-25) — same thread-local
    # lifecycle as set_preferred_provider: reset once per report-
    # generation job, accumulates every Claude call made on that thread,
    # never leaks into an unrelated later job reusing the same thread.
    text_ai_client.reset_claude_token_usage()
    with patch("app.integrations.text_ai_client.settings") as mock_settings, \
         patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic:
        mock_settings.claude_api_key = "sk-ant-test"
        mock_anthropic.return_value.messages.create.side_effect = [
            _fake_claude_response("answer one", 500, 100),
            _fake_claude_response("answer two", 300, 50),
        ]
        text_ai_client._try_claude("prompt one", 1024)
        text_ai_client._try_claude("prompt two", 1024)

    usage = text_ai_client.get_claude_token_usage()
    assert usage == {"calls": 2, "input_tokens": 800, "output_tokens": 150}

    # A fresh reset (the next job on a reused thread) must not see the
    # previous job's totals.
    text_ai_client.reset_claude_token_usage()
    assert text_ai_client.get_claude_token_usage() == {"calls": 0, "input_tokens": 0, "output_tokens": 0}


def test_set_claude_model_rejects_unknown_model():
    with pytest.raises(ValueError):
        text_ai_client.set_claude_model("gpt-4")


def test_current_claude_model_defaults_to_the_module_constant():
    text_ai_client.set_claude_model(None)
    assert text_ai_client._current_claude_model() == text_ai_client.CLAUDE_MODEL


def test_current_claude_model_reflects_the_selected_override():
    text_ai_client.set_claude_model("claude-haiku-4-5-20251001")
    try:
        assert text_ai_client._current_claude_model() == "claude-haiku-4-5-20251001"
    finally:
        text_ai_client.set_claude_model(None)


def test_try_claude_uses_the_selected_model_override():
    # 2026-09-25: a per-job model selection (cheaper/faster Claude model)
    # must actually reach the real API call, not just sit in the
    # thread-local unused.
    text_ai_client.set_claude_model("claude-haiku-4-5-20251001")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings, \
             patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic:
            mock_settings.claude_api_key = "sk-ant-test"
            mock_anthropic.return_value.messages.create.return_value = _fake_claude_response("answer", 10, 5)
            text_ai_client._try_claude("prompt", 1024)
        _, kwargs = mock_anthropic.return_value.messages.create.call_args
        assert kwargs["model"] == "claude-haiku-4-5-20251001"
    finally:
        text_ai_client.set_claude_model(None)


def test_generate_text_with_images_sends_every_image_to_claude():
    # UI-Level Fixes rebuild (2026-09-25) needs all 4 screenshots (desktop/
    # mobile x first-screen/full-page) in ONE vision call, not one call per
    # image — this is the real regression risk of generalizing the
    # single-image path.
    with patch("app.integrations.text_ai_client.settings") as mock_settings, \
         patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic:
        mock_settings.claude_api_key = "sk-ant-test"
        mock_anthropic.return_value.messages.create.return_value = _fake_claude_response("4 issues found", 100, 50)
        images = [(b"img1", "image/png"), (b"img2", "image/png"), (b"img3", "image/png"), (b"img4", "image/png")]
        text, provider = generate_text_with_images("find issues", images, max_tokens=1024)
    assert text == "4 issues found"
    assert provider == "claude"
    _, kwargs = mock_anthropic.return_value.messages.create.call_args
    content = kwargs["messages"][0]["content"]
    image_blocks = [c for c in content if c["type"] == "image"]
    assert len(image_blocks) == 4
    assert [b["source"]["data"] for b in image_blocks] == [
        __import__("base64").b64encode(b).decode() for b in (b"img1", b"img2", b"img3", b"img4")
    ]
    text_blocks = [c for c in content if c["type"] == "text"]
    assert text_blocks[0]["text"] == "find issues"


def _make_png(width: int, height: int) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (width, height), color=(200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_cap_image_dimensions_downscales_an_oversized_full_page_screenshot():
    # Regression (confirmed real, Geopits regen, 2026-09-26): a Playwright
    # full_page=True screenshot of a long homepage (ui_audit_capture.py)
    # came back at 1280x8600px. Claude's vision API hard-rejects anything
    # over 8000px on either side with a 400 before its own paid, last-
    # resort fallback ever gets a chance to run — this happened AFTER Groq
    # timed out and Gemini's daily quota was exhausted, so all three
    # providers failed and the whole UI-Level Fixes slide was dropped.
    from PIL import Image

    oversized = _make_png(1280, 8600)
    capped_bytes, mime_type = _cap_image_dimensions(oversized, "image/png")
    img = Image.open(__import__("io").BytesIO(capped_bytes))
    assert max(img.size) <= 8000
    assert mime_type == "image/png"


def test_cap_image_dimensions_leaves_a_normal_screenshot_untouched():
    normal = _make_png(1280, 800)
    capped_bytes, mime_type = _cap_image_dimensions(normal, "image/png")
    assert capped_bytes == normal
    assert mime_type == "image/png"


def test_cap_image_dimensions_falls_back_to_original_bytes_on_decode_failure():
    # A corrupt/truncated capture shouldn't vanish here — it should still
    # reach the provider's own error handling, same as before this cap
    # existed.
    garbage = b"not a real image"
    capped_bytes, mime_type = _cap_image_dimensions(garbage, "image/png")
    assert capped_bytes == garbage
    assert mime_type == "image/png"


def test_generate_text_with_image_single_image_wrapper_still_works():
    # Backward-compat: existing single-image callers (semrush_parser,
    # ux_findings_service) must keep working unchanged.
    with patch("app.integrations.text_ai_client.settings") as mock_settings, \
         patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic:
        mock_settings.claude_api_key = "sk-ant-test"
        mock_anthropic.return_value.messages.create.return_value = _fake_claude_response("one image analyzed", 50, 20)
        text, provider = generate_text_with_image("describe this", b"single-image-bytes", "image/jpeg")
    assert text == "one image analyzed"
    assert provider == "claude"
    _, kwargs = mock_anthropic.return_value.messages.create.call_args
    content = kwargs["messages"][0]["content"]
    image_blocks = [c for c in content if c["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/jpeg"


def test_claude_token_usage_is_inert_without_a_reset_first():
    # A one-off script or test that never calls reset_claude_token_usage()
    # must not silently accumulate into a stale thread-local from some
    # earlier, unrelated call on the same thread.
    calls = getattr(text_ai_client._claude_token_usage, "calls", None)
    text_ai_client._claude_token_usage.calls = None
    try:
        text_ai_client._record_claude_usage("text", 100, 50)
        assert text_ai_client.get_claude_token_usage() == {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    finally:
        text_ai_client._claude_token_usage.calls = calls
