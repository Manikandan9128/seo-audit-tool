"""Universal SEO Audit Engine spec (2026-09-20) section 31: every technical
recommendation must carry Issue/Evidence/Affected URLs/Impact/Action/
Priority as real fields, not just one combined free-text sentence."""

from app.reporting.pptx_builder import build_structured_technical_recommendations


def test_empty_page_audit_returns_empty_list():
    assert build_structured_technical_recommendations(None) == []
    assert build_structured_technical_recommendations({"pages": []}) == []


def test_recommendation_has_all_required_fields():
    page_audit = {
        "pages": [
            {"url": "https://example.com/trucks", "issues": ["Missing <title> tag", "No <h1> tag found"]},
        ]
    }
    recs = build_structured_technical_recommendations(page_audit)
    assert len(recs) == 1
    rec = recs[0]
    for field in ("issue", "evidence", "affected_urls", "impact", "action", "priority", "category"):
        assert field in rec
    assert rec["affected_urls"] == ["/trucks"]
    assert rec["priority"] == 1
    assert "Fix" not in rec["impact"]  # impact is a severity statement, not a fix instruction
    assert rec["action"]  # the actual fix text still present, just in its own field


def test_priority_follows_scored_rows_order_never_re_derived():
    page_audit = {
        "pages": [
            {"url": "https://example.com/a", "issues": ["Missing meta description"]},  # warn
            {"url": "https://example.com/b", "issues": ["Page not reachable"]},  # error, higher priority
        ]
    }
    recs = build_structured_technical_recommendations(page_audit)
    assert recs[0]["priority"] == 1
    assert recs[0]["issue"] == "1 issue"
    assert "/b" in recs[0]["affected_urls"][0]
    assert recs[1]["priority"] == 2


def test_semrush_count_only_row_gets_low_impact_and_validation_action():
    site_audit_pages_rows = [{"page_url": "https://example.com/careers", "issues": 12}]
    recs = build_structured_technical_recommendations({"pages": []}, site_audit_pages_rows=site_audit_pages_rows)
    assert len(recs) == 1
    assert recs[0]["impact"].startswith("Low")
    assert "issue-level validation" in recs[0]["action"]
