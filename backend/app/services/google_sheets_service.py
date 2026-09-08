"""Creates a Google Sheet per competitor holding that competitor's FULL
keyword list (not the ~14-row table the report's per-competitor slide was
capped at) and shares it link-viewable, so the report can link out to
everything instead of showing a truncated table.

Two ways to authenticate, tried in this order:

1. OAuth — the app owner's own Google account, connected once via Settings
   ("Connect Google Account" under Google Sheets). RECOMMENDED, especially
   for a plain personal Gmail account: the Sheet is created directly under
   that real account, so it just works exactly like manually creating a
   file in Drive — no storage-quota edge cases.
2. Service account (google_service_account_json in Settings) + a Drive
   folder shared with it (google_drive_folder_id) — works well on a real
   Google Workspace domain, but a bare service account has no Drive
   storage of its own, and on a plain personal Gmail account this can fail
   with a confirmed-real "storage quota exceeded" error even inside a
   folder shared with Editor access and plenty of real free space — a
   known Google Drive API quirk, not a config or quota problem. Kept as a
   fallback for setups where it does work (a real Workspace domain with
   Domain-wide Delegation would be the fully robust version of this path,
   but isn't implemented here since it needs a verified company domain)."""

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


class NoDriveFolderConfigured(Exception):
    pass


class NoSheetsCredentials(Exception):
    """Neither the OAuth connection nor the service account is configured."""


def _service_account_credentials():
    if not settings.google_service_account_json:
        raise NoServiceAccountConfigured(
            "No Google service account configured — add one in Settings to enable competitor keyword sheets"
        )
    info = json.loads(settings.google_service_account_json)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _resolve_credentials(db=None):
    """Returns (credentials, mode) — mode is "oauth" or "service_account".
    Tries the OAuth connection first when a db session is given (the
    preferred path — see module docstring); falls back to the service
    account otherwise. Raises NoSheetsCredentials if neither is usable."""
    if db is not None:
        from app.services.app_settings_service import get_sheets_oauth_credentials

        oauth_creds = get_sheets_oauth_credentials(db)
        if oauth_creds is not None:
            return oauth_creds, "oauth"
    if settings.google_service_account_json:
        return _service_account_credentials(), "service_account"
    raise NoSheetsCredentials(
        "No way to create Google Sheets configured — connect a Google account (recommended) or "
        "add a service account in Settings"
    )


def _create_spreadsheet_file(drive, title: str, mode: str) -> str:
    """Creates the (initially empty) spreadsheet via the Drive API. In OAuth
    mode this needs no parent folder — it's created directly in that
    account's own Drive, same as if they'd clicked "New" themselves. In
    service-account mode it MUST go inside a folder a real human shared
    with the service account (Editor access) — a bare service account has
    no Drive storage of its own to create into otherwise."""
    body = {"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"}
    if mode == "service_account":
        if not settings.google_drive_folder_id:
            raise NoDriveFolderConfigured(
                "No Google Drive folder configured — create a folder, share it with the service account "
                "email (Editor access), and add its folder ID in Settings"
            )
        body["parents"] = [settings.google_drive_folder_id]
    file = drive.files().create(body=body, fields="id").execute()
    return file["id"]


def _test_connection(db=None) -> str:
    """Create + immediately delete a throwaway spreadsheet — proves the
    resolved credentials, Sheets API, and Drive API are all actually
    working together. Raises on any failure; caller decides how to report
    it. Returns which mode was used ("oauth" or "service_account")."""
    creds, mode = _resolve_credentials(db)
    drive = build("drive", "v3", credentials=creds)
    spreadsheet_id = _create_spreadsheet_file(drive, "SEO Audit Tool — connection test", mode)
    drive.files().delete(fileId=spreadsheet_id).execute()
    return mode


def create_competitor_keyword_sheet(client_name: str, domain: str, rows: list[dict], db=None) -> str:
    """Creates a new Sheet titled after the client + competitor, writes the
    full (uncapped) keyword list, sets it link-viewable, and returns the
    edit URL. Raises NoSheetsCredentials/NoDriveFolderConfigured if
    unconfigured, or whatever the Google API raises on a real failure —
    caller falls back to the old capped-table slide on any exception, see
    pptx_builder.py."""
    creds, mode = _resolve_credentials(db)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    title = f"{client_name} — Competitor Keywords — {domain}"[:200]
    spreadsheet_id = _create_spreadsheet_file(drive, title, mode)

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
