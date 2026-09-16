"""Regression test for the BharatBenz incident: a real Semrush Keyword Gap
export compares the client's own domain against several competitors in ONE
file, and includes every keyword any compared domain ranks for — including
keywords entirely outside the client's own business that a broader-scope
competitor happens to rank for (BharatBenz, a commercial-truck brand, is
compared against tatamotors.com, which also sells passenger cars — so "tata
nexon" showed up as one of BharatBenz's own "Target Keywords"). This checks
that _filter_own_keyword_rows strips those out the same way
_filter_competitor_keywords already does for the competitor-facing pipeline.
"""

from types import SimpleNamespace

from app.api.routes import site_audit


def _client():
    return SimpleNamespace(name="Bharatbenz", website_url="https://www.bharatbenz.com")


def test_irrelevant_keywords_are_stripped(monkeypatch):
    rows = [
        {"keyword": "6x4", "search_volume": 27100},
        {"keyword": "dumper lorry", "search_volume": 27100},
        {"keyword": "tanker truck", "search_volume": 5400},
        {"keyword": "tata nexon", "search_volume": 4400},
        {"keyword": "tata motors share price", "search_volume": 9900},
        {"keyword": "jaguar land rover new", "search_volume": 4400},
    ]
    data = {"keyword_rows": rows, "company_overview": None}

    def fake_classify(client_name, client_domain, brand_tokens, keywords, client_description=None):
        exclude = {"tata nexon", "tata motors share price", "jaguar land rover new"}
        return {kw: ("exclude" if kw in exclude else "highly_relevant") for kw in keywords}

    monkeypatch.setattr(site_audit, "classify_keywords", fake_classify)

    site_audit._filter_own_keyword_rows(_client(), data)

    kept = {r["keyword"] for r in data["keyword_rows"]}
    assert kept == {"6x4", "dumper lorry", "tanker truck"}


def test_fails_open_when_classifier_returns_nothing(monkeypatch):
    rows = [{"keyword": "tata nexon", "search_volume": 4400}]
    data = {"keyword_rows": rows, "company_overview": None}

    monkeypatch.setattr(site_audit, "classify_keywords", lambda *a, **k: {})

    site_audit._filter_own_keyword_rows(_client(), data)

    assert data["keyword_rows"] == rows


def test_keyword_outside_candidate_cap_is_left_untouched(monkeypatch):
    # Below the classifier's candidate pool (sorted by volume, capped at
    # _CLASSIFY_CANDIDATE_CAP) should never be silently dropped without
    # ever being judged.
    high_volume_padding = [
        {"keyword": f"padding {i}", "search_volume": 100000} for i in range(site_audit._CLASSIFY_CANDIDATE_CAP)
    ]
    low_volume_row = {"keyword": "tata nexon", "search_volume": 1}
    data = {"keyword_rows": high_volume_padding + [low_volume_row], "company_overview": None}

    def fake_classify(client_name, client_domain, brand_tokens, keywords, client_description=None):
        return {kw: "highly_relevant" for kw in keywords}

    monkeypatch.setattr(site_audit, "classify_keywords", fake_classify)

    site_audit._filter_own_keyword_rows(_client(), data)

    kept = {r["keyword"] for r in data["keyword_rows"]}
    assert "tata nexon" in kept
