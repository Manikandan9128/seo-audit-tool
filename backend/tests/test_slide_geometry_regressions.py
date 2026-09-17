"""Regression tests for the three real bugs found in a 2026-09-17 Geopits
report review, all confirmed live via _audit_slide_geometry: SEO Issues'
fallback classifier leaving Errors empty, the Site Health card's AI Search
Health label overlapping its score ring, and a wrap_cols table (Onboarding
Breakdown) overflowing past the slide's bottom edge. _audit_slide_geometry
itself only logs on a real build_report() call — nothing previously failed
a test or blocked anything when a slide function drifted out of bounds, so
each of these shipped and was only caught by a human reviewing a downloaded
deck. These pin each fix so a future edit that reintroduces the same class
of drift fails here first."""

from pptx import Presentation

from app.reporting import pptx_builder
from app.reporting.pptx_builder import (
    _audit_slide_geometry,
    _draw_table,
    add_seo_issues_slide,
    add_site_health_slide,
)


def test_seo_issues_fallback_surfaces_real_full_crawl_scope():
    # Confirmed live on Geopits (2026-09-17): SEO Issues showed 2 tiny rows
    # from this tool's own ~20-page crawl, while Priority Issues - Page
    # Wise (same client, same generation) read the real Crawled Pages
    # export and listed 1,137 issues across 150 blog pages alone. Same
    # underlying data (site_audit_pages_rows) was already uploaded and
    # already used one slide over — SEO Issues' fallback just never looked
    # at it. User: "priority issues slide has information whereas SEO
    # issues slide shows empty — data problem or did you miss this?"
    # Answer: code gap, not missing data — this note makes the real scope
    # visible instead of letting the two slides silently disagree.
    audit = {"issues": []}
    page_audit = {"pages": [{"issues": ["Missing structured data (JSON-LD)"]} for _ in range(20)]}
    site_audit_pages_rows = [{"page_url": f"https://example.com/p{i}", "issues": 5} for i in range(200)]

    prs = Presentation()
    add_seo_issues_slide(prs, audit, page_audit, site_audit_issues=None, site_audit_pages_rows=site_audit_pages_rows)

    texts = [
        run.text
        for shape in prs.slides[0].shapes
        if shape.has_text_frame
        for para in shape.text_frame.paragraphs
        for run in para.runs
    ]
    scope_note = next((t for t in texts if "full crawl found" in t), None)
    assert scope_note is not None
    assert "200" in scope_note and "1,000" in scope_note
    assert not any("overlap" in i or "out of bounds" in i for i in _audit_slide_geometry(prs))


def test_seo_issues_fallback_classifies_missing_elements_as_errors():
    # No site_audit_issues uploaded -> fallback path. Before the fix, only
    # "not reachable/https/robots/sitemap" counted as Errors, so a page
    # missing structured data or a meta description landed in Warnings and
    # Errors came up completely empty (confirmed live: Geopits report (5)).
    audit = {"issues": []}
    page_audit = {
        "pages": [
            {"issues": ["Missing structured data (JSON-LD)", "Missing meta description"]},
            {"issues": ["Missing structured data (JSON-LD)"]},
        ]
    }
    prs = Presentation()
    add_seo_issues_slide(prs, audit, page_audit)

    texts = [
        run.text
        for shape in prs.slides[0].shapes
        if shape.has_text_frame
        for para in shape.text_frame.paragraphs
        for run in para.runs
    ]
    errors_header = next((t for t in texts if t.startswith("Errors (")), None)
    assert errors_header is not None, f"expected a non-empty Errors column, got: {texts}"
    assert "Errors (0)" not in texts


def test_site_health_ai_search_health_label_does_not_overlap_score_ring():
    # AI Search Health text was hardcoded at a fixed y written before the
    # score ring above it grew to its current diameter (drifted apart
    # 2026-08-27 vs 2026-08-31, never reconciled until this fix).
    prs = Presentation()
    audit = {"url": "example.com"}
    site_audit_overview = {"site_health_pct": 72, "ai_search_health_pct": 82}
    site_audit_pages_rows = [{"page_url": "https://example.com/"}]
    add_site_health_slide(prs, audit, site_audit_overview, site_audit_pages_rows)

    issues = _audit_slide_geometry(prs)
    assert not any("overlap" in i for i in issues), issues


