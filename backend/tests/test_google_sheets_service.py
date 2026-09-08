import pytest

from app.config import settings
from app.services.google_sheets_service import NoServiceAccountConfigured, create_competitor_keyword_sheet


def test_raises_when_unconfigured():
    original = settings.google_service_account_json
    settings.google_service_account_json = ""
    try:
        with pytest.raises(NoServiceAccountConfigured):
            create_competitor_keyword_sheet("Client", "competitor.com", [{"keyword": "x"}])
    finally:
        settings.google_service_account_json = original
