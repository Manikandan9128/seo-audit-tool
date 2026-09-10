from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, _schema_validator_insights, add_schema_combined_slide
from app.services.technical_seo_service import aggregate_schema_validation


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


def _pages(blog_n=310, product_n=55, other_n=1596):
    pages = []
    for i in range(blog_n):
        pages.append({"url": f"https://x.com/blog/post-{i}", "meta": {"schema_types_found": [], "schema_field_issues": []}})
    for i in range(product_n):
        pages.append({"url": f"https://x.com/product/item-{i}", "meta": {"schema_types_found": [], "schema_field_issues": []}})
    for i in range(other_n):
        pages.append({"url": f"https://x.com/page-{i}", "meta": {"schema_types_found": ["WebSite", "Organization"], "schema_field_issues": []}})
    return pages


def test_missing_types_no_longer_duplicates_by_page_type_rows():
    # 2026-09-10 spec: the same 0%-coverage fact (Article/Product schema
    # entirely missing) must not appear both as a by_page_type row AND as
    # a separate "entire type missing" Finding — only truly sitewide types
    # (never bucketed by page type) belong in missing_types.
    sv = aggregate_schema_validation(_pages())
    missing_type_names = {m["type"] for m in sv["missing_types"]}
    by_page_type_names = {r["page_type"] for r in sv["by_page_type"]}
    assert missing_type_names.isdisjoint(by_page_type_names)
    assert "BreadcrumbList" in missing_type_names


def test_overall_coverage_excludes_baseline_only_other_pages():
    analytics = {"top_pages": {"rows": [{"path": "/blog/post-0", "page_views": 672}, {"path": "/product/item-0", "page_views": 405}]}}
    sv = aggregate_schema_validation(_pages(), analytics)
    insights = _schema_validator_insights(sv)
    assert insights
    assert "0%" in insights[0]
    assert "not blended" in insights[0]


def test_biggest_gap_names_highest_pageviews_page_type():
    analytics = {"top_pages": {"rows": [{"path": "/blog/post-0", "page_views": 672}, {"path": "/product/item-0", "page_views": 405}]}}
    sv = aggregate_schema_validation(_pages(), analytics)
    insights = _schema_validator_insights(sv)
    gap_line = next(i for i in insights if i.startswith("Biggest gap"))
    assert "Article-type" in gap_line
    assert "672" in gap_line


def test_no_content_pages_returns_no_insights():
    sv = aggregate_schema_validation(_pages(blog_n=0, product_n=0, other_n=50))
    assert _schema_validator_insights(sv) == []


def test_slide_renders_without_duplicate_finding_row():
    # "Article-type" legitimately appears twice — once as the coverage
    # table's own row, once in the "Biggest gap" insight bullet naming it.
    # What must NOT happen is a THIRD appearance as its own row in the
    # separate "Schema Validator Findings" table below (the old duplicate:
    # the same 0%-coverage fact restated as "(entire type missing)").
    analytics = {"top_pages": {"rows": [{"path": "/blog/post-0", "page_views": 672}, {"path": "/product/item-0", "page_views": 405}]}}
    sv = aggregate_schema_validation(_pages(), analytics)
    slide = add_schema_combined_slide(_prs(), sv)
    text = _slide_text(slide)
    assert text.count("Article-type") == 2
    assert "Product\n(entire type missing)" not in text
    assert "Article-type\n(entire type missing)" not in text
