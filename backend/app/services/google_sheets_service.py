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


# Universal SEO engine §54 master keyword dataset (2026-09-24): the first
# five columns are the original ones, unchanged; the engine's per-keyword
# fields follow, blank wherever a row has no value.
_CLIENT_COLUMNS = [
    ("Keyword", "keyword"), ("Cluster", "cluster"), ("Search Volume", "search_volume"), ("KD", "keyword_difficulty"),
    ("Intent", "intent"), ("Role", "primary_or_secondary"), ("Detected Intent", "detected_intent"),
    ("Secondary Intent", "secondary_intent"), ("Funnel Stage", "funnel_stage"), ("Audience", "audience"),
    ("User Need", "user_need"), ("Geography", "geography"), ("Time-Sensitive", "temporal"),
    ("Relevance", "relevance_status"), ("Current Position", "current_position"), ("Ranking URL", "current_url"),
    ("Matched Existing Page", "existing_page_url"), ("Recommended Action", "recommended_action"),
    ("Page Type", "recommended_page_type"), ("Cluster Confidence", "cluster_confidence"),
    ("Opportunity Score", "opportunity_score"), ("Roadmap Priority", "roadmap_priority"),
    ("Cannibalization", "cannibalization_status"),
    # 2026-09-24 spec-coverage additions (§54 master dataset).
    ("Cluster ID", "cluster_id"), ("Cluster Type", "cluster_type"), ("Parent Topic", "parent_topic"),
    ("Entity Type", "entity_type"), ("Brand Type", "brand_type"), ("Modifier Type", "modifier_type"),
    ("Mixed Intent", "mixed_intent"), ("CPC", "cpc"), ("Trend", "trend"),
    ("Business Relevance (0-1)", "business_relevance"), ("Conversion Potential (0-1)", "conversion_potential"),
    ("Competitor Gap (0-1)", "competitor_gap"), ("Content Gap", "content_gap_flag"),
    ("Content Gap Type", "content_gap_type"), ("Decision (§53)", "decision"), ("Reason", "decision_reason"),
    ("Language", "language"), ("Entity Relationship", "entity_relationship"), ("Location Served", "geo_served"),
    ("Primary Keyword Score", "primary_score"), ("Topical Authority (0-1)", "topical_authority"),
    ("Existing URL Traffic (clicks)", "existing_url_traffic"), ("Programmatic", "programmatic_flag"),
]
_CLIENT_HEADER = [label for label, _key in _CLIENT_COLUMNS]


def _client_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else ""
    return value if isinstance(value, (int, float, str)) else str(value)


def _sanitize_tab_title(name: str) -> str:
    """Sheets tab names can't contain : \\ / ? * [ ] and are capped at 100
    chars — strip/truncate rather than let the API reject the whole batch
    over one bad competitor domain name."""
    cleaned = re.sub(r'[:\\/?*\[\]]', "-", name or "").strip()
    return (cleaned or "Sheet")[:100]


def _keyword_gap_position_url(position, url) -> str:
    if not position:
        return "Not ranking"
    disp = (url or "").replace("https://", "").replace("http://", "").rstrip("/")
    return f"#{int(position)} — {disp}" if disp else f"#{int(position)}"


def _keyword_gap_tabs(keyword_gap_rows: list[dict]) -> list[tuple[str, list[list]]]:
    """Full Keyword Gap Analysis lists for the combined Sheet, ONE TAB PER
    GAP CATEGORY (Missing/Untapped/Shared) — same relevance/KD filter and
    the identical fixed competitor-to-column mapping as add_keyword_gap_
    slide (pptx_builder._prepare_keyword_gap_rows), so the slide's capped
    table and these tabs' full lists never disagree on which domain is
    "Competitor 1" or which rows qualify. Uncapped (unlike the slide) —
    these tabs ARE the overflow destination for the slide's top-5-per-
    category table (2026-09-19 spec: split from one mixed "Gap Category"-
    column tab into three, so a client can open exactly the category they
    care about instead of filtering a single huge list). A category with
    no qualifying rows contributes no tab at all rather than an empty one."""
    from app.reporting.pptx_builder import _prepare_keyword_gap_rows

    kd_filtered, _ambiguous, _kd_unavailable, competitor_columns = _prepare_keyword_gap_rows(keyword_gap_rows)
    if not kd_filtered:
        return []

    header = ["Keyword", "Volume", "KD", "My Position + URL"]
    header += [f"{domain} Position + URL" for domain in competitor_columns]

    by_category: dict[str, list[list]] = {"Missing": [], "Untapped": [], "Shared": []}
    for r in kd_filtered:
        by_domain = {cp.get("competitor"): cp for cp in (r.get("competitor_positions") or [])}
        row = [
            r.get("keyword", ""), r.get("search_volume", ""), r.get("keyword_difficulty", ""),
            _keyword_gap_position_url(r.get("your_position"), r.get("your_url")),
        ]
        for domain in competitor_columns:
            cp = by_domain.get(domain)
            row.append(_keyword_gap_position_url(cp.get("position"), cp.get("ranking_url")) if cp else "Not ranking")
        by_category.setdefault(r.get("gap_category") or "Missing", []).append(row)

    return [
        (f"Keyword Gap - {category}", [header] + rows)
        for category in ("Missing", "Untapped", "Shared")
        if (rows := by_category.get(category))
    ]


