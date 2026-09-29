"""Website Performance — 2026-09-24 spec: slides 2-5 (Score Breakdown, PSI
Opportunities & Diagnostics, JavaScript Bundle Breakdown, Script Weight
Breakdown) collapse into exactly two: "Why Is Performance Low?" and "What
Will We Fix & What Is the Impact?". Slide 1 (add_pagespeed_slide) is
untouched — not covered here."""

from pptx import Presentation

from app.reporting.pptx_builder import (
    SLIDE_H, SLIDE_W, _audit_slide_geometry, _performance_root_causes, _resource_contributors,
    add_pagespeed_fix_impact_slide, add_pagespeed_why_low_slide,
)


def _prs():
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
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


def _mobile(**overrides):
    base = {
        "current_score": 19,
        "metric_table": [
            {"id": "largest-contentful-paint", "label": "LCP", "value": 4200, "display_value": "4.2 s", "good_threshold": 2500, "status": "Poor"},
            {"id": "cumulative-layout-shift", "label": "CLS", "value": 0.05, "display_value": "0.05", "good_threshold": 0.1, "status": "Good"},
        ],
        "diagnostics": [
            {"id": "total-byte-weight", "label": "Total network payload", "value": "7,127 KiB"},
            {"id": "bootup-time", "label": "Total JavaScript execution time", "value": "3.3 s"},
            {"id": "mainthread-work-breakdown", "label": "Long main-thread tasks", "value": "5.2 s"},
        ],
        "opportunities": [
            {"title": "Reduce unused JavaScript", "savings": "1,063 KiB", "savings_ms": 900},
            {"title": "Reduce unused CSS", "savings": "183 KiB", "savings_ms": 200},
        ],
        "script_weight": {
            "top_scripts": [
                {"url": "https://www.google.com/recaptcha/api.js", "encoded_bytes": 826000, "resource_bytes": 826000, "is_third_party": True},
                {"url": "https://www.googletagmanager.com/gtag/js?id=UA-1", "encoded_bytes": 300000, "resource_bytes": 300000, "is_third_party": True},
                {"url": "https://www.googletagmanager.com/gtag/js?id=UA-2", "encoded_bytes": 244000, "resource_bytes": 244000, "is_third_party": True},
                {"url": "https://example.com/static/gtm.js", "encoded_bytes": 480000, "resource_bytes": 480000, "is_third_party": False},
            ],
            "top_waste": [],
        },
    }
    base.update(overrides)
    return base


def _device_performance(mobile_bounce=58.0, desktop_bounce=39.0, mobile_share=64.0):
    return {
        "by_device": {
            "mobile": {"sessions": 6400, "bounce_rate_pct": mobile_bounce, "engagement_rate_pct": 41.0, "key_events": 40, "key_event_rate_pct": 0.6, "pct_share": mobile_share},
            "desktop": {"sessions": 3600, "bounce_rate_pct": desktop_bounce, "engagement_rate_pct": 60.0, "key_events": 60, "key_event_rate_pct": 1.7, "pct_share": 100 - mobile_share},
        },
        "total_sessions": 10000,
    }


# --- root cause / contributor extraction -------------------------------------------------
def test_root_causes_read_from_diagnostics_and_opportunities():
    causes = _performance_root_causes(_mobile())
    labels = [c[0] for c in causes]
    assert labels[:5] == ["Heavy network payload", "JavaScript execution", "Long main-thread tasks",
                          "Unused JavaScript", "Unused CSS"]
    evidence = dict((c[0], c[1]) for c in causes)
    assert evidence["Heavy network payload"] == "7,127 KiB"
    assert evidence["Unused JavaScript"] == "1,063 KiB"  # from opportunities fallback, not diagnostics


def test_root_causes_never_padded_when_psi_didnt_flag_them():
    causes = _performance_root_causes(_mobile(diagnostics=[], opportunities=[]))
    assert causes == [("Slow LCP rendering", "4.2 s", "delays when the main content becomes visible")]


