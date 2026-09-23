"""Semrush MCP as a report data source — the "Semrush MCP" option next to
"Manual Upload" when generating a report.

Design: the report pipeline (_gather_report_data) reads Semrush data as a
list of SemrushImport rows, each an `import_type` + `parsed_data` produced
by semrush_parser.parse_semrush_file from an uploaded CSV. Semrush MCP's
execute_report returns the same semicolon-separated CSV the Semrush API
and UI exports use (confirmed live 2026-09-23: e.g. resource_organic comes
back as "Keyword;Position;Previous Position;Search Volume;..."), so each
MCP result is run through that SAME unchanged parser and becomes an
in-memory import record of the same shape. Nothing downstream of the
import list — calculations, Key Insights, slides — knows or cares which
source it came from.

The two sources are never combined: when a report runs with source "mcp",
every DB import of a type this module supplies (MCP_IMPORT_TYPES) is
dropped and replaced by the MCP records. Upload types MCP doesn't cover
(Site Audit exports, Structured Data, manual keyword clusters, GeoPulse)
are unaffected either way — they aren't Semrush-account data MCP can
fetch, so an MCP report keeps using them exactly as a manual one does.

API units: every execute_report call spends the connected account's
units (roughly 10/row for organic keywords, 40/row for backlinks and
competitors, 80/row for keyword gap — observed live). A fetch is stored
as a per-client snapshot (in app_settings, encrypted like the tokens, no
schema change) and reused for 24 hours for the same database + competitor
set, so Generate -> Preview -> Download spends units once, not three
times. The snapshot is only saved when EVERY call succeeded — a partial
fetch never reaches a report."""

import json
import threading
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.integrations import semrush_mcp
from app.integrations.crypto import decrypt, encrypt
from app.models.app_setting import AppSetting
from app.models.client import Client
from app.models.domain_rating import DomainRating
from app.services.semrush_parser import parse_semrush_file

# Import types an MCP snapshot supplies — and therefore replaces — in an
# MCP-sourced report.
MCP_IMPORT_TYPES = {
    "domain_overview", "overview_trend", "organic_positions", "organic_competitors",
    "keyword_gap", "backlinks", "backlink_summary",
}

SNAPSHOT_MAX_AGE_SECONDS = 24 * 3600
MAX_COMPETITORS = 4
AUTO_COMPETITORS = 3
OWN_KEYWORDS_LIMIT = 100
COMPETITOR_KEYWORDS_LIMIT = 50
OWN_BACKLINKS_LIMIT = 50
ORGANIC_COMPETITORS_LIMIT = 10
KEYWORD_GAP_LIMIT = 50

_SNAPSHOT_KEY_PREFIX = "semrush_mcp_snapshot:"


class SemrushMcpDataError(semrush_mcp.SemrushError):
    code = "data_fetch_failed"


def _root_domain(url_or_domain: str) -> str:
    d = url_or_domain.strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0]
    return d[4:] if d.startswith("www.") else d


# ---------------------------------------------------------------- fetch


class _Fetcher:
    def __init__(self, db: Session):
        self.db = db
        self.api_units = 0

    def csv(self, report: str, params: dict) -> str:
        try:
            result = semrush_mcp.call_semrush_tool(self.db, "execute_report", {"report": report, "params": params})
        except semrush_mcp.SemrushError as e:
            # Semrush's API answers "ERROR 50 :: NOTHING FOUND" for a
            # domain with no data in that database — a real, empty result
            # (e.g. a small competitor), not a failure.
            if "NOTHING FOUND" in e.message.upper():
                return ""
            if type(e) is semrush_mcp.SemrushError:
                raise SemrushMcpDataError(f"Semrush {report} failed: {e.message}") from e
            raise
        structured = result.get("structured")
        if not isinstance(structured, dict):
            try:
                structured = json.loads(result["content"][0])
            except (ValueError, IndexError, KeyError, TypeError):
                raise SemrushMcpDataError(f"Semrush {report} returned an unreadable response")
        self.api_units += int(((structured.get("metadata") or {}).get("usage") or {}).get("api_units") or 0)
        return structured.get("data") or ""

    def parsed(self, report: str, params: dict, expected_type: str) -> dict:
        text = self.csv(report, params)
        if not text.strip():
            return {"row_count": 0, "rows": []}
        import_type, parsed = parse_semrush_file(f"semrush-mcp-{report}.csv", text.encode())
        if import_type != expected_type:
            raise SemrushMcpDataError(
                f"Semrush {report} came back in an unexpected format (read as {import_type!r}, expected {expected_type!r})"
            )
        return parsed


def _record(import_type: str, parsed_data: dict, is_own_site: bool, domain_label: str | None) -> dict:
    return {"import_type": import_type, "is_own_site": is_own_site, "domain_label": domain_label, "parsed_data": parsed_data}


