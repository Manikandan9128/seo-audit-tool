from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_conversion_seo_next_steps_slide


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


def test_states_tracking_data_required_when_no_evidence_at_all():
    # Universal SEO Audit Engine spec (2026-09-20) section 39: no manual UX
    # pass and no backlink/funnel data must state "Tracking data required"
    # explicitly, not silently omit the slide.
    slide = add_conversion_seo_next_steps_slide(_prs(), None, 0)
    assert slide is not None
    text = _slide_text(slide)
    assert "Tracking data required" in text


def test_renders_real_content_when_backlink_data_available():
    slide = add_conversion_seo_next_steps_slide(_prs(), None, 500)
    text = _slide_text(slide)
    assert "Tracking data required" not in text
    assert "500" in text
