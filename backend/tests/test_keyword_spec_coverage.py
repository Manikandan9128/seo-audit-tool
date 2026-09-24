"""Universal SEO Keyword prompt — the 16 sections the 2026-09-24 deep audit
found missing or partial, plus the page map that was computed but never
shown. One test (or more) per item; see docs/keyword_engine_spec_coverage.md."""

from pptx import Presentation

from app.reporting.pptx_builder import (
    SLIDE_H, SLIDE_W, _audit_slide_geometry, add_content_seo_next_steps_slide, add_programmatic_seo_slide,
)
from app.services.google_sheets_service import _CLIENT_COLUMNS, keyword_strategy_tabs
from app.services.keyword_intelligence_service import (
    annotate_keyword_rows, entity_type, modifier_types, normalize_keyword,
)
from app.services.keyword_relevance_service import keyword_brand_type
from app.services.keyword_strategy_service import (
    _feasibility, assign_cluster_types, assign_decisions, build_full_keyword_strategy, build_review_queue,
    detect_cannibalization, infer_business_model, run_quality_checks,
)


def _prs():
    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H
    return prs


def _text(slide):
    out = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            out.append(sh.text_frame.text)
        elif sh.has_table:
            out.extend(c.text_frame.text for row in sh.table.rows for c in row.cells)
    return "\n".join(out)


def _summary(name, kws, family="Commercial", url=None, strength="none", **extra):
    rows = [{"keyword": k, "search_volume": v, "keyword_difficulty": 30} for k, v in kws]
    annotate_keyword_rows(rows)
    for r in rows:
        r.pop("_core_key", None), r.pop("_core_tokens", None)
        r["intent_family"] = r.get("intent_family") if family is None else family
    return {"name": name, "rows": rows, "target_url": url, "match_strength": strength,
            "confidence_level": "High", "dominant_family": family, "primary_keyword": kws[0][0], **extra}


# §5 spelling variants ---------------------------------------------------------
def test_british_and_us_spellings_are_one_entity():
    assert normalize_keyword("database optimisation")["core_key"] == normalize_keyword("database optimization")["core_key"]
    assert normalize_keyword("18 tyre truck")["core_key"] == normalize_keyword("18 tire truck")["core_key"]


# §6 entity type / §7 modifier type / §8 mixed intent ---------------------------
def test_entity_modifier_and_mixed_intent_fields():
    assert entity_type("remote dba services", "Commercial") == "Service"
    assert entity_type("payroll software", "Commercial") == "Product"
    assert entity_type("sql server error 18456", "Informational") == "Problem"
    assert entity_type("what is azure data studio", "Informational") == "Concept / Topic"
    assert set(modifier_types("best payroll software for small business 2026")) >= {"Commercial Investigation", "Audience", "Temporal"}
    rows = [{"keyword": "best payroll software guide"}, {"keyword": "tipper truck"}]
    annotate_keyword_rows(rows)
    assert rows[0]["mixed_intent"] and not rows[1]["mixed_intent"]


# §45 brand type ------------------------------------------------------------------
def test_brand_type_per_keyword():
    own, comp, vendor = {"acme"}, {"rival"}, {"aws"}
    assert keyword_brand_type("acme payroll", own, comp, vendor) == "Brand"
    assert keyword_brand_type("rival pricing", own, comp, vendor) == "Competitor Brand"
    assert keyword_brand_type("aws aurora", own, comp, vendor) == "Product Brand"
    assert keyword_brand_type("acme vs rival", own, comp, vendor) == "Mixed Brand"
    assert keyword_brand_type("payroll software", own, comp, vendor) == "Non-brand"


# §29 cluster type / §22 page type / §25 content-gap type ----------------------------
def test_cluster_page_and_gap_types():
    summaries = [
        _summary("Trucks", [("truck", 9000), ("trucks", 800)]),
        _summary("Truck Guides", [("what is truck payload meaning", 300)], family="Informational"),
        _summary("Payroll Calculator", [("payroll calculator", 900), ("calculate payroll", 300)]),
        _summary("Invoice Template", [("invoice template", 900), ("invoice format", 200)]),
        _summary("Rival Alternatives", [("rival alternatives", 400), ("rival competitors", 100)], family="Comparison"),
        _summary("Dealers", [("truck dealer near me", 500)], family="Local"),
    ]
    topics = [{"parent": "Truck", "clusters": ["Trucks", "Truck Guides", "Dealers"]}]
    for s in summaries:
        s["opportunity"] = 50
    for r in summaries[0]["rows"]:
        r["relevance_status"] = "Core Relevant"  # a bare product noun is core only with this evidence
    assign_cluster_types(summaries, topics, {"models": ["Product"]})
    by = {s["name"]: s for s in summaries}
    assert by["Trucks"]["cluster_type"] == "Core Topic" and by["Trucks"]["page_type"] == "Category Page"
    assert by["Trucks"]["content_gap_type"] == "Missing Core Page"
    assert by["Truck Guides"]["cluster_type"] == "Supporting Topic"
    assert by["Payroll Calculator"]["page_type"] == "Tool / Calculator"
    assert by["Invoice Template"]["page_type"] == "Template"
    assert by["Rival Alternatives"]["page_type"] == "Alternative Page"
    assert by["Dealers"]["page_type"] == "Location Page" and by["Dealers"]["content_gap_type"] == "Missing Location Page"


