"""High-usage popup estimate (2026-09-29): heavy AI steps with tokens and
cost for the selected paid provider, sized from what the client uploaded."""

import uuid
from types import SimpleNamespace

from app.services import ai_usage_estimate as est


class _Db:
    def __init__(self, imports):
        self._imports = imports

    def query(self, *_):
        return self

    def filter(self, *_):
        return self

    def all(self):
        return self._imports


def _imp(import_type, own=True, label=None, raw=""):
    return SimpleNamespace(import_type=import_type, is_own_site=own, domain_label=label,
                           parsed_data={"rows": [{"raw_text": raw}]})


def test_claude_estimate_lists_only_steps_that_apply_with_cost():
    db = _Db([_imp("geopulse", raw="x" * 40000), _imp("domain_overview", own=False, label="rival.com")])
    result = est.estimate_report_ai_usage(uuid.uuid4(), db, "claude", "claude-sonnet-5")
    keys = [s["key"] for s in result["steps"]]
    assert keys == ["aeo_geo", "ui_audit", "core_problem", "competitor_narratives", "next_steps"]
    aeo = result["steps"][0]
    assert aeo["input_tokens"] == 40000 // 4 + 1600
    assert aeo["cost_usd"] == round(aeo["input_tokens"] / 1e6 * 2 + aeo["output_tokens"] / 1e6 * 10, 4)
    assert result["paid"] is True


def test_no_geopulse_or_competitors_means_no_such_steps():
    result = est.estimate_report_ai_usage(uuid.uuid4(), _Db([]), "claude", None)
    assert {s["key"] for s in result["steps"]} == {"ui_audit", "core_problem", "next_steps"}


def test_browser_use_has_no_per_token_price_and_free_tiers_are_not_paid():
    bu = est.estimate_report_ai_usage(uuid.uuid4(), _Db([]), "browser_use", None)
    assert bu["per_run_billing"] and all(s["cost_usd"] is None for s in bu["steps"])
    assert est.estimate_report_ai_usage(uuid.uuid4(), _Db([]), "groq", None)["paid"] is False


def test_steps_over_the_warning_say_why_and_hard_limit_blocks():
    db = _Db([_imp("geopulse", raw="x" * 400_000)])  # ~100k tokens of GeoPulse text
    result = est.estimate_report_ai_usage(uuid.uuid4(), db, "claude", "claude-sonnet-5")
    aeo = next(s for s in result["steps"] if s["key"] == "aeo_geo")
    assert aeo["over_warning"] and aeo["over_hard_limit"]
    assert "GeoPulse export" in aeo["reason"]
    assert result["needs_confirmation"] is True
    message = est.hard_limit_violation(result, skipped=[])
    assert message.startswith("This analysis exceeds the configured maximum token limit.")
    assert "AEO / GEO" in message
    assert est.hard_limit_violation(result, skipped=["aeo_geo"]) is None


def test_small_report_on_free_provider_needs_no_confirmation():
    result = est.estimate_report_ai_usage(uuid.uuid4(), _Db([]), "gemini", None)
    assert result["needs_confirmation"] is False and result["paid"] is False
