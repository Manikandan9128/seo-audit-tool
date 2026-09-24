"""Universal SEO Keyword engine — §31/§63 stored decision history and
opportunity-score recalibration (backend-only, no review UI yet: `outcome`
is a manual DB flag). See docs/keyword_engine_spec_coverage.md."""

from app.services.keyword_history_service import cluster_key, multiplier_for, record_run, recalibrate
from app.services.keyword_strategy_depth import apply_history_recalibration


def _strategy(clusters):
    return {"clusters": clusters}


def test_record_run_upserts_and_preserves_outcome():
    strategy = _strategy([{"name": "Payroll Services — C01", "parent_topic": "Payroll",
                            "cluster_type": "Core Topic", "business_rule": None, "decision": "EXISTING URL",
                            "priority": "High", "opportunity": 70, "confidence": "High"}])
    history = record_run(None, strategy, "2026-09-24T00:00:00Z")
    key = cluster_key({"name": "Payroll Services — C01", "parent_topic": "Payroll"})
    assert history[key]["opportunity"] == 70 and history[key]["outcome"] is None

    history[key]["outcome"] = "accepted"  # the manual DB flag
    strategy2 = _strategy([{"name": "Payroll Services — C03", "parent_topic": "Payroll",
                             "cluster_type": "Core Topic", "business_rule": None, "decision": "EXISTING URL",
                             "priority": "High", "opportunity": 85, "confidence": "High"}])
    history = record_run(history, strategy2, "2026-09-25T00:00:00Z")
    assert history[key]["opportunity"] == 85 and history[key]["outcome"] == "accepted"


def test_recalibrate_is_neutral_with_no_or_too_little_data():
    assert recalibrate(None) == {}
    small = {f"k{i}": {"cluster_type": "Core Topic", "business_rule": "§48 e-commerce attribute", "outcome": "rejected"}
             for i in range(3)}
    assert recalibrate(small) == {}


def test_recalibrate_nudges_a_group_with_enough_judged_outcomes():
    history = {}
    for i in range(4):
        history[f"rej{i}"] = {"cluster_type": "Product Topic", "business_rule": "§48 e-commerce attribute", "outcome": "rejected"}
    history["acc0"] = {"cluster_type": "Product Topic", "business_rule": "§48 e-commerce attribute", "outcome": "accepted"}
    multipliers = recalibrate(history)
    key = ("Product Topic", "§48 e-commerce attribute")
    assert key in multipliers and 0.8 <= multipliers[key] < 1.0

    s = {"cluster_type": "Product Topic", "business_rule": "§48 e-commerce attribute"}
    assert multiplier_for(s, multipliers) == multipliers[key]
    assert multiplier_for({"cluster_type": "Other", "business_rule": None}, multipliers) == 1.0


def test_apply_history_recalibration_is_a_noop_with_no_multipliers():
    summaries = [{"opportunity": 70, "cluster_type": "Product Topic", "business_rule": "§48 e-commerce attribute",
                  "rows": [{"opportunity_score": 70}]}]
    apply_history_recalibration(summaries, None)
    assert summaries[0]["opportunity"] == 70
    apply_history_recalibration(summaries, {})
    assert summaries[0]["opportunity"] == 70


def test_apply_history_recalibration_scales_a_matched_group():
    summaries = [{"opportunity": 70, "cluster_type": "Product Topic", "business_rule": "§48 e-commerce attribute",
                  "rows": [{"opportunity_score": 70}]}]
    apply_history_recalibration(summaries, {("Product Topic", "§48 e-commerce attribute"): 0.8})
    assert summaries[0]["opportunity"] == 56
    assert summaries[0]["rows"][0]["opportunity_score"] == 56
    assert summaries[0]["rows"][0]["cluster_opportunity"] == 56
