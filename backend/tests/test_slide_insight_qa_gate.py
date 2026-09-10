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
