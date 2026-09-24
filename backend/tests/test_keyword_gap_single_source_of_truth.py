"""Competitor Keyword Gap — single source of truth (2026-09-24 spec): every
slide and Core Problem must read counts computed ONCE from the same
validated dataset (_prepare_keyword_gap_rows + keyword_gap_by_category),
never a separately/earlier-computed number. See docs comment on
semrush_analysis_service's "type": "keyword_gap" issue entries and
site_audit.py's competitor_gap_findings splice."""

from app.reporting.pptx_builder import _prepare_keyword_gap_rows, build_keyword_gap_summary_finding, keyword_gap_by_category


def _row(keyword, volume, category, kd=30, relevance=None, competitors=None):
    return {
        "keyword": keyword, "search_volume": volume, "gap_category": category, "keyword_difficulty": kd,
        "relevance": relevance, "competitor_positions": competitors or [{"competitor": "rival.com", "position": 5}],
    }


def test_keyword_gap_by_category_buckets_and_counts_sum_to_total():
    rows = [_row("a", 100, "Missing"), _row("b", 200, "Missing"), _row("c", 50, "Shared"), _row("d", 10, "Untapped")]
    by_category, counts = keyword_gap_by_category(rows)
    assert counts == {"Missing": 2, "Shared": 1, "Untapped": 1}
    assert sum(counts.values()) == len(rows)
    assert [r["keyword"] for r in by_category["Missing"]] == ["a", "b"]


def test_keyword_gap_by_category_defaults_unset_category_to_missing():
    rows = [_row("no category", 100, None)]
    by_category, counts = keyword_gap_by_category(rows)
    assert counts["Missing"] == 1


def test_build_keyword_gap_summary_finding_matches_the_same_counts():
    rows = [_row("a", 1000, "Missing"), _row("b", 500, "Shared"), _row("c", 200, "Untapped")]
    by_category, counts = keyword_gap_by_category(rows)
    finding = build_keyword_gap_summary_finding(rows, counts, off_topic_count=5)
    assert finding["type"] == "keyword_gap"
    assert "3 relevant keyword gap" in finding["summary"]
    assert "1 shared" in finding["summary"] and "1 missing" in finding["summary"] and "1 untapped" in finding["summary"]
    assert "1,700" in finding["summary"]  # 1000+500+200 combined volume
    assert "5 off-topic" in finding["summary"]
    assert "\"a\"" in finding["detail"]  # highest-volume row named


def test_build_keyword_gap_summary_finding_is_none_when_nothing_relevant():
    assert build_keyword_gap_summary_finding([], {"Missing": 0, "Shared": 0, "Untapped": 0}, 0) is None


def test_relevance_and_kd_filtering_produce_the_exact_same_count_everywhere():
    """Regression guard for the reported bug: Core Problem showing a larger
    total (e.g. 229) than the Keyword Gap Executive Summary (e.g. 200)
    because Core Problem read an earlier, less-filtered count. Both must
    now derive from _prepare_keyword_gap_rows, so they can't diverge."""
    raw_rows = [
        _row("relevant missing", 1000, "Missing", kd=40, relevance="highly_relevant"),
        _row("relevant shared", 500, "Shared", kd=50, relevance="highly_relevant"),
        _row("ambiguous - should be excluded", 900, "Missing", kd=30, relevance="potentially_relevant"),
        _row("kd unavailable - should be excluded", 800, "Missing", kd=None, relevance="highly_relevant"),
        _row("zero volume - should be excluded", 0, "Missing", kd=20, relevance="highly_relevant"),
    ]
    kd_filtered, _ambiguous, _kd_unavailable, _cols = _prepare_keyword_gap_rows(raw_rows)
    by_category, counts = keyword_gap_by_category(kd_filtered)
    finding = build_keyword_gap_summary_finding(kd_filtered, counts, off_topic_count=0)

    # The slide-facing total (what add_keyword_gap_executive_summary_slide
    # would show) and the Core-Problem-facing finding both come from the
    # same kd_filtered — 2 real rows, not 5.
    slide_total_relevant = len(kd_filtered)
    assert slide_total_relevant == 2
    assert "2 relevant keyword gap" in finding["summary"]
    assert counts["Missing"] + counts["Shared"] + counts["Untapped"] == slide_total_relevant == 2
