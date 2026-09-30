import uuid
from types import SimpleNamespace

from app.api.routes import competitors
from app.api.routes.competitors import live_domain_ratings
from app.models.domain_rating import DomainRating
from app.models.semrush_import import SemrushImport

CLIENT_ID = uuid.uuid4()


class FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def distinct(self):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._rows


class FakeDB:
    def __init__(self, competitor_labels=(), manual_ratings=()):
        self._competitor_labels = list(competitor_labels)
        self._manual_ratings = list(manual_ratings)

    def query(self, *args):
        if args and args[0] is SemrushImport.domain_label:
            return FakeQuery(self._competitor_labels)
        if args and args[0] is DomainRating:
            return FakeQuery(self._manual_ratings)
        return FakeQuery([])


def _client(website_url="example.com"):
    return SimpleNamespace(website_url=website_url)


def _live(monkeypatch, db, dr_by_domain: dict, client=None):
    monkeypatch.setattr(competitors, "_get_owned_client", lambda *a: client or _client())
    monkeypatch.setattr(competitors, "fetch_domain_rating", lambda domain: dr_by_domain.get(domain))
    return live_domain_ratings(CLIENT_ID, db=db, current_user=None)


def test_ahrefs_success_marks_source_ahrefs(monkeypatch):
    db = FakeDB(competitor_labels=[("rival.com",)])
    result = _live(monkeypatch, db, {"example.com": 55, "rival.com": 40})
    assert result == [
        {"domain": "example.com", "dr": 55, "source": "ahrefs", "is_own": True},
        {"domain": "rival.com", "dr": 40, "source": "ahrefs", "is_own": False},
    ]


def test_ahrefs_failure_never_uses_manual_rating(monkeypatch):
    manual = [SimpleNamespace(domain="rival.com", dr=33)]
    db = FakeDB(competitor_labels=[("rival.com",)], manual_ratings=manual)
    result = _live(monkeypatch, db, {"example.com": 55})  # no ahrefs value for rival.com
    rival = next(r for r in result if r["domain"] == "rival.com")
    assert rival == {"domain": "rival.com", "dr": None, "source": "unavailable", "is_own": False}


def test_no_ahrefs_no_manual_is_unavailable(monkeypatch):
    db = FakeDB(competitor_labels=[("rival.com",)])
    result = _live(monkeypatch, db, {"example.com": 55})
    rival = next(r for r in result if r["domain"] == "rival.com")
    assert rival == {"domain": "rival.com", "dr": None, "source": "unavailable", "is_own": False}


def test_competitor_domains_deduped_against_own_domain(monkeypatch):
    db = FakeDB(competitor_labels=[("example.com",), ("rival.com",)])
    result = _live(monkeypatch, db, {"example.com": 55, "rival.com": 40})
    assert [r["domain"] for r in result] == ["example.com", "rival.com"]
