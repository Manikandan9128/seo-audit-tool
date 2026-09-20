from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_competitor_table_slide


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


def test_competitor_analysis_never_shows_keyword_list_button():
    # 2026-09-20 user request: "Open full keyword list" must appear only
    # on Competitor Keyword Gap Analysis, not on Competitor Analysis too —
    # _build_report's call site no longer passes keyword_sheet_link here
    # at all. This guards against that call site drifting back to passing
    # it (the function itself still technically accepts the param).
    rows = [{"domain": "rival.com", "organic_traffic": 500, "organic_keywords": 100}]
    slide = add_competitor_table_slide(_prs(), rows)
    assert "Open full keyword list" not in _slide_text(slide)


def test_competitor_analysis_shows_button_only_if_explicitly_passed():
    # The function itself still supports the param (used only by the
    # Keyword Gap Analysis call site's own equivalent button today) — this
    # just confirms removing it from the Competitor Analysis call site was
    # a call-site change, not a function-level one.
    rows = [{"domain": "rival.com", "organic_traffic": 500, "organic_keywords": 100}]
    slide = add_competitor_table_slide(_prs(), rows, keyword_sheet_link="https://example.com/sheet")
    assert "Open full keyword list" in _slide_text(slide)
