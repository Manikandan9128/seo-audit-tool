from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from app.api.routes.google_oauth import _google_error_message


def _http_error(status: int, reason: str) -> HttpError:
    resp = MagicMock(status=status)
    err = HttpError(resp, b"{}", uri="https://example.com")
    err._get_reason = lambda: reason
    return err


def test_403_includes_real_reason_not_just_generic_permission_text():
    # 2026-09-21 bugfix: collapsing every 403 to one generic sentence hid
    # the real distinction (API-not-enabled vs. no-access-to-property) a
    # user reconnecting repeatedly needs to actually see.
    e = _http_error(403, "Analytics Admin API has not been used in project 123 before or it is disabled.")
    msg = _google_error_message(e)
    assert "doesn't have permission" in msg
    assert "Analytics Admin API has not been used" in msg


def test_non_403_includes_reason_when_available():
    e = _http_error(429, "Quota exceeded for quota metric 'Requests'.")
    msg = _google_error_message(e)
    assert "429" in msg
    assert "Quota exceeded" in msg


def test_falls_back_gracefully_when_reason_unavailable():
    resp = MagicMock(status=500)
    e = HttpError(resp, b"{}", uri="https://example.com")
    msg = _google_error_message(e)
    assert "500" in msg
