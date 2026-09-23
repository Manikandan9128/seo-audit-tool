from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, _audit_slide_geometry, _goals_report_period, add_goals_slide, build_goals_kpis


def _by_metric(kpis):
    return {k["metric"]: k for k in kpis}


def test_no_data_means_no_slide():
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    assert build_goals_kpis(None, None, None) == []
    assert add_goals_slide(prs, None, None, None) is None


def test_organic_sessions_baseline_from_ga4_organic_channel_only():
    analytics = {"traffic_sources": {"rows": [
        {"channel": "Organic Search", "sessions": "1200"}, {"channel": "Direct", "sessions": "5000"},
    ]}}
    k = _by_metric(build_goals_kpis(None, None, None, analytics))["Organic sessions"]
    assert "1,200" in k["current"] and "5,000" not in k["current"]
    assert not any(ch.isdigit() for ch in k["target"])  # no invented numeric target


def test_referring_domains_use_backlink_summary_never_row_count():
    kpis = _by_metric(build_goals_kpis(40, None, None, backlink_summary={"referring_domains": 812, "backlinks_total": 33800}))
    assert kpis["Referring domains"]["current"] == "812 referring domains / 33,800 backlinks"
    assert "10,000" not in str(kpis) and "rows" not in str(kpis).lower()


def test_domain_rating_target_names_real_competitor_leader():
    comp = [{"domain": "rival.com", "authority_score": 55}, {"domain": "small.com", "authority_score": 20}]
    k = _by_metric(build_goals_kpis(30, comp, None))["Domain Rating"]
    assert k["current"] == "DR 30" and "rival.com (DR 55)" in k["target"]


def test_conversions_never_get_an_assumed_rate():
    k = _by_metric(build_goals_kpis(30, None, None))["Organic conversions"]
    assert "%" not in k["current"] + k["target"] and "assum" not in (k["current"] + k["target"]).lower()


def test_rankings_count_only_validated_keywords():
    rows = [
        {"keyword": "payroll software", "position": 4, "keyword_difficulty": 20, "cluster": "Payroll"},
        {"keyword": "payroll app", "position": 25, "keyword_difficulty": 15, "cluster": "Payroll"},
        {"keyword": "rival login", "position": 2, "cluster": "Competitor / Comparison Opportunities"},
    ]
    k = _by_metric(build_goals_kpis(None, None, rows))["Target keywords on page 1"]
    assert k["current"] == "1 of 2 ranking target keyword(s) in the top 10"
    assert "1 low-difficulty" in k["target"]


def test_report_period_only_from_real_iso_dates():
    assert _goals_report_period({"date_range": {"start": "30daysAgo", "end": "today"}}) is None
    assert _goals_report_period({"date_range": {"start": "2026-08-01", "end": "2026-08-31"}}) == "01 Aug 2026 – 31 Aug 2026"


def test_full_goals_slide_fits_page():
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    add_goals_slide(
        prs, 30, [{"domain": "rival.com", "authority_score": 55}],
        [{"keyword": "payroll software", "position": 4, "keyword_difficulty": 20, "cluster": "Payroll"}],
        {"traffic_sources": {"rows": [{"channel": "Organic Search", "sessions": "1200"}]}, "date_range": {"start": "2026-08-01", "end": "2026-08-31"}},
        {"referring_domains": 812, "backlinks_total": 33800},
        {"total_pages": 120, "pages_with_schema": 40, "missing_types": ["Product"]},
        {"site_health_pct": 81},
    )
    assert _audit_slide_geometry(prs) == []
