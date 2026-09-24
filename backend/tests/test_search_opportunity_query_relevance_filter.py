"""Search Opportunities — Pages/Query Relevance Fix (2026-09-24 spec):
"only include a keyword/query as a Search Opportunity when it is genuinely
relevant to the target page and business... exclude irrelevant/noise
queries even if they have high impressions." _filter_search_opportunity_
queries stamps the AI relevance label onto each candidate GSC (page,
query) row in place, the same classify_keywords/candidate-cap pattern
test_own_keyword_relevance_filter.py already covers for
_filter_keyword_rows."""

from types import SimpleNamespace

from app.api.routes import site_audit


def _client():
    return SimpleNamespace(name="Acme", website_url="https://www.acme.com")


def test_off_topic_query_is_labeled_exclude(monkeypatch):
    rows = [{"page": "https://acme.com/hydraulic-lifts", "query": "unrelated celebrity gossip", "impressions": 900, "position": 5.0}]

    def fake_classify(client_name, client_domain, brand_tokens, keywords, client_description=None):
        return {kw: {"label": "exclude", "status": "Industry Mismatch", "reason": "off-topic"} for kw in keywords}

    monkeypatch.setattr(site_audit, "classify_keywords", fake_classify)
    site_audit._filter_search_opportunity_queries(_client(), rows, None, None, set(), None)
    assert rows[0]["relevance"] == "exclude"


def test_relevant_query_is_labeled_highly_relevant(monkeypatch):
    rows = [{"page": "https://acme.com/hydraulic-lifts", "query": "hydraulic lifts", "impressions": 800, "position": 5.5}]

    def fake_classify(client_name, client_domain, brand_tokens, keywords, client_description=None):
        return {kw: {"label": "highly_relevant", "status": "Core Relevant", "reason": "direct match"} for kw in keywords}

    monkeypatch.setattr(site_audit, "classify_keywords", fake_classify)
    site_audit._filter_search_opportunity_queries(_client(), rows, None, None, set(), None)
    assert rows[0]["relevance"] == "highly_relevant"


def test_branded_query_never_sent_to_classifier(monkeypatch):
    rows = [{"page": "https://acme.com/about", "query": "acme corp", "impressions": 900, "position": 5.0}]
    seen_keywords = []

    def fake_classify(client_name, client_domain, brand_tokens, keywords, client_description=None):
        seen_keywords.extend(keywords)
        return {kw: {"label": "exclude"} for kw in keywords}

    monkeypatch.setattr(site_audit, "classify_keywords", fake_classify)
    site_audit._filter_search_opportunity_queries(_client(), rows, None, None, {"acme"}, None)
    assert seen_keywords == []
    assert "relevance" not in rows[0]


def test_fails_open_when_classifier_returns_nothing(monkeypatch):
    rows = [{"page": "https://acme.com/hydraulic-lifts", "query": "hydraulic lifts", "impressions": 800, "position": 5.5}]
    monkeypatch.setattr(site_audit, "classify_keywords", lambda *a, **k: {})
    site_audit._filter_search_opportunity_queries(_client(), rows, None, None, set(), None)
    assert "relevance" not in rows[0]


def test_query_outside_the_candidate_cap_is_left_untouched(monkeypatch):
    padding = [
        {"page": "https://acme.com/x", "query": f"padding query {i}", "impressions": 100000, "position": 5.0}
        for i in range(site_audit._CLASSIFY_CANDIDATE_CAP)
    ]
    low_impression_row = {"page": "https://acme.com/hydraulic-lifts", "query": "hydraulic lifts", "impressions": 11, "position": 5.5}
    rows = padding + [low_impression_row]

    def fake_classify(client_name, client_domain, brand_tokens, keywords, client_description=None):
        return {kw: {"label": "highly_relevant"} for kw in keywords}

    monkeypatch.setattr(site_audit, "classify_keywords", fake_classify)
    site_audit._filter_search_opportunity_queries(_client(), rows, None, None, set(), None)
    assert "relevance" not in low_impression_row


def test_no_page_query_rows_is_a_noop():
    site_audit._filter_search_opportunity_queries(_client(), [], None, None, set(), None)  # must not raise
    site_audit._filter_search_opportunity_queries(_client(), None, None, None, set(), None)  # must not raise
