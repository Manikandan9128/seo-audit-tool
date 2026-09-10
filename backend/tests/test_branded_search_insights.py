from pptx import Presentation

from app.reporting.pptx_builder import (
    SLIDE_H, SLIDE_W, add_branded_vs_nonbranded_slide, add_search_opportunities_slide,
    build_branded_vs_nonbranded_comparison, build_high_potential_countries, build_high_potential_pages,
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


def test_branded_share_of_clicks_is_headline_number():
    branded = [{"query": "lumberfi", "clicks": 80, "impressions": 200, "position": 1.0}]
    nonbranded = [{"query": "construction payroll", "clicks": 20, "impressions": 800, "position": 12.0}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    assert comparison["branded_share_pct"] == 80.0
    assert comparison["branded"]["clicks"] == 80
    assert comparison["nonbranded"]["clicks"] == 20


def test_high_potential_page_flagged_for_near_page_one_position():
    pages = [{"page": "https://x.com/a", "impressions": 500, "clicks": 5, "ctr": 0.01, "position": 12.0}]
    flagged = build_high_potential_pages(pages)
    assert len(flagged) == 1
    assert "page 1" in flagged[0]["opportunity"]


def test_high_potential_page_flagged_for_low_ctr_in_band():
    # Position 5 (band 3-10%), CTR way under at 0.5% — should flag on CTR
    # even though position 5 isn't in the 8-20 "near page 1" range.
    pages = [{"page": "https://x.com/b", "impressions": 1000, "clicks": 5, "ctr": 0.005, "position": 5.0}]
    flagged = build_high_potential_pages(pages)
    assert len(flagged) == 1
    assert "CTR" in flagged[0]["opportunity"]


def test_page_below_impression_floor_excluded():
    pages = [{"page": "https://x.com/c", "impressions": 10, "clicks": 0, "ctr": 0.0, "position": 15.0}]
    assert build_high_potential_pages(pages) == []


def test_page_with_no_flag_reason_not_included():
    # Position 2 (not 8-20), CTR well within its 15-30% band — no flag.
    pages = [{"page": "https://x.com/d", "impressions": 1000, "clicks": 200, "ctr": 0.20, "position": 2.0}]
    assert build_high_potential_pages(pages) == []


def test_high_potential_country_flagged_for_near_zero_clicks():
    countries = [
        {"country": "usa", "clicks": 50, "impressions": 500, "ctr": 0.10, "position": 8.0},
        {"country": "ind", "clicks": 0, "impressions": 100, "ctr": 0.0, "position": 9.0},
    ]
    flagged = build_high_potential_countries(countries)
    ind = next(r for r in flagged if r["country"] == "IND")
    assert "near-zero clicks" in ind["opportunity"]


def test_country_below_impression_floor_excluded():
    countries = [{"country": "fra", "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 20.0}]
    assert build_high_potential_countries(countries) == []


def test_branded_slide_states_headline_share():
    comparison = build_branded_vs_nonbranded_comparison(
        [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}],
        [{"query": "generic", "clicks": 20, "impressions": 800, "position": 12.0}],
    )
    slide = add_branded_vs_nonbranded_slide(_prs(), comparison, None, "Google Search Console")
    text = _slide_text(slide)
    assert "80.0%" in text


def test_opportunities_slide_says_so_when_nothing_flagged():
    slide = add_search_opportunities_slide(_prs(), [], [], "Google Search Console")
    assert slide is None  # both empty -> no slide at all, matches "silently absent" convention


def test_opportunities_slide_explicit_message_when_only_one_side_empty():
    high_pages = build_high_potential_pages([{"page": "https://x.com/e", "impressions": 500, "clicks": 5, "ctr": 0.01, "position": 12.0}])
    slide = add_search_opportunities_slide(_prs(), high_pages, [], "Google Search Console")
    text = _slide_text(slide)
    assert "No countries met the high-potential bar" in text
