import csv
import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import competitors
from app.api.routes.competitors import _rows_to_csv, download_semrush_import

CLIENT_ID = uuid.uuid4()


def _download(monkeypatch, record):
    monkeypatch.setattr(competitors, "_get_owned_client", lambda *a: None)
    db = SimpleNamespace(get=lambda model, _id: record)
    return download_semrush_import(CLIENT_ID, uuid.uuid4(), db=db, current_user=None)


def _record(**kw):
    base = dict(client_id=CLIENT_ID, original_filename="bharatbenz-backlinks.csv", original_file=None, parsed_data={})
    return SimpleNamespace(**{**base, **kw})


def test_original_bytes_returned_unchanged(monkeypatch):
    res = _download(monkeypatch, _record(original_file=b"Keyword;Volume\ntruck;100\n"))
    assert res.body == b"Keyword;Volume\ntruck;100\n"
    assert "bharatbenz-backlinks.csv" in res.headers["content-disposition"]


def test_old_import_rebuilt_as_csv_and_named_so(monkeypatch):
    rows = [{"keyword": "truck", "domain_positions": {"a.com": 3}}, {"keyword": "bus", "cpc": 1.5}]
    res = _download(monkeypatch, _record(original_filename="gap.xlsx", parsed_data={"rows": rows}))
    assert "gap%20%28rebuilt%29.csv" in res.headers["content-disposition"]
    parsed = list(csv.reader(io.StringIO(res.body.decode("utf-8-sig"))))
    assert parsed == [["keyword", "domain_positions", "cpc"], ["truck", '{"a.com": 3}', ""], ["bus", "", "1.5"]]


def test_old_import_with_no_rows_is_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _download(monkeypatch, _record(parsed_data={"rows": []}))
    assert e.value.status_code == 404


def test_other_clients_import_is_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _download(monkeypatch, _record(client_id=uuid.uuid4(), original_file=b"x"))
    assert e.value.status_code == 404


def test_rows_to_csv_skips_non_dict_rows():
    assert _rows_to_csv(["junk", {"a": 1}]).decode("utf-8-sig").splitlines() == ["a", "1"]
