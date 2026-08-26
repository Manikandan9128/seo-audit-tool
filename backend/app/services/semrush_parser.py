import io
import re

import pandas as pd
import pdfplumber

# Semrush export column names vary slightly by report/locale — match case-insensitively
# on a set of known aliases per field, so a real export loads without manual mapping.

BACKLINKS_COLUMN_ALIASES = {
    "source_url": ["source url", "page ascii url", "source", "source_url"],
    "target_url": ["target url", "target", "target_url"],
    "anchor": ["anchor", "anchor text"],
    "domain_score": ["source title", "page score", "domain score", "authority score", "page_score", "source_title"],
    "first_seen": ["first seen", "first_seen"],
    "last_seen": ["last seen", "last_seen"],
    "nofollow": ["nofollow"],
}

ORGANIC_COMPETITORS_COLUMN_ALIASES = {
    "domain": ["domain", "competitor"],
    "competitor_relevance": ["competitor relevance", "competitor_relevance"],
    "common_keywords": ["common keywords", "common_keywords"],
    "organic_keywords": ["organic keywords", "se keywords", "organic_keywords"],
    "organic_traffic": ["organic traffic", "se traffic", "organic_traffic"],
    "organic_cost": ["organic cost", "se traffic cost", "organic_cost"],
}

KEYWORD_GAP_COLUMN_ALIASES = {
    "keyword": ["keyword"],
    "cluster": ["cluster", "topic", "group"],
    "search_volume": ["search volume", "volume", "search_volume"],
    "keyword_difficulty": ["keyword difficulty", "kd", "kd%", "keyword_difficulty"],
    "cpc": ["cpc"],
}

# Semrush Organic Research > Positions export for a single domain — what that
# domain currently ranks for, and where. Distinct from Keyword Gap (which
# compares keyword coverage across multiple domains): this always carries a
# position/previous-position pair for one domain.
ORGANIC_POSITIONS_COLUMN_ALIASES = {
    "keyword": ["keyword"],
    "search_volume": ["search volume", "volume", "search_volume"],
    "keyword_difficulty": ["keyword difficulty", "kd", "kd%", "keyword_difficulty"],
    "position": ["position"],
    "previous_position": ["previous position", "previous_position"],
    "url": ["url"],
}

DOMAIN_OVERVIEW_COLUMN_ALIASES = {
    "domain": ["domain"],
    "rank": ["rank", "semrush rank", "semrush_rank"],
    "organic_keywords": ["organic keywords", "organic_keywords"],
    "organic_traffic": ["organic traffic", "organic_traffic"],
    "organic_cost": ["organic cost", "organic traffic cost", "organic_traffic_cost", "organic_cost"],
    "paid_keywords": ["adwords keywords", "paid keywords", "paid_keywords"],
    "paid_traffic": ["adwords traffic", "paid traffic", "paid_traffic"],
    "paid_cost": ["adwords cost", "paid traffic cost", "paid_traffic_cost", "paid_cost"],
    # Competitor Analysis comparison table columns (DR, Backlinks, Top
    # Countries, Branded/Non-Branded split) — present on Semrush's bulk
    # Domain Overview / Traffic Analytics export, not the single-domain one.
    "authority_score": ["authority score", "domain rank", "domain authority", "dr", "authority_score"],
    "backlinks_total": ["backlinks", "total backlinks", "backlinks_total"],
    "referring_domains": ["referring domains", "ref. domains", "referring_domains"],
    "top_countries": ["top countries", "top country", "geo distribution", "top_countries"],
    "branded_pct": ["branded traffic share", "branded traffic", "branded keywords share", "branded %", "branded_pct"],
    "nonbranded_pct": ["non-branded traffic share", "non-branded traffic", "non branded traffic share", "non-branded %", "nonbranded_pct"],
}

