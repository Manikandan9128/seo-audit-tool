from app.services.keyword_relevance_service import _classify_keyword_page_category, match_existing_page


def test_comparison_signal_wins_even_with_semrush_intent_present():
    # Comparison is a page-format signal Semrush's Intent taxonomy has no
    # concept of, so the word-list check must win outright regardless of intent.
    assert _classify_keyword_page_category("payroll software vs alternative", "Commercial") == "Comparison / Alternative"


def test_semrush_informational_intent_overrides_landing_word_in_text():
    # Regression guard: "pricing guide" contains the Landing signal "pricing"
    # AND the Blog signal "guide" — old first-match-wins word list picked
    # Blog since it was checked first, even when Semrush's real intent data
    # says this is actually commercial. Semrush intent must win here.
    assert _classify_keyword_page_category("pricing guide", "Commercial") == "Landing Page"


def test_semrush_transactional_intent_overrides_blog_word_in_text():
    assert _classify_keyword_page_category("how to buy payroll software", "Transactional") == "Landing Page"


def test_semrush_informational_intent_maps_to_blog():
    assert _classify_keyword_page_category("random keyword with no signal words", "Informational") == "Blog / Guide"


def test_falls_back_to_word_list_when_intent_missing():
    assert _classify_keyword_page_category("pricing guide", None) == "Blog / Guide"
    assert _classify_keyword_page_category("pricing guide", "") == "Blog / Guide"


def test_match_existing_page_finds_real_overlap():
    pages = [
        {"page_url": "https://example.com/certified-payroll-software", "page_title": "Certified Payroll Software | Example"},
        {"page_url": "https://example.com/about", "page_title": "About Us"},
    ]
    match = match_existing_page("certified payroll software", pages)
    assert match is not None
    assert match["url"] == "https://example.com/certified-payroll-software"


def test_match_existing_page_returns_none_when_no_real_overlap():
    pages = [{"page_url": "https://example.com/about", "page_title": "About Us"}]
    assert match_existing_page("certified payroll software", pages) is None


def test_match_existing_page_returns_none_for_empty_pages():
    assert match_existing_page("certified payroll software", []) is None
    assert match_existing_page("certified payroll software", None) is None


def test_match_existing_page_rejects_single_incidental_word_overlap():
    # Regression guard: sharing just one common-ish word shouldn't count as
    # "this page already covers the keyword" for a multi-word keyword.
    pages = [{"page_url": "https://example.com/software-careers", "page_title": "Careers at Example Software"}]
    assert match_existing_page("certified payroll reporting compliance", pages) is None


def test_falls_back_to_word_list_when_intent_navigational():
    # Navigational doesn't map to either Blog or Landing, so text signals decide.
    assert _classify_keyword_page_category("enterprise pricing", "Navigational") == "Landing Page"


def test_returns_none_when_no_signal_and_no_usable_intent():
    assert _classify_keyword_page_category("acme corp login", "Navigational") is None
