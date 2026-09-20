from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_priority_issues_page_wise_slide, _page_wise_group_insights


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


def _other_row(path: str, issue_count: int, page_views: int = 0):
    # Matches _tech_fixes_scored_rows' "other" tuple shape:
    # (severity_rank, -score, issue_text, path, fix_text, page_views, category)
    fix_text = (
        f"{issue_count} issues detected on this page by the Site Audit crawl — "
        "review the individual failed checks for this URL and prioritize by severity."
    )
    return (2, -page_views, f"{issue_count} issues", path, fix_text, page_views, "other")


def test_group_insight_states_only_counts_no_invented_cause():
    rows = [_other_row("/blog/a", 10), _other_row("/blog/b", 5)]
    insights = _page_wise_group_insights(rows)
    blog_insight = next(i for i in insights if "/blog/*" in i)
    assert "2 blog page(s)" in blog_insight
    assert "15 combined Site Audit issues" in blog_insight
    # Must not guess a root cause or claim an unsupported outcome.
    assert "BlogPosting" not in blog_insight
    assert "suppress" not in blog_insight.lower()
    assert "crawl budget" not in blog_insight.lower()


def test_top_page_insight_has_no_unsupported_claim():
    rows = [_other_row("/x", 3), _other_row("/event-images.php", 97)]
    insights = _page_wise_group_insights(rows)
    top_insight = next(i for i in insights if "event-images" in i)
    assert "97" in top_insight
    assert "highest issue count" in top_insight


def test_additional_pages_count_excludes_shown_rows():
    # 2026-09-20 fix: "additional pages" must be the total minus the 9
    # actually shown, not the full total again (which double-counted the
    # shown rows as "additional" on top of themselves).
    rows = [_other_row(f"/p{i}", 1) for i in range(12)]
    slide = add_priority_issues_page_wise_slide(_prs(), rows, ai_result=None)
    text = _slide_text(slide)
    assert "3 additional page" in text
    assert "12 additional page" not in text


def test_fix_column_never_repeats_source_or_guesses_page_type():
    rows = [_other_row("/blog/some-post", 22)]
    slide = add_priority_issues_page_wise_slide(_prs(), rows, ai_result=None)
    text = _slide_text(slide)
    assert "Semrush" not in text.replace("Source: Semrush Site Audit", "")
    assert "audit meta tags" not in text.lower()
    assert "review the individual failed checks for this URL" in text
