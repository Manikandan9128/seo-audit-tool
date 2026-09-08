"""Creates a Google Sheet per competitor holding that competitor's FULL
keyword list (not the ~14-row table the report's per-competitor slide was
capped at) and shares it link-viewable, so the report can link out to
everything instead of showing a truncated table. Uses an app-owned Google
Cloud service account (google_service_account_json in Settings) — separate
from the per-client GA4/GSC OAuth flow in app.config, since these sheets
aren't tied to any one client's Google account and the service account
alone can create as many as needed, for any number of clients, with no
per-client setup."""

import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

_HEADER = ["Keyword", "Search Volume", "KD", "Position", "Previous Position"]


class NoServiceAccountConfigured(Exception):
    pass


def _credentials():
    if not settings.google_service_account_json:
        raise NoServiceAccountConfigured(
            "No Google service account configured — add one in Settings to enable competitor keyword sheets"
        )
    info = json.loads(settings.google_service_account_json)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _test_connection() -> None:
    """Create + immediately delete a throwaway spreadsheet — proves both the
    Sheets API (create) and Drive API (delete) are reachable and enabled for
    this service account. Raises on any failure; caller decides how to
    report it."""
    creds = _credentials()
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    spreadsheet = sheets.spreadsheets().create(
        body={"properties": {"title": "SEO Audit Tool — connection test"}},
        fields="spreadsheetId",
    ).execute()
    drive.files().delete(fileId=spreadsheet["spreadsheetId"]).execute()


def create_competitor_keyword_sheet(client_name: str, domain: str, rows: list[dict]) -> str:
    """Creates a new Sheet titled after the client + competitor, writes the
    full (uncapped) keyword list, sets it link-viewable, and returns the
    edit URL. Raises NoServiceAccountConfigured if unconfigured, or
    whatever the Google API raises on a real failure — caller falls back to
    the old capped-table slide on any exception, see pptx_builder.py."""
    creds = _credentials()
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    title = f"{client_name} — Competitor Keywords — {domain}"[:200]
    spreadsheet = sheets.spreadsheets().create(
        body={"properties": {"title": title}},
        fields="spreadsheetId,spreadsheetUrl",
    ).execute()
    spreadsheet_id = spreadsheet["spreadsheetId"]

    values = [_HEADER] + [
        [
            r.get("keyword", ""),
            r.get("search_volume", ""),
            r.get("keyword_difficulty", ""),
            r.get("position", ""),
            r.get("previous_position", ""),
        ]
        for r in rows
    ]
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    drive.permissions().create(
        fileId=spreadsheet_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return spreadsheet["spreadsheetUrl"]
