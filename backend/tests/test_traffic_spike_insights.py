from app.reporting.pptx_builder import _traffic_spike_hypothesis

BASE = {"by_channel": [{"label": "Direct", "pct": 81}], "by_country": [{"label": "Singapore", "pct": 72}],
        "by_landing_page": [{"label": "/", "pct": 77}]}


def _bounce_line(lines):
    return next((l for l in lines if l.startswith("Bounce rate")), None)


def test_higher_bounce_flags_lower_engagement_without_bot_claim():
    lines = _traffic_spike_hypothesis({**BASE, "avg_bounce_rate": 0.42, "spike_bounce_rate": 0.71})
    b = _bounce_line(lines)
    assert "71% versus 42%" in b and "+29 percentage points" in b and "lower engagement" in b
    assert not any("bot" in l.lower() for l in lines)


def test_similar_bounce_is_not_flagged():
    b = _bounce_line(_traffic_spike_hypothesis({**BASE, "avg_bounce_rate": 0.42, "spike_bounce_rate": 0.44}))
    assert "does not indicate a clear quality deterioration" in b


def test_missing_spike_bounce_omits_insight_and_missing_baseline_skips_comparison():
    assert _bounce_line(_traffic_spike_hypothesis({**BASE, "avg_bounce_rate": 0.42})) is None
    b = _bounce_line(_traffic_spike_hypothesis({**BASE, "spike_bounce_rate": 0.5}))
    assert "no period baseline" in b and "percentage points" not in b


def test_source_geography_bounce_come_first_and_use_attribution_language():
    lines = _traffic_spike_hypothesis({**BASE, "avg_bounce_rate": 0.4, "spike_bounce_rate": 0.4,
                                       "avg_engagement_rate": 0.5, "spike_engagement_rate": 0.5,
                                       "avg_key_events": 10, "spike_key_events": 10})
    assert lines[0].startswith("Direct accounted for 81%") and lines[1].startswith("Singapore was the dominant")
    assert lines[2].startswith("Bounce rate")
    assert not any(" caused " in l or " drove " in l for l in lines)