def test_wrap_cols_table_does_not_overflow_slide_bottom():
    # Long cells in every wrap_cols column (Onboarding Breakdown's real
    # shape) used to grow row height with no ceiling, pushing the table
    # 0.9in past the slide's bottom edge (confirmed live: Geopits report (5)).
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    long_cell = (
        "A long directional suggestion cell describing exactly what to change "
        "on the landing page and why it matters for conversion, long enough to "
        "wrap several times over at this column width in a real report."
    )
    rows = [(long_cell, long_cell, long_cell) for _ in range(5)]
    _draw_table(
        slide, ["Bias", "Where It Shows Up", "Directional Suggestion"], rows,
        top=1_143_000,  # 1.25in in EMU
        col_widths=[2.6, 3.6, 5.9], row_height=0.6, wrap_cols={0, 1, 2},
    )

    issues = _audit_slide_geometry(prs)
    assert not any("out of bounds" in i for i in issues), issues


def test_backlink_profile_prefers_domain_overview_total_over_csv_row_cap():
    from app.reporting.pptx_builder import add_backlink_profile_slide

    # row_count (10,000) is a round number matching Semrush's CSV export-tier
    # cap, not the site's real backlink total (confirmed live: Lumber report,
    # 10,000 vs a real 33,800 from that domain's Domain Overview upload) —
    # this is why the slide was pulled from the report entirely on
    # 2026-09-08. domain_overview_backlinks_total should win over row_count
    # whenever both are available and no PDF summary was uploaded.
    prs = Presentation()
    add_backlink_profile_slide(
        prs, backlink_rows=[], row_count=10_000, backlink_summary=None,
        own_domain_rating=45, domain_overview_backlinks_total=33_800,
    )
    texts = [
        run.text
        for shape in prs.slides[0].shapes
        if shape.has_text_frame
        for para in shape.text_frame.paragraphs
        for run in para.runs
    ]
    assert "33,800" in texts
    assert "10,000" not in texts


def test_seo_issues_insights_section_does_not_overlap_footer():
    # Confirmed live on Geopits (2026-09-17): a real AI-generated headline +
    # 4 supporting bullets + takeaway advanced y by a flat per-item guess
    # with no check against the footer's fixed position, landing the
    # takeaway line on top of the "{client} · {domain}" footer text.
    pptx_builder._theme["footer"] = "Geopits  ·  www.geopits.com"
    try:
        audit = {"issues": []}
        site_audit_issues = [
            {"issue": f"Real error type {i}", "issue_type": "ERROR", "failed_checks": 10 + i}
            for i in range(6)
        ]
        insights_ai = {
            "headline": (
                "A long, realistic AI-generated headline summarizing the dominant pattern across every "
                "error and warning found on this crawl, written the way the real prompt actually produces it."
            ),
            "supporting": [
                "A long supporting bullet point restating one specific finding with enough detail to wrap "
                "across more than one line at this column width, same as real AI output regularly does.",
                "Another long supporting bullet, similarly detailed, about a second distinct finding from "
                "the same crawl, also long enough to wrap onto a second line here.",
                "A third bullet, again realistically long, covering yet another distinct issue category "
                "found across the site's crawled pages.",
                "A fourth and final bullet, just as long as the others, rounding out the maximum four "
                "supporting points this section ever renders.",
            ],
            "takeaway": (
                "The majority of problems stem from missing structured data and oversized title tags "
                "across the site's most heavily trafficked page templates."
            ),
        }

        prs = Presentation()
        add_seo_issues_slide(prs, audit, page_audit=None, site_audit_issues=site_audit_issues, insights_ai=insights_ai)

        issues = _audit_slide_geometry(prs)
        assert not any("overlap" in i for i in issues), issues
    finally:
        pptx_builder._theme["footer"] = ""
