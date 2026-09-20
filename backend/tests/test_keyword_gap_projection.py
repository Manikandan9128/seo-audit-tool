from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, _audit_slide_geometry, _ctr_decay_pct, add_keyword_gap_slide


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


def _gap_row(keyword, volume, kd, your_position=None, your_url=None, competitors=None, gap_category=None):
    return {
        "keyword": keyword, "search_volume": volume, "keyword_difficulty": kd,
        "your_position": your_position, "your_url": your_url,
        "competitor_positions": competitors or [],
        "gap_category": gap_category,
    }


def test_not_ranking_is_exact_string_no_dash_no_blank_url():
    analysis = {"keyword_gap_rows": [
        _gap_row("kw1", 1000, 40, your_position=None, gap_category="Untapped"),
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "Not ranking" in text
    assert "Not ranking —" not in text
    assert "Not ranking -" not in text


def test_position_and_url_shown_together_when_ranking():
    analysis = {"keyword_gap_rows": [
        _gap_row(
            "kw1", 1000, 40, your_position=None,
            competitors=[{"competitor": "rival.com", "position": 7, "ranking_url": "https://rival.com/payroll-compliance"}],
            gap_category="Missing",
        ),
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "#7 · /payroll-compliance" in text


def test_same_competitor_column_mapping_across_rows():
    # rival.com ranks on both rows, leader.com only on the second — the
    # column for rival.com must be the same column on every row, and a row
    # where a tracked competitor doesn't rank shows "Not ranking" in that
    # domain's own column rather than shifting columns.
    analysis = {"keyword_gap_rows": [
        _gap_row("kw1", 1000, 40, competitors=[{"competitor": "rival.com", "position": 5, "ranking_url": None}], gap_category="Missing"),
        _gap_row(
            "kw2", 900, 30,
            competitors=[
                {"competitor": "rival.com", "position": 9, "ranking_url": None},
                {"competitor": "leader.com", "position": 2, "ranking_url": None},
            ],
            gap_category="Missing",
        ),
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    table = next(s for s in slide.shapes if s.has_table).table
    headers = [c.text_frame.text for c in table.rows[0].cells]
    rival_col = headers.index("rival.com")
    leader_col = headers.index("leader.com")
    row1 = [c.text_frame.text for c in table.rows[1].cells]
    row2 = [c.text_frame.text for c in table.rows[2].cells]
    assert row1[rival_col] == "#5"
    assert row1[leader_col] == "Not ranking"
    assert row2[rival_col] == "#9"
    assert row2[leader_col] == "#2"


def test_kd_above_max_is_excluded():
    analysis = {"keyword_gap_rows": [
        _gap_row("too hard", 1000, 95, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Missing"),
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    assert slide is None


def test_kd_unavailable_flagged_separately():
    analysis = {"keyword_gap_rows": [
        _gap_row("no kd data", 1000, None, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Missing"),
    ]}
    slide = add_keyword_gap_slide(_prs(), analysis)
    assert slide is None


def test_ambiguous_relevance_excluded_from_table_with_review_note():
    rows = [
        _gap_row("clearly relevant", 1000, 40, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Missing"),
        _gap_row("unsure keyword", 500, 30, competitors=[{"competitor": "rival.com", "position": 4, "ranking_url": None}], gap_category="Missing"),
    ]
    rows[1]["relevance"] = "potentially_relevant"
    analysis = {"keyword_gap_rows": rows}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "clearly relevant" in text
    assert "unsure keyword" not in text.split("KEY INSIGHTS")[0]
    assert "manual relevance review" in text
    assert "unsure keyword" in text


def test_all_three_categories_shown_even_when_missing_dominates_volume():
    # Regression (2026-09-19 live report): a client ranking-weak against
    # its competitors can have Missing keywords fill every high-volume
    # slot, silently pushing every real Untapped/Shared example off the
    # slide even though the legend advertises all three. 20 high-volume
    # Missing rows plus one lower-volume row each of Untapped/Shared —
    # both must still appear, not just Missing.
    rows = [
        _gap_row(f"missing {i}", 10000 - i, 30, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Missing")
        for i in range(20)
    ]
    rows.append(_gap_row("untapped kw", 50, 20, competitors=[], gap_category="Untapped"))
    rows.append(_gap_row("shared kw", 40, 20, your_position=6, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Shared"))
    analysis = {"keyword_gap_rows": rows}
    slide = add_keyword_gap_slide(_prs(), analysis)
    text = _slide_text(slide)
    assert "untapped kw" in text.split("KEY INSIGHTS")[0]
    assert "shared kw" in text.split("KEY INSIGHTS")[0]


def test_all_categories_worst_case_fits_slide_no_overlap():
    # Standing no-overlap/fit-to-page rule — worst case for the new
    # per-category row selection: the max row count (4 per category x 3),
    # 3 competitor columns, long wrapping URLs, and the keyword-sheet-link
    # button all present at once. Caught a real overflow during development
    # at 5 rows/category (table ran 0.22in past the slide edge) — 4 is the
    # safe cap.
    competitors = [
        {"competitor": "rivalonecompany.com", "position": 3, "ranking_url": "https://rivalonecompany.com/very-long-descriptive-category-slug/product-page"},
        {"competitor": "competitortwo.com", "position": 5, "ranking_url": "https://competitortwo.com/another-long-slug/product"},
        {"competitor": "thirdrivalbrand.com", "position": 8, "ranking_url": "https://thirdrivalbrand.com/yet-another-long-path/here"},
    ]
    rows = []
    for i in range(4):
        rows.append(_gap_row(f"missing keyword number {i} long tail phrase", 5000 - i, 30, competitors=competitors, gap_category="Missing"))
    for i in range(4):
        rows.append(_gap_row(f"untapped keyword number {i} long tail phrase", 4000 - i, 30, gap_category="Untapped"))
    for i in range(4):
        rows.append(_gap_row(
            f"shared keyword number {i} long tail phrase", 3000 - i, 30,
            your_position=6, your_url="https://example.com/very-long-descriptive-category-slug/product-page",
            competitors=competitors, gap_category="Shared",
        ))
    analysis = {"keyword_gap_rows": rows, "keyword_gap_off_topic_count": 3}
    prs = _prs()
    add_keyword_gap_slide(prs, analysis, client_name="Acme Trucks", keyword_gap_sheet_link="https://sheets.google.com/x")
    assert _audit_slide_geometry(prs) == []


def test_off_topic_count_and_split_stated_in_insights():
    analysis = {
        "keyword_gap_rows": [
            _gap_row("shared kw", 1000, 40, your_position=5, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Shared"),
            _gap_row("missing kw", 800, 40, competitors=[{"competitor": "rival.com", "position": 3, "ranking_url": None}], gap_category="Missing"),
            _gap_row("untapped kw", 300, 40, competitors=[], gap_category="Untapped"),
        ],
        "keyword_gap_off_topic_count": 4,
    }
    slide = add_keyword_gap_slide(_prs(), analysis, client_name="Acme")
    text = _slide_text(slide)
    assert "4 off-topic excluded" in text
    assert "unrelated to Acme's business" in text
    assert "3 relevant keyword" in text
    assert "1 Shared / 1 Missing / 1 Untapped" in text
