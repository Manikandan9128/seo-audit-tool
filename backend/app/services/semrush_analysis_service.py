"""Compares the client's own uploaded Semrush data against competitor
uploads and surfaces concrete gaps — what's behind, by how much, and what
to do about it. Pure comparison logic over already-parsed imports; no
external calls."""

from urllib.parse import urlparse


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _referring_domains(rows: list[dict]) -> int:
    domains = {urlparse(r["source_url"]).netloc for r in rows if r.get("source_url")}
    return len(domains)


def _latest(records: list[dict], import_type: str, own: bool | None = None) -> dict | None:
    matches = [
        r for r in records
        if r["import_type"] == import_type and (own is None or bool(r.get("is_own_site", True)) == own)
    ]
    if not matches:
        return None
    return max(matches, key=lambda r: r["created_at"])


def _all_rows(records: list[dict], import_type: str, own: bool | None = None) -> list[dict]:
    """Every row from every upload of this type — not just the latest one —
    so a second Keyword Gap or Organic Competitors file isn't silently dropped."""
    matches = [
        r for r in records
        if r["import_type"] == import_type and (own is None or bool(r.get("is_own_site", True)) == own)
    ]
    rows = []
    for r in matches:
        rows.extend(r["parsed_data"]["rows"])
    return rows


def _normalize_domain(d: str) -> str:
    return (d or "").strip().lower().removeprefix("www.").rstrip("/")


