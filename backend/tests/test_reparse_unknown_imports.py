from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.api.routes.site_audit import _reparse_unknown_imports


def _imp(t="unknown", data=b"x"):
    return SimpleNamespace(import_type=t, original_filename="shot.png", original_file=data, parsed_data={"row_count": 0, "rows": []})


def test_unknown_upload_is_identified_and_saved_at_report_time():
    imp, db = _imp(), MagicMock()
    with patch("app.services.semrush_parser.parse_semrush_file", return_value=("site_audit_overview", {"row_count": 1, "rows": [{"site_health_pct": 67}]})):
        _reparse_unknown_imports(db, [imp])
    assert imp.import_type == "site_audit_overview"
    assert imp.parsed_data["rows"][0]["site_health_pct"] == 67
    db.commit.assert_called_once()


def test_still_unknown_stays_unknown_and_known_types_untouched():
    still, known, db = _imp(), _imp("backlinks"), MagicMock()
    with patch("app.services.semrush_parser.parse_semrush_file", return_value=("unknown", {"row_count": 0, "rows": []})) as parse:
        _reparse_unknown_imports(db, [still, known])
    assert still.import_type == "unknown" and known.import_type == "backlinks"
    assert parse.call_count == 1
    db.commit.assert_not_called()


def test_parse_error_or_missing_bytes_never_breaks_the_report():
    bad, nobytes, db = _imp(), _imp(data=None), MagicMock()
    with patch("app.services.semrush_parser.parse_semrush_file", side_effect=RuntimeError("boom")):
        _reparse_unknown_imports(db, [bad, nobytes])
    assert bad.import_type == "unknown"
