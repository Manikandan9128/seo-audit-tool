import copy
from unittest.mock import MagicMock, patch

from app.integrations.ui_audit_capture import capture_ui_audit

_MOD = "app.integrations.ui_audit_capture"


def _mock_page(evaluate_return=None, goto_side_effect=None):
    page = MagicMock()
    if goto_side_effect is not None:
        page.goto.side_effect = goto_side_effect
    # Called twice per viewport (once per device, since new_page returns
    # this same mock both times in these tests) — a callable side_effect
    # keyed on the full_page kwarg works for any number of calls, unlike a
    # fixed-length list.
    page.screenshot.side_effect = lambda **kw: b"full-page-bytes" if kw.get("full_page") else b"first-screen-bytes"
    facts = evaluate_return if evaluate_return is not None else {
        "h1_count": 1, "h1_texts": ["Welcome"], "hero_headline": "Welcome", "ctas": [], "carousel": None,
        "overlays": [], "overlaps": [], "trust_elements": [], "small_tap_targets": [], "viewport": {"width": 1440, "height": 900},
    }
    # A fresh copy per call, matching real Playwright (page.evaluate()
    # deserializes a new JSON object every call) — desktop's
    # page_facts.pop("small_tap_targets") in production code must not be
    # able to affect what mobile's own separate evaluate() call sees.
    page.title.return_value = "Example Home"
    page.goto.return_value.status = 200
    # The block check reads document.body.innerText via page.evaluate too.
    page.evaluate.side_effect = lambda script="", *a, **kw: (
        "A perfectly normal homepage " * 40 if "innerText" in str(script) else copy.deepcopy(facts)
    )
    return page


def _mock_playwright_context(page):
    browser = MagicMock()
    browser.new_context.return_value.new_page.return_value = page
    p = MagicMock()
    p.chromium.launch.return_value = browser
    ctx = MagicMock()
    ctx.__enter__.return_value = p
    ctx.__exit__.return_value = False
    return ctx, browser


def test_capture_ui_audit_returns_both_viewports_on_success():
    page = _mock_page()
    ctx, browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert result is not None
    assert set(result.keys()) == {"desktop", "mobile"}
    assert result["desktop"]["first_screen_png"] == b"first-screen-bytes"
    assert result["desktop"]["full_page_png"] == b"full-page-bytes"
    assert result["desktop"]["page_facts"]["hero_headline"] == "Welcome"
    browser.close.assert_called_once()


def test_capture_ui_audit_returns_none_when_chromium_launch_fails():
    p = MagicMock()
    p.chromium.launch.side_effect = RuntimeError("no chromium")
    ctx = MagicMock()
    ctx.__enter__.return_value = p
    ctx.__exit__.return_value = False
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert result is None


def test_capture_ui_audit_falls_back_to_domcontentloaded_on_load_timeout():
    page = _mock_page(goto_side_effect=[Exception("timeout"), None])
    ctx, _browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert result is not None
    assert page.goto.call_count >= 2
    # Second call used the domcontentloaded fallback.
    assert page.goto.call_args_list[1].kwargs["wait_until"] == "domcontentloaded"


def test_capture_ui_audit_skips_a_viewport_that_never_loads():
    page = _mock_page(goto_side_effect=Exception("blocked"))
    ctx, _browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert result is None  # neither viewport ever navigated successfully


def test_capture_ui_audit_only_keeps_small_tap_targets_for_mobile():
    facts_with_tap_targets = {
        "h1_count": 1, "h1_texts": ["Welcome"], "hero_headline": "Welcome", "ctas": [], "carousel": None,
        "overlays": [], "overlaps": [], "trust_elements": [],
        "small_tap_targets": [{"text": "X", "width": 20, "height": 20}],
        "viewport": {"width": 1440, "height": 900},
    }
    page = _mock_page(evaluate_return=facts_with_tap_targets)
    page.screenshot.side_effect = [b"a", b"b", b"c", b"d"]
    ctx, _browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert "small_tap_targets" not in result["desktop"]["page_facts"]
    assert "small_tap_targets" in result["mobile"]["page_facts"]


def test_capture_ui_audit_reports_block_on_403_instead_of_auditing_it():
    page = _mock_page()
    page.goto.return_value.status = 403
    ctx, _browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert result == {"blocked_reason": "HTTP 403"}
    page.screenshot.assert_not_called()


def test_capture_ui_audit_reports_block_on_challenge_page_with_200():
    page = _mock_page()
    page.title.return_value = "Just a moment..."
    ctx, _browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert "desktop" not in result
    assert "challenge page" in result["blocked_reason"]


def test_capture_ui_audit_tries_next_launch_mode_when_blocked():
    blocked_page, ok_page = _mock_page(), _mock_page()
    blocked_page.goto.return_value.status = 403
    blocked_browser, ok_browser = MagicMock(), MagicMock()
    blocked_browser.new_context.return_value.new_page.return_value = blocked_page
    ok_browser.new_context.return_value.new_page.return_value = ok_page
    p = MagicMock()
    p.chromium.launch.side_effect = [blocked_browser, ok_browser]
    ctx = MagicMock()
    ctx.__enter__.return_value = p
    ctx.__exit__.return_value = False
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert set(result.keys()) == {"desktop", "mobile"}
    assert p.chromium.launch.call_count == 2


def test_capture_ui_audit_handles_page_facts_extraction_failure_gracefully():
    page = _mock_page()
    page.evaluate.side_effect = Exception("JS threw")
    ctx, _browser = _mock_playwright_context(page)
    with patch(f"{_MOD}.sync_playwright", return_value=ctx):
        result = capture_ui_audit("example.com")
    assert result is not None
    assert result["desktop"]["page_facts"] == {}