# §53 all decisions ------------------------------------------------------------------
def test_decisions_cover_the_spec_vocabulary():
    s_primary = _summary("A", [("alpha widget", 900)], url="https://x.com/a", strength="strong",
                         roadmap_priority="High", opportunity=80, recommended_action="Existing URL — Primary Target")
    s_second = _summary("B", [("beta gadget", 500)], url="https://x.com/a", strength="partial",
                        roadmap_priority="Medium", opportunity=60, recommended_action="Existing URL — Primary Target")
    s_new = _summary("C", [("gamma thing", 300)], roadmap_priority="Medium", opportunity=55)
    s_review = _summary("D", [("delta", 100)], roadmap_priority="Human Review", opportunity=20)
    s_redirect = _summary("E", [("epsilon tool", 400)], url="https://x.com/e", strength="strong",
                          roadmap_priority="High", opportunity=70)
    s_redirect["rows"][0]["current_url"] = "https://x.com/old-epsilon"
    s_top = _summary("F", [("zeta kit", 400)], url="https://x.com/f", strength="strong",
                     roadmap_priority="Low", opportunity=40)
    s_top["rows"][0]["current_position"] = 2
    summaries = [s_primary, s_second, s_new, s_review, s_redirect, s_top]
    assign_decisions(summaries, dead_urls={"https://x.com/old-epsilon"})
    got = {s["name"]: s["decision"] for s in summaries}
    assert got == {"A": "EXISTING URL — PRIMARY TARGET", "B": "EXISTING URL — SECONDARY TARGET",
                   "C": "NEW URL REQUIRED", "D": "REVIEW", "E": "REDIRECT", "F": "NO TARGET"}
    assert all(s["decision_reason"] for s in summaries)


def test_no_target_requires_the_primary_keyword_itself_to_rank_top_3():
    # Regression (confirmed real, Geopits report, 2026-09-24 — same bug as
    # an earlier BharatBenz fix): NO TARGET used to fire off the BEST
    # position across every keyword in the cluster, so one easy long-tail
    # keyword ranking top-3 marked the whole cluster "no work needed" even
    # though the cluster's real primary keyword (850 combined searches
    # here) wasn't ranking at all.
    s = _summary(
        "DBA Managed Services",
        [("dba managed services", 850), ("oracle dba services", 140)],
        url="https://x.com/technologies/oracle", strength="strong",
        roadmap_priority="High", opportunity=68,
    )
    # Only the secondary keyword ranks well — the primary (kws[0], and
    # therefore primary_keyword) has no ranking at all.
    s["rows"][1]["current_position"] = 2
    assign_decisions([s])
    assert s["decision"] == "EXISTING URL — PRIMARY TARGET"
    assert s["decision"] != "NO TARGET"


# §24 / §58 cannibalization from Search Console ------------------------------------------
def test_cannibalization_uses_search_console_pages_per_query():
    rows = [
        {"page": "https://x.com/blog/sql-index", "query": "sql index", "clicks": 40, "impressions": 3000, "position": 6},
        {"page": "https://x.com/blog/sql-index-tips", "query": "sql index", "clicks": 12, "impressions": 1400, "position": 11},
        {"page": "https://x.com/services/dba", "query": "dba services", "clicks": 30, "impressions": 900, "position": 4},
        {"page": "https://x.com/blog/dba-guide", "query": "dba services", "clicks": 1, "impressions": 40, "position": 30},
        {"page": "https://x.com/p?a=1", "query": "widget", "clicks": 5, "impressions": 400, "position": 5},
        {"page": "https://x.com/p", "query": "widget", "clicks": 9, "impressions": 500, "position": 4},
        {"page": "https://x.com/only", "query": "solo", "clicks": 9, "impressions": 500, "position": 4},
    ]
    found = {c["query"]: c for c in detect_cannibalization(rows)}
    assert "solo" not in found
    assert found["sql index"]["action"] == "Differentiate" and found["sql index"]["risk"] == "High"
    assert found["sql index"]["preferred_url"].endswith("/sql-index")
    assert found["dba services"]["action"] == "Retarget"
    assert found["widget"]["action"] == "Canonicalize"


