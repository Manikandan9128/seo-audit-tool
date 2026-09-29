from unittest.mock import MagicMock, patch

import httpx
import pytest

import app.integrations.text_ai_client as text_ai_client
from app.integrations import ai_usage
from app.integrations.text_ai_client import (
    NoAIProviderConfigured, _cap_image_dimensions, _reserve_gemini_slot, generate_text, generate_text_with_image,
    generate_text_with_images,
)


@pytest.fixture(autouse=True)
def _reset_provider_selection():
    yield
    text_ai_client.set_preferred_provider(None)
    text_ai_client.set_claude_model(None)


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


_ALL_PROVIDERS = ["groq", "gemini", "claude", "browser_use", "openrouter"]
_TEXT_TRY = {
    "groq": "_try_groq", "gemini": "_try_gemini", "claude": "_try_claude",
    "browser_use": "_try_browser_use", "openrouter": "_try_openrouter",
}
_VISION_ATTEMPT = {
    "groq": "_try_groq_vision", "gemini": "_try_gemini_vision", "claude": "_try_claude_vision",
    "browser_use": "_try_browser_use", "openrouter": "_try_openrouter_vision",
}


def _all_keys(mock_settings):
    for provider in _ALL_PROVIDERS:
        setattr(mock_settings, f"{provider}_api_key", f"{provider}-key")


@pytest.mark.parametrize("selected", _ALL_PROVIDERS)
def test_selected_provider_is_the_only_one_called_for_text(selected):
    text_ai_client.set_preferred_provider(selected)
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            _all_keys(mock_settings)
            mocks = {p: patch(f"app.integrations.text_ai_client.{fn}", return_value=f"{p} answer") for p, fn in _TEXT_TRY.items()}
            started = {p: m.start() for p, m in mocks.items()}
            try:
                assert generate_text("prompt") == (f"{selected} answer", selected)
            finally:
                for m in mocks.values():
                    m.stop()
        for provider, mock in started.items():
            assert mock.called == (provider == selected), provider
    finally:
        text_ai_client.set_preferred_provider(None)


@pytest.mark.parametrize("selected", _ALL_PROVIDERS)
def test_selected_provider_is_the_only_one_called_for_vision(selected):
    text_ai_client.set_preferred_provider(selected)
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            _all_keys(mock_settings)
            mocks = {p: patch(f"app.integrations.text_ai_client.{fn}", return_value=f"{p} saw it") for p, fn in _VISION_ATTEMPT.items()}
            started = {p: m.start() for p, m in mocks.items()}
            try:
                with patch("app.integrations.text_ai_client._cap_image_dimensions", side_effect=lambda b, m: (b, m)):
                    assert generate_text_with_images("find issues", [(b"img", "image/png")]) == (f"{selected} saw it", selected)
            finally:
                for m in mocks.values():
                    m.stop()
        for provider, mock in started.items():
            assert mock.called == (provider == selected), provider
    finally:
        text_ai_client.set_preferred_provider(None)


def test_selected_provider_failure_never_calls_another_provider():
    # Groq's own error text (org id and all) is reported as-is, followed by
    # the no-fallback note — Gemini/Claude are never touched.
    text_ai_client.set_preferred_provider("groq")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            _all_keys(mock_settings)
            with patch("app.integrations.text_ai_client._try_groq", side_effect=_groq_429(1550)), \
                 patch("app.integrations.text_ai_client._try_gemini") as mock_gemini, \
                 patch("app.integrations.text_ai_client._try_claude") as mock_claude:
                with pytest.raises(NoAIProviderConfigured) as exc_info:
                    generate_text("some prompt")
        mock_gemini.assert_not_called()
        mock_claude.assert_not_called()
    finally:
        text_ai_client.set_preferred_provider(None)
    # A long Retry-After is a cap that won't clear inside this report, so
    # the request layer stops after one attempt instead of retrying.
    groq_part, note = str(exc_info.value).split(" | ")
    assert groq_part.startswith("[PROVIDER_ERROR] Groq rate-limited, not retrying (Retry-After 1550s)")
    assert "org_01m1nfvvxbe998jw8y33xx4qce" in groq_part
    assert note == "No fallback provider was used because Groq was selected."


