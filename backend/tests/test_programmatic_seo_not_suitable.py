from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_programmatic_seo_slide


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


def test_states_not_suitable_when_every_cluster_fails_demand_gate():
    # Universal SEO Audit Engine spec (2026-09-20) section 27: "Not
    # suitable for programmatic SEO" must be an explicit stated finding,
    # not a silently-omitted slide, when real clusters existed but none
    # cleared eligibility.
    rows = [
        {"keyword": "small topic a", "search_volume": 10, "cluster": "Small Topic"},
        {"keyword": "small topic b", "search_volume": 10, "cluster": "Small Topic"},
    ]
    slide = add_programmatic_seo_slide(_prs(), rows)
    assert slide is not None
    text = _slide_text(slide)
    assert "Not suitable for programmatic SEO" in text
    assert "search volume" in text.lower()


def test_states_not_suitable_when_no_cluster_has_enough_distinct_subpages():
    rows = [
        {"keyword": "widget", "search_volume": 500, "cluster": "Widget"},
        {"keyword": "widget", "search_volume": 400, "cluster": "Widget"},  # duplicate keyword, no new sub-intent
    ]
    slide = add_programmatic_seo_slide(_prs(), rows)
    assert slide is not None
    text = _slide_text(slide)
    assert "Not suitable for programmatic SEO" in text


def test_returns_none_when_there_is_no_clustered_keyword_data_at_all():
    # No real clusters at all (not even a failed candidate) — nothing was
    # evaluated, so this stays a silent omission, not a false "Not
    # suitable" claim about data that was never assessed.
    rows = [{"keyword": "kw", "search_volume": 500}]  # no "cluster" key
    assert add_programmatic_seo_slide(_prs(), rows) is None
    assert add_programmatic_seo_slide(_prs(), None) is None


def test_renders_real_opportunities_when_eligible():
    rows = [
        {"keyword": "widget pricing calculator", "search_volume": 1000, "cluster": "Widgets"},
        {"keyword": "widget installation guide", "search_volume": 900, "cluster": "Widgets"},
        {"keyword": "widget maintenance tips", "search_volume": 800, "cluster": "Widgets"},
        {"keyword": "widget troubleshooting steps", "search_volume": 700, "cluster": "Widgets"},
    ]
    slide = add_programmatic_seo_slide(_prs(), rows)
    text = _slide_text(slide)
    assert "Not suitable" not in text
    assert "Widgets" in text
