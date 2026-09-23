from app.reporting.pptx_builder import build_technical_next_steps


def test_homepage_only_issue_is_not_broadened_sitewide():
    items = build_technical_next_steps({"issues": ["Missing meta description"]}, None)
    assert items == ["Homepage: Missing meta description — add a relevant meta description to each affected page."]
    assert not any("every page" in i for i in items)


def test_semrush_errors_cite_real_counts_and_dedupe_page_level_duplicate():
    issues = [
        {"issue": "Broken internal links", "failed_checks": 42, "issue_type": "ERROR"},
        {"issue": "Pages with duplicate title tags", "failed_checks": 12, "issue_type": "ERROR"},
        {"issue": "Images without alt", "failed_checks": 90, "issue_type": "WARNING"},
    ]
    page_items = ["Fix Duplicate title — confirmed on 3 pages (/a, /b, /c) — write unique titles"]
    items = build_technical_next_steps(None, None, technical_fix_items=page_items, site_audit_issues=issues)
    assert any("Broken internal links" in i and "42 pages" in i for i in items)
    assert sum("uplicate title" in i for i in items) == 1  # same problem, two sources -> one bullet
    assert not any("alt" in i.lower() for i in items)  # warnings are not technical-next-step errors


def test_no_findings_means_no_recommendations():
    assert build_technical_next_steps({"issues": []}, None, tech_stack={"https": True}) == []


def test_https_is_sitewide_and_first():
    items = build_technical_next_steps(
        {"issues": ["Missing meta description"]}, None, tech_stack={"https": False},
    )
    assert items[0].startswith("The site isn't fully served over HTTPS")


def test_schema_only_for_applicable_under_covered_page_types():
    schema = {"total_pages": 50, "by_page_type": [
        {"page_type": "Product Pages", "applicable_schema": "Product", "pages": 20, "valid_pages": 5},
        {"page_type": "Other Pages", "applicable_schema": "—", "pages": 30, "valid_pages": 0},
    ]}
    items = build_technical_next_steps(None, None, schema_validation=schema)
    assert items == ["Add valid Product schema on Product Pages — 15 of 20 pages lack valid markup per the Structured Data validator."]


def test_performance_only_from_poor_pagespeed_metrics():
    psi = {"metric_table": [
        {"label": "Largest Contentful Paint", "value": 6100, "display_value": "6.1 s", "status": "Poor"},
        {"label": "Cumulative Layout Shift", "value": 0.02, "display_value": "0.02", "status": "Good"},
    ]}
    items = build_technical_next_steps(None, None, psi_mobile=psi)
    assert len(items) == 1 and "Largest Contentful Paint 6.1 s" in items[0] and "Layout Shift" not in items[0]


def test_never_recommends_domain_migration():
    items = build_technical_next_steps({"issues": ["Missing H1"]}, None)
    assert not any("domain" in i.lower() for i in items)