def test_no_selection_raises_without_calling_any_provider():
    # No provider order to fall back on: every key configured, nothing
    # selected -> nothing is called.
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        _all_keys(mock_settings)
        with patch("app.integrations.text_ai_client._try_groq") as mock_groq, \
             patch("app.integrations.text_ai_client._try_gemini") as mock_gemini:
            with pytest.raises(NoAIProviderConfigured, match="No Report AI Provider selected"):
                generate_text("some prompt")
    mock_groq.assert_not_called()
    mock_gemini.assert_not_called()


def test_selected_provider_without_a_key_gives_a_clear_error_not_a_substitute():
    text_ai_client.set_preferred_provider("claude")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            _all_keys(mock_settings)
            mock_settings.claude_api_key = None
            with patch("app.integrations.text_ai_client._try_groq") as mock_groq:
                with pytest.raises(NoAIProviderConfigured) as exc_info:
                    list(text_ai_client.iter_text_with_images_attempts("p", [(b"img", "image/png")], 1024, []))
            mock_groq.assert_not_called()
    finally:
        text_ai_client.set_preferred_provider(None)
    assert str(exc_info.value) == (
        "Claude is selected but no Claude API key is configured — add it in Settings. "
        "No fallback provider was used because Claude was selected."
    )


def test_failure_message_names_provider_and_section():
    text_ai_client.set_preferred_provider("claude")
    try:
        message = text_ai_client.failure_message(
            "UI/UX analysis",
            ["Claude returned an empty response", "No fallback provider was used because Claude was selected."],
        )
    finally:
        text_ai_client.set_preferred_provider(None)
    assert message == (
        "UI/UX analysis — Failed. The analysis could not be generated. (Claude returned an empty response) "
        "No fallback provider was used because Claude was selected."
    )


def test_preferred_provider_is_a_strict_pin_not_just_first_priority():
    # 2026-09-28: a preference used to only move that provider to the
    # FRONT of the fallback order — Claude failing still silently fell
    # through to Groq, so "I selected paid Claude" wasn't a real
    # guarantee. Groq and Gemini are both configured and would happily
    # answer here; with Claude pinned and failing, neither should ever be
    # called, and the whole call should fail rather than substitute one.
    text_ai_client.set_preferred_provider("claude")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            mock_settings.groq_api_key = "gsk_test"
            mock_settings.gemini_api_key = "test"
            mock_settings.claude_api_key = "sk-ant-test"
            with patch("app.integrations.text_ai_client._try_groq", return_value="groq answer") as mock_groq, \
                 patch("app.integrations.text_ai_client._try_gemini", return_value="gemini answer") as mock_gemini, \
                 patch("app.integrations.text_ai_client._try_claude", side_effect=Exception("Claude overloaded")):
                with pytest.raises(NoAIProviderConfigured):
                    generate_text("some prompt")
            mock_groq.assert_not_called()
            mock_gemini.assert_not_called()
    finally:
        text_ai_client.set_preferred_provider(None)


def test_preferred_provider_vision_is_a_strict_pin_not_just_first_priority():
    text_ai_client.set_preferred_provider("claude")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            mock_settings.groq_api_key = "gsk_test"
            mock_settings.gemini_api_key = "test"
            mock_settings.claude_api_key = "sk-ant-test"
            with patch("app.integrations.text_ai_client._try_groq_vision", return_value="groq answer") as mock_groq_v, \
                 patch("app.integrations.text_ai_client._try_gemini_vision", return_value="gemini answer") as mock_gemini_v, \
                 patch("app.integrations.text_ai_client._try_claude_vision", side_effect=Exception("Claude overloaded")):
                with pytest.raises(NoAIProviderConfigured):
                    generate_text_with_images("find issues", [(b"img1", "image/png")], max_tokens=1024)
            mock_groq_v.assert_not_called()
            mock_gemini_v.assert_not_called()
    finally:
        text_ai_client.set_preferred_provider(None)


