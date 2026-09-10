from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, _ctr_decay_pct, add_keyword_gap_slide


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


def test_ctr_tiers_match_spec_exactly():
    assert _ctr_decay_pct(1) == 22.0
    assert _ctr_decay_pct(2) == 13.0
    assert _ctr_decay_pct(3) == 9.0
    assert _ctr_decay_pct(4) == 6.0
    assert _ctr_decay_pct(5) == 4.5
    assert _ctr_decay_pct(6) == 1.5
    assert _ctr_decay_pct(10) == 1.5
    assert _ctr_decay_pct(11) == 0.2
    assert _ctr_decay_pct(20) == 0.2
    assert _ctr_decay_pct(21) == 0.0
    assert _ctr_decay_pct(50) == 0.0


def test_missing_or_not_ranking_position_is_zero_ctr():
    assert _ctr_decay_pct(None) == 0.0
    assert _ctr_decay_pct(0) == 0.0


def test_projected_clicks_rounds_to_whole_integer():
    analysis = {"keyword_gap_rows": [
        {"keyword": "kw1", "competitor_domain": "riv.com", "your_position": 4, "competitor_position": 1, "search_volume": 1000, "keyword_difficulty": 40, "cpc": 2},
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    # position 4 -> 6.0% of 1000 = 60; position 1 -> 22.0% of 1000 = 220
    assert "60" in text
    assert "220" in text


def test_missing_competitor_position_marked_no_data_not_guessed():
    analysis = {"keyword_gap_rows": [
        {"keyword": "kw2", "competitor_domain": "riv.com", "your_position": 8, "competitor_position": None, "search_volume": 500, "keyword_difficulty": 30, "cpc": 1},
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "no data" in text


def test_totals_are_summed_across_all_keywords():
    analysis = {"keyword_gap_rows": [
        {"keyword": "kw1", "competitor_domain": "riv.com", "your_position": 1, "competitor_position": 1, "search_volume": 100, "keyword_difficulty": 10, "cpc": 1},
        {"keyword": "kw2", "competitor_domain": "riv.com", "your_position": 2, "competitor_position": 2, "search_volume": 100, "keyword_difficulty": 10, "cpc": 1},
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    # your total: 22 (pos1@100) + 13 (pos2@100) = 35; competitor same = 35
    assert "35" in text


def test_zero_total_states_negligible_tier_reason():
    analysis = {"keyword_gap_rows": [
        {"keyword": "kw1", "competitor_domain": "riv.com", "your_position": 45, "competitor_position": 30, "search_volume": 1000, "keyword_difficulty": 10, "cpc": 1},
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "negligible" in text.lower()


def test_conservative_assumption_always_stated():
    analysis = {"keyword_gap_rows": [
        {"keyword": "kw1", "competitor_domain": "riv.com", "your_position": 3, "competitor_position": 1, "search_volume": 500, "keyword_difficulty": 10, "cpc": 1},
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "not a claim that real-world traffic is literally zero" in text
