from unittest.mock import MagicMock, patch

from app.services import ahrefs_service


def test_no_key_configured_returns_none_without_network_call():
    with patch("app.services.ahrefs_service.settings") as mock_settings:
        mock_settings.ahrefs_api_key = ""
        with patch("app.services.ahrefs_service.httpx.get") as mock_get:
            assert ahrefs_service.fetch_domain_rating("example.com") is None
            mock_get.assert_not_called()


def test_successful_lookup_returns_rounded_dr():
    response = MagicMock(status_code=200)
    response.json.return_value = {"domain_rating": {"domain_rating": 54.7, "license": "CC BY-NC 4.0"}}
    with patch("app.services.ahrefs_service.settings") as mock_settings:
        mock_settings.ahrefs_api_key = "fake-key"
        with patch("app.services.ahrefs_service.httpx.get", return_value=response):
            assert ahrefs_service.fetch_domain_rating("example.com") == 55


def test_non_200_response_returns_none():
    response = MagicMock(status_code=401, text="unauthorized")
    with patch("app.services.ahrefs_service.settings") as mock_settings:
        mock_settings.ahrefs_api_key = "bad-key"
        with patch("app.services.ahrefs_service.httpx.get", return_value=response):
            assert ahrefs_service.fetch_domain_rating("example.com") is None


def test_network_error_returns_none_not_raises():
    with patch("app.services.ahrefs_service.settings") as mock_settings:
        mock_settings.ahrefs_api_key = "fake-key"
        with patch("app.services.ahrefs_service.httpx.get", side_effect=ConnectionError("boom")):
            assert ahrefs_service.fetch_domain_rating("example.com") is None
