from app.reporting.pptx_builder import _channel_breakdown_date_span


def test_channel_breakdown_span_uses_its_own_wider_window():
    date_range = {
        "ga4_start": "2026-08-12", "ga4_end": "2026-09-11",
        "channel_breakdown_start": "2026-05-14", "channel_breakdown_end": "2026-09-11",
    }
    span = _channel_breakdown_date_span(date_range)
    assert span == "May 14, 2026 – Sep 11, 2026"
    assert span != "Aug 12, 2026 – Sep 11, 2026"  # never falls back to the 30-day ga4 window


def test_channel_breakdown_span_empty_when_keys_missing():
    assert _channel_breakdown_date_span({"ga4_start": "2026-08-12", "ga4_end": "2026-09-11"}) == ""
    assert _channel_breakdown_date_span(None) == ""
