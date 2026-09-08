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


def parse_geopulse_file(filename: str, content: bytes) -> dict:
    name = (filename or "").lower()
    text = ""

    if name.endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
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
        "rows": [{"filename": filename, "raw_text": text[:MAX_RAW_TEXT_CHARS]}],
    }
