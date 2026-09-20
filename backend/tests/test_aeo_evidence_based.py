from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_aeo_slide


def _prs():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def _slide_text(slide) -> str:
    parts = []
    for shape in slide.shapes:
        if shape.has_text_frame and shape.text_frame.text.strip():
            parts.append(shape.text_frame.text)
    return "\n".join(parts)


def _pages(faq_n=13, other_n=810):
    pages = []
    for i in range(faq_n):
        pages.append({"url": f"https://x.com/faq/{i}", "meta": {"schema_types_found": [], "schema_field_issues": []}})
    for i in range(other_n):
        pages.append({"url": f"https://x.com/page-{i}", "meta": {"schema_types_found": ["WebSite", "Organization"], "schema_field_issues": []}})
    return pages


def test_cites_real_faq_missing_count_when_schema_validation_available():
    from app.services.technical_seo_service import aggregate_schema_validation
    sv = aggregate_schema_validation(_pages())
    slide = add_aeo_slide(_prs(), None, None, sv)
    text = _slide_text(slide)
    assert "FAQPage structured data is missing on 13 of 13 applicable page(s)" in text
    assert "not a guarantee of inclusion" in text
    # Old generic, evidence-free bullet must not also appear alongside real data.
    assert "Add structured FAQ sections to key pages" not in text


def test_confirmed_win_when_faq_schema_fully_present():
    pages = [{"url": f"https://x.com/faq/{i}", "meta": {"schema_types_found": ["FAQPage"], "schema_field_issues": []}} for i in range(5)]
    from app.services.technical_seo_service import aggregate_schema_validation
    sv = aggregate_schema_validation(pages)
    slide = add_aeo_slide(_prs(), None, None, sv)
    text = _slide_text(slide)
    assert "already present on all 5 applicable page(s)" in text


def test_falls_back_to_generic_bullets_when_no_schema_validation():
    slide = add_aeo_slide(_prs(), None, None, None)
    text = _slide_text(slide)
    assert "Add structured FAQ sections to key pages" in text
