"""Regression tests for filter_other_brand_keywords / is_branded_or_near_brand's
compound-domain prefix matching — BharatBenz kept surfacing competitor brand
names in its own Target Keywords slide because domains like
"mahindratruckandbus.com" have no corporate-suffix ending (Motors/Group/Corp/
...) brand_token_variants could strip, so a plain whole-word match against
the raw compound token never fired for real, space-separated search queries
like "mahindra dealer near me"."""

from app.services.keyword_relevance_service import (
    filter_other_brand_keywords,
    is_branded_or_near_brand,
)

BHARATBENZ_COMPETITOR_DOMAINS = [
    "tatamotors.com",
    "mahindratruckandbus.com",
    "ashokleyland.com",
    "vecv.in",
]


def test_compound_domain_with_no_corporate_suffix_matches_leading_brand_word():
    assert is_branded_or_near_brand("mahindra dealer near me", {"mahindratruckandbus"})
    assert is_branded_or_near_brand("mahindra dealers near me", {"mahindratruckandbus"})


def test_ashok_leyland_two_word_brand_matches_via_prefix():
    assert is_branded_or_near_brand("ashok leyland dealer near me", {"ashokleyland"})


def test_short_generic_word_does_not_false_positive_as_prefix():
    # "cars" is a genuine prefix-length-4 word (below the >=5 gate) and
    # should never accidentally match a long unrelated brand token.
    assert not is_branded_or_near_brand("cars for sale", {"carsathomedelivery"})


def test_filter_other_brand_keywords_strips_mahindra_row_end_to_end():
    rows = [
        {"keyword": "mahindra dealer near me"},
        {"keyword": "mahindra dealers near me"},
        {"keyword": "bharatbenz truck price"},
        {"keyword": "heavy duty truck dealer"},
    ]
    kept = filter_other_brand_keywords(rows, "bharatbenz.com", BHARATBENZ_COMPETITOR_DOMAINS)
    kept_keywords = {r["keyword"] for r in kept}
    assert kept_keywords == {"bharatbenz truck price", "heavy duty truck dealer"}


def test_filter_other_brand_keywords_still_strips_tata_via_corporate_suffix():
    rows = [
        {"keyword": "tata nexon price"},
        {"keyword": "tata sierra review"},
        {"keyword": "bharatbenz truck price"},
    ]
    kept = filter_other_brand_keywords(rows, "bharatbenz.com", BHARATBENZ_COMPETITOR_DOMAINS)
    kept_keywords = {r["keyword"] for r in kept}
    assert kept_keywords == {"bharatbenz truck price"}


def test_filter_other_brand_keywords_never_excludes_clients_own_brand():
    rows = [{"keyword": "bharatbenz truck dealer"}]
    kept = filter_other_brand_keywords(rows, "bharatbenz.com", BHARATBENZ_COMPETITOR_DOMAINS)
    assert len(kept) == 1
