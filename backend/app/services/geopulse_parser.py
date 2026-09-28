"""Best-effort text extraction for GeoPulse exports (the client's own AI-
visibility tool, not Semrush) — feeds the AEO/GEO slide AI content generator
in geopulse_ai_service.py. GeoPulse's export shape isn't known ahead of time
(user: "it can be any format, so all formats should be acceptable"), so this
doesn't try to map specific columns like semrush_parser.py does — it just
gets whatever text/tabular content is in the file into a single string and
lets the AI prompt make sense of it. Real, clean extraction for CSV/TSV/
Excel/PDF/JSON; anything else (e.g. a Word doc) falls back to a raw utf-8
decode, which for a genuinely binary format degrades to noisy text rather
than failing outright — the caller still gets *something* rather than a
hard error blocking the upload."""

import io
import json

import pandas as pd
import pdfplumber

MAX_RAW_TEXT_CHARS = 40_000
# A real GeoPulse export needs far fewer pages than this to reach
# MAX_RAW_TEXT_CHARS — this is a hard safety cap so a pathological PDF
# (hundreds of pages, or one with content pdfplumber struggles with)
# can't turn the request into an unbounded, worker-blocking scan. Bug
# report (2026-09-25): a real GeoPulse export ("report-run-52.pdf")
# failed with no error detail at all in the browser — the shape of a
# timeout/worker crash rather than a caught parsing exception, which the
# route's own try/except already turns into a clear 400 message.
MAX_PDF_PAGES = 200


def _cap_text(text: str, max_chars: int) -> str:
    """Caps text length by keeping both the start AND the end, not just
    the start. 2026-09-28: the same real export named above
    (report-run-52.pdf) still failed downstream even after the timeout
    fix — it's a dense 100-page report (headline stats + competitor
    landscape on page 1, then ~90 pages of verbatim per-prompt AI
    transcripts, with its own "Recommended Actions" section only at the
    very end). A pure head-truncation to MAX_RAW_TEXT_CHARS sliced off
    partway through the transcript pages, well before the AI prompt
    (geopulse_ai_service.py) ever saw the Recommended Actions section —
    plausible root cause of "the AI returned no usable result" on this
    exact file. Splitting the budget between head and tail means both
    survive even when whatever's in the middle gets cut."""
    if len(text) <= max_chars:
        return text
    head_len = max_chars * 3 // 5
    tail_len = max_chars - head_len
    return text[:head_len] + text[-tail_len:]


def parse_geopulse_file(filename: str, content: bytes) -> dict:
    name = (filename or "").lower()
    text = ""

    if name.endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            parts: list[str] = []
            for page in pdf.pages[:MAX_PDF_PAGES]:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    # One malformed page (rare, but pdfminer/pdfplumber can
                    # throw on certain embedded content) must never fail
                    # the whole upload when the rest of the file is
                    # readable — skip it and keep going.
                    continue
                parts.append(page_text)
                # No longer stops early once MAX_RAW_TEXT_CHARS is
                # reached (2026-09-28) — that used to silently drop
                # everything after the first ~40k characters, including
                # a real report's own Recommended Actions section near
                # the end. MAX_PDF_PAGES above is already the safety
                # bound on scan cost; capping to MAX_RAW_TEXT_CHARS now
                # happens once, on the full assembled text, via
                # _cap_text (head + tail) below.
            text = "\n".join(parts)
    elif name.endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(io.BytesIO(content), sheet_name=None)
        text = "\n\n".join(f"[{sheet}]\n{df.to_string(index=False)}" for sheet, df in sheets.items())
    elif name.endswith((".csv", ".tsv")):
        sep = "\t" if name.endswith(".tsv") else ","
        df = pd.read_csv(io.BytesIO(content), sep=sep)
        text = df.to_string(index=False)
    elif name.endswith(".json"):
        text = json.dumps(json.loads(content), indent=2)
    else:
        text = content.decode("utf-8", errors="ignore")

    text = text.strip()
    return {
        "row_count": 1,
        "rows": [{"filename": filename, "raw_text": _cap_text(text, MAX_RAW_TEXT_CHARS)}],
    }