def _join(values) -> str:
    return ", ".join(str(v) for v in values or [] if v)


def keyword_strategy_tabs(strategy: dict | None) -> list[tuple[str, list[list]]]:
    """Universal SEO keyword engine outputs as their own tabs: §56 page map
    (with §36 page purpose), §58 cannibalization, §57 content gaps, §62
    review queue, §67 quality checks. A section with nothing in it adds no
    tab."""
    if not strategy:
        return []
    tabs = []
    pages = strategy.get("page_map") or []
    if pages:
        tabs.append(("Page Map", [[
            "Priority", "URL", "Page Type", "Topic", "Primary Keyword", "Secondary Keywords", "Primary Intent",
            "Secondary Intents", "Audience", "Page Purpose", "Business Goal", "Current Status", "Decision (§53)",
            "Action", "Content Gap", "Cannibalization Risk", "Internal Links In", "Internal Links Out", "Clusters",
            "Opportunity",
        ]] + [[
            p.get("priority") or "", p.get("url") or "(new page)", p.get("page_type") or "", p.get("topic") or "",
            p.get("primary_keyword") or "", _join(p.get("secondary_keywords")), p.get("primary_intent") or "",
            _join(p.get("secondary_intents")), p.get("audience") or "", p.get("purpose") or "",
            p.get("business_goal") or "", p.get("current_status") or "", p.get("decision") or "", p.get("action") or "",
            p.get("content_gap") or "", "Yes" if p.get("cannibalization_risk") else "", _join(p.get("links_in")),
            _join(p.get("links_out")), _join(p.get("clusters")), p.get("opportunity") or "",
        ] for p in pages]))
    canni = strategy.get("cannibalization") or []
    if canni:
        tabs.append(("Cannibalization", [["Keyword / Signal", "Preferred URL", "Other URLs", "Risk", "Similarity",
                                          "Recommended Action", "Evidence", "Source", "SERP Overlap"]]
                     + [[c["query"], c["preferred_url"], _join(c["other_urls"]), c["risk"], c.get("similarity", ""),
                         c["action"], c["evidence"], c.get("source") or "", "Not available (no SERP data yet)"]
                        for c in canni]))
    gaps = strategy.get("content_gaps") or []
    if gaps:
        tabs.append(("Content Gaps", [["Gap Type", "Missing Cluster", "Topic", "Search Intent", "Business Relevance",
                                        "Competitor Evidence", "Search Demand", "Recommended Page Type", "Suggested URL",
                                        "Suggested Primary Keyword", "Supporting Keywords", "Priority", "Reason"]]
                     + [[g["gap_type"], g["cluster"], g.get("topic") or "", g.get("intent") or "", g["business_relevance"],
                         g["competitor_evidence"], g["search_demand"], g.get("page_type") or "", g["suggested_url"],
                         g.get("primary_keyword") or "", _join(g["supporting_keywords"]), g.get("priority") or "",
                         g["reason"]] for g in gaps]))
    clusters = strategy.get("clusters") or []
    if clusters:
        tabs.append(("Clusters", [["Cluster ID", "Cluster Name", "Parent Topic", "Cluster Type", "Search Intent",
                                    "Audience", "Geography", "Primary Keyword", "Secondary Keywords", "Keyword Count",
                                    "Search Demand", "Difficulty", "Existing URL", "Page Type", "Business Value",
                                    "Opportunity", "Confidence", "Decision (§53)", "Reason", "SERP Evidence"]]
                     + [[c["cluster_id"], c["name"], c.get("parent_topic") or "", c.get("cluster_type") or "",
                         c.get("intent") or "", c.get("audience") or "", c.get("geography") or "",
                         c.get("primary_keyword") or "", _join(c.get("secondary_keywords")), c.get("keyword_count") or "",
                         c.get("search_demand") or "", c.get("difficulty") if c.get("difficulty") is not None else "",
                         c.get("target_url") or "(new page)", c.get("page_type") or "", c.get("business_value") or "",
                         c.get("opportunity") or "", c.get("confidence") or "", c.get("decision") or "",
                         c.get("decision_reason") or "", "Not available (no SERP data yet)"] for c in clusters]))
    topics = strategy.get("topics") or []
    if topics:
        hier = [["Parent Topic", "Subtopic", "Search Intent", "Cluster", "Coverage", "Topical Authority"]]
        for t in topics:
            for sub, by_intent in (t.get("hierarchy") or {}).items():
                for intent, names in by_intent.items():
                    for n in names:
                        hier.append([t["parent"], sub, intent, n, t.get("coverage") or "", t.get("authority_level") or ""])
        tabs.append(("Topic Hierarchy", hier))
    patterns = strategy.get("programmatic_patterns") or []
    if patterns:
        tabs.append(("Programmatic", [["Pattern", "Dimension A", "Dimension B", "Example Queries", "Search Demand",
                                        "Business Relevance", "SERP Validation", "Page Uniqueness Potential",
                                        "Content Requirements", "Risk", "Recommendation"]]
                     + [[p["pattern"], p["dimension_a"], p["dimension_b"], _join(p["example_queries"]), p["search_demand"],
                         p["business_relevance"], p["serp_validation"], p["uniqueness_potential"],
                         p["content_requirements"], p["risk"], p["recommendation"]] for p in patterns]))
    site = strategy.get("site") or {}
    if site.get("pages"):
        tabs.append(("Site Pages", [["URL", "Page Type", "Primary Entity", "Topic", "Audience", "Intent", "Funnel Stage",
                                      "Geography", "Content Depth", "Internal Links In", "Crawl Depth",
                                      "Internal-Link Role", "Business Purpose", "Clicks (Search Console)",
                                      "Authority Score", "Referring Backlinks"]]
                     + [[p["url"], p["page_type"], p["primary_entity"], p.get("topic") or "", p.get("audience") or "",
                         p["intent"], p["funnel_stage"], p.get("geography") or "", p["content_depth"],
                         p.get("incoming_internal_links") if p.get("incoming_internal_links") is not None else "",
                         p.get("crawl_depth") if p.get("crawl_depth") is not None else "", p["internal_link_role"],
                         p["business_purpose"], p.get("traffic_clicks") if p.get("traffic_clicks") is not None else "",
                         p.get("authority_score") if p.get("authority_score") is not None else "",
                         p.get("referring_backlinks") if p.get("referring_backlinks") is not None else ""]
                        for p in site["pages"]]))
    if site.get("graph"):
        tabs.append(("Entity Graph", [["Entity Kind", "Entities (from the site's own pages + company overview)"]]
                     + [[k.replace("_", " ").title(), _join(v)] for k, v in site["graph"].items() if v]
                     + ([["Service Areas", _join(site.get("service_areas"))]] if site.get("service_areas") else [])))
    queue = strategy.get("review_queue") or []
    if queue:
        tabs.append(("Review Queue", [["Review Type", "Item", "Detail"]]
                     + [[q["type"], q["item"], q.get("detail") or ""] for q in queue]))
    checks = strategy.get("quality_checks") or []
    if checks:
        tabs.append(("Quality Checks", [["#", "Check", "Status", "Detail"]]
                     + [[c["check"], c["name"], c["status"], c["detail"]] for c in checks]))
    return tabs


