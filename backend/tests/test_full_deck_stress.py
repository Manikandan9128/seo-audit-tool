"""Full-deck geometry stress test (2026-09-26): a single build_report() call
with EVERY parameter populated with maximal-stress, realistic-but-deliberately-
long content (long client/competitor names, 4+ long AI-narrative bullets per
slide, many-row tables, a perfect-100 PageSpeed score, 20-issue UI audit,
long schema/keyword-gap/topic-map text) — designed to exercise every add_*_
slide function pptx_builder.py's build_report() can reach in one deck,
including several (PageSpeed root-cause/fix-impact/resource-contributor
tables, the Schema Validator's two real build_schema_report_parts()-derived
tables, Branded vs Non-Branded, Search Opportunities Pages/Countries) that
prior ad-hoc reports/tests only ever exercised with short/empty data and so
never actually stressed their wrap/overflow guards.

_audit_slide_geometry runs automatically inside build_report() and, when it
finds any overlap or out-of-bounds shape, appends one entry to the
content_issues list build_report() was given (pptx_builder.py's own
"Surfaced into the same content_issues list..." comment) — asserting that
list stays empty is the same check a real report generation surfaces to a
user via the job record, just pinned here so a future slide edit that
reintroduces overlap/overflow under realistic stress fails in CI first."""

from app.reporting.pptx_builder import build_report

LONG_TEXT = (
    "A long, realistic AI-generated sentence describing a specific finding in detail, "
    "written the way real model output actually reads, long enough to wrap across "
    "more than one line at typical column widths used throughout this report."
)


def _psi_result(score):
    return {
        "scores": {"performance": score, "seo": 88, "accessibility": 91, "best_practices": 100 if score == 100 else 85},
        "current_score": score,
        "metric_table": [
            {"id": "largest-contentful-paint", "label": "Largest Contentful Paint", "value": 4200,
             "display_value": "4.2 s", "good_threshold": 2500, "status": "Poor"},
            {"id": "cumulative-layout-shift", "label": "Cumulative Layout Shift", "value": 0.35,
             "display_value": "0.35", "good_threshold": 0.1, "status": "Poor"},
            {"id": "total-blocking-time", "label": "Total Blocking Time", "value": 900,
             "display_value": "900 ms", "good_threshold": 200, "status": "Poor"},
            {"id": "first-contentful-paint", "label": "First Contentful Paint", "value": 1800,
             "display_value": "1.8 s", "good_threshold": 1800, "status": "Needs Improvement"},
            {"id": "speed-index", "label": "Speed Index", "value": 5200,
             "display_value": "5.2 s", "good_threshold": 3400, "status": "Poor"},
        ],
        "diagnostics": [
            {"id": "total-byte-weight", "value": "6.8 MB"},
            {"id": "bootup-time", "value": "3.1 s"},
            {"id": "mainthread-work-breakdown", "value": "4.4 s"},
        ],
        "opportunities": [
            {"title": "Reduce unused JavaScript", "savings": "1.2 s"},
            {"title": "Eliminate render-blocking resources", "savings": "0.8 s"},
        ],
        "script_weight": {
            "third_party_pct": 62, "total_js_bytes": 4_200_000,
            # Raw hashed-looking filenames with no natural break point — the
            # exact shape that overlapped its own KB line on a live Geopits
            # report before add_pagespeed_why_low_slide's _short_resource_name
            # fix (see that function's own comment, pptx_builder.py:~827).
            "top_scripts": [
                {"url": f"https://cdn.example.com/assets/{'a1b2c3d4e5f6' * 3}{i}.js", "encoded_bytes": 900_000 - i * 50_000}
                for i in range(6)
            ],
        },
    }


def _narrative():
    return {"best_at": [LONG_TEXT] * 4, "opportunity": [LONG_TEXT] * 4, "summary": LONG_TEXT}


