"""Competitor Organic Positions uploads take their domain from the uploaded
file itself (Semrush's export filename, else the URL column), never from
the label typed into the upload form — a typed label is how a domain the
file never mentions reached a "Competitor Keywords" slide."""

from app.api.routes.site_audit import _positions_file_domain


def test_domain_from_semrush_default_filename():
    assert _positions_file_domain("tatamotors.com-organic.Positions-in-20260901.csv", []) == "tatamotors.com"


def test_subdomain_filename_kept_exactly():
    rows = [{"url": "https://www.tatamotors.com/x"}]
    assert _positions_file_domain("trucks.tatamotors.com-organic.Positions-in-20260901.xlsx", rows) == "trucks.tatamotors.com"


def test_renamed_file_falls_back_to_most_common_url_host():
    rows = [
        {"url": "https://www.ashokleyland.com/a"},
        {"url": "https://www.ashokleyland.com/b"},
        {"url": "https://careers.ashokleyland.com/c"},
    ]
    assert _positions_file_domain("competitor positions.csv", rows) == "www.ashokleyland.com"


def test_none_when_file_has_no_domain():
    assert _positions_file_domain("positions.csv", [{"keyword": "truck"}]) is None
    assert _positions_file_domain(None, []) is None