def _create_combined_keyword_sheet_inner(
    client_name: str, client_keyword_rows: list[dict], competitor_positions: dict[str, list[dict]], db,
    client_positions_rows: list[dict] | None = None, keyword_gap_rows: list[dict] | None = None,
    keyword_strategy: dict | None = None,
) -> str | None:
    """ONE spreadsheet, multiple tabs — Tab 1 the client's own tracked
    (curated/clustered) target-keyword list, Tab 2 the client's own FULL
    Organic Positions ranking list (2026-09-11 fix — see below), Tab 3+ one
    per competitor's FULL (uncapped) ranking keyword list (2026-09-10 user
    spec: replaces the old one-Sheet-per-competitor approach + its own
    "Competitor Keywords — Full Data" slide; now linked from the bottom of
    the Competitor Analysis slide instead).

    2026-09-11 fix: client_positions_rows is the client's own Organic
    Positions import (same shape/scale as competitor_positions' values) —
    confirmed live as missing: without it, Tab 1's small curated keyword-
    gap list (31 rows for Lumber) was the ONLY client-side tab, sitting
    next to competitor tabs with 1000+ rows each. Individually correct
    (different datasets, by design) but looked broken side by side in the
    same spreadsheet. Now the client gets a directly comparable full-scale
    tab too.

    No row cap is applied to either full-Positions tab — confirmed real: a
    competitor (Rippling) with 10,000+ tracked keywords was assumed to be
    hitting an "Excel limit," but neither this function nor semrush_
    parser.py's own ingest caps organic_positions rows (see that file's
    explicit exclusion of "organic_positions" from its 500-row cap) — a
    spreadsheet tab can hold far more than 10,000 rows (Sheets' real
    ceiling is ~10 million cells total across the whole file). If a
    competitor's data still tops out at exactly 10,000 rows, that ceiling
    was set when the CSV was exported from Semrush itself (a plan-tier
    export cap), not by anything in this pipeline — re-exporting from
    Semrush with a higher row allowance (or a plan that permits it) is the
    only fix for that, uploading it here passes every row straight
    through.

    Returns None if there's nothing to write (no rows anywhere) rather
    than creating an empty spreadsheet."""
    tabs: list[tuple[str, list[list]]] = []
    if client_keyword_rows:
        values = [_CLIENT_HEADER] + [
            [_client_cell(r.get(key)) for _label, key in _CLIENT_COLUMNS]
            for r in client_keyword_rows
        ]
        tabs.append((_sanitize_tab_title(client_name or "Client"), values))
    if client_positions_rows:
        values = [_HEADER] + [
            [r.get("keyword", ""), r.get("search_volume", ""), r.get("keyword_difficulty", ""), r.get("position", ""), r.get("previous_position", "")]
            for r in client_positions_rows
        ]
        tabs.append((_sanitize_tab_title(f"{client_name or 'Client'} (All Rankings)"), values))
    for domain, rows in competitor_positions.items():
        if not rows:
            continue
        values = [_HEADER] + [
            [r.get("keyword", ""), r.get("search_volume", ""), r.get("keyword_difficulty", ""), r.get("position", ""), r.get("previous_position", "")]
            for r in rows
        ]
        tabs.append((_sanitize_tab_title(domain), values))
    if keyword_gap_rows:
        for tab_title, gap_values in _keyword_gap_tabs(keyword_gap_rows):
            tabs.append((_sanitize_tab_title(tab_title), gap_values))
    for tab_title, values in keyword_strategy_tabs(keyword_strategy):
        tabs.append((_sanitize_tab_title(tab_title), values))
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


