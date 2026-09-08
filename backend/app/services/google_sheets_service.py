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


class NoDriveFolderConfigured(Exception):
    pass


def _create_spreadsheet_file(drive, title: str) -> str:
    """Creates the (initially empty) spreadsheet via the Drive API rather
    than sheets.spreadsheets().create(), and — critically — inside a folder
    a real human owns and shared with the service account (Editor access),
    not the service account's own Drive. A bare service account has no
    Drive storage of its own under a Google Workspace org's default policy
    (confirmed real: sheets.spreadsheets().create() alone returned a 403
    "The caller does not have permission" with both Sheets API and Drive
    API enabled and valid credentials — the well-documented symptom of this
    exact limitation, not a config-enablement problem). Creating into a
    human-owned shared folder sidesteps it entirely: the file's storage
    quota comes from that human's Drive, and the service account only
    needs Editor access to the folder, granted once at setup."""
    if not settings.google_drive_folder_id:
        raise NoDriveFolderConfigured(
            "No Google Drive folder configured — create a folder, share it with the service account "
            "email (Editor access), and add its folder ID in Settings"
        )
    file = drive.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [settings.google_drive_folder_id],
        },
        fields="id",
    ).execute()
    return file["id"]


def _test_connection() -> None:
    """Create + immediately delete a throwaway spreadsheet in the shared
    folder — proves the service account, Sheets API, Drive API, and the
    shared-folder access are all actually working together. Raises on any
    failure; caller decides how to report it."""
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    spreadsheet_id = _create_spreadsheet_file(drive, "SEO Audit Tool — connection test")
    drive.files().delete(fileId=spreadsheet_id).execute()


def create_competitor_keyword_sheet(client_name: str, domain: str, rows: list[dict]) -> str:
    """Creates a new Sheet (inside the shared Drive folder — see
    _create_spreadsheet_file) titled after the client + competitor, writes
    the full (uncapped) keyword list, sets it link-viewable, and returns the
    edit URL. Raises NoServiceAccountConfigured/NoDriveFolderConfigured if
    unconfigured, or whatever the Google API raises on a real failure —
    caller falls back to the old capped-table slide on any exception, see
    pptx_builder.py."""
    creds = _credentials()
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    title = f"{client_name} — Competitor Keywords — {domain}"[:200]
    spreadsheet_id = _create_spreadsheet_file(drive, title)

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

    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
