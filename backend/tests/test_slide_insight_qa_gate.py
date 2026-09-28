from app.reporting.pptx_builder import _validate_slide_insights


def test_drops_insight_naming_a_query_outside_shown_rows():
    shown = [{"query": "lumberfi loans"}, {"query": "lumberfi reviews"}]
    insights = [
        'Raw: 100 clicks / 1,000 impressions (10.0% CTR) across all branded queries.',
        '"lumberfy" (5 clicks / 40 impressions, 12.5% CTR) is a recruitment query.',
    ]
    validated, removed = _validate_slide_insights(insights, shown, "query")
    assert validated == [insights[0]]
    assert len(removed) == 1
    assert "lumberfy" in removed[0]


def test_keeps_insight_naming_a_row_that_is_shown():
    shown = [{"query": "lumberfi loans"}, {"query": "lumberfi reviews"}]
    insights = ['"lumberfi loans" (10 clicks / 100 impressions, 10.0% CTR) is the top query.']
    validated, removed = _validate_slide_insights(insights, shown, "query")
    assert validated == insights
    assert removed == []


def test_no_quoted_name_always_passes():
    shown = [{"query": "lumberfi loans"}]
    insights = ["No new-vs-returning data available for this channel."]
    validated, removed = _validate_slide_insights(insights, shown, "query")
    assert validated == insights
    assert removed == []


def test_client_facing_insights_drops_internal_process_and_duplicates():
    # 2026-09-28 KEY INSIGHTS — FINAL DEDUPLICATION + client-facing filter.
    from app.reporting.pptx_builder import client_facing_insights

    out = client_facing_insights([
        "20 keywords with 5,000 combined monthly searches. Grouped by shared entity and intent.",
        "20 keywords, 5,000 combined monthly searches.",
        "This is a high-priority opportunity.",
        "Keywords with unclear relevance to your business: \"x\".",
        "Not targeted: 3 job searches — kept for review.",
        "Strongest keyword: \"payroll software\" — 900 searches/month.",
    ])
    assert out == [
        "20 keywords with 5,000 combined monthly searches.",
        "Strongest keyword: \"payroll software\" — 900 searches/month.",
    ]
