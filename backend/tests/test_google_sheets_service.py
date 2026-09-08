from unittest.mock import MagicMock, patch

import pytest

from app.services.google_sheets_service import NoSheetsCredentials, create_competitor_keyword_sheet


def test_raises_when_no_oauth_connection():
    with patch("app.services.app_settings_service.get_sheets_oauth_credentials", return_value=None):
        with pytest.raises(NoSheetsCredentials):
            create_competitor_keyword_sheet("Client", "competitor.com", [{"keyword": "x"}], db=MagicMock())


def test_creates_sheet_under_connected_account():
    fake_creds = MagicMock()
    fake_drive = MagicMock()
    fake_drive.files.return_value.create.return_value.execute.return_value = {"id": "sheet123"}
    fake_sheets = MagicMock()
    with patch(
        "app.services.app_settings_service.get_sheets_oauth_credentials", return_value=fake_creds
    ), patch("app.services.google_sheets_service.build", side_effect=lambda name, v, credentials: (
        fake_sheets if name == "sheets" else fake_drive
    )):
        url = create_competitor_keyword_sheet("Client", "competitor.com", [{"keyword": "x"}], db=MagicMock())
    assert "sheet123" in url
    create_kwargs = fake_drive.files.return_value.create.call_args.kwargs
    assert "parents" not in create_kwargs["body"]
