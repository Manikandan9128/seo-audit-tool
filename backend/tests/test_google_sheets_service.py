from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.services.google_sheets_service import (
    NoDriveFolderConfigured,
    NoServiceAccountConfigured,
    create_competitor_keyword_sheet,
)


def test_raises_when_unconfigured():
    original = settings.google_service_account_json
    settings.google_service_account_json = ""
    try:
        with pytest.raises(NoServiceAccountConfigured):
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
