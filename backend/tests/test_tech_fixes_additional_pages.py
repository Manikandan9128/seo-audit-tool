from app.reporting.pptx_builder import _issue_noun, _tech_fixes_scored_rows


def test_semrush_only_row_fix_text_does_not_repeat_the_source_header():
    # 2026-09-10 user request: the slide already states "Source: Semrush
    # Site Audit" once, in the header - the per-row Fix cell shouldn't
    # repeat "review it in Semrush's Site Audit" on every single row.
    # 2026-09-20 spec: the Fix must be grounded only in what's actually
    # known (an issue count, no issue names) — no page-type-guessed
    # checklist, no "(Semrush)" tag beside the count.
    page_audit = {"pages": []}
    site_audit_pages_rows = [{"page_url": "https://example.com/careers", "issues": 12}]

    rows = _tech_fixes_scored_rows(page_audit, analytics=None, site_audit_pages_rows=site_audit_pages_rows)

    assert len(rows) == 1
    issue_text, fix_text = rows[0][2], rows[0][4]
    assert "Semrush" not in fix_text
    assert issue_text == "12 issues"
    assert "issue(s)" not in issue_text
    # 2026-09-20 spec section 32 bans "review the individual failed
    # checks" as unacceptable generic text; section 34's own preferred
    # wording for genuinely insufficient issue-level evidence is used
    # instead.
    assert "Specific fix requires issue-level validation" in fix_text


def test_issue_noun_singular_plural():
    assert _issue_noun(1) == "1 issue"
    assert _issue_noun(0) == "0 issues"
    assert _issue_noun(38) == "38 issues"


def test_semrush_row_excludes_non_200_status_as_optimization_target():
    # 2026-09-21 spec rule 6: never recommend optimizing a 404/redirected
    # URL — it isn't a valid page to fix title/meta/content on.
    page_audit = {"pages": []}
    site_audit_pages_rows = [
        {"page_url": "https://example.com/gone", "issues": 5, "http_status_code": "404"},
        {"page_url": "https://example.com/moved", "issues": 5, "http_status_code": "301"},
        {"page_url": "https://example.com/ok", "issues": 5, "http_status_code": "200"},
        {"page_url": "https://example.com/unknown", "issues": 5},  # no status column — unknown, still allowed
    ]
    rows = _tech_fixes_scored_rows(page_audit, analytics=None, site_audit_pages_rows=site_audit_pages_rows)
    paths = {r[3] for r in rows}
    assert paths == {"/ok", "/unknown"}