def test_full_deck_under_maximal_stress_has_no_geometry_issues():
    long_domain = "supercalifragilisticexpialidocious-enterprises-international"
    competitor_rows = [
        {"domain": "acme-widgets-and-industrial-solutions-corporation.com", "authority_score": 40,
         "backlinks_total": 500000, "organic_traffic": 1000000, "organic_keywords": 50000},
    ] + [
        {"domain": f"{long_domain[:40]}-{i}.com", "authority_score": 60 + i, "backlinks_total": 20000 * i,
         "organic_traffic": 50000 * i, "organic_keywords": 2000 * i}
        for i in range(1, 5)
    ]
    keyword_rows = [
        {"keyword": f"long realistic multi word target keyword phrase example number {i} for stress testing layout",
         "search_volume": 1000 - i * 10, "position": i + 1, "url": f"https://acme.com/page-{i}",
         "traffic": 500 - i, "keyword_difficulty": 45, "cpc": 2.5, "intent": "commercial"}
        for i in range(60)
    ]
    backlink_rows = [
        {"source_url": f"https://referrer-domain-example-{i}.com/a-very-long-referring-page-slug-here",
         "domain_rating": 50, "anchor_text": "a fairly long anchor text phrase used for this backlink row",
         "target_url": "https://acme.com/"}
        for i in range(30)
    ]
    site_audit_pages_rows = [
        {"page_url": f"https://acme.com/a-very-long-descriptive-url-path-segment-for-page-{i}/subsection/detail",
         "issues": 5, "status_code": 200, "title": "A fairly long page title used to stress table wrapping"}
        for i in range(80)
    ]
    analytics = {
        "date_range": {"ga4": "Jan 1 - Jan 31, 2026", "gsc": "Jan 1 - Jan 31, 2026", "channel_breakdown": "Jan 1 - Jan 31, 2026"},
        "traffic_overview": {"rows": [
            {"sessions": 12000, "total_users": 9000, "page_views": 30000, "engagement_duration": 500000,
             "engagement_rate": 0.55, "bounce_rate": 0.45},
        ]},
        "traffic_sources": {"rows": [
            {"channel": "Organic Search", "sessions": 6000}, {"channel": "Direct", "sessions": 3000},
            {"channel": "Referral", "sessions": 1000}, {"channel": "Paid Search", "sessions": 800},
            {"channel": "Social", "sessions": 700}, {"channel": "Email", "sessions": 300},
            {"channel": "Other Channel A", "sessions": 150}, {"channel": "Other Channel B", "sessions": 50},
        ]},
        "traffic_spike": {
            "date": "2026-01-15", "day_of_week": "Thursday", "sessions": 5000,
            "pct_above_avg": 150, "avg_sessions": 2000,
            "by_channel": [{"label": "Organic Search", "pct": 70}],
            "by_landing_page": [{"label": "/a-very-long-landing-page-slug-for-the-spike-day", "pct": 60}],
            "by_country": [{"label": "United States", "pct": 80}],
            "avg_bounce_rate": 0.4, "spike_bounce_rate": 0.6,
            "avg_engagement_rate": 0.5, "spike_engagement_rate": 0.3,
            "avg_session_duration_sec": 120, "spike_session_duration_sec": 60,
            "avg_key_events": 10, "spike_key_events": 25,
        },
        "traffic_channel_breakdown": {
            "by_country": [{"country": "United States", "sessions": 4000}, {"country": "India", "sessions": 2000}],
            "by_device": [{"device": "mobile", "sessions": 5000}, {"device": "desktop", "sessions": 4000}],
            "by_channel": [{"channel": "Organic Search", "sessions": 6000}],
        },
        "conversion_evidence": {"key_events": [{"name": "purchase", "count": 120}]},
        "device_performance": {"by_device": {
            "mobile": {"bounce_rate_pct": 62.0, "engagement_rate_pct": 38.0, "key_events": 45,
                       "key_event_rate_pct": 1.2, "pct_share": 68.0},
            "desktop": {"bounce_rate_pct": 41.0, "engagement_rate_pct": 55.0, "key_events": 30,
                        "key_event_rate_pct": 2.1, "pct_share": 32.0},
        }},
    }
    competitor_narratives = {c["domain"]: _narrative() for c in competitor_rows[1:]}
    content_issues: list[str] = []

    build_report(
        client_name="Supercalifragilistic Enterprises International Holdings Group",
        website_url="https://acme.com",
        site_audit={"company_summary": LONG_TEXT, "issues": []},
        page_audit={"pages": [{"issues": ["Missing structured data (JSON-LD)", "Missing meta description"]} for _ in range(20)]},
        psi_mobile=_psi_result(100),
        psi_desktop=_psi_result(92),
        analytics=analytics,
        competitor_rows=competitor_rows,
        keyword_rows=keyword_rows,
        backlink_rows=backlink_rows,
        backlink_row_count=len(backlink_rows),
        competitor_narratives=competitor_narratives,
        brand_color_hex="#CC0000",
        company_overview={"description": LONG_TEXT, "products": ["Product A", "Product B"], "industry": "Industrial Manufacturing"},
        tech_stack={"cms": "WordPress", "hosting": "AWS", "frameworks": ["React", "Next.js"]},
        competitor_analysis={"keyword_gap_rows": [
            {"keyword": f"gap keyword phrase number {i} reasonably long for stress test", "search_volume": 500 - i,
             "keyword_difficulty": 40, "gap_category": ["Missing", "Shared", "Untapped"][i % 3],
             "client_position": None,
             "competitor_positions": [{"competitor": c["domain"], "position": i + 1} for c in competitor_rows[1:]]}
            for i in range(30)
        ]},
        ux_findings={"note": None},
        site_audit_issues=[{"issue": f"Real error type {i}", "issue_type": "ERROR", "failed_checks": 10 + i} for i in range(10)],
        structured_data_rows=[{"page_url": f"https://acme.com/p{i}", "schema_type": "Product", "valid": i % 2 == 0} for i in range(20)],
        site_audit_overview={"site_health_pct": 72, "ai_search_health_pct": 100, "crawled_pages": 1978},
        backlink_summary={"total_backlinks": 33800, "referring_domains": 1200},
        own_domain_rating=45,
        core_problem={
            "headline": "A long realistic AI headline describing the single biggest structural problem holding "
                         "back organic growth for this business right now.",
            "missing_count": 120, "shared_count": 340, "untapped_count": 88, "summary": LONG_TEXT,
        },
        site_audit_pages_rows=site_audit_pages_rows,
        next_steps_ai={"categories": {
            "local_seo": {"applicable": True, "intro": LONG_TEXT, "items": [LONG_TEXT] * 6},
        }},
        schema_validation={
            "total_pages": 400,
            "by_page_type": [
                {"page_type": "Article-type", "pages": 150, "present_pages": 60, "valid_pages": 40,
                 "valid_pct": 27, "valid_pct_traffic_weighted": 33},
                {"page_type": "Product", "pages": 100, "present_pages": 90, "valid_pages": 80,
                 "valid_pct": 80, "valid_pct_traffic_weighted": None},
                {"page_type": "FAQPage", "pages": 20, "present_pages": 5, "valid_pages": 5,
                 "valid_pct": 25, "valid_pct_traffic_weighted": None},
                {"page_type": "Other Pages", "pages": 130, "present_pages": 0, "valid_pages": 0,
                 "valid_pct": 0, "valid_pct_traffic_weighted": None},
            ],
            "type_coverage": [
                {"type": "WebSite", "pages_with_it": 1}, {"type": "Organization", "pages_with_it": 1},
                {"type": "BreadcrumbList", "pages_with_it": 200}, {"type": "VideoObject", "pages_with_it": 12},
            ],
            "missing_properties": [
                {"type": "Article-type", "severity": "required", "pages_missing": 20},
                {"type": "BreadcrumbList", "severity": "required", "pages_missing": 15},
            ],
            "overall_coverage_pct_traffic_weighted": 48, "priority_page_coverage_pct_traffic_weighted": 62,
        },
        brand_citations=[{"source": f"citation-source-{i}.com", "snippet": LONG_TEXT} for i in range(10)],
        brand_wikipedia={"summary": LONG_TEXT},
        geopulse_analysis={"aeo_items": [LONG_TEXT] * 6, "geo_items": [LONG_TEXT] * 6},
        keyword_sheet_link=None,
        seo_issues_ai_insights={"headline": LONG_TEXT, "supporting": [LONG_TEXT] * 4, "takeaway": LONG_TEXT},
        page_wise_ai=None,
        page_wise_exclude_paths=None,
        schema_ai_insights={"headline": LONG_TEXT, "supporting": [LONG_TEXT] * 3},
        branded_vs_nonbranded_comparison={
            "branded_share_pct": 42.5,
            "branded": {"clicks": 3000, "impressions": 50000, "clicks_pct": 45.0, "impressions_pct": 20.0,
                        "ctr_pct": 6.0, "avg_position": 3.2},
            "nonbranded": {"clicks": 4000, "impressions": 200000, "clicks_pct": 55.0, "impressions_pct": 80.0,
                           "ctr_pct": 2.0, "avg_position": 8.5},
        },
        branded_vs_nonbranded_narrative={
            "headline": LONG_TEXT, "demand_gap": {"text": LONG_TEXT}, "cost_of_inaction": LONG_TEXT,
            "concrete_example": {"text": LONG_TEXT},
        },
        branded_vs_nonbranded_ai_insights={"insights": [LONG_TEXT] * 5},
        high_potential_pages=[
            {"page": f"https://acme.com/a-very-long-high-potential-page-url-segment-{i}", "impressions": 5000,
             "clicks": 50, "ctr_pct": 1.0, "position": 15.5,
             "recommended_action": LONG_TEXT if i % 3 else "Insufficient query-level GSC evidence."}
            for i in range(15)
        ],
        high_potential_countries={
            "material": [
                {"country": f"A Fairly Long Country Name {i}", "impressions": 5000, "clicks": 50,
                 "ctr_pct": 1.0, "fix": LONG_TEXT}
                for i in range(12)
            ],
            "low_signal": {"summary": "United Kingdom, Australia, Indonesia, Singapore, Canada, Germany, "
                                       "France, Brazil: " + LONG_TEXT},
        },
        competitor_top_opportunities=[LONG_TEXT] * 5,
        content_issues=content_issues,
        strategic_keyword_clusters=None,
        keyword_strategy={
            "business_model": {"models": ["B2B SaaS", "Marketplace"]},
            "topics": [
                {
                    "parent": f"A fairly long parent topic name number {i}",
                    "clusters": [f"Cluster {i}-{j} with a reasonably long descriptive name" for j in range(5)],
                    "coverage": "Weak", "covered": 1, "total": 5, "demand": 1000 - i * 10,
                    "intents_without_page": ["commercial", "informational"],
                    "hierarchy": {f"Subtopic {i}": {"commercial": [f"Cluster {i}-0"]}},
                    "links": [
                        {"relation": "hub-and-spoke", "from_cluster": f"Cluster {i}-0", "to_cluster": f"Cluster {i}-1",
                         "from": f"/topic-{i}/cluster-0", "to": f"/topic-{i}/cluster-1"},
                    ],
                }
                for i in range(6)
            ],
            "clusters": [
                {"cluster_id": f"C{i:03d}", "name": f"Cluster {i} with a reasonably long descriptive name",
                 "parent_topic": f"A fairly long parent topic name number {i % 6}",
                 "primary_keyword": f"a fairly long primary keyword phrase {i}",
                 "intent": "commercial", "priority": "High", "decision": "build_new_page"}
                for i in range(15)
            ],
        },
        ui_audit={
            "issues": [
                {"title": f"A fairly long UI issue title describing the problem number {i}", "evidence": LONG_TEXT,
                 "where": f"/a-long-page-path-{i}", "fix": LONG_TEXT, "outcome_tags": ["clarity", "trust"],
                 "priority": "High", "device": "Both", "impact_score": 8}
                for i in range(1, 21)
            ],
            "total_count": 20,
            "counts_by_priority": {"High": 10, "Medium": 7, "Low": 3},
            "full_list_url": "https://sheets.google.com/x", "ga4_available": True,
            "ga4_evidence": {"bounce_rate_pct": 55.0, "mobile_bounce_rate_pct": 60.0, "engagement_rate_pct": 45.0},
            "heatmap_tool_detected": True,
            "primary_cta_text": "A fairly long primary call to action button text", "business_type": "ecommerce",
        },
        traffic_capture_rate=0.35,
    )

    assert content_issues == []
