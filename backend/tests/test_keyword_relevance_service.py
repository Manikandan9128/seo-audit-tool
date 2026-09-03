from app.services.keyword_relevance_service import _classify_keyword_page_category


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


def test_falls_back_to_word_list_when_intent_navigational():
    # Navigational doesn't map to either Blog or Landing, so text signals decide.
    assert _classify_keyword_page_category("enterprise pricing", "Navigational") == "Landing Page"


def test_returns_none_when_no_signal_and_no_usable_intent():
    assert _classify_keyword_page_category("acme corp login", "Navigational") is None
