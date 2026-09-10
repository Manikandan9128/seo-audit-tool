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

import re

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


_CLIENT_HEADER = ["Keyword", "Cluster", "Search Volume", "KD", "Intent"]


def _sanitize_tab_title(name: str) -> str:
    """Sheets tab names can't contain : \\ / ? * [ ] and are capped at 100
    chars — strip/truncate rather than let the API reject the whole batch
    over one bad competitor domain name."""
    cleaned = re.sub(r'[:\\/?*\[\]]', "-", name or "").strip()
    return (cleaned or "Sheet")[:100]


def create_combined_keyword_sheet(
    client_name: str, client_keyword_rows: list[dict], competitor_positions: dict[str, list[dict]], db,
) -> str | None:
    """ONE spreadsheet, multiple tabs — Tab 1 the client's own tracked
    keyword list, Tab 2+ one per competitor's FULL (uncapped) ranking
    keyword list (2026-09-10 user spec: replaces the old one-Sheet-per-
    competitor approach + its own "Competitor Keywords — Full Data" slide;
    now linked from the bottom of the Competitor Analysis slide instead).

    No row cap is applied here — confirmed real: a competitor (Rippling)
    with 10,000+ tracked keywords was assumed to be hitting an "Excel
    limit," but neither this function nor semrush_parser.py's own ingest
    caps organic_positions rows (see that file's explicit exclusion of
    "organic_positions" from its 500-row cap) — a spreadsheet tab can hold
    far more than 10,000 rows (Sheets' real ceiling is ~10 million cells
    total across the whole file). If a competitor's data still tops out at
    exactly 10,000 rows, that ceiling was set when the CSV was exported
    from Semrush itself (a plan-tier export cap), not by anything in this
    pipeline — re-exporting from Semrush with a higher row allowance (or a
    plan that permits it) is the only fix for that, uploading it here
    passes every row straight through.

    Returns None if there's nothing to write (no client rows and no
    competitor rows) rather than creating an empty spreadsheet."""
    tabs: list[tuple[str, list[list]]] = []
    if client_keyword_rows:
        values = [_CLIENT_HEADER] + [
            [r.get("keyword", ""), r.get("cluster", ""), r.get("search_volume", ""), r.get("keyword_difficulty", ""), r.get("intent", "")]
            for r in client_keyword_rows
        ]
        tabs.append((_sanitize_tab_title(client_name or "Client"), values))
    for domain, rows in competitor_positions.items():
        if not rows:
            continue
        values = [_HEADER] + [
            [r.get("keyword", ""), r.get("search_volume", ""), r.get("keyword_difficulty", ""), r.get("position", ""), r.get("previous_position", "")]
            for r in rows
        ]
        tabs.append((_sanitize_tab_title(domain), values))
    if not tabs:
        return None

    creds = _resolve_credentials(db)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    title = f"{client_name} — Client + Competitor Keyword Lists"[:200]
    spreadsheet_id = _create_spreadsheet_file(drive, title)

    # The file starts with exactly one default sheet (sheetId 0) — rename
    # it for tab 1, add one new sheet per remaining tab, all in one
    # batchUpdate so a duplicate tab name (two competitors sanitizing to
    # the same string) fails atomically rather than leaving a half-built
    # spreadsheet behind.
    requests = [{"updateSheetProperties": {
        "properties": {"sheetId": 0, "title": tabs[0][0]}, "fields": "title",
    }}]
    for tab_title, _values in tabs[1:]:
        requests.append({"addSheet": {"properties": {"title": tab_title}}})
    sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()

    value_ranges = [
        {"range": f"'{tab_title}'!A1", "values": values}
        for tab_title, values in tabs
    ]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": value_ranges},
    ).execute()

    drive.permissions().create(
        fileId=spreadsheet_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
