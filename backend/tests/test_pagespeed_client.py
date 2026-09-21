from app.integrations.pagespeed_client import (
    _cwv_field_data,
    _extract_diagnostics,
    _extract_lcp_breakdown,
    _extract_opportunities,
    _metric_status,
    _metric_table,
    _perf_audit_groups,
)


def test_metric_table_uses_numeric_value_and_real_psi_score():
    audits = {
        "largest-contentful-paint": {"numericValue": 4800, "displayValue": "4.8 s", "score": 0.3},
        "total-blocking-time": {"numericValue": 720, "displayValue": "720 ms", "score": 0.2},
        "cumulative-layout-shift": {"numericValue": 0.28, "displayValue": "0.28", "score": 0.4},
        "first-contentful-paint": {"numericValue": 2600, "displayValue": "2.6 s", "score": 0.6},
        "speed-index": {"numericValue": 6100, "displayValue": "6.1 s", "score": 0.1},
    }
    rows = _metric_table(audits)
    assert {m["id"] for m in rows} == set(audits.keys())
    lcp = next(m for m in rows if m["id"] == "largest-contentful-paint")
    assert lcp["value"] == 4800
    assert lcp["good_threshold"] == 2500
    assert lcp["status"] == "Poor"
    # No score/weight/if-fixed/score-impact fields anywhere on the row.
    for forbidden in ("score", "weight", "score_if_fixed", "score_delta", "weighted_points"):
        assert forbidden not in lcp


def test_metric_table_skips_missing_metrics():
    assert len(_metric_table({"largest-contentful-paint": {"numericValue": 4000, "score": 0.5}})) == 1
    assert _metric_table({"largest-contentful-paint": {"score": 0.5}}) == []  # no numericValue at all


def test_metric_status_bands_match_lighthouse_thresholds():
    assert _metric_status(0.95) == "Good"
    assert _metric_status(0.9) == "Good"
    assert _metric_status(0.6) == "Needs Improvement"
    assert _metric_status(0.2) == "Poor"
    assert _metric_status(None) is None


def test_cwv_field_data_none_when_no_field_data_present():
    assert _cwv_field_data({}) is None
    assert _cwv_field_data({"loadingExperience": {}}) is None


def test_cwv_field_data_reads_real_crux_metrics():
    response = {
        "loadingExperience": {
            "overall_category": "SLOW",
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 4200, "category": "SLOW"},
                "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 8, "category": "AVERAGE"},
                "INTERACTION_TO_NEXT_PAINT": {"percentile": 350, "category": "AVERAGE"},
            },
        }
    }
    field = _cwv_field_data(response)
    assert field["overall_category"] == "SLOW"
    assert field["lcp"]["category"] == "SLOW"
    assert field["inp"]["category"] == "AVERAGE"
    assert field["is_origin_fallback"] is False


def test_cwv_field_data_falls_back_to_origin_and_flags_it():
    response = {
        "loadingExperience": {},
        "originLoadingExperience": {
            "overall_category": "AVERAGE",
            "metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 3000, "category": "AVERAGE"}},
        },
    }
    field = _cwv_field_data(response)
    assert field is not None
    assert field["is_origin_fallback"] is True


def test_cwv_field_data_never_derives_inp_from_tbt():
    # 2026-09-21 spec rule 4: INP must never be inferred from a lab TBT
    # value — only real CrUX INTERACTION_TO_NEXT_PAINT/its experimental
    # predecessor key counts.
    response = {"loadingExperience": {"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2000, "category": "FAST"}}}}
    field = _cwv_field_data(response)
    assert field["inp"] is None


def test_perf_audit_groups_reads_real_auditrefs():
    categories = {"performance": {"auditRefs": [{"id": "render-blocking-resources", "group": "load-opportunities"}, {"id": "bootup-time", "group": "diagnostics"}]}}
    groups = _perf_audit_groups(categories)
    assert groups["render-blocking-resources"] == "load-opportunities"
    assert groups["bootup-time"] == "diagnostics"


def test_extract_opportunities_only_real_savings_from_opportunity_group():
    audits = {
        "render-blocking-resources": {
            "score": 0.2, "title": "Eliminate render-blocking resources",
            "details": {"overallSavingsMs": 1200},
        },
        "uses-passive-event-listeners": {"score": 0.1, "title": "No savings figure"},
    }
    audit_groups = {"render-blocking-resources": "load-opportunities", "uses-passive-event-listeners": "diagnostics"}
    opps = _extract_opportunities(audits, audit_groups)
    assert len(opps) == 1
    assert opps[0]["title"] == "Eliminate render-blocking resources"
    assert "1.2s" in opps[0]["savings"]


def test_extract_opportunities_excludes_passing_audit():
    audits = {"unused-css-rules": {"score": 0.95, "title": "Unused CSS", "details": {"overallSavingsMs": 500}}}
    audit_groups = {"unused-css-rules": "load-opportunities"}
    assert _extract_opportunities(audits, audit_groups) == []


def test_extract_diagnostics_only_returns_real_values():
    audits = {"bootup-time": {"displayValue": "2.1 s"}, "total-byte-weight": {"displayValue": "1,800 KiB"}}
    assert {d["id"] for d in _extract_diagnostics(audits, {})} == {"bootup-time", "total-byte-weight"}


def test_extract_diagnostics_skips_audit_already_shown_as_opportunity():
    # Rule 9: never show the same audit in both sections.
    audits = {"render-blocking-resources": {"displayValue": "Potential savings of 400 ms"}}
    audit_groups = {"render-blocking-resources": "load-opportunities"}
    assert _extract_diagnostics(audits, audit_groups) == []


def test_extract_diagnostics_network_requests_uses_real_item_count():
    audits = {"network-requests": {"details": {"items": [{}, {}, {}]}}}
    diags = _extract_diagnostics(audits, {})
    assert diags[0]["value"] == "3 requests"


def test_extract_lcp_breakdown_matches_known_phase_labels():
    audits = {
        "largest-contentful-paint-element": {
            "details": {"items": [
                {"phase": "TTFB", "timing": 200},
                {"phase": "Load Delay", "timing": 800},
                {"phase": "unrecognized-shape", "timing": 100},
            ]}
        }
    }
    rows = _extract_lcp_breakdown(audits)
    assert {r["phase"] for r in rows} == {"TTFB", "Resource load delay"}


def test_extract_lcp_breakdown_none_when_audit_absent():
    assert _extract_lcp_breakdown({}) is None
