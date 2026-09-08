"""Creates a Google Sheet per competitor holding that competitor's FULL
keyword list (not the ~14-row table the report's per-competitor slide was
capped at) and shares it link-viewable, so the report can link out to
everything instead of showing a truncated table.

Uses the app owner's own Google account, connected once via Settings
("Connect Google Account" under Google Sheets) — the Sheet is created
directly under that real account, so it just works exactly like manually
creating a file in Drive, no storage-quota edge cases. (A service-account +
shared-Drive-folder approach was tried first and removed: a bare service
account has no Drive storage of its own, and on a plain personal Gmail
account this hit a confirmed-real "storage quota exceeded" error even
inside a folder shared with Editor access and plenty of real free space —
a known Google Drive API quirk that OAuth avoids entirely.)"""

from googleapiclient.discovery import build

_HEADER = ["Keyword", "Search Volume", "KD", "Position", "Previous Position"]


class NoSheetsCredentials(Exception):
    """No Google account connected for Sheets — see Settings."""


def _resolve_credentials(db):
    from app.services.app_settings_service import get_sheets_oauth_credentials

    creds = get_sheets_oauth_credentials(db) if db is not None else None
    if creds is None:
        raise NoSheetsCredentials(
            "No Google account connected for Sheets — connect one in Settings"
        )
    return creds


def _create_spreadsheet_file(drive, title: str) -> str:
    file = drive.files().create(
        body={"name": title, "mimeType": "application/vnd.google-apps.spreadsheet"},
        fields="id",
    ).execute()
    return file["id"]


def _test_connection(db) -> None:
    """Create + immediately delete a throwaway spreadsheet — proves the
    connected account, Sheets API, and Drive API are all actually working
    together. Raises on any failure; caller decides how to report it."""
    creds = _resolve_credentials(db)
    drive = build("drive", "v3", credentials=creds)
    spreadsheet_id = _create_spreadsheet_file(drive, "SEO Audit Tool — connection test")
    drive.files().delete(fileId=spreadsheet_id).execute()


def create_competitor_keyword_sheet(client_name: str, domain: str, rows: list[dict], db) -> str:
    """Creates a new Sheet titled after the client + competitor, writes the
    full (uncapped) keyword list, sets it link-viewable, and returns the
    edit URL. Raises NoSheetsCredentials if not connected, or whatever the
    Google API raises on a real failure — caller falls back to the old
    capped-table slide on any exception, see pptx_builder.py."""
    creds = _resolve_credentials(db)
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