def test_root_causes_skip_good_metrics():
    m = _mobile(diagnostics=[], opportunities=[])
    m["metric_table"][0]["status"] = "Good"
    assert _performance_root_causes(m) == []


def test_root_causes_add_third_party_overhead_when_significant():
    m = _mobile(diagnostics=[], opportunities=[])
    m["metric_table"][0]["status"] = "Good"
    m["script_weight"]["third_party_pct"] = 62
    m["script_weight"]["total_js_bytes"] = 1_500_000
    causes = _performance_root_causes(m)
    assert causes == [("Third-party resource overhead", "62% of 1465 KB JS", "adds third-party processing and network load")]


def test_resource_contributors_name_fits_one_line_in_the_narrowest_card():
    # Regression (Geopits, 2026-09-25): raw filenames wrapped onto the KB
    # line. 2026-09-29: the fix that truncated hashed names ("O5JBjZ_SBv52dH8...")
    # left labels no reader could interpret — hashed chunks now get a
    # meaningful label instead of a cut-off hash.
    script_weight = {"top_scripts": [
        {"url": "https://example.com/_next/static/chunks/O5JBjZ_SBodhEgBNnZ3e48-TaSN123456.js",
         "encoded_bytes": 181000, "resource_bytes": 181000},
    ]}
    contributors = _resource_contributors(script_weight, "https://example.com")
    assert len(contributors[0][0]) <= 17


def test_resource_contributors_never_show_hash_fragments_or_ellipsis_on_hashed_first_party_chunks():
    script_weight = {"top_scripts": [
        {"url": "https://example.com/_next/static/chunks/O5JBjZ_SBodhEgBNnZ3e48-TaSN123456.js", "encoded_bytes": 181000},
        {"url": "https://example.com/_next/static/chunks/a8Kd93Jsl20Xq.js", "encoded_bytes": 90000},
    ]}
    contributors = _resource_contributors(script_weight, "https://example.com")
    assert contributors == [("Top site scripts", 271000)]  # merged, one readable card


def test_resource_contributors_third_party_hashed_script_shows_its_domain():
    script_weight = {"top_scripts": [
        {"url": "https://cdn.somevendor.io/assets/O5JBjZ_SBv52dH8xQ.js", "encoded_bytes": 120000},
    ]}
    contributors = _resource_contributors(script_weight, "https://example.com")
    assert contributors[0][0] == "somevendor.io"


def test_resource_contributors_third_party_domain_handles_co_uk_style_suffix():
    script_weight = {"top_scripts": [
        {"url": "https://cdn.vendor.co.uk/assets/O5JBjZ_SBv52dH8xQ.js", "encoded_bytes": 120000},
    ]}
    assert _resource_contributors(script_weight, "https://example.com")[0][0] == "vendor.co.uk"


def test_resource_contributors_keeps_readable_filenames():
    script_weight = {"top_scripts": [
        {"url": "https://example.com/static/main.js", "encoded_bytes": 120000},
    ]}
    assert _resource_contributors(script_weight, "https://example.com")[0][0] == "main.js"


def test_resource_contributors_dedup_by_vendor_and_rank_by_size():
    contributors = _resource_contributors(_mobile()["script_weight"], "https://example.com")
    names = [c[0] for c in contributors]
    assert names[0] == "reCAPTCHA"
    ga = next(c for c in contributors if "Google Analytics" in c[0])
    assert ga[1] == 300000 + 244000  # gtag + analytics.js merged onto one vendor row


# --- Slide 2 -------------------------------------------------------------------------
def test_why_low_slide_none_when_no_metric_table():
    assert add_pagespeed_why_low_slide(_prs(), None, None) is None
    assert add_pagespeed_why_low_slide(_prs(), {"metric_table": []}, None) is None