# §3 business model ------------------------------------------------------------------
def test_business_model_is_read_from_site_structure():
    pages = [{"page_url": f"https://x.com{p}"} for p in ("/pricing", "/signup", "/features/payroll", "/industries/construction")]
    model = infer_business_model(pages, {"target_market": "Mid-Market and Enterprise"})
    assert "SaaS / Subscription" in model["models"] and "B2B" in model["models"]
    assert infer_business_model([], None)["models"] == ["Undetermined"]


# §32 difficulty read with own authority -------------------------------------------------
def test_difficulty_is_easier_for_a_stronger_site():
    assert _feasibility(50, 70) > _feasibility(50, None) > _feasibility(50, 20)
    assert _feasibility(None, 70) is None


# §62 review queue / §67 quality checks -------------------------------------------------
def test_review_queue_and_all_14_quality_checks():
    s = _summary("Mixed", [("widget price", 500), ("widget cost", 450), ("what is a widget", 400), ("widget guide", 300)], family=None)
    s.update(roadmap_priority="Human Review", decision_reason="Low confidence", match_strength="weak",
             outliers=["a", "b"], opportunity=20)
    queue = build_review_queue([s], [], {"Jobs / Careers": 3})
    types = {q["type"] for q in queue}
    assert {"Low confidence cluster", "Mixed intent", "Unclear page mapping", "Borderline cluster separation",
            "Jobs / Careers"} <= types
    checks = run_quality_checks([s], [], {"models": ["Service"]})
    assert [c["check"] for c in checks] == list(range(1, 15))
    assert {c["status"] for c in checks} <= {"Pass", "Warn", "Not checked"}
    assert next(c for c in checks if c["check"] == 3)["status"] == "Not checked"


# §56 page map + §36 page purpose, §54 master fields, Sheet tabs ----------------------------
def test_full_strategy_page_map_and_sheet_outputs():
    summaries = [
        _summary("Remote DBA", [("remote dba services", 1300), ("remote dba", 900)], url="https://x.com/services/dba", strength="strong"),
        _summary("SQL Index Guides", [("what is sql index", 700)], family="Informational"),
    ]
    ctx = {"site_authority": 35, "brands": {"own": {"acme"}}, "site_audit_pages_rows": [{"page_url": "https://x.com/services/dba"}],
           "page_query_rows": [
               {"page": "https://x.com/blog/a", "query": "q", "clicks": 10, "impressions": 500, "position": 5},
               {"page": "https://x.com/blog/b", "query": "q", "clicks": 8, "impressions": 400, "position": 7}]}
    strategy = build_full_keyword_strategy(summaries, ctx)
    page = next(p for p in strategy["page_map"] if p["url"] == "https://x.com/services/dba")
    for field in ("page_type", "purpose", "business_goal", "current_status", "decision", "links_in", "links_out",
                  "primary_intent", "topic", "priority"):
        assert field in page
    row = summaries[0]["rows"][0]
    for field in ("cluster_id", "cluster_type", "parent_topic", "entity_type", "brand_type", "decision",
                  "business_relevance", "conversion_potential", "content_gap_flag", "modifier_type", "mixed_intent"):
        assert field in row, field
    keys = {k for _l, k in _CLIENT_COLUMNS}
    assert {"cluster_id", "cluster_type", "parent_topic", "entity_type", "brand_type", "decision", "trend", "cpc"} <= keys
    tabs = dict(keyword_strategy_tabs(strategy))
    assert {"Page Map", "Cannibalization", "Content Gaps", "Review Queue", "Quality Checks"} <= set(tabs)
    assert tabs["Page Map"][0][0] == "Priority"


def test_content_seo_slide_shows_cannibalization_and_review_queue_and_fits():
    rows = []
    for i in range(6):
        rows += [{"keyword": f"topic{i} widget", "cluster": f"Topic {i}", "search_volume": 900 - i, "relevance_status": "Relevant",
                  "page_category": "Landing Page", "roadmap_priority": "High", "cluster_opportunity": 80 - i,
                  "existing_page_action": "Create New Page", "primary_or_secondary": "Primary"}]
    strategy = {"cannibalization": [{"query": "sql index", "preferred_url": "https://x.com/a", "other_urls": ["https://x.com/b"],
                                     "risk": "High", "action": "Differentiate", "evidence": "2 pages share 4,400 impressions."}],
                "review_queue": [{"type": "Mixed intent", "item": "X", "detail": ""}]}
    prs = _prs()
    text = _text(add_content_seo_next_steps_slide(prs, rows, strategy))
    assert "Cannibalization (High risk)" in text and "Human review queue" in text
    assert _audit_slide_geometry(prs) == []