def test_browser_use_pin_runs_vision_pass_by_browsing_the_page_itself():
    # 2026-09-28: a Browser Use pin used to fail the UI-Level Fixes pass
    # outright ("isn't a vision-capable provider"). Browser Use can't take
    # images but it is a browser agent, so it opens the page instead —
    # one billed run, no silent substitution with Groq/Gemini/Claude.
    text_ai_client.set_preferred_provider("browser_use")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            mock_settings.browser_use_api_key = "bu_test"
            with patch("app.integrations.text_ai_client._try_browser_use", return_value='{"issues": []}') as mock_bu, \
                    patch("app.integrations.text_ai_client._try_groq_vision") as mock_groq_v:
                results = list(text_ai_client.iter_text_with_images_attempts(
                    "find issues", [(b"img1", "image/png")], 1024, [], start_url="https://example.com",
                ))
            mock_groq_v.assert_not_called()
        assert results == [('{"issues": []}', "browser_use")]
        task, _timeout, start_url = mock_bu.call_args.args
        assert "Open the website yourself" in task and task.endswith("find issues")
        assert start_url == "https://example.com"
    finally:
        text_ai_client.set_preferred_provider(None)


def test_openrouter_pin_sends_images_to_openrouter():
    text_ai_client.set_preferred_provider("openrouter")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings:
            mock_settings.openrouter_api_key = "or_test"
            with patch("app.integrations.text_ai_client._try_openrouter_vision", return_value="ok") as mock_or, \
                    patch("app.integrations.text_ai_client._cap_image_dimensions", side_effect=lambda b, m: (b, m)):
                assert generate_text_with_images("find issues", [(b"img1", "image/png")], max_tokens=1024) == ("ok", "openrouter")
        assert mock_or.call_args.args[1] == [(b"img1", "image/png")]
    finally:
        text_ai_client.set_preferred_provider(None)


def test_groq_prompt_fits_matches_try_groq_budget():
    assert text_ai_client.groq_prompt_fits("x" * 4000, 4096)
    assert not text_ai_client.groq_prompt_fits("x" * 40_000, 4096)


def test_request_layer_retries_an_empty_claude_answer_then_succeeds():
    # Regression (Geopits Core Problem, 2026-09-24): a 200 with no text is a
    # per-request roll — the shared request layer retries it (no hidden
    # extra retry inside _attempt_claude any more).
    text_ai_client.set_preferred_provider("claude")
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.claude_api_key = "sk-ant-test"
        with patch(
            "app.integrations.text_ai_client._try_claude",
            side_effect=[RuntimeError("empty content, stop_reason=end_turn"), "claude answer"],
        ) as mock_claude:
            assert generate_text("some prompt") == ("claude answer", "claude")
    assert mock_claude.call_count == 2


def test_attempt_claude_makes_one_request_and_reports_stop_reason():
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        mock_settings.claude_api_key = "sk-ant-test"
        with patch(
            "app.integrations.text_ai_client._try_claude",
            side_effect=RuntimeError("empty content, stop_reason=refusal"),
        ) as mock_claude:
            errors: list[str] = []
            text = text_ai_client._attempt_claude("some prompt", 1024, errors)
    assert text is None and mock_claude.call_count == 1
    assert "stop_reason=refusal" in errors[0]


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


def test_generate_text_with_images_respects_a_job_level_claude_preference():
    # Regression (confirmed real, Geopits regen 2026-09-26): generate_text()
    # already reorders on set_preferred_provider(), but generate_text_with_
    # images() used to be hardcoded Groq -> Gemini -> Claude with no
    # awareness of the preference at all. A job pinned to "claude" (the
    # user's paid choice) still burned through Groq/Gemini failures on
    # every vision call (UI-Level Fixes, Onboarding Breakdown) before ever
    # reaching Claude. Both Groq and Gemini keys are configured here too —
    # if the fix regresses, one of them gets called first and this fails.
    text_ai_client.set_preferred_provider("claude")
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings, \
             patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic, \
             patch("app.integrations.text_ai_client._try_groq_vision") as mock_groq_vision, \
             patch("app.integrations.text_ai_client._try_gemini_vision") as mock_gemini_vision:
            mock_settings.groq_api_key = "test-groq"
            mock_settings.gemini_api_key = "test-gemini"
            mock_settings.claude_api_key = "sk-ant-test"
            mock_anthropic.return_value.messages.create.return_value = _fake_claude_response("issues found", 100, 50)
            text, provider = generate_text_with_images("find issues", [(b"img1", "image/png")], max_tokens=1024)
        assert provider == "claude"
        assert text == "issues found"
        mock_groq_vision.assert_not_called()
        mock_gemini_vision.assert_not_called()
    finally:
        text_ai_client.set_preferred_provider(None)


