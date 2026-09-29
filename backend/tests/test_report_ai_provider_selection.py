"""The Report AI Provider is a strict selection (2026-09-28): AEO/GEO,
UI/UX vision, Preview and Download all run on exactly the provider the user
picked — never another one, even when the picked one fails."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import app.integrations.text_ai_client as text_ai_client
from app.api.deps import ReportAISelection
from app.api.routes import site_audit
from app.services.geopulse_ai_service import generate_aeo_geo_content
from app.services.ui_audit_service import generate_ui_audit_issues

_ALL = ["groq", "gemini", "claude", "browser_use", "openrouter"]
_TEXT_TRY = {
    "groq": "_try_groq", "gemini": "_try_gemini", "claude": "_try_claude",
    "browser_use": "_try_browser_use", "openrouter": "_try_openrouter",
}
_VISION_TRY = {
    "groq": "_try_groq_vision", "gemini": "_try_gemini_vision", "claude": "_try_claude_vision",
    "browser_use": "_try_browser_use", "openrouter": "_try_openrouter_vision",
}


@pytest.fixture(autouse=True)
def _all_keys_and_reset():
    with patch("app.integrations.text_ai_client.settings") as mock_settings:
        for provider in _ALL:
            setattr(mock_settings, f"{provider}_api_key", f"{provider}-key")
        text_ai_client._gemini_minute_window.clear()
        text_ai_client._gemini_day_window.clear()
        yield
    text_ai_client.set_preferred_provider(None)
    text_ai_client.set_claude_model(None)


def _patch_all(names: dict, answer):
    patches = {p: patch(f"app.integrations.text_ai_client.{fn}", **answer(p)) for p, fn in names.items()}
    return patches, {p: m.start() for p, m in patches.items()}


def _stop(patches):
    for m in patches.values():
        m.stop()


_GEOPULSE_JSON = json.dumps({
    "aeo_items": ["Answer the pricing prompt GeoPulse shows 0 citations for."],
    "geo_items": ["Get cited where the report shows Competitor X cited 12 times."],
})


@pytest.mark.parametrize("selected", ["gemini", "claude", "openrouter", "browser_use"])
def test_aeo_and_geo_come_from_the_selected_provider_only(selected):
    text_ai_client.set_preferred_provider(selected)
    patches, mocks = _patch_all(_TEXT_TRY, lambda p: {"return_value": _GEOPULSE_JSON})
    try:
        result = generate_aeo_geo_content("GeoPulse report text")
    finally:
        _stop(patches)
    assert result["aeo_items"] and result["geo_items"]
    assert [p for p, m in mocks.items() if m.called] == [selected]


def test_aeo_geo_failure_names_provider_and_never_switches():
    text_ai_client.set_preferred_provider("gemini")
    patches, mocks = _patch_all(
        _TEXT_TRY, lambda p: {"side_effect": RuntimeError("quota exhausted")} if p == "gemini" else {"return_value": _GEOPULSE_JSON},
    )
    try:
        result = generate_aeo_geo_content("GeoPulse report text")
    finally:
        _stop(patches)
    assert [p for p, m in mocks.items() if m.called] == ["gemini"]
    assert result["error"].startswith("AEO/GEO analysis — Provider API error.")
    assert "Gemini" in result["error"]
    assert result["error"].endswith("No fallback provider was used because Gemini was selected.")


@pytest.mark.parametrize("selected", _ALL)
def test_ui_ux_vision_runs_on_the_selected_provider_only(selected):
    text_ai_client.set_preferred_provider(selected)
    answer = json.dumps({"issues": [{"title": "Hero CTA below the fold"}]})
    patches, mocks = _patch_all(_VISION_TRY, lambda p: {"return_value": answer})
    try:
        with patch("app.integrations.text_ai_client._cap_image_dimensions", side_effect=lambda b, m: (b, m)):
            result = generate_ui_audit_issues("Acme", "https://acme.test", None, {}, [(b"png", "image/png")])
    finally:
        _stop(patches)
    assert result == {"issues": [{"title": "Hero CTA below the fold"}]}
    assert [p for p, m in mocks.items() if m.called] == [selected]


def test_ui_ux_failure_on_claude_stops_without_sending_screenshots_elsewhere():
    text_ai_client.set_preferred_provider("claude")
    patches, mocks = _patch_all(
        _VISION_TRY, lambda p: {"side_effect": RuntimeError("overloaded")} if p == "claude" else {"return_value": '{"issues": []}'},
    )
    try:
        with patch("app.integrations.text_ai_client._cap_image_dimensions", side_effect=lambda b, m: (b, m)):
            result = generate_ui_audit_issues("Acme", "https://acme.test", None, {}, [(b"png", "image/png")])
    finally:
        _stop(patches)
    assert [p for p, m in mocks.items() if m.called] == ["claude"]
    assert mocks["claude"].call_count == 3  # first try + 2 retries, SAME provider
    assert result["error"].startswith("UI/UX analysis — Provider API error.")
    assert result["error"].endswith("No fallback provider was used because Claude was selected.")


@pytest.fixture(autouse=True)
def _no_estimate_db(monkeypatch):
    # Route tests use a fake DB; the hard-limit estimate has its own tests.
    monkeypatch.setattr(site_audit, "_enforce_hard_limit", lambda *a: None)


def _preview(monkeypatch, body_provider=None, header_provider=None):
    seen = {}

    def fake_gather(*args, **kwargs):
        seen["provider"] = text_ai_client.pinned_provider()
        return {}

    monkeypatch.setattr(site_audit, "_get_owned_client", lambda *a: SimpleNamespace(name="Acme", website_url="https://acme.test"))
    monkeypatch.setattr(site_audit, "_semrush_snapshot_for", lambda *a: None)
    monkeypatch.setattr(site_audit, "_gather_report_data", fake_gather)
    monkeypatch.setattr(site_audit, "_scrub_report_data", lambda d: d)
    result = site_audit.report_preview(
        uuid.uuid4(), company_overview_override=None, competitor_analysis_override=None, ux_notes=None,
        semrush_source=None, preferred_provider=body_provider, claude_model=None, skip_ai_steps=None,
        ai=ReportAISelection(header_provider, None), db=None, current_user=None,
    )
    return result, seen


def test_preview_runs_on_the_selected_provider(monkeypatch):
    result, seen = _preview(monkeypatch, body_provider="claude")
    assert seen["provider"] == "claude"
    assert result["ai_provider"] == "claude"
    assert text_ai_client.pinned_provider() is None  # cleared after the request


def test_preview_uses_the_header_selection_when_body_has_none(monkeypatch):
    _, seen = _preview(monkeypatch, header_provider="gemini")
    assert seen["provider"] == "gemini"


def test_preview_without_a_selection_is_refused_not_defaulted(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        _preview(monkeypatch)
    assert exc_info.value.status_code == 400
    assert "Select a Report AI Provider" in exc_info.value.detail


class _FakeDb:
    def __init__(self):
        self.added = []

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def with_for_update(self):
        return self

    def one(self):
        return None

    def add(self, obj):
        obj.id = uuid.uuid4()
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, _):
        pass


def _start(monkeypatch, provider, active=None):
    started = {}

    class FakeThread:
        def __init__(self, target, args, daemon):
            started["args"] = args

        def start(self):
            pass

    db = _FakeDb()
    monkeypatch.setattr(site_audit, "_get_owned_client", lambda *a: None)
    monkeypatch.setattr(site_audit, "_active_report_job", lambda *a: active)
    monkeypatch.setattr(site_audit, "_semrush_snapshot_for", lambda *a: None)
    monkeypatch.setattr(site_audit.threading, "Thread", FakeThread)
    result = site_audit.start_generate_report_job(
        uuid.uuid4(), company_overview_override=None, competitor_analysis_override=None, ux_notes=None,
        preferred_provider=provider, claude_model=None, semrush_source=None, skip_ai_steps=None,
        ai=ReportAISelection(None, None), db=db, current_user=None,
    )
    return result, started, db


def test_download_build_runs_on_and_records_the_selected_provider(monkeypatch):
    result, started, db = _start(monkeypatch, "openrouter")
    assert result["reused"] is False
    assert db.added[0].ai_provider == "openrouter"
    assert "openrouter" in started["args"]  # passed to the job thread, which pins it


def test_download_never_hands_back_a_build_running_on_another_provider(monkeypatch):
    active = SimpleNamespace(id=uuid.uuid4(), ai_provider="groq")
    with pytest.raises(HTTPException) as exc_info:
        _start(monkeypatch, "claude", active=active)
    assert exc_info.value.status_code == 409
    assert "already generating with Groq" in exc_info.value.detail


def test_download_without_a_selection_is_refused(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        _start(monkeypatch, None)
    assert exc_info.value.status_code == 400


def test_download_passes_skipped_steps_to_the_job_and_rejects_unknown_ones(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target, args, daemon):
            started["args"] = args

        def start(self):
            pass

    monkeypatch.setattr(site_audit, "_get_owned_client", lambda *a: None)
    monkeypatch.setattr(site_audit, "_active_report_job", lambda *a: None)
    monkeypatch.setattr(site_audit, "_semrush_snapshot_for", lambda *a: None)
    monkeypatch.setattr(site_audit.threading, "Thread", FakeThread)
    kwargs = dict(company_overview_override=None, competitor_analysis_override=None, ux_notes=None,
                  preferred_provider="claude", claude_model=None, semrush_source=None,
                  ai=ReportAISelection(None, None), db=_FakeDb(), current_user=None)
    site_audit.start_generate_report_job(uuid.uuid4(), skip_ai_steps=["aeo_geo", "ui_audit"], **kwargs)
    assert started["args"][-1] == ["aeo_geo", "ui_audit"]
    with pytest.raises(HTTPException) as exc_info:
        site_audit.start_generate_report_job(uuid.uuid4(), skip_ai_steps=["everything"], **kwargs)
    assert exc_info.value.status_code == 400


def test_skipped_steps_follow_the_job_into_worker_threads():
    text_ai_client.set_skipped_ai_steps(["aeo_geo"])
    try:
        with text_ai_client.JobContextThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(text_ai_client.ai_step_skipped, "aeo_geo").result() is True
            assert pool.submit(text_ai_client.ai_step_skipped, "ui_audit").result() is False
    finally:
        text_ai_client.set_skipped_ai_steps(())
