from pptx import Presentation

from app.reporting.pptx_builder import (
    SLIDE_H, SLIDE_W, add_branded_vs_nonbranded_slide,
    add_search_opportunities_pages_slide, add_search_opportunities_countries_slide,
    build_branded_dependency_narrative, build_branded_vs_nonbranded_comparison,
    build_high_potential_countries, build_high_potential_pages, build_search_opportunity_pages,
    _audit_slide_geometry,
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


def test_high_potential_page_flagged_for_ranking_lever_below_page_one():
    pages = [{"page": "https://x.com/a", "impressions": 500, "clicks": 5, "ctr": 0.01, "position": 12.0}]
    flagged = build_high_potential_pages(pages)
    assert len(flagged) == 1
    assert flagged[0]["lever"] == "RANKING"
    assert "isn't ranking on page 1" in flagged[0]["fix"]
    assert "title/meta rewrite won't move clicks" in flagged[0]["fix"]


def test_high_potential_page_flagged_for_low_ctr_in_band():
    # Position 5 (band 3-10%), CTR way under at 0.5% — should flag on CTR
    # even though position 5 isn't in the 8-20 "near page 1" range.
    pages = [{"page": "https://x.com/b", "impressions": 1000, "clicks": 5, "ctr": 0.005, "position": 5.0}]
    flagged = build_high_potential_pages(pages)
    assert len(flagged) == 1
    assert flagged[0]["lever"] == "CTR"
    assert "click-through-rate gap" in flagged[0]["fix"]
    assert "source:" in flagged[0]["fix"]


def test_page_below_impression_floor_excluded():
    pages = [{"page": "https://x.com/c", "impressions": 10, "clicks": 0, "ctr": 0.0, "position": 15.0}]
    assert build_high_potential_pages(pages) == []


def test_page_lever_never_glues_ctr_and_ranking():
    # Position 12 (RANKING territory) with a terrible CTR too — must state
    # only the RANKING fix, never a CTR rewrite glued onto it.
    pages = [{"page": "https://x.com/z", "impressions": 500, "clicks": 1, "ctr": 0.002, "position": 12.0}]
    flagged = build_high_potential_pages(pages)
    assert flagged[0]["lever"] == "RANKING"
    assert "Rewrite title/meta" not in flagged[0]["fix"]


def test_country_benchmark_never_cites_another_countrys_raw_ctr():
    # Old behavior benchmarked every country against the single best-CTR
    # peer row; must now cite an aggregate/external source, never a bare
    # peer country CTR comparison like "vs France's 9.0%".
    countries = [
        {"country": "usa", "clicks": 50, "impressions": 1500, "ctr": 50 / 1500},
        {"country": "fra", "clicks": 20, "impressions": 1000, "ctr": 90 / 1000},
    ]
    result = build_high_potential_countries(countries)
    for r in result["material"]:
        assert "source:" in r["fix"] or "outside the client's stated target markets" in r["fix"] or "checking query-level breakdown" in r["fix"]
        assert "vs France" not in r["fix"] and "vs United States" not in r["fix"]


def test_page_with_no_flag_reason_not_included():
    # Position 2 (not 8-20), CTR well within its 15-30% band — no flag.
    pages = [{"page": "https://x.com/d", "impressions": 1000, "clicks": 200, "ctr": 0.20, "position": 2.0}]
    assert build_high_potential_pages(pages) == []


def test_high_potential_country_material_vs_low_signal_split():
    # USA clears the 1,000-impression material bar; India has real
    # impressions but a single-digit click count — must land in the
    # low-signal group, never as an equal-weight material row.
    countries = [
        {"country": "usa", "clicks": 50, "impressions": 1500, "ctr": 50 / 1500, "position": 8.0},
        {"country": "ind", "clicks": 1, "impressions": 200, "ctr": 1 / 200, "position": 9.0},
    ]
    result = build_high_potential_countries(countries)
    material_codes = {r["country"] for r in result["material"]}
    assert "United States" in material_codes
    assert "India" not in material_codes
    assert result["material"][0]["fix"]  # every material row carries a specific fix
    assert "India" in result["low_signal"]["countries"]
    assert "sample too small to act on" in result["low_signal"]["summary"]


def test_country_below_low_signal_impression_floor_excluded_entirely():
    countries = [{"country": "fra", "clicks": 0, "impressions": 5, "ctr": 0.0, "position": 20.0}]
    result = build_high_potential_countries(countries)
    assert result == {"material": [], "low_signal": None}


def test_country_code_resolved_to_full_name_not_raw_acronym():
    # Regression: the old code rendered raw alpha-3 codes like "ARM"
    # unexplained on the slide — every displayed country must be a full
    # name (or the low-signal group, never a bare 3-letter code).
    countries = [
        {"country": "usa", "clicks": 1687, "impressions": 261348, "ctr": 1687 / 261348, "position": 8.0},
        {"country": "arm", "clicks": 7, "impressions": 110, "ctr": 7 / 110, "position": 5.0},
    ]
    result = build_high_potential_countries(countries)
    assert result["material"][0]["country"] == "United States"
    assert "Armenia" in result["low_signal"]["countries"]
    assert "ARM" not in result["material"][0]["fix"]


def test_best_ctr_benchmark_never_drawn_from_a_low_signal_country():
    # Regression: confirmed live on a real report — Ecuador (2 clicks, 1
    # of them lucky) had the highest CTR of any country and got used as
    # the benchmark every material country was told to match, even though
    # Ecuador itself sits in the low-signal group. The benchmark must come
    # from the material pool only.
    countries = [
        {"country": "usa", "clicks": 1687, "impressions": 261348, "ctr": 1687 / 261348},
        {"country": "gbr", "clicks": 19, "impressions": 12857, "ctr": 19 / 12857},
        {"country": "ecu", "clicks": 2, "impressions": 150, "ctr": 2 / 150},  # 1.3% CTR, low-signal, would win on CTR alone
    ]
    result = build_high_potential_countries(countries)
    material_fixes = " ".join(r["fix"] for r in result["material"])
    assert "Ecuador" not in material_fixes
    assert "Ecuador" in result["low_signal"]["countries"]


def test_branded_slide_states_headline_share():
    comparison = build_branded_vs_nonbranded_comparison(
        [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}],
        [{"query": "generic", "clicks": 20, "impressions": 800, "position": 12.0}],
    )
    slide = add_branded_vs_nonbranded_slide(_prs(), comparison, None, None, "Google Search Console")
    text = _slide_text(slide)
    assert "80.0%" in text


