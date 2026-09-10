from unittest.mock import MagicMock, patch

import pytest

from app.services.google_sheets_service import NoSheetsCredentials, create_combined_keyword_sheet


def test_raises_when_no_oauth_connection():
    with patch("app.services.app_settings_service.get_sheets_oauth_credentials", return_value=None):
        with pytest.raises(NoSheetsCredentials):
            create_combined_keyword_sheet("Client", [{"keyword": "x"}], {"competitor.com": [{"keyword": "y"}]}, db=MagicMock())


def test_returns_none_when_nothing_to_write():
    fake_creds = MagicMock()
    with patch("app.services.app_settings_service.get_sheets_oauth_credentials", return_value=fake_creds):
        url = create_combined_keyword_sheet("Client", [], {}, db=MagicMock())
    assert url is None


def _run_with_mocks(client_rows, competitor_positions):
    fake_creds = MagicMock()
    fake_drive = MagicMock()
    fake_drive.files.return_value.create.return_value.execute.return_value = {"id": "sheet123"}
    fake_sheets = MagicMock()
    with patch(
        "app.services.app_settings_service.get_sheets_oauth_credentials", return_value=fake_creds
    ), patch("app.services.google_sheets_service.build", side_effect=lambda name, v, credentials: (
        fake_sheets if name == "sheets" else fake_drive
    )):
        url = create_combined_keyword_sheet("Client", client_rows, competitor_positions, db=MagicMock())
    return url, fake_sheets, fake_drive


def test_creates_one_spreadsheet_with_a_tab_per_domain():
    url, fake_sheets, _ = _run_with_mocks(
        [{"keyword": "x", "cluster": "c", "search_volume": 10, "keyword_difficulty": 5, "intent": "informational"}],
        {"competitor.com": [{"keyword": "y", "search_volume": 20, "keyword_difficulty": 8, "position": 3, "previous_position": 5}]},
    )
    assert "sheet123" in url
    batch_requests = fake_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]["requests"]
    # First tab renamed (client), one addSheet per competitor.
    assert batch_requests[0]["updateSheetProperties"]["properties"]["title"] == "Client"
    assert any("addSheet" in r and r["addSheet"]["properties"]["title"] == "competitor.com" for r in batch_requests)


def test_no_row_cap_on_a_large_competitor_export():
    # Confirmed real (2026-09-10): a competitor with 10,000+ tracked
    # keywords must get every row — neither this function nor the parser
    # caps organic_positions data.
    big_export = [{"keyword": f"kw{i}", "search_volume": i} for i in range(12000)]
    _, fake_sheets, _ = _run_with_mocks([], {"rippling.com": big_export})
    value_ranges = fake_sheets.spreadsheets.return_value.values.return_value.batchUpdate.call_args.kwargs["body"]["data"]
    rippling_range = next(v for v in value_ranges if "rippling.com" in v["range"])
    assert len(rippling_range["values"]) == 12000 + 1  # + header row


def test_domain_with_no_rows_is_skipped_not_an_empty_tab():
    url, fake_sheets, _ = _run_with_mocks([{"keyword": "x"}], {"empty-competitor.com": []})
    batch_requests = fake_sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]["requests"]
    assert not any("addSheet" in r for r in batch_requests)
