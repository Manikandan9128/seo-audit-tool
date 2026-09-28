from pptx import Presentation

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_keyword_research_slide


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


def _insights_text(rows) -> str:
    slides = add_keyword_research_slide(_prs(), rows)
    return _slide_text(slides[0])


def test_cluster_insight_carries_only_client_facing_items():
    # 2026-09-28 Target Keywords client-facing output filter: opportunity,
    # demand, strongest keyword, intent, page call — never the page-format
    # split/validation reasoning behind the cluster.
    rows = [
        {"keyword": "what is certified payroll", "cluster": "C", "search_volume": 500, "page_category": "Blog / Guide", "intent": "Informational"},
        {"keyword": "how does certified payroll work", "cluster": "C", "search_volume": 300, "page_category": "Blog / Guide", "intent": "Informational"},
        {"keyword": "certified payroll software", "cluster": "C", "search_volume": 900, "page_category": "Landing Page", "intent": "Commercial", "keyword_difficulty": 22},
        {"keyword": "certified payroll pricing", "cluster": "C", "search_volume": 200, "page_category": "Landing Page", "intent": "Commercial"},
    ]
    text = _insights_text(rows)
    assert "4 keyword(s) with 1,900 combined monthly searches" in text
    assert 'Strongest keyword: "certified payroll software" — 900 searches/month, KD 22.' in text
    assert "Search intent:" in text
    for banned in ("mixed page formats", "consistent format", "Recommended format", "Grouped by", "Confidence", "Avg. KD", "CPC"):
        assert banned not in text


def test_strong_existing_page_match_is_an_existing_page_opportunity():
    rows = [
        {"keyword": "certified payroll software", "cluster": "C", "search_volume": 900, "page_category": "Landing Page",
         "existing_page_url": "https://x.com/payroll", "existing_page_match_strength": "strong"},
        {"keyword": "certified payroll pricing", "cluster": "C", "search_volume": 200, "page_category": "Landing Page",
         "existing_page_url": "https://x.com/payroll", "existing_page_match_strength": "strong"},
    ]
    assert "Existing page opportunity: https://x.com/payroll" in _insights_text(rows)


def test_partial_or_weak_match_never_claims_the_existing_page():
    for strength in ("partial", "weak"):
        rows = [
            {"keyword": "certified payroll software", "cluster": "C", "search_volume": 900, "page_category": "Landing Page",
             "existing_page_url": "https://x.com/blog/payroll", "existing_page_match_strength": strength},
        ]
        text = _insights_text(rows)
        assert "Existing page is a partial match; a dedicated page may be required." in text
        assert "https://x.com/blog/payroll" not in text


def test_no_matching_page_is_a_new_page_opportunity():
    rows = [
        {"keyword": "certified payroll software", "cluster": "C", "search_volume": 900, "page_category": "Landing Page",
         "existing_page_url": None, "existing_page_match_strength": "none"},
    ]
    assert "New page opportunity" in _insights_text(rows)


def test_no_page_category_data_omits_validation_bullet():
    rows = [{"keyword": "some keyword", "cluster": "C", "search_volume": 500}]
    slides = add_keyword_research_slide(_prs(), rows)
    text = _slide_text(slides[0])
    assert "Cluster validation" not in text


def test_unclustered_bucket_gets_a_distinguishing_subheading_not_bare_title():
    # Regression (2026-09-19 live report): when real clustering DID run and
    # produced several real clusters PLUS a leftover unclustered bucket
    # (cluster == ""), that bucket's slide rendered as a bare "Target
    # Keywords" title — identical to, and easily confused with, the
    # separate no-clustering-ran-at-all fallback — even though every other
    # slide in the same deck has a real subheading.
    rows = [
        {"keyword": "clustered kw", "cluster": "Real Cluster", "search_volume": 900},
        {"keyword": "leftover kw one", "cluster": "", "search_volume": 500},
        {"keyword": "leftover kw two", "cluster": "", "search_volume": 400},
    ]
    slides = add_keyword_research_slide(_prs(), rows)
    all_text = [_slide_text(s) for s in slides]
    unclustered_slide_text = next(t for t in all_text if "leftover kw one" in t)
    assert "Other / Ungrouped Keywords" in unclustered_slide_text
    assert "Real Cluster" not in unclustered_slide_text
