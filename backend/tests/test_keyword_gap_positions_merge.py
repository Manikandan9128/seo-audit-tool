"""Regression test for the 2026-09-18 report: Target Keywords clustering
was only ever reading the client's own Keyword Gap rows — a small
comparison-against-competitors export — and completely ignoring the
Organic Positions upload (BharatBenz: 3,000+ rows never surfaced), so
keywords the client already ranks well for on their own never made it
into the keyword clusters. _merge_keyword_gap_and_positions combines both
own-site sources into one deduped list feeding clustering, keeping
everything downstream (relevance filter, clustering, Primary/Secondary
labeling, flat-table fallback) untouched."""

from app.api.routes.site_audit import _merge_keyword_gap_and_positions


def test_keywords_unique_to_organic_positions_are_included():
    gap_rows = [{"keyword": "widget insurance", "search_volume": 500, "cluster": "Insurance"}]
    positions_rows = [{"keyword": "widget warranty", "search_volume": 300, "position": 4, "url": "https://client.com/warranty"}]
    merged = _merge_keyword_gap_and_positions(gap_rows, positions_rows)
    keywords = {r["keyword"] for r in merged}
    assert keywords == {"widget insurance", "widget warranty"}


def test_duplicate_keyword_kept_once():
    gap_rows = [{"keyword": "widget insurance", "search_volume": 500, "cluster": "Insurance"}]
    positions_rows = [{"keyword": "widget insurance", "search_volume": 900, "position": 3, "url": "https://client.com/insurance"}]
    merged = _merge_keyword_gap_and_positions(gap_rows, positions_rows)
    assert len(merged) == 1


def test_duplicate_prefers_organic_positions_search_volume_and_position():
    gap_rows = [{"keyword": "widget insurance", "search_volume": 500, "keyword_difficulty": 20, "cluster": "Insurance", "intent": "Commercial"}]
    positions_rows = [{"keyword": "widget insurance", "search_volume": 900, "position": 3, "url": "https://client.com/insurance"}]
    merged = _merge_keyword_gap_and_positions(gap_rows, positions_rows)
    row = merged[0]
    # Organic Positions' numbers win for what it directly measures.
    assert row["search_volume"] == 900
    assert row["position"] == 3
    assert row["url"] == "https://client.com/insurance"
    # Keyword Gap fields Organic Positions never carries are preserved —
    # downstream clustering/labeling untouched.
    assert row["cluster"] == "Insurance"
    assert row["intent"] == "Commercial"
    assert row["keyword_difficulty"] == 20


def test_duplicate_keyword_matching_is_case_insensitive():
    gap_rows = [{"keyword": "Widget Insurance", "search_volume": 500}]
    positions_rows = [{"keyword": "widget insurance", "search_volume": 900, "position": 3}]
    merged = _merge_keyword_gap_and_positions(gap_rows, positions_rows)
    assert len(merged) == 1
    assert merged[0]["search_volume"] == 900


def test_empty_organic_positions_leaves_keyword_gap_rows_unchanged():
    gap_rows = [{"keyword": "widget insurance", "search_volume": 500, "cluster": "Insurance"}]
    merged = _merge_keyword_gap_and_positions(gap_rows, [])
    assert merged == gap_rows


def test_empty_keyword_gap_uses_organic_positions_alone():
    positions_rows = [{"keyword": "widget warranty", "search_volume": 300, "position": 4, "url": "https://client.com/warranty"}]
    merged = _merge_keyword_gap_and_positions([], positions_rows)
    assert merged == positions_rows


def test_rows_without_a_keyword_are_skipped():
    gap_rows = [{"keyword": "", "search_volume": 500}, {"keyword": "real keyword", "search_volume": 100}]
    positions_rows = [{"keyword": None, "search_volume": 900}]
    merged = _merge_keyword_gap_and_positions(gap_rows, positions_rows)
    assert [r["keyword"] for r in merged] == ["real keyword"]


def test_gsc_only_query_becomes_new_row_without_invented_search_volume():
    # Universal SEO Audit Engine spec (2026-09-20) sections 1-3: GSC demand
    # evidence is added as its own row when no Semrush source has it —
    # search_volume must stay unset, never backfilled from impressions/clicks.
    gsc_rows = [{"query": "widget maintenance tips", "clicks": 40, "impressions": 900, "position": 6.2, "ctr": 0.044}]
    merged = _merge_keyword_gap_and_positions([], [], gsc_rows)
    assert len(merged) == 1
    row = merged[0]
    assert row["keyword"] == "widget maintenance tips"
    assert row["search_volume"] is None
    assert row["gsc_clicks"] == 40
    assert row["gsc_impressions"] == 900
    assert row["source"] == "GSC"


def test_gsc_query_matching_existing_keyword_only_adds_evidence_fields():
    gap_rows = [{"keyword": "widget insurance", "search_volume": 500, "cluster": "Insurance"}]
    gsc_rows = [{"query": "widget insurance", "clicks": 12, "impressions": 300, "position": 8.1}]
    merged = _merge_keyword_gap_and_positions(gap_rows, [], gsc_rows)
    assert len(merged) == 1
    row = merged[0]
    # Semrush's own search_volume/cluster are never overwritten by GSC.
    assert row["search_volume"] == 500
    assert row["cluster"] == "Insurance"
    assert row["gsc_clicks"] == 12
    assert "GSC" in row["source"]


def test_gsc_query_with_no_keyword_text_is_skipped():
    gsc_rows = [{"query": "", "clicks": 10}, {"query": None, "clicks": 5}]
    merged = _merge_keyword_gap_and_positions([], [], gsc_rows)
    assert merged == []