def _domain_overview(f: _Fetcher, domain: str, database: str, export_date: str) -> tuple[dict, dict | None]:
    """domain_rank + backlinks_overview for one domain. Returns (overview
    parsed_data, backlink overview row or None)."""
    overview = f.parsed("domain_rank", {"target": domain, "database": database}, "domain_overview")
    bl_text = f.csv("backlinks_overview", {"target": domain, "target_type": "root_domain"})
    bl = None
    lines = [l for l in bl_text.strip().splitlines() if l.strip()]
    if len(lines) >= 2:
        bl = dict(zip(lines[0].split(";"), lines[1].split(";")))
    for row in overview["rows"]:
        row["database"] = database.upper()
        row["export_date"] = export_date
        if bl:
            row["backlinks_total"] = _num(bl.get("total"))
            row["referring_domains"] = _num(bl.get("domains_num"))
    return overview, bl


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


def _backlink_summary(bl: dict, export_date: str) -> dict:
    total = _num(bl.get("total")) or 0
    follows = _num(bl.get("follows_num"))
    nofollows = _num(bl.get("nofollows_num"))
    summary = {
        "backlinks_total": total,
        "referring_domains": _num(bl.get("domains_num")),
        "referring_ips": _num(bl.get("ips_num")),
        "authority_score": _num(bl.get("score")),
        "export_date": export_date,
    }
    if total and follows is not None:
        summary["follow_count"] = follows
        summary["follow_pct"] = round(follows / total * 100, 1)
    if total and nofollows is not None:
        summary["nofollow_count"] = nofollows
        summary["nofollow_pct"] = round(nofollows / total * 100, 1)
    return summary


def _iso_from_timestamp(v):
    try:
        return datetime.fromtimestamp(int(float(v)), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return v


def _competitor_domains(db: Session, client: Client, own: str, f: _Fetcher, organic_competitors: dict) -> list[str]:
    """The competitor set: the domains already entered for this client on
    the Domain Rating editor (the user's own chosen competitors), else the
    top Semrush organic competitors for the client's domain."""
    chosen = []
    for r in db.query(DomainRating).filter(DomainRating.client_id == client.id).order_by(DomainRating.domain).all():
        d = _root_domain(r.domain)
        if d and d != own and d not in chosen:
            chosen.append(d)
    if chosen:
        return chosen[:MAX_COMPETITORS]
    auto = []
    for row in organic_competitors.get("rows", []):
        d = _root_domain(str(row.get("domain") or ""))
        if d and d != own and d not in auto:
            auto.append(d)
    return auto[:AUTO_COMPETITORS]


def fetch_snapshot(db: Session, client: Client, database: str) -> dict:
    """Fetches every Semrush dataset the report uses, for the client and
    its competitors. Raises (and saves nothing) if any call fails."""
    own = _root_domain(client.website_url)
    f = _Fetcher(db)
    export_date = date.today().strftime("%b %d, %Y")
    records: list[dict] = []

    own_overview, own_bl = _domain_overview(f, own, database, export_date)
    records.append(_record("domain_overview", own_overview, True, None))
    if own_bl:
        records.append(_record("backlink_summary", {"row_count": 1, "rows": [_backlink_summary(own_bl, export_date)]}, True, None))

    records.append(_record("organic_positions", f.parsed("resource_organic", {
        "target": own, "database": database, "display_limit": OWN_KEYWORDS_LIMIT, "display_sort": "traffic_desc",
        "export_columns": ["keyword", "position", "previous_position", "volume", "keyword_difficulty", "cpc", "url"],
    }, "organic_positions"), True, None))

    backlinks = f.parsed("backlinks", {
        "target": own, "target_type": "root_domain", "display_limit": OWN_BACKLINKS_LIMIT,
        "export_columns": ["page_score", "source_title", "source_url", "target_url", "anchor", "first_seen", "last_seen", "nofollow"],
    }, "backlinks")
    for row in backlinks["rows"]:
        row["first_seen"] = _iso_from_timestamp(row.get("first_seen"))
        row["last_seen"] = _iso_from_timestamp(row.get("last_seen"))
    # The Backlink Profile slide counts row_count as "total backlinks" when
    # there's no summary — use the account-wide total, not the sample size.
    if own_bl and _num(own_bl.get("total")):
        backlinks["row_count"] = _num(own_bl.get("total"))
    records.append(_record("backlinks", backlinks, True, None))

    organic_competitors = f.parsed("domain_organic_organic", {
        "domain": own, "database": database, "display_limit": ORGANIC_COMPETITORS_LIMIT,
    }, "organic_competitors")
    records.append(_record("organic_competitors", organic_competitors, True, None))

    competitors = _competitor_domains(db, client, own, f, organic_competitors)
    for comp in competitors:
        comp_overview, _ = _domain_overview(f, comp, database, export_date)
        records.append(_record("domain_overview", comp_overview, False, comp))
        records.append(_record("organic_positions", f.parsed("resource_organic", {
            "target": comp, "database": database, "display_limit": COMPETITOR_KEYWORDS_LIMIT, "display_sort": "traffic_desc",
            "export_columns": ["keyword", "position", "previous_position", "volume", "keyword_difficulty", "cpc", "url"],
        }, "organic_positions"), False, comp))

    if competitors:
        gap_domains = [{"domain": own, "sign": "*", "type": "organic"}] + [
            {"domain": c, "sign": "+", "type": "organic"} for c in competitors
        ]
        records.append(_record("keyword_gap", f.parsed("domain_domains", {
            "domains": gap_domains, "database": database, "display_limit": KEYWORD_GAP_LIMIT, "display_sort": "volume_desc",
        }, "keyword_gap"), True, None))

    return {
        "client_id": str(client.id),
        "database": database,
        "domain": own,
        "competitors": competitors,
        "fetched_at": time.time(),
        "api_units": f.api_units,
        "imports": records,
    }


# -------------------------------------------------------------- storage


def _key(client_id) -> str:
    return f"{_SNAPSHOT_KEY_PREFIX}{client_id}"


def load_snapshot(db: Session, client_id) -> dict | None:
    row = db.get(AppSetting, _key(client_id))
    if not row or not row.value:
        return None
    try:
        return json.loads(decrypt(row.value))
    except Exception:
        return None


def save_snapshot(db: Session, snapshot: dict) -> None:
    value = encrypt(json.dumps(snapshot))
    row = db.get(AppSetting, _key(snapshot["client_id"]))
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_key(snapshot["client_id"]), value=value))
    db.commit()