def test_generate_text_with_images_sends_every_image_to_claude():
    # UI-Level Fixes rebuild (2026-09-25) needs all 4 screenshots (desktop/
    # mobile x first-screen/full-page) in ONE vision call, not one call per
    # image — this is the real regression risk of generalizing the
    # single-image path.
    text_ai_client.set_preferred_provider("claude")  # reset by _reset_provider_selection
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
    text_ai_client.set_preferred_provider("claude")  # reset by _reset_provider_selection
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


def test_claude_model_and_usage_survive_the_timeout_worker_thread():
    # Regression (2026-09-28): every provider call runs inside
    # _call_with_timeout's worker thread, so the thread-local model choice
    # and token-usage list were invisible there — the selected model was
    # silently replaced by the default and usage was never recorded.
    text_ai_client.set_claude_model("claude-haiku-4-5-20251001")
    text_ai_client.reset_claude_token_usage()
    try:
        with patch("app.integrations.text_ai_client.settings") as mock_settings, \
             patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic:
            mock_settings.claude_api_key = "sk-ant-test"
            mock_anthropic.return_value.messages.create.return_value = _fake_claude_response("answer", 10, 5)
            text_ai_client._call_with_timeout(text_ai_client._try_claude, 5, "prompt", 1024)
        _, kwargs = mock_anthropic.return_value.messages.create.call_args
        assert kwargs["model"] == "claude-haiku-4-5-20251001"
        assert text_ai_client.get_claude_token_usage()["calls"] == 1
    finally:
        text_ai_client.set_claude_model(None)


def test_job_context_pool_propagates_pin_and_restores_worker_state():
    text_ai_client.set_preferred_provider("claude")
    try:
        with text_ai_client.JobContextThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(text_ai_client.pinned_provider).result() == "claude"
            # The worker's own state is restored after the task.
            assert pool.submit(lambda: None).result() is None
        with text_ai_client.JobContextThreadPoolExecutor(max_workers=1) as pool:
            text_ai_client.set_preferred_provider(None)
            assert pool.submit(text_ai_client.pinned_provider).result() is None
    finally:
        text_ai_client.set_preferred_provider(None)


def test_selected_provider_scope_pins_then_clears():
    with text_ai_client.selected_provider_scope("gemini", None):
        assert text_ai_client.pinned_provider() == "gemini"
    assert text_ai_client.pinned_provider() is None


def test_claude_calls_leave_room_for_default_thinking():
    # Sonnet 5 / Opus 5.5 think by default and thinking counts against
    # max_tokens: a 4096 cap came back as "empty content,
    # stop_reason=max_tokens" (Lumber, 2026-09-29).
    text_ai_client.set_preferred_provider("claude")
    with patch("app.integrations.text_ai_client.settings") as mock_settings, \
         patch("app.integrations.text_ai_client.Anthropic") as mock_anthropic:
        mock_settings.claude_api_key = "sk-ant-test"
        mock_anthropic.return_value.messages.create.return_value = _fake_claude_response("ok", 10, 5)
        generate_text("prompt", max_tokens=4096)
    _, kwargs = mock_anthropic.return_value.messages.create.call_args
    assert kwargs["max_tokens"] == ai_usage.MODULE_OUTPUT_BUDGETS["other"]
    assert kwargs["output_config"] == {"effort": "medium"}


def test_haiku_gets_no_effort_setting():
    assert text_ai_client._claude_output_options("claude-haiku-4-5-20251001", 16000) == {"max_tokens": 16000}