# Semrush Site Audit "Crawled Pages" export — one row per crawled URL with
# on-page/technical findings. Distinct from our own crawler's Site Audit feature.
SITE_AUDIT_PAGES_COLUMN_ALIASES = {
    "page_url": ["page url", "page_url"],
    "http_status_code": ["http status code", "http_status_code"],
    "issues": ["issues"],
    "crawl_depth": ["crawl depth", "crawl_depth"],
    "in_sitemap": ["in sitemap", "in_sitemap"],
    "canonicalization": ["canonicalization"],
    "page_title": ["page title", "page_title"],
    "description": ["description"],
    "schema_jsonld": ["schema.org (json-ld)", "schema_jsonld"],
    "open_graph": ["open graph", "open_graph"],
    "twitter_cards": ["twitter cards", "twitter_cards"],
    "hreflang_issues": ["hreflang issues", "hreflang_issues"],
}


def _parse_abbrev_number(text: str) -> float | None:
    """'12.7K' -> 12700.0, '599.7k' -> 599700.0, '$60.4K' -> 60400.0. Semrush's
    Domain Overview PDF only ever shows abbreviated figures, never raw counts."""
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*([KMB])?", text.replace("$", ""), re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((match.group(2) or "").upper(), 1)
    return value * multiplier


def _pdf_section(text: str, start_marker: str, end_markers: list[str]) -> str:
    """Slice out the text between one Domain Overview PDF section heading and
    whichever of the given following headings comes first."""
    start = text.find(start_marker)
    if start == -1:
        return ""
    start += len(start_marker)
    end = len(text)
    for marker in end_markers:
        idx = text.find(marker, start)
        if idx != -1:
            end = min(end, idx)
    return text[start:end]


