"""UI-Level Fixes analysis pipeline, Part A5 (2026-09-25 spec): writes
EVERY validated issue (not just the top 5 shown on slide 1) somewhere the
client can see the full list — a Google Sheet when that integration is
connected, an .xlsx export otherwise, per the spec's own explicit
fallback ("Google Sheet if that integration exists, otherwise an
.xlsx/.csv exported with the report").

Reuses the exact same connected-account Sheets pattern google_sheets_
service.py already uses for the competitor/keyword sheets (same
_resolve_credentials, same create-spreadsheet-file + batchUpdate +
public-reader-permission sequence) rather than a second OAuth flow."""

import io
import logging

from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from openpyxl import Workbook

from app.services.app_settings_service import SheetsTokenExpired, disconnect_sheets_oauth, get_sheets_oauth_credentials
from app.services.google_sheets_service import NoSheetsCredentials, _create_spreadsheet_file

logger = logging.getLogger(__name__)

_HEADER = ["#", "Issue", "Evidence", "Where", "Fix", "Priority", "Device"]


def _rows(issues: list[dict]) -> list[list]:
    return [
        [i + 1, issue.get("title", ""), issue.get("evidence", ""), issue.get("where", ""),
         issue.get("fix", ""), issue.get("priority", ""), issue.get("device", "")]
        for i, issue in enumerate(issues)
    ]


def create_ui_issues_sheet(client_name: str, issues: list[dict], db) -> str | None:
    """Creates a single-tab Google Sheet with every validated issue.
    Returns the sheet's edit URL, or None when Sheets isn't connected or
    the call fails for any reason — callers fall back to export_ui_
    issues_xlsx in that case, never crash report generation over this."""
    if not issues:
        return None
    try:
        creds = get_sheets_oauth_credentials(db) if db is not None else None
        if creds is None:
            raise NoSheetsCredentials("No Google account connected for Sheets — connect one in Settings")
        sheets = build("sheets", "v4", credentials=creds)
        drive = build("drive", "v3", credentials=creds)

        title = f"{client_name} — UI-Level Fixes (Full Issue List)"[:200]
        spreadsheet_id = _create_spreadsheet_file(drive, title)
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"updateSheetProperties": {"properties": {"sheetId": 0, "title": "UI Issues"}, "fields": "title"}}]},
        ).execute()
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range="'UI Issues'!A1",
            valueInputOption="RAW", body={"values": [_HEADER] + _rows(issues)},
        ).execute()
        drive.permissions().create(fileId=spreadsheet_id, body={"type": "anyone", "role": "reader"}).execute()
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    except NoSheetsCredentials:
        return None
    except RefreshError:
        # Same dead-refresh-token handling as create_combined_keyword_sheet
        # (google_sheets_service.py) — auto-disconnect so the next report
        # generation surfaces a clear "reconnect Sheets" message instead of
        # repeating the same doomed API call every time.
        if db is not None:
            try:
                disconnect_sheets_oauth(db)
            except Exception:
                pass
        logger.warning("UI issue sheet: Sheets refresh token dead for %s — disconnected, falling back to xlsx", client_name)
        return None
    except SheetsTokenExpired:
        return None
    except Exception:
        logger.exception("UI issue sheet: Sheets export failed for %s — falling back to xlsx", client_name)
        return None


def export_ui_issues_xlsx(issues: list[dict]) -> bytes:
    """Fallback when Sheets isn't connected — same columns, one .xlsx
    file, returned as raw bytes so the caller can bundle it with the
    report however it delivers files today."""
    wb = Workbook()
    ws = wb.active
    ws.title = "UI Issues"
    ws.append(_HEADER)
    for row in _rows(issues):
        ws.append(row)
    for col_idx, header in enumerate(_HEADER, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = max(12, len(header) + 4)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_full_issue_list(client_name: str, issues: list[dict], db) -> dict:
    """Part A5's own top-level rule: try the Sheet first, fall back to an
    xlsx export only when Sheets isn't available. Returns
    {"full_list_url": str | None, "xlsx_bytes": bytes | None} — exactly
    one of the two is set when there are issues, both None when
    `issues` is empty (Part B's 0-issues edge case never needs either)."""
    if not issues:
        return {"full_list_url": None, "xlsx_bytes": None}
    url = create_ui_issues_sheet(client_name, issues, db)
    if url:
        return {"full_list_url": url, "xlsx_bytes": None}
    return {"full_list_url": None, "xlsx_bytes": export_ui_issues_xlsx(issues)}
