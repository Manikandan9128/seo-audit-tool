from pptx import Presentation

from app.reporting.pptx_builder import (
    SLIDE_H, SLIDE_W, _combined_page_fix, _issue_noun, _page_level_issue_records,
    _page_wise_group_insights, _page_wise_priority_rows, _structured_data_fix_text,
    add_priority_issues_page_wise_slide,
)


def _prs():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def _slide_text(slide) -> str:
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            parts.append(shape.text_frame.text)
        elif shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    parts.append(cell.text_frame.text)
    return "\n".join(parts)


def _row(path: str, issue_count: int, page_views: int = 0, severity_rank: int = 2) -> dict:
    return {
        "path": path, "page_views": page_views, "score": page_views, "severity_rank": severity_rank,
        "issue_names": [f"Issue {i}" for i in range(issue_count)],
        "fix_text": f"{issue_count} confirmed fix(es) for {path}.",
        "confirmed_issue_count": issue_count,
    }


def test_issue_noun_singular_plural():
    assert _issue_noun(1) == "1 issue"
    assert _issue_noun(0) == "0 issues"
    assert _issue_noun(38) == "38 issues"


def test_group_insight_states_only_counts_no_invented_cause():
    rows = [_row("/blog/a", 10), _row("/blog/b", 5)]
    insights = _page_wise_group_insights(rows)
    blog_insight = next(i for i in insights if "/blog/*" in i)
    assert "2 blog page(s)" in blog_insight
    assert "15 combined confirmed SEO issues" in blog_insight
    # Must not guess a root cause or claim an unsupported outcome.
    assert "BlogPosting" not in blog_insight
    assert "suppress" not in blog_insight.lower()
    assert "crawl budget" not in blog_insight.lower()


def test_top_page_insight_has_no_unsupported_claim():
    rows = [_row("/x", 3), _row("/event-images.php", 97)]
    insights = _page_wise_group_insights(rows)
    top_insight = next(i for i in insights if "event-images" in i)
    assert "97" in top_insight
    assert "highest confirmed issue count" in top_insight


def test_additional_pages_count_excludes_shown_rows():
    # 2026-09-20 fix: "additional pages" must be the total minus the 9
    # actually shown, not the full total again (which double-counted the
    # shown rows as "additional" on top of themselves).
    rows = [_row(f"/p{i}", 1) for i in range(12)]
    slide = add_priority_issues_page_wise_slide(_prs(), rows)
    text = _slide_text(slide)
    assert "3 additional page" in text
    assert "12 additional page" not in text


def test_fix_column_never_repeats_source_or_guesses_page_type():
    rows = [_row("/blog/some-post", 22)]
    slide = add_priority_issues_page_wise_slide(_prs(), rows)
    text = _slide_text(slide)
    assert "Semrush" not in text
    assert "audit meta tags" not in text.lower()


def test_combined_page_fix_uses_real_issue_evidence_not_generic_text():
    fixes = [
        "Fix the broken link/redirect, or add a 301 redirect to a working page.",
        "Add a unique, keyword-relevant <title> tag (50-60 characters).",
    ]
    text = _combined_page_fix(fixes, total_issue_count=2)
    assert "broken link" in text
    assert "unique, keyword-relevant" in text
    # 2026-09-20 spec section 32's exact banned phrasing must never appear.
    assert "review the individual failed checks" not in text.lower()
    assert "optimize this page" not in text.lower()
    assert "improve seo" not in text.lower()


def test_combined_page_fix_caps_shown_items_and_names_the_remainder():
    fixes = [f"Fix number {i}." for i in range(5)]
    text = _combined_page_fix(fixes, total_issue_count=5)
    assert "Fix number 0." in text and "Fix number 2." in text
    assert "Fix number 3." not in text
    assert "2 more issue" in text


def test_crawled_sample_pages_get_a_confirmed_priority_row():
    # 2026-09-22 spec: a page this tool's own crawl actually found real
    # issues on must get ONE combined, evidence-based row on Priority
    # Issues - Page Wise, naming the real confirmed issue types.
    page_audit = {
        "pages": [
            {
                "url": "https://example.com/trucks",
                "issues": ["Missing <title> tag", "Missing meta description", "No <h1> tag found"],
            },
        ],
    }
    rows = _page_wise_priority_rows(page_audit, analytics=None)
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == "/trucks"
    assert row["confirmed_issue_count"] == 3
    assert row["issue_names"] == ["Missing <title> tag", "Missing meta description", "No <h1> tag found"]
    assert "Add a unique, keyword-relevant" in row["fix_text"]
    assert "Write a unique meta description" in row["fix_text"]


def test_semrush_count_only_export_never_produces_a_page_wise_row():
    # 2026-09-22 spec rule 4: a bare Semrush issue count (no issue names)
    # must never become a page-wise row — the whole page-wise pipeline no
    # longer reads site_audit_pages_rows at all.
    page_audit = {"pages": []}
    rows = _page_wise_priority_rows(page_audit, analytics=None)
    assert rows == []


def test_page_with_no_confirmed_issues_never_appears():
    page_audit = {"pages": [{"url": "https://example.com/about", "issues": []}]}
    rows = _page_wise_priority_rows(page_audit, analytics=None)
    assert rows == []


def test_excluded_path_is_dropped_even_with_confirmed_issues():
    page_audit = {"pages": [{"url": "https://example.com/careers", "issues": ["Missing <title> tag"]}]}
    rows = _page_wise_priority_rows(page_audit, analytics=None, exclude_paths={"/careers"})
    assert rows == []


def test_priority_sorts_by_severity_then_traffic_then_count_not_count_alone():
    # 2026-09-22 spec rule 2: a page with many low-severity issues must not
    # automatically outrank a page with fewer but more severe ones.
    page_audit = {
        "pages": [
            {"url": "https://example.com/low-severity-many", "issues": ["Missing canonical tag"] * 1},
            {"url": "https://example.com/error-page", "issues": ["Page not reachable"]},
        ],
    }
    rows = _page_wise_priority_rows(page_audit, analytics=None)
    assert rows[0]["path"] == "/error-page"  # error severity beats info severity regardless of count


def test_structured_data_fix_names_a_real_schema_type_not_a_generic_list():
    # 2026-09-22 spec rule 6: never "Add Article, Product, FAQ, etc. schema."
    text = _structured_data_fix_text("https://example.com/blog/some-post")
    assert "Article" in text
    assert "FAQ, etc" not in text


def test_structured_data_fix_falls_back_to_breadcrumblist_when_type_unknown():
    text = _structured_data_fix_text("https://example.com/about")
    assert "BreadcrumbList" in text
    assert "FAQ, etc" not in text


def test_structured_data_issue_overridden_with_page_type_specific_fix():
    page_audit = {"pages": [{"url": "https://example.com/products/widget", "issues": ["Missing structured data (JSON-LD)"]}]}
    by_path = _page_level_issue_records(page_audit, analytics=None)
    fix_text = by_path["/products/widget"]["items"][0]["fix_text"]
    assert "Product" in fix_text
    assert "FAQ, etc" not in fix_text