def snapshot_summary(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    counts: dict[str, int] = {}
    for r in snapshot["imports"]:
        counts[r["import_type"]] = counts.get(r["import_type"], 0) + len(r["parsed_data"].get("rows", []))
    return {
        "database": snapshot["database"],
        "domain": snapshot["domain"],
        "competitors": snapshot["competitors"],
        "fetched_at": snapshot["fetched_at"],
        "api_units": snapshot["api_units"],
        "row_counts": counts,
    }


def get_or_fetch_snapshot(db: Session, client: Client, database: str, refresh: bool = False) -> tuple[dict, bool]:
    """Returns (snapshot, reused). Reuses the stored snapshot when it's for
    the same database, under 24h old, and the competitor set the user
    entered hasn't changed — otherwise fetches (spending API units)."""
    existing = load_snapshot(db, client.id)
    if existing and not refresh and existing.get("database") == database:
        fresh = time.time() - existing.get("fetched_at", 0) < SNAPSHOT_MAX_AGE_SECONDS
        own = _root_domain(client.website_url)
        chosen = [
            _root_domain(r.domain)
            for r in db.query(DomainRating).filter(DomainRating.client_id == client.id).order_by(DomainRating.domain).all()
        ]
        chosen = [d for i, d in enumerate(chosen) if d and d != own and d not in chosen[:i]][:MAX_COMPETITORS]
        same_competitors = not chosen or chosen == existing.get("competitors")
        if fresh and same_competitors and existing.get("domain") == own:
            return existing, True
    snapshot = fetch_snapshot(db, client, database)
    save_snapshot(db, snapshot)
    return snapshot, False


# --------------------------------------------- report-pipeline hand-off

# Same thread-local approach as text_ai_client.set_preferred_provider: the
# report job/preview runs start to finish on one thread, so the MCP import
# records set here are visible where _gather_report_data reads imports,
# without threading a parameter through every function in between. Always
# reset in a finally block.
_active = threading.local()


def set_active_snapshot(snapshot: dict | None) -> None:
    _active.snapshot = snapshot


def apply_report_source(db_imports: list) -> list:
    """No-op unless an MCP snapshot is active for this thread (Manual
    Upload path: returns db_imports untouched). With one, drops every DB
    import of an MCP-covered type and substitutes the snapshot's records."""
    snapshot = getattr(_active, "snapshot", None)
    if snapshot is None:
        return db_imports
    fetched_at = datetime.fromtimestamp(snapshot["fetched_at"], tz=timezone.utc)
    kept = [r for r in db_imports if r.import_type not in MCP_IMPORT_TYPES]
    mcp_records = [
        SimpleNamespace(
            id=None,
            original_filename=f"Semrush MCP ({r['import_type']})",
            created_at=fetched_at,
            **r,
        )
        for r in snapshot["imports"]
    ]
    return kept + mcp_records