def test_why_low_slide_shows_causes_contributors_takeaway_and_source():
    prs = _prs()
    slide = add_pagespeed_why_low_slide(prs, _mobile(), None, website_url="https://example.com")
    text = _slide_text(slide)
    assert "Why Is Performance Low?" in text
    assert "7,127 KiB" in text and "3.3 s" in text and "5.2 s" in text
    assert "reCAPTCHA" in text
    assert "KEY TAKEAWAY" in text
    assert "Source: Google PageSpeed Insights" in text
    assert "Live run:" in text
    assert _audit_slide_geometry(prs) == []


def test_why_low_slide_never_shows_banned_projection_language():
    prs = _prs()
    slide = add_pagespeed_why_low_slide(prs, _mobile(), None)
    text = _slide_text(slide)
    for banned in ("caused", "If Fixed", "Score Impact"):
        assert banned not in text


# --- Slide 3 -------------------------------------------------------------------------
def test_fix_impact_slide_none_when_no_metric_table():
    assert add_pagespeed_fix_impact_slide(_prs(), None, None) is None


def test_fix_impact_slide_maps_causes_to_fixes_and_shows_target():
    prs = _prs()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None)
    text = _slide_text(slide)
    assert "Heavy network payload" in text
    assert "Defer, remove, or conditionally load non-critical JavaScript." in text
    assert "19" in text and "60+" in text
    assert "Target performance score" in text
    assert "guaranteed business outcome" not in text or "not a guaranteed business outcome" in text
    assert _audit_slide_geometry(prs) == []


def test_fix_impact_slide_performance_target_follows_tiered_rule_per_device():
    # Client spec (2026-09-25): Poor (0-49) -> 60+, Moderate (50-89) ->
    # 90+, Good (90-100) -> Maintain 90+, applied per device from that
    # device's OWN current score — never one fixed target for both.
    prs = _prs()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(current_score=35), _mobile(current_score=72))
    text = _slide_text(slide)
    assert "35" in text and "60+" in text  # Poor -> 60+
    assert "72" in text and "90+" in text  # Moderate -> 90+
    assert _audit_slide_geometry(prs) == []

    prs2 = _prs()
    slide2 = add_pagespeed_fix_impact_slide(prs2, _mobile(current_score=95), None)
    text2 = _slide_text(slide2)
    assert "95" in text2 and "Maintain 90+" in text2  # Good -> Maintain 90+
    assert _audit_slide_geometry(prs2) == []


def test_fix_impact_slide_shows_ga4_device_evidence_and_observed_signal():
    prs = _prs()
    dp = _device_performance()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, device_performance=dp)
    text = _slide_text(slide)
    assert "58%" in text and "39%" in text
    assert "19.0 percentage points higher bounce rate on mobile" in text
    assert "OBSERVED SIGNAL" in text
    assert "supports prioritizing mobile performance remediation" in text
    assert "caused" not in text.lower()


def test_fix_impact_slide_falls_back_when_ga4_unavailable():
    prs = _prs()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, device_performance=None)
    text = _slide_text(slide)
    assert "Device-level behavioural impact cannot be quantified from the available GA4 data." in text
    assert "58%" not in text


def test_fix_impact_slide_skips_observed_signal_when_mobile_share_too_small():
    prs = _prs()
    dp = _device_performance(mobile_share=15.0)
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, device_performance=dp)
    text = _slide_text(slide)
    assert "supports prioritizing mobile performance remediation" not in text
    assert "POST-FIX VALIDATION" not in text  # removed 2026-09-29


def test_fix_impact_slide_source_label_includes_ga4_date_range():
    prs = _prs()
    date_range = {"ga4_start": "2026-08-01", "ga4_end": "2026-08-31"}
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, date_range=date_range)
    text = _slide_text(slide)
    assert "Aug 01, 2026" in text and "Aug 31, 2026" in text


# --- Slide 3 — Fix -> Target -> Outcome -> Measurement spec (2026-09-25) -------------

def test_fix_impact_slide_right_column_order_is_target_evidence_outcomes():
    prs = _prs()
    dp = _device_performance()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, device_performance=dp)
    text = _slide_text(slide)
    target_i = text.index("PERFORMANCE TARGET")
    evidence_i = text.index("CURRENT GA4 EVIDENCE")
    outcomes_i = text.index("EXPECTED OUTCOMES")
    assert target_i < evidence_i < outcomes_i
    assert "POST-FIX MEASUREMENT" not in text  # removed 2026-09-29
    assert _audit_slide_geometry(prs) == []