def _parse_domain_overview_text(text: str) -> dict | None:
    if "Domain Overview" not in text:
        return None

    domain_match = re.search(r"\|\s*Domain\s*\|\s*([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
    if not domain_match:
        return None
    domain = domain_match.group(1)

    organic = _pdf_section(text, "Organic Search: Summary", ["Paid Search: Summary"])
    paid = _pdf_section(text, "Paid Search: Summary", ["Backlinks: Summary"])
    backlinks = _pdf_section(text, "Backlinks: Summary", ["Organic Search: Keywords By Country", "Paid Search: Ad Keywords"])
    branded = _pdf_section(text, "Branded vs Non-Branded", ["Organic Search: Branded Traffic Trend", "Paid search traffic", "Backlinks\n"])

    def _first_number(section: str, pattern: str) -> float | None:
        m = re.search(pattern, section)
        return _parse_abbrev_number(m.group(1)) if m else None

    row = {
        "domain": domain,
        "organic_traffic": _first_number(organic, r"([\d.,]+[KMB]?)\s*[\d.\-]*%?\s*TRAFFIC"),
        "organic_keywords": _first_number(organic, r"Keywords\s+([\d.,]+[KMB]?)"),
        "organic_cost": _first_number(organic, r"Traffic Cost\s+\$?([\d.,]+[KMB]?)"),
        "paid_traffic": _first_number(paid, r"([\d.,]+[KMB]?)\s*[\d.\-]*%?\s*TRAFFIC"),
        "paid_keywords": _first_number(paid, r"Keywords\s+([\d.,]+[KMB]?)"),
        "paid_cost": _first_number(paid, r"Traffic Cost\s+\$?([\d.,]+[KMB]?)"),
        "backlinks_total": _first_number(backlinks, r"([\d.,]+[KMB]?)\s*TOTAL BACKLINKS"),
        "referring_domains": _first_number(backlinks, r"Referring Domains\s+([\d.,]+[KMB]?)"),
    }
    branded_pct = re.search(r"([\d.]+)\s*%\s*\n?\s*Branded Traffic", branded)
    nonbranded_pct = re.search(r"([\d.]+)\s*%\s*\n?\s*Non-Branded Traffic", branded)
    if branded_pct:
        row["branded_pct"] = f"{branded_pct.group(1)}%"
    if nonbranded_pct:
        row["nonbranded_pct"] = f"{nonbranded_pct.group(1)}%"
    return {k: v for k, v in row.items() if v is not None}


def parse_domain_overview_pdf(content: bytes) -> dict | None:
    """Parses Semrush's "Domain Overview (Desktop)" PDF export — the
    single-domain report available on every plan (no Bulk Analysis needed).
    Pulls Organic/Paid Traffic, Keywords, Backlinks, Referring Domains, and
    Branded/Non-Branded split — the same fields DOMAIN_OVERVIEW_COLUMN_ALIASES
    expects from a CSV. Authority Score/DR is NOT included in this PDF
    export at all (confirmed missing even when visible on the live page) —
    callers must fill that in manually. Returns None if this doesn't look
    like a Domain Overview PDF."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return _parse_domain_overview_text(text)


def _read_table(filename: str, content: bytes) -> pd.DataFrame:
    buffer = io.BytesIO(content)
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(buffer)
    # Semrush CSV exports are typically semicolon or comma separated
    try:
        return pd.read_csv(buffer, sep=None, engine="python")
    except Exception:
        buffer.seek(0)
        return pd.read_csv(buffer)


def _map_columns(df: pd.DataFrame, aliases: dict[str, list[str]]) -> pd.DataFrame:
    lower_cols = {c.lower().strip(): c for c in df.columns}
    rename_map = {}
    for field, candidates in aliases.items():
        for candidate in candidates:
            if candidate in lower_cols:
                rename_map[lower_cols[candidate]] = field
                break
    mapped = df.rename(columns=rename_map)
    keep_cols = [c for c in aliases.keys() if c in mapped.columns]
    return mapped[keep_cols]


def detect_import_type(df: pd.DataFrame) -> str:
    cols = {c.lower().strip() for c in df.columns}
    if {"source url", "target url", "source_url", "target_url"} & cols or "anchor" in cols:
        return "backlinks"
    if {"common keywords", "competitor relevance", "common_keywords", "competitor_relevance"} & cols:
        return "organic_competitors"
    if "keyword" in cols and "position" in cols:
        return "organic_positions"
    if "keyword" in cols and ({"search volume", "volume", "search_volume"} & cols):
        return "keyword_gap"
    if "domain" in cols and ({"rank", "semrush rank", "semrush_rank"} & cols):
        return "domain_overview"
    if {"page url", "page_url"} & cols and ({"http status code", "http_status_code"} & cols):
        return "site_audit_pages"
    return "unknown"


def parse_semrush_file(filename: str, content: bytes) -> tuple[str, dict]:
    if filename.lower().endswith(".pdf"):
        row = parse_domain_overview_pdf(content)
        if row is None:
            return "unknown", {"row_count": 0, "rows": []}
        return "domain_overview", {"row_count": 1, "rows": [row]}

    df = _read_table(filename, content)
    import_type = detect_import_type(df)

    if import_type == "backlinks":
        mapped = _map_columns(df, BACKLINKS_COLUMN_ALIASES)
    elif import_type == "organic_competitors":
        mapped = _map_columns(df, ORGANIC_COMPETITORS_COLUMN_ALIASES)
    elif import_type == "keyword_gap":
        mapped = _map_columns(df, KEYWORD_GAP_COLUMN_ALIASES)
    elif import_type == "organic_positions":
        mapped = _map_columns(df, ORGANIC_POSITIONS_COLUMN_ALIASES)
    elif import_type == "domain_overview":
        mapped = _map_columns(df, DOMAIN_OVERVIEW_COLUMN_ALIASES)
    elif import_type == "site_audit_pages":
        mapped = _map_columns(df, SITE_AUDIT_PAGES_COLUMN_ALIASES)
    else:
        mapped = df

    mapped = mapped.head(500)  # cap rows stored to keep JSONB payload reasonable
    parsed_data = {
        "row_count": len(df),
        "rows": mapped.fillna("").to_dict(orient="records"),
    }
    return import_type, parsed_data
