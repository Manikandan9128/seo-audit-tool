from app.services.url_mapping_service import (
    CREATE_NEW,
    INTERNAL_LINK,
    MERGE,
    OPTIMIZE_EXISTING,
    REDIRECT,
    SITEWIDE,
    build_context,
    resolve_for_keyword,
    resolve_for_pages,
    resolve_sitewide,
)


def test_level1_own_semrush_ranking_url_wins_outright():
    ctx = build_context(
        own_organic_positions_rows=[{"keyword": "payroll software", "position": 8, "url": "https://x.com/payroll"}],
        gsc_query_page_rows=[{"query": "payroll software", "page": "https://x.com/other", "position": 3, "impressions": 500}],
    )
    result = resolve_for_keyword(ctx, "payroll software")
    assert result["target_url"] == "https://x.com/payroll"
    assert result["url_action"] == OPTIMIZE_EXISTING
    assert result["current_ranking_keyword"] == "payroll software"
    assert result["current_position"] == 8


def test_level2_gsc_query_to_page_used_when_no_semrush_ranking():
    ctx = build_context(
        gsc_query_page_rows=[{"query": "certified payroll", "page": "https://x.com/certified", "position": 12, "impressions": 300}],
    )
    result = resolve_for_keyword(ctx, "certified payroll")
    assert result["target_url"] == "https://x.com/certified"
    assert result["url_action"] == OPTIMIZE_EXISTING
    assert result["current_position"] == 12


def test_level3_existing_crawl_url_strong_title_match():
    ctx = build_context(crawl_pages=[{"url": "https://x.com/certified-payroll-services", "title": "Certified Payroll Services"}])
    result = resolve_for_keyword(ctx, "certified payroll")
    assert result["target_url"] == "https://x.com/certified-payroll-services"
    assert result["url_action"] == OPTIMIZE_EXISTING
    assert result["current_ranking_keyword"] is None


def test_level4_canonical_redirects_matched_page():
    ctx = build_context(
        crawl_pages=[{"url": "https://x.com/certified-payroll-copy", "title": "Certified Payroll"}],
        canonicalization_by_url={"https://x.com/certified-payroll-copy": "Canonical to https://x.com/certified-payroll-main"},
    )
    result = resolve_for_keyword(ctx, "certified payroll")
    assert result["target_url"] == "https://x.com/certified-payroll-main"
    assert result["url_action"] == MERGE


def test_level5_search_intent_creates_new_when_no_page_exists():
    ctx = build_context()
    result = resolve_for_keyword(ctx, "government payroll rules", intent="Informational")
    assert result["target_url"] is None
    assert result["url_action"] == CREATE_NEW
    assert result["target_page_type"] == "Blog / Guide"


def test_level6_page_type_from_keyword_text_when_no_intent():
    ctx = build_context()
    result = resolve_for_keyword(ctx, "payroll software pricing")
    assert result["url_action"] == CREATE_NEW
    assert result["target_page_type"] == "Landing Page"


def test_level7_weak_relevance_suggests_internal_link():
    ctx = build_context(crawl_pages=[{"url": "https://x.com/services", "title": "Our Payroll Services Overview"}])
    # "certified payroll audit" only partially overlaps "payroll" — not a full
    # title match (level 3 needs every token), so falls to level 7.
    result = resolve_for_keyword(ctx, "certified payroll audit")
    assert result["url_action"] == INTERNAL_LINK
    assert result["target_url"] == "https://x.com/services"


def test_no_signal_anywhere_returns_empty_mapping():
    ctx = build_context()
    result = resolve_for_keyword(ctx, "zzz nonsense query")
    assert result["target_url"] is None
    assert result["url_action"] is None


def test_resolve_for_pages_uses_supplied_action():
    ctx = build_context(crawl_pages=[{"url": "https://x.com/broken", "title": "Broken Page"}])
    result = resolve_for_pages(ctx, ["https://x.com/broken"], REDIRECT)
    assert result["target_url"] == "https://x.com/broken"
    assert result["url_action"] == REDIRECT


def test_resolve_for_pages_multiple_urls_returns_url_set():
    ctx = build_context()
    result = resolve_for_pages(ctx, ["https://x.com/a", "https://x.com/b"], MERGE)
    assert result["target_url"] == ["https://x.com/a", "https://x.com/b"]


def test_resolve_sitewide_has_no_single_target_url():
    result = resolve_sitewide("Domain-wide (Backlinks)")
    assert result["target_url"] is None
    assert result["url_action"] == SITEWIDE
    assert result["target_page_type"] == "Domain-wide (Backlinks)"