def test_fix_impact_slide_ga4_evidence_shows_only_bounce_and_share_of_sessions():
    # 2026-09-29 user spec: the Current GA4 Evidence cards show ONLY bounce
    # percentage and percentage of sessions per device — no engagement rate
    # and no key events.
    prs = _prs()
    dp = _device_performance()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, device_performance=dp)
    text = _slide_text(slide)
    assert "Bounce" in text and "% of sessions" in text
    assert "Engagement 41%" not in text and "Engagement 60%" not in text
    evidence = text[text.index("CURRENT GA4 EVIDENCE"):text.index("EXPECTED OUTCOMES")]
    assert "Key events" not in evidence and "Engagement" not in evidence
    assert _audit_slide_geometry(prs) == []


def test_fix_impact_slide_expected_outcomes_only_shows_themes_tied_to_real_evidence():
    prs = _prs()
    dp = _device_performance()
    slide = add_pagespeed_fix_impact_slide(prs, _mobile(), None, device_performance=dp)
    text = _slide_text(slide)
    assert "EXPECTED OUTCOMES" in text
    # _mobile()'s default causes (network payload, JS execution, main-thread,
    # unused JS/CSS, LCP) plus this device signal (elevated bounce, real key
    # events, mobile is primary) trigger every theme — capped at the
    # canonical-order top 4, never all six crowding the column.
    assert "Lower Bounce Rates" in text
    assert "Higher Conversions" in text
    assert "Stronger Search Performance" in text
    assert "Faster Content Discovery" in text
    assert "Better Mobile Experience" not in text  # 5th in canonical order, past the cap
    assert "Lower Resource Overhead" not in text  # 6th
    assert _audit_slide_geometry(prs) == []


def test_fix_impact_slide_better_mobile_experience_needs_real_comparative_evidence():
    # "Better Mobile Experience" must tie to mobile actually being the
    # weaker (or only-audited) device, not just that mobile happened to be
    # PSI's primary data source — true on almost every run regardless of
    # whether mobile has any real problem relative to desktop.
    prs = _prs()
    m = _mobile(diagnostics=[], opportunities=[])  # only Slow LCP rendering -> no bounce/conversion themes either
    slide = add_pagespeed_fix_impact_slide(prs, m, None, device_performance=None)  # desktop absent entirely
    text = _slide_text(slide)
    assert "Better Mobile Experience" in text
    assert _audit_slide_geometry(prs) == []

    prs2 = _prs()
    # Desktop present and NOT meaningfully worse than mobile -> no
    # comparative case for a mobile-specific outcome.
    mobile_ok = _mobile(current_score=85, diagnostics=[], opportunities=[])
    desktop_similar = _mobile(current_score=80, diagnostics=[], opportunities=[])
    slide2 = add_pagespeed_fix_impact_slide(prs2, mobile_ok, desktop_similar, device_performance=None)
    text2 = _slide_text(slide2)
    assert "Better Mobile Experience" not in text2
    assert _audit_slide_geometry(prs2) == []


def test_fix_impact_slide_expected_outcomes_skips_untied_themes():
    # No device_performance at all -> no bounce/conversion evidence, so
    # neither "Lower Bounce Rates" nor "Higher Conversions" can be claimed.
    prs = _prs()
    m = _mobile(diagnostics=[], opportunities=[])  # only "Slow LCP rendering" survives -> no resource-overhead cause
    slide = add_pagespeed_fix_impact_slide(prs, m, None, device_performance=None)
    text = _slide_text(slide)
    assert "Lower Bounce Rates" not in text
    assert "Higher Conversions" not in text
    assert "Lower Resource Overhead" not in text
    assert "Stronger Search Performance" in text  # from Slow LCP rendering
    assert _audit_slide_geometry(prs) == []
