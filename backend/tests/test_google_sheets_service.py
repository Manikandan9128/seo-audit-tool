from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.services.google_sheets_service import (
    NoDriveFolderConfigured,
    NoSheetsCredentials,
    create_competitor_keyword_sheet,
)


def test_raises_when_unconfigured():
    original = settings.google_service_account_json
    settings.google_service_account_json = ""
    try:
        with pytest.raises(NoSheetsCredentials):
            create_competitor_keyword_sheet("Client", "competitor.com", [{"keyword": "x"}])
    finally:
        settings.google_service_account_json = original


def test_raises_when_no_drive_folder_configured():
    original_json = settings.google_service_account_json
    original_folder = settings.google_drive_folder_id
    settings.google_service_account_json = '{"client_email": "x@y.iam.gserviceaccount.com", "private_key": "fake", "type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"}'
    settings.google_drive_folder_id = ""
    try:
        with patch("app.services.google_sheets_service.service_account.Credentials.from_service_account_info"), \
             patch("app.services.google_sheets_service.build"):
            with pytest.raises(NoDriveFolderConfigured):
                create_competitor_keyword_sheet("Client", "competitor.com", [{"keyword": "x"}])
    finally:
        settings.google_service_account_json = original_json
        settings.google_drive_folder_id = original_folder


def test_oauth_credentials_preferred_and_need_no_folder():
    original_folder = settings.google_drive_folder_id
    settings.google_drive_folder_id = ""
    fake_creds = MagicMock()
    fake_drive = MagicMock()
    fake_drive.files.return_value.create.return_value.execute.return_value = {"id": "sheet123"}
    fake_sheets = MagicMock()
    try:
        with patch(
            "app.services.app_settings_service.get_sheets_oauth_credentials", return_value=fake_creds
        ), patch("app.services.google_sheets_service.build", side_effect=lambda name, v, credentials: (
            fake_sheets if name == "sheets" else fake_drive
        )):
            url = create_competitor_keyword_sheet("Client", "competitor.com", [{"keyword": "x"}], db=MagicMock())
        assert "sheet123" in url
        create_kwargs = fake_drive.files.return_value.create.call_args.kwargs
        assert "parents" not in create_kwargs["body"]
    finally:
        settings.google_drive_folder_id = original_folder
