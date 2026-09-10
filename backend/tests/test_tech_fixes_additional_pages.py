from app.reporting.pptx_builder import _tech_fixes_scored_rows


def test_semrush_only_row_fix_text_does_not_repeat_the_source_header():
    # 2026-09-10 user request: the slide already states "Source: Semrush
    # Site Audit" once, in the header - the per-row Fix cell shouldn't
    # repeat "review it in Semrush's Site Audit" on every single row.
    page_audit = {"pages": []}
    site_audit_pages_rows = [{"page_url": "https://example.com/careers", "issues": 12}]

    rows = _tech_fixes_scored_rows(page_audit, analytics=None, site_audit_pages_rows=site_audit_pages_rows)

    assert len(rows) == 1
    fix_text = rows[0][4]
    assert "Semrush" not in fix_text
    assert "see SEO Issues for the site-wide breakdown by type" in fix_text
