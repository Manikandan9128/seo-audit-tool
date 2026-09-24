import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import report_prep as rp

OWNER = uuid.uuid4()


class _FakeSession:
    def get(self, model, _id):
        return SimpleNamespace(id=OWNER, owner_user_id=OWNER)

    def close(self):
        pass


@pytest.fixture
def fake_session(monkeypatch):
    monkeypatch.setattr(rp, "SessionLocal", _FakeSession)


def _run(monkeypatch, fn):
    monkeypatch.setitem(rp.SECTION_RUNNERS, "tech_stack", fn)
    return rp._run_section("tech_stack", uuid.uuid4(), OWNER, {})


def test_section_result_is_saved_as_json(fake_session, monkeypatch):
    r = _run(monkeypatch, lambda c, d, u, p: {"checked": date(2026, 9, 24)})
    assert r == {"status": "done", "data": {"checked": "2026-09-24"}, "error": None}


def test_http_error_keeps_the_endpoints_message(fake_session, monkeypatch):
    def fail(c, d, u, p):
        raise HTTPException(502, "PageSpeed Insights request failed: timed out")
    r = _run(monkeypatch, fail)
    # The page matches "timed out" to show the soft PageSpeed note instead of an error.
    assert r == {"status": "failed", "data": None, "error": "PageSpeed Insights request failed: timed out"}


def test_unexpected_error_fails_only_that_section(fake_session, monkeypatch):
    def crash(c, d, u, p):
        raise ValueError("boom")
    r = _run(monkeypatch, crash)
    assert r["status"] == "failed" and r["error"] == "ValueError: boom"


def test_every_page_section_has_a_runner():
    # Keys must match SECTION_OPTIONS in ClientDetailPage.tsx.
    assert set(rp.SECTION_RUNNERS) == set(rp.SECTION_KEYS) == {
        "overview", "site_audit", "all_pages", "pagespeed", "tech_stack", "analytics",
    }


@pytest.mark.parametrize("sections", [[], ["bogus"], ["overview", "bogus"]])
def test_start_rejects_unknown_or_empty_sections(sections):
    with pytest.raises(HTTPException) as e:
        rp.start_report_prep_job(uuid.uuid4(), sections, "30daysAgo", "today", db=_FakeSession(), current_user=SimpleNamespace(id=OWNER))
    assert e.value.status_code == 400