def create_combined_keyword_sheet(
    client_name: str, client_keyword_rows: list[dict], competitor_positions: dict[str, list[dict]], db,
    client_positions_rows: list[dict] | None = None, keyword_gap_rows: list[dict] | None = None,
    keyword_strategy: dict | None = None,
) -> str | None:
    """Thin wrapper around _create_combined_keyword_sheet_inner that catches
    a dead refresh token at the point it actually fails. Confirmed live
    2026-09-17: _resolve_credentials' own get_sheets_oauth_credentials call
    already auto-disconnects on RefreshError, but only when the access
    token is ALREADY expired at that moment — Credentials objects rebuilt
    from storage (sheets_credentials_from_stored) carry no `expiry`, so
    `creds.valid` reads True regardless of the refresh token's real state,
    and the eager refresh check there never fires. The actual invalid_grant
    only surfaces later, deep inside googleapiclient's own lazy refresh on
    the first 401 from a real Sheets/Drive API call below — outside that
    earlier catch entirely, and previously propagated as Google's raw
    RefreshError tuple repr straight into the user-visible content_issues
    list. Caught here instead, at the one place that wraps every real API
    call this function makes."""
    from google.auth.exceptions import RefreshError

    from app.services.app_settings_service import SheetsTokenExpired, disconnect_sheets_oauth

    try:
        return _create_combined_keyword_sheet_inner(
            client_name, client_keyword_rows, competitor_positions, db, client_positions_rows, keyword_gap_rows,
            keyword_strategy,
        )
    except RefreshError as e:
        if db is not None:
            disconnect_sheets_oauth(db)
        raise SheetsTokenExpired(
            "Google Sheets connection expired (Google revoked the refresh token) — reconnect it in Settings > Google Sheets."
        ) from e