def analyze(records: list[dict], own_domain: str | None = None) -> dict:
    """records: list of {import_type, is_own_site, domain_label, created_at, parsed_data}
    (parsed_data has "rows" and "row_count"). own_domain: the client's own
    website domain (e.g. "startek.com") — needed to tell which column in a
    real multi-domain Keyword Gap export (see semrush_parser.py's
    domain_positions) is "you" vs a competitor. Returns {issues: [...],
    has_data: bool, coverage: {...}} where each issue is {summary, detail,
    recommendation, severity}."""
    issues = []

    own_overview = _latest(records, "domain_overview", own=True)
    own_backlinks = _latest(records, "backlinks", own=True)
    competitor_backlinks = [
        r for r in records if r["import_type"] == "backlinks" and not r.get("is_own_site", True)
    ]
    organic_competitor_rows = _all_rows(records, "organic_competitors")
    keyword_gap_rows = _all_rows(records, "keyword_gap")
    own_site_audit_rows = _all_rows(records, "site_audit_pages", own=True)

    has_data = any([
        own_overview, own_backlinks, competitor_backlinks, organic_competitor_rows,
        keyword_gap_rows, own_site_audit_rows,
    ])

    # What's uploaded vs. what would unlock more comparisons — surfaced in the UI
    # as a hint rather than an "issue" so the report stays focused on real gaps.
    coverage = {
        "own_domain_overview": own_overview is not None,
        "own_backlinks": own_backlinks is not None,
        "competitor_backlinks": len(competitor_backlinks) > 0,
        "organic_competitors": len(organic_competitor_rows) > 0,
        "keyword_gap": len(keyword_gap_rows) > 0,
        "own_site_audit_pages": len(own_site_audit_rows) > 0,
    }

    # Backlinks / referring domains gap, per competitor upload
    own_ref_domains = _referring_domains(own_backlinks["parsed_data"]["rows"]) if own_backlinks else None
    own_backlink_count = own_backlinks["parsed_data"]["row_count"] if own_backlinks else None

    for comp in competitor_backlinks:
        comp_domain = comp.get("domain_label") or "competitor"
        comp_rows = comp["parsed_data"]["rows"]
        comp_ref_domains = _referring_domains(comp_rows)
        comp_count = comp["parsed_data"]["row_count"]

        if own_ref_domains is not None and comp_ref_domains > own_ref_domains:
            gap = comp_ref_domains - own_ref_domains
            issues.append({
                "summary": f"{comp_domain} has {gap} more referring domains than you",
                "detail": f"{comp_domain}: {comp_ref_domains} referring domains vs. yours: {own_ref_domains}.",
                "recommendation": "Build links from domains that link to this competitor but not to you — check its referring-domain list for guest post, directory, or partnership opportunities.",
                "severity": "warn",
            })
        elif own_ref_domains is None:
            issues.append({
                "summary": f"No backlink data uploaded for your own site — can't compare against {comp_domain}",
                "detail": f"{comp_domain} has {comp_ref_domains} referring domains and {comp_count} backlinks.",
                "recommendation": "Upload a Backlinks export under \"Our Website Data\" to see the gap.",
                "severity": "info",
            })

    # Organic traffic / keyword gap vs each competitor row in the organic_competitors export(s)
    if organic_competitor_rows and own_overview:
        own_row = own_overview["parsed_data"]["rows"][0] if own_overview["parsed_data"]["rows"] else {}
        own_traffic = _num(own_row.get("organic_traffic"))
        own_keywords = _num(own_row.get("organic_keywords"))
        seen_domains = set()
        for row in organic_competitor_rows:
            comp_domain = row.get("domain", "competitor")
            if comp_domain in seen_domains:
                continue
            seen_domains.add(comp_domain)
            if len(seen_domains) > 15:
                break
            comp_traffic = _num(row.get("organic_traffic"))
            comp_keywords = _num(row.get("organic_keywords"))
            if comp_traffic > own_traffic and own_traffic >= 0:
                pct = round(100 * (comp_traffic - own_traffic) / own_traffic) if own_traffic else None
                issues.append({
                    "summary": f"{comp_domain} gets {'more' if pct is None else f'{pct}% more'} organic traffic than you",
                    "detail": f"{comp_domain}: ~{comp_traffic:,.0f} monthly organic visits vs. yours: ~{own_traffic:,.0f}.",
                    "recommendation": "Check which keywords drive their traffic (Keyword Gap export) and target the ones with real search volume you don't rank for yet.",
                    "severity": "warn",
                })
            if comp_keywords > own_keywords:
                issues.append({
                    "summary": f"{comp_domain} ranks for {int(comp_keywords - own_keywords):,} more keywords than you",
                    "detail": f"{comp_domain}: {int(comp_keywords):,} organic keywords vs. yours: {int(own_keywords):,}.",
                    "recommendation": "Expand content/landing pages around topics this competitor covers that you don't.",
                    "severity": "warn",
                })
    elif organic_competitor_rows and not own_overview:
        issues.append({
            "summary": "No domain overview uploaded for your own site — can't compare organic traffic/keywords",
            "detail": f"{len(organic_competitor_rows)} competitor rows uploaded, but nothing to compare them against.",
            "recommendation": "Upload a Domain Overview export under \"Our Website Data\".",
            "severity": "info",
        })

    # Keyword gap opportunities (deduped across every Keyword Gap upload).
    # A real Semrush Keyword Gap export compares several domains in one
    # file (see semrush_parser.py's domain_positions: {domain: SERP
    # position, 0 = not ranking}) — when that's present and we know the
    # client's own domain, a real gap is "you don't rank (0) but at least
    # one competitor does (>0)", not just "any keyword with volume".
    matrix_rows = [r for r in keyword_gap_rows if r.get("domain_positions")]
    own_col = None
    if matrix_rows and own_domain:
        own_norm = _normalize_domain(own_domain)
        own_col = next(
            (d for d in matrix_rows[0]["domain_positions"] if _normalize_domain(d) == own_norm), None
        )

    if matrix_rows and own_col:
        seen_keywords = set()
        real_gaps = []
        for r in matrix_rows:
            kw = r.get("keyword")
            if not kw or kw in seen_keywords:
                continue
            positions = r.get("domain_positions") or {}
            if _num(positions.get(own_col)) > 0:
                continue  # you already rank for this one
            competitor_ranks = {d: _num(p) for d, p in positions.items() if d != own_col and _num(p) > 0}
            if not competitor_ranks:
                continue  # nobody ranks for it either — not a real gap
            seen_keywords.add(kw)
            best_domain, best_pos = min(competitor_ranks.items(), key=lambda kv: kv[1])
            real_gaps.append((r, best_domain, best_pos))
        if real_gaps:
            real_gaps.sort(key=lambda g: -_num(g[0].get("search_volume")))
            total_volume = sum(_num(g[0].get("search_volume")) for g in real_gaps)
            top_row, top_domain, top_pos = real_gaps[0]
            issues.append({
                "summary": f"{len(real_gaps)} keywords a competitor ranks for and you don't, {int(total_volume):,} combined monthly searches",
                "detail": (
                    f"Highest-volume gap: \"{top_row.get('keyword')}\" — {top_domain} ranks #{int(top_pos)}, "
                    f"you don't rank at all ({int(_num(top_row.get('search_volume'))):,} monthly searches)."
                ),
                "recommendation": "Prioritize the highest-volume, lowest-difficulty keywords from this list for new content.",
                "severity": "opportunity",
            })
    elif keyword_gap_rows:
        # Fallback: a simple (non-matrix) Keyword Gap upload, or we don't
        # know the client's own domain — can't tell who ranks for what, so
        # fall back to the old "any keyword with real search volume" signal
        # rather than silently producing nothing.
        seen_keywords = set()
        opportunities = []
        for r in keyword_gap_rows:
            kw = r.get("keyword")
            if not kw or kw in seen_keywords or _num(r.get("search_volume")) <= 0:
                continue
            seen_keywords.add(kw)
            opportunities.append(r)
        total_volume = sum(_num(r.get("search_volume")) for r in opportunities)
        if opportunities:
            issues.append({
                "summary": f"{len(opportunities)} keyword gap opportunities, {int(total_volume):,} combined monthly searches",
                "detail": "Keywords competitors rank for that you don't, with measurable search volume.",
                "recommendation": "Prioritize the highest-volume, lowest-difficulty keywords from this list for new content.",
                "severity": "opportunity",
            })

    # Technical/on-page issues from a Semrush Site Audit "crawled pages" export of our own site
    if own_site_audit_rows:
        bad_status = [r for r in own_site_audit_rows if str(r.get("http_status_code", "200")).strip() not in ("200", "")]
        if bad_status:
            examples = ", ".join(str(r.get("page_url")) for r in bad_status[:3])
            issues.append({
                "summary": f"{len(bad_status)} page(s) return a non-200 status",
                "detail": f"Broken or redirecting pages found in the crawl, e.g. {examples}.",
                "recommendation": "Fix broken links/redirects — non-200 pages waste crawl budget and lose any link equity pointing at them.",
                "severity": "warn",
            })

        not_canonical_self = [
            r for r in own_site_audit_rows
            if r.get("canonicalization") and "self" not in str(r.get("canonicalization")).lower()
        ]
        if not_canonical_self:
            issues.append({
                "summary": f"{len(not_canonical_self)} page(s) canonicalize to a different URL",
                "detail": f"Canonical tag points elsewhere on {len(not_canonical_self)} crawled page(s) — check these are intentional.",
                "recommendation": "Confirm each of these pages is meant to defer ranking signals elsewhere; fix any accidental cross-canonicals.",
                "severity": "warn",
            })

        not_in_sitemap = [r for r in own_site_audit_rows if _num(r.get("in_sitemap"), default=1) == 0]
        if not_in_sitemap:
            issues.append({
                "summary": f"{len(not_in_sitemap)} crawled page(s) missing from the sitemap",
                "detail": "Pages Semrush found by crawling links aren't listed in the XML sitemap.",
                "recommendation": "Add these URLs to the sitemap (if they should be indexed) so search engines discover them faster.",
                "severity": "opportunity",
            })

        high_issue_pages = [r for r in own_site_audit_rows if _num(r.get("issues")) >= 5]
        if high_issue_pages:
            worst = sorted(high_issue_pages, key=lambda r: _num(r.get("issues")), reverse=True)[:3]
            examples = ", ".join(f"{r.get('page_url')} ({int(_num(r.get('issues')))})" for r in worst)
            issues.append({
                "summary": f"{len(high_issue_pages)} page(s) with 5+ flagged issues",
                "detail": f"Highest issue counts: {examples}.",
                "recommendation": "Start with these pages — clearing the highest issue counts first fixes the most flagged problems per hour spent.",
                "severity": "warn",
            })

        missing_schema = [r for r in own_site_audit_rows if _num(r.get("schema_jsonld")) == 0]
        if missing_schema and len(missing_schema) < len(own_site_audit_rows):
            issues.append({
                "summary": f"{len(missing_schema)} page(s) missing schema.org (JSON-LD) markup",
                "detail": f"{len(missing_schema)} of {len(own_site_audit_rows)} crawled pages have no structured data, while others do.",
                "recommendation": "Add the same schema type used on your other pages (e.g. Product, Article) so these are eligible for rich results too.",
                "severity": "opportunity",
            })

    severity_order = {"warn": 0, "opportunity": 1, "info": 2}
    issues.sort(key=lambda i: severity_order[i["severity"]])

    return {"has_data": has_data, "issues": issues, "coverage": coverage}