# §59 programmatic output -------------------------------------------------------------------
def test_programmatic_lines_carry_pattern_risk_and_content_needs():
    rows = [{"keyword": k, "search_volume": v, "cluster": "Payroll Software", "relevance_status": "Relevant"} for k, v in [
        ("payroll software", 5000), ("payroll software for construction", 400), ("payroll software for restaurants", 300),
        ("payroll software for nonprofits", 250), ("payroll software for small business", 900)]]
    slide = add_programmatic_seo_slide(_prs(), rows)
    text = _text(slide)
    assert "pattern: Payroll Software ×" in text and "thin-page risk" in text and "each page needs" in text


def test_bare_concept_is_not_a_core_topic_and_service_business_gets_service_page():
    concept = _summary("Stored Procedures", [("stored procedures", 1000), ("sql stored procedure", 880)])
    service = _summary("DBA Services", [("remote dba services", 260), ("dba support services", 140)])
    for s in (concept, service):
        s["opportunity"] = 60
    assign_cluster_types([concept, service], [{"parent": "Sql", "clusters": ["Stored Procedures", "DBA Services"]}],
                         {"models": ["Service", "B2B"]})
    assert concept["cluster_type"] != "Core Topic"
    assert service["cluster_type"] == "Core Topic" and service["page_type"] == "Service Page"


def test_own_product_pages_are_not_e_commerce():
    pages = [{"page_url": f"https://www.geopits.com{p}"} for p in (
        "/products/geodatamon", "/products/geoops", "/products/migrationiq", "/service/remote-support-dba-services")]
    models = infer_business_model(pages, None)["models"]
    assert "E-commerce" not in models and "Product" in models and "Service" in models
    shop = [{"page_url": f"https://x.com{p}"} for p in ("/cart", "/checkout", "/products/red-shoe")]
    assert "E-commerce" in infer_business_model(shop, None)["models"]


def test_redirect_only_for_error_pages_never_for_existing_301s():
    from app.services.keyword_relevance_service import is_error_page
    assert is_error_page({"page_url": "https://x.com/old", "http_status_code": "404"})
    assert not is_error_page({"page_url": "https://x.com/old", "http_status_code": "301"})
    assert not is_error_page({"page_url": "https://x.com/remote-support-dba-services", "http_status_code": "200"})
    s = _summary("A", [("alpha service", 900)], url="https://x.com/service/a", strength="strong",
                 roadmap_priority="High", opportunity=80)
    s["rows"][0]["current_url"] = "https://x.com/service/a"  # ranks on the target itself
    assign_decisions([s], dead_urls={"https://x.com/service/a"})
    assert s["decision"] != "REDIRECT"


def test_service_cluster_never_targets_a_blog_just_because_it_ranks():
    from app.services.keyword_intelligence_service import ranking_page_target
    from app.services.keyword_relevance_service import classify_page_type
    rows = [{"keyword": "database administration services", "current_position": 1, "search_volume": 170,
             "current_url": "https://x.com/blog/outsourcing-database-administration"}]
    assert ranking_page_target(rows, page_type_fn=classify_page_type, family="Commercial") is None
    assert ranking_page_target(rows, page_type_fn=classify_page_type, family="Informational")["url"].endswith("administration")


def test_one_shared_word_does_not_merge_clusters():
    from app.services.keyword_cluster_pipeline import _same_topic
    assert not _same_topic({"sql", "dba", "support"}, {"top", "rated", "managed", "data", "pipeline", "support"})
    assert _same_topic({"index", "sql"}, {"index", "database"})


# §13/§41/§43 abbreviation-aware merge — no longer needs the AI grouping step
# for the documented known limit ("remote dba services" / "database support
# services" share no literal words without it).
def test_abbreviation_expansion_catches_the_documented_known_limit():
    from app.services.keyword_cluster_pipeline import _same_topic
    from app.services.keyword_intelligence_service import expand_abbreviations
    assert expand_abbreviations({"dba"}) == {"dba", "database", "administrator"}
    assert _same_topic({"database", "support"}, {"dba", "remote"})


def test_review_queue_skips_low_priority_and_caps_each_type():
    many = []
    for i in range(30):
        s = _summary(f"C{i}", [(f"widget{i} thing", 100)], match_strength="weak")
        s.update(roadmap_priority="High" if i < 20 else "Low", opportunity=90 - i)
        many.append(s)
    queue = build_review_queue(many, [])
    unclear = [q for q in queue if q["type"] == "Unclear page mapping"]
    assert len(unclear) == 10 and all(int(q["item"][1:]) < 20 for q in unclear)