def test_branded_dependency_headline_flags_minimal_nonbranded_discovery():
    branded = [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}]
    nonbranded = [{"query": "generic thing", "clicks": 20, "impressions": 5000, "position": 12.0, "ctr": 20 / 5000}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    narrative = build_branded_dependency_narrative(comparison, nonbranded)
    assert "non-branded discovery is minimal" in narrative["headline"]


def test_demand_gap_estimate_states_assumption_and_number():
    # Position 12 -> benchmark 0.2% (the strict decay tier); non-branded CTR
    # here is 0% — a real gap the estimate must quantify.
    nonbranded = [{"query": "generic thing", "clicks": 0, "impressions": 5000, "position": 12.0, "ctr": 0.0}]
    branded = [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    narrative = build_branded_dependency_narrative(comparison, nonbranded, period_days=30)
    gap = narrative["demand_gap"]
    assert gap is not None
    assert "Estimate:" in gap["text"]
    assert "assumes" in gap["text"]
    assert gap["extra_clicks_monthly"] > 0


def test_demand_gap_absent_when_nonbranded_already_meets_benchmark():
    # Position 1 -> benchmark 22%; 50% CTR already clears it, no gap to model.
    nonbranded = [{"query": "generic thing", "clicks": 100, "impressions": 200, "position": 1.0, "ctr": 0.5}]
    branded = [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0, "ctr": 0.4}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    narrative = build_branded_dependency_narrative(comparison, nonbranded)
    assert narrative["demand_gap"] is None


def test_concrete_example_names_a_real_nonbranded_query():
    nonbranded = [
        {"query": "big opportunity term", "clicks": 1, "impressions": 4000, "position": 12.0, "ctr": 1 / 4000},
        {"query": "small term", "clicks": 0, "impressions": 60, "position": 12.0, "ctr": 0.0},
    ]
    branded = [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    narrative = build_branded_dependency_narrative(comparison, nonbranded)
    example = narrative["concrete_example"]
    assert example is not None
    assert example["query"] == "big opportunity term"  # highest impressions wins
    assert "Estimate:" in example["text"]


def test_cost_of_inaction_names_competitor_overlap_when_available():
    nonbranded = [{"query": "shared term", "clicks": 5, "impressions": 1000, "position": 8.0, "ctr": 0.005}]
    branded = [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    competitor_positions = {"rival.com": [{"keyword": "shared term", "position": 3}]}
    narrative = build_branded_dependency_narrative(comparison, nonbranded, competitor_positions)
    assert "rival.com" in narrative["cost_of_inaction"]
    assert "shared term" in narrative["cost_of_inaction"]


def test_outcome_case_slide_has_all_five_sections():
    branded = [{"query": "brand", "clicks": 80, "impressions": 200, "position": 1.0}]
    nonbranded = [{"query": "generic thing", "clicks": 5, "impressions": 5000, "position": 12.0, "ctr": 5 / 5000}]
    comparison = build_branded_vs_nonbranded_comparison(branded, nonbranded)
    narrative = build_branded_dependency_narrative(comparison, nonbranded)
    ai_insights = {"insights": ["Branded dependency is high.", "Recommend building non-branded content."]}
    slide = add_branded_vs_nonbranded_slide(_prs(), comparison, narrative, ai_insights, "Google Search Console")
    text = _slide_text(slide)
    assert "non-branded discovery is minimal" in text or "branded search" in text  # headline
    assert "The Gap in Demand Terms" in text.upper() or "GAP IN DEMAND TERMS" in text.upper()
    assert "Cost of Inaction".upper() in text.upper()
    assert "One Concrete Example".upper() in text.upper()
    assert "Key Insights".upper() in text.upper()


def test_pages_slide_absent_when_no_pages_flagged():
    # Split slides (2026-09-11 user spec) — each is silently absent on its
    # own empty data, no cross-slide "no countries met..." placeholder.
    assert add_search_opportunities_pages_slide(_prs(), [], "Google Search Console") is None


def test_countries_slide_absent_when_nothing_flagged():
    assert add_search_opportunities_countries_slide(_prs(), {}, "Google Search Console") is None


def test_search_opportunity_pages_excludes_outside_4_to_10_band():
    pages = [
        {"page": "https://x.com/a", "impressions": 500, "ctr": 0.01, "position": 2.0},  # top-3, out of band
        {"page": "https://x.com/b", "impressions": 500, "ctr": 0.01, "position": 12.0},  # below page 1, out of band
    ]
    assert build_search_opportunity_pages(pages) == []


def test_search_opportunity_pages_flags_real_ctr_gap_in_band():
    pages = [{"page": "https://x.com/hydraulic-lifts", "impressions": 1000, "ctr": 0.01, "position": 6.0}]
    flagged = build_search_opportunity_pages(pages)
    assert len(flagged) == 1
    assert flagged[0]["position"] == 6.0
    assert "internal" in flagged[0]["recommended_action"]  # labeled internal, never an external/industry standard
    assert flagged[0]["priority"] == "High"


def test_search_opportunity_pages_excludes_row_already_meeting_band():
    pages = [{"page": "https://x.com/c", "impressions": 1000, "ctr": 0.08, "position": 6.0}]
    assert build_search_opportunity_pages(pages) == []


def test_search_opportunity_pages_recommendation_uses_driving_query_when_title_doesnt_cover_it():
    # 2026-09-20 spec: the Recommended Action must cite a real GSC (page,
    # query) row, never a keyword guessed from the URL slug.
    pages = [{"page": "https://x.com/hydraulic-lifts", "impressions": 1000, "ctr": 0.01, "position": 6.0}]
    crawled = [{"url": "https://x.com/hydraulic-lifts", "meta": {"title": "Home - Acme Corp"}}]
    page_query_rows = [
        {"page": "https://x.com/hydraulic-lifts", "query": "hydraulic lifts", "impressions": 800, "clicks": 8, "ctr": 0.01, "position": 5.5},
    ]
    brand_tokens = {"acme"}
    flagged = build_search_opportunity_pages(pages, crawled, page_query_rows=page_query_rows, brand_tokens=brand_tokens)
    action = flagged[0]["recommended_action"]
    assert "hydraulic lifts" in action
    assert "doesn't lead with" in action
    assert flagged[0]["driving_query"] == "hydraulic lifts"


def test_search_opportunity_pages_states_insufficient_when_no_query_data():
    pages = [{"page": "https://x.com/12345", "impressions": 1000, "ctr": 0.01, "position": 6.0}]
    flagged = build_search_opportunity_pages(pages)
    assert "No qualifying non-brand query found" in flagged[0]["recommended_action"]
    assert flagged[0]["driving_query"] is None


def test_search_opportunity_pages_excludes_page_driven_only_by_branded_query():
    # 2026-09-20 spec: a page whose real query breakdown shows the top
    # query is BRANDED doesn't qualify as an incremental non-brand SEO
    # opportunity, even though it clears the page-level CTR-gap gate.
    pages = [{"page": "https://x.com/about", "impressions": 1000, "ctr": 0.01, "position": 6.0}]
    page_query_rows = [
        {"page": "https://x.com/about", "query": "acme corp", "impressions": 900, "clicks": 9, "ctr": 0.01, "position": 5.0},
    ]
    brand_tokens = {"acme"}
    flagged = build_search_opportunity_pages(pages, page_query_rows=page_query_rows, brand_tokens=brand_tokens)
    assert flagged == []


def test_search_opportunity_pages_keeps_page_with_mixed_brand_and_nonbrand_queries():
    pages = [{"page": "https://x.com/about", "impressions": 1000, "ctr": 0.01, "position": 6.0}]
    page_query_rows = [
        {"page": "https://x.com/about", "query": "acme corp", "impressions": 50, "clicks": 5, "ctr": 0.01, "position": 5.0},
        {"page": "https://x.com/about", "query": "company overview", "impressions": 900, "clicks": 9, "ctr": 0.01, "position": 6.0},
    ]
    brand_tokens = {"acme"}
    flagged = build_search_opportunity_pages(pages, page_query_rows=page_query_rows, brand_tokens=brand_tokens)
    assert len(flagged) == 1
    assert flagged[0]["driving_query"] == "company overview"
    assert "acme corp" not in flagged[0]["recommended_action"]


def test_search_opportunity_pages_sorted_by_estimated_opportunity_clicks():
    pages = [
        {"page": "https://x.com/small", "impressions": 100, "ctr": 0.01, "position": 6.0},
        {"page": "https://x.com/big", "impressions": 5000, "ctr": 0.01, "position": 6.0},
    ]
    flagged = build_search_opportunity_pages(pages)
    assert flagged[0]["page"] == "https://x.com/big"
    assert flagged[0]["opportunity_clicks"] > flagged[1]["opportunity_clicks"]


def test_search_opportunity_pages_slide_recommended_action_not_truncated_to_one_line():
    # Regression: confirmed live on the BharatBenz regen (2026-09-18) — 9
    # rows of real 2-line Recommended Action text made _draw_table's
    # row-height auto-shrink collapse every row to a single truncated
    # line, cutting the page-specific half of every sentence off. row_cap
    # is now 6 (not 9) specifically so this doesn't happen at realistic
    # sentence lengths.
    pages = [
        {"page": f"https://www.bharatbenz.com/trucks/tipper-trucks-{i}", "impressions": 99931 - i * 1000, "ctr": 0.012, "position": 4.3}
        for i in range(9)
    ]
    flagged = build_search_opportunity_pages(pages)
    slide = add_search_opportunities_pages_slide(_prs(), flagged, "Google Search Console")
    for shape in slide.shapes:
        if shape.has_table:
            for i, row in enumerate(shape.table.rows):
                if i == 0:
                    continue
                text = row.cells[4].text_frame.text
                assert not text.endswith("…"), f"row {i} truncated: {text!r}"
                assert text.endswith("."), f"row {i} incomplete: {text!r}"


def test_search_opportunity_pages_slide_page_column_is_a_real_hyperlink():
    # 2026-09-19 user spec: a viewer must be able to click the Page column
    # straight through to the real page, not copy-paste a truncated URL.
    pages = [{"page": "https://www.bharatbenz.com/trucks/tipper-trucks-1", "impressions": 5000, "ctr": 0.01, "position": 4.5}]
    flagged = build_search_opportunity_pages(pages)
    slide = add_search_opportunities_pages_slide(_prs(), flagged, "Google Search Console")
    table = next(s for s in slide.shapes if s.has_table).table
    run = table.cell(1, 0).text_frame.paragraphs[0].runs[0]
    assert run.hyperlink.address == "https://www.bharatbenz.com/trucks/tipper-trucks-1"


def test_search_opportunity_pages_slide_no_overlap_worst_case():
    # Standing no-overlap/fit-to-page rule: max flagged rows, long slugs
    # (long Recommended Action text), no crawled title for any of them —
    # the worst case for table row growth + Key Insights strip height.
    pages = [
        {
            "page": f"https://example.com/very-long-descriptive-category-slug-{i}/hydraulic-lift-industrial-equipment",
            "impressions": 5000 - i * 100, "ctr": 0.01, "position": 4.0 + (i % 6),
        }
        for i in range(12)
    ]
    flagged = build_search_opportunity_pages(pages)
    prs = _prs()
    add_search_opportunities_pages_slide(prs, flagged, "Google Search Console")
    assert _audit_slide_geometry(prs) == []


def test_countries_slide_low_signal_line_never_a_table_row():
    high_countries = build_high_potential_countries([
        {"country": "chn", "clicks": 1, "impressions": 300, "ctr": 1 / 300, "position": 10.0},
    ])
    slide = add_search_opportunities_countries_slide(_prs(), high_countries, "Google Search Console")
    text = _slide_text(slide)
    assert "No countries met the material-opportunity bar" in text  # material empty, only low-signal qualifies
    assert "Low-Signal, Monitor Only" in text
    assert "China" in text
    for shape in slide.shapes:
        if shape.has_table:
            table_text = "\n".join(c.text_frame.text for row in shape.table.rows for c in row.cells)
            assert "China" not in table_text  # never rendered as a table row
