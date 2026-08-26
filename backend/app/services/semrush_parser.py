import io

import pandas as pd

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
