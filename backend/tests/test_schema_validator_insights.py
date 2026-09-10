from pptx import Presentation

from app.reporting.pptx_builder import (
    SLIDE_H, SLIDE_W, add_schema_combined_slide, build_schema_report_parts, schema_eligibility_notes,
)
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


def test_other_pages_marked_na_not_a_content_schema_row():
    sv = aggregate_schema_validation(_pages())
    parts = build_schema_report_parts(sv)
    other = next(r for r in parts["part1"] if r["page_type"] == "Other Pages")
    assert other["recommended_schema"] == "N/A"
    assert not any(r["schema_type"] == "Other Pages" for r in parts["part2"])


def test_content_type_coverage_denominator_is_its_own_page_count_not_total():
    # Part 2's Coverage % denominator must be the pages that type applies
    # to (310 blog pages), never the full site total (~1961 pages).
    sv = aggregate_schema_validation(_pages())
    parts = build_schema_report_parts(sv)
    article_row = next(r for r in parts["part2"] if r["schema_type"] == "Article")
    assert article_row["applicable"] == 310
    product_row = next(r for r in parts["part2"] if r["schema_type"] == "Product")
    assert product_row["applicable"] == 55


def test_baseline_schema_uses_total_pages_denominator():
    sv = aggregate_schema_validation(_pages())
    parts = build_schema_report_parts(sv)
    total_pages = sv["total_pages"]
    website_row = next(r for r in parts["part2"] if r["schema_type"] == "WebSite")
    assert website_row["applicable"] == total_pages
    org_row = next(r for r in parts["part2"] if r["schema_type"] == "Organization")
    assert org_row["applicable"] == total_pages


def test_breadcrumb_applicable_is_content_pages_only_not_other_pages():
    # BreadcrumbList's denominator should be real content pages (blog +
    # product), matching the SNIP reference — not blended with the "Other
    # Pages" utility bucket, and not the full site total either.
    sv = aggregate_schema_validation(_pages())
    parts = build_schema_report_parts(sv)
    breadcrumb_row = next(r for r in parts["part2"] if r["schema_type"] == "BreadcrumbList")
    assert breadcrumb_row["applicable"] == 310 + 55


def test_site_wide_part1_row_present():
    sv = aggregate_schema_validation(_pages())
    parts = build_schema_report_parts(sv)
    site_wide = next(r for r in parts["part1"] if r["page_type"] == "Site-wide")
    assert site_wide["recommended_schema"] == "WebSite, Organization"
    assert site_wide["pages"] == sv["total_pages"]


def test_eligibility_notes_flag_retired_type_only():
    sv = aggregate_schema_validation(_pages(blog_n=0, product_n=0, other_n=50))
    parts = build_schema_report_parts(sv)
    notes = schema_eligibility_notes(parts["part2"])
    assert "Product" not in notes
    assert "WebSite" not in notes


def test_slide_renders_with_no_ai_insights_and_no_part3_section():
    sv = aggregate_schema_validation(_pages())
    slide = add_schema_combined_slide(_prs(), sv, schema_ai_insights=None)
    text = _slide_text(slide)
    assert "Part 1" in text
    assert "Part 2" in text
    assert "KEY INSIGHTS" not in text.upper()


def test_slide_renders_ai_insights_when_provided():
    sv = aggregate_schema_validation(_pages())
    slide = add_schema_combined_slide(_prs(), sv, schema_ai_insights={"insights": ["Article schema missing on all 310 blog pages — Fix: add it to the blog template."]})
    text = _slide_text(slide)
    assert "Fix: add it to the blog template" in text
