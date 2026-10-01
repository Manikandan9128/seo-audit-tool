from app.reporting.pptx_builder import _content_seo_eligible_rows
from app.services.technical_seo_service import _commerce_signals_present, aggregate_schema_validation


def _page(url, html_commerce, types=("Organization", "WebSite")):
    meta = {"schema_types_found": list(types), "schema_field_issues": []}
    if html_commerce is not None:
        meta["commerce_signals"] = html_commerce
    return {"url": url, "meta": meta}


def _bucket_pages(result, page_type):
    return next((b["pages"] for b in result["by_page_type"] if b["page_type"] == page_type), 0)


def test_commerce_signal_detection():
    assert _commerce_signals_present('<button>Add to Cart</button>')
    assert _commerce_signals_present('<span class="price">$5</span>')
    assert not _commerce_signals_present('<a href="/demo">Book a demo</a><div class="pricing-hero">')


def test_saas_product_url_is_not_a_product_page():
    pages = [_page("https://x.com/product/payroll", False), _page("https://x.com/product/hr", False)]
    result = aggregate_schema_validation(pages)
    assert _bucket_pages(result, "Product") == 0
    assert _bucket_pages(result, "Other Pages") == 2


def test_shop_product_url_with_price_stays_product():
    result = aggregate_schema_validation([_page("https://x.com/product/shoe", True)])
    assert _bucket_pages(result, "Product") == 1


def test_old_crawl_job_without_commerce_field_keeps_url_behaviour():
    result = aggregate_schema_validation([_page("https://x.com/product/payroll", None)])
    assert _bucket_pages(result, "Product") == 1


def test_competitor_only_adjacent_keyword_is_dropped_but_ranking_one_kept():
    rows = [
        {"keyword": "employee benefits", "search_volume": 201000, "cluster": "HR", "source": "Keyword Gap",
         "relevance_status": "Adjacent / Potential"},
        {"keyword": "minimum wage memphis", "search_volume": 70, "cluster": "HR", "source": "Keyword Gap, Organic Positions",
         "relevance_status": "Adjacent / Potential", "position": 25},
        {"keyword": "construction payroll software", "search_volume": 1300, "cluster": "Payroll", "source": "Keyword Gap",
         "relevance_status": "Core Relevant"},
    ]
    kept = [r["keyword"] for r in _content_seo_eligible_rows(rows)]
    assert kept == ["minimum wage memphis", "construction payroll software"]


def test_guard_never_empties_the_slide():
    rows = [{"keyword": "a b", "search_volume": 10, "cluster": "X", "source": "Keyword Gap", "relevance_status": "Adjacent / Potential"}]
    assert len(_content_seo_eligible_rows(rows)) == 1


def test_client_safe_insight_strips_internal_scaffolding():
    from app.services.structured_data_insights_service import client_safe_insight
    raw = ("ISSUE: FAQPage status unconfirmed. EVIDENCE: flagged with ELIGIBILITY_CHECK_STALE, "
           "so not verified. ACTION: Fix: re-validate.")
    out = client_safe_insight(raw)
    for banned in ("ISSUE:", "EVIDENCE:", "ACTION:", "ELIGIBILITY_CHECK_STALE"):
        assert banned not in out
    assert "re-validate" in out


def test_programmatic_slide_has_no_internal_wording():
    from pptx import Presentation
    from app.reporting.pptx_builder import add_programmatic_seo_slide
    rows = [{"keyword": k, "search_volume": v, "cluster": "Federal Minimum Wage"} for k, v in (
        ("federal minimum wage", 9000), ("federal minimum wage 2026", 5000),
        ("minimum wage usa", 4000), ("minimum wage poster", 3000), ("minimum wage history", 2500))]
    slide = add_programmatic_seo_slide(Presentation(), rows)
    text = " ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame) if slide else ""
    assert "SERP not validated" not in text and "folded in" not in text
