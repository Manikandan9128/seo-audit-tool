import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import settings
from app.db.session import SessionLocal
from app.integrations import google_oauth
from app.integrations.crypto import decrypt, encrypt
from app.integrations.pagespeed_client import run_pagespeed
from app.integrations.screenshot_client import capture_homepage_screenshots
from app.models.client import Client
from app.models.domain_rating import DomainRating
from app.models.google_connection import GoogleConnection
from app.models.page_audit_job import PageAuditJob
from app.models.report_generation_job import ReportGenerationJob
from app.models.semrush_import import SemrushImport
from app.models.site_audit_run import SiteAuditRun
from app.models.user import User
from app.reporting.pptx_builder import build_report
from app.services import ga4_service, gsc_service
from app.services.company_overview_service import extract_company_overview, fetch_homepage_text
from app.services.core_problem_service import generate_core_problem
from app.services.keyword_cluster_service import generate_keyword_clusters
from app.services.domain_strategy_service import check_domain_strategy
from app.services.ux_findings_service import generate_ux_findings, static_no_ux_pass
from app.services.competitor_narrative_service import generate_competitor_narratives_batch
from app.services.keyword_relevance_service import _brand_token, _classify_keyword_page_category, classify_keywords
from app.services.logo_service import fetch_logo_bytes
from app.services.next_steps_service import generate_next_steps
from app.services.product_catalogue_service import crawl_product_catalogue
from app.services.semrush_analysis_service import analyze as analyze_semrush_data, _normalize_domain
from app.services.tech_stack_service import detect_tech_stack
from app.services.technical_seo_service import aggregate_schema_validation, run_multi_page_audit_async, run_site_audit

router = APIRouter(prefix="/clients", tags=["site-audit"])
logger = logging.getLogger(__name__)

# A real report generation's worst realistic case (every AI call needing
# its full retry, several competitors, PageSpeed included) tops out around
# 10-12 minutes — well under this. A "running" job whose progress hasn't
# moved in this long is treated as dead (see get_generate_report_job).
STALE_JOB_MINUTES = 15


def _get_owned_client(client_id: uuid.UUID, db: Session, user: User) -> Client:
    client = db.get(Client, client_id)
    if not client or client.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.post("/{client_id}/site-audit")
def site_audit(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    client = _get_owned_client(client_id, db, current_user)
    result = run_site_audit(client.website_url)
    db.add(SiteAuditRun(client_id=client_id, result=result))
    db.commit()
    return result


@router.get("/{client_id}/site-audit/history")
def site_audit_history(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Past Site Audit runs for this client, newest first — powers the
    history tabs on the client page."""
    _get_owned_client(client_id, db, current_user)
    runs = (
        db.query(SiteAuditRun)
        .filter(SiteAuditRun.client_id == client_id)
        .order_by(SiteAuditRun.created_at.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "created_at": r.created_at.isoformat(),
            "issue_count": len(r.result.get("issues", [])),
            "reachable": r.result.get("reachable"),
        }
        for r in runs
    ]


@router.get("/{client_id}/site-audit/{run_id}")
def site_audit_run_detail(
    client_id: uuid.UUID, run_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    _get_owned_client(client_id, db, current_user)
    run = db.query(SiteAuditRun).filter(SiteAuditRun.id == run_id, SiteAuditRun.client_id == client_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Site audit run not found")
    return {"id": str(run.id), "created_at": run.created_at.isoformat(), "result": run.result}


@router.post("/{client_id}/site-audit-pages")
def site_audit_pages(
    client_id: uuid.UUID,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = _get_owned_client(client_id, db, current_user)
    limit = max(1, min(limit, 50))
    # Async version fetches pages concurrently with no artificial delay,
    # instead of the serial run_multi_page_audit's one-at-a-time loop with a
    # hardcoded time.sleep(0.3) after every page — this is a live request
    # blocking on the response, so that delay was directly felt by whoever
    # called this endpoint.
    return asyncio.run(run_multi_page_audit_async(client.website_url, page_limit=limit))


def _run_page_audit_job(job_id: uuid.UUID, website_url: str, page_limit: int):
    """Runs the crawl in a background thread with its own event loop and DB
    sessions, so a large-site crawl doesn't block the request/response cycle."""
    db = SessionLocal()
    progress_db = SessionLocal()
    try:
        job = db.get(PageAuditJob, job_id)
        job.status = "running"
        db.commit()

        def progress(checked: int, total: int, issues: int):
            if checked != total and checked % 25 != 0:
                return
            j = progress_db.get(PageAuditJob, job_id)
            j.pages_checked = checked
            j.pages_total = total
            j.pages_with_issues = issues
            progress_db.commit()

        result = asyncio.run(run_multi_page_audit_async(website_url, page_limit, on_progress=progress))

        job = db.get(PageAuditJob, job_id)
        stored = dict(result)
        stored["pages"] = result["pages"][:2000]
        stored["truncated"] = len(result["pages"]) > 2000
        job.status = "done"
        job.pages_checked = result["pages_checked"]
        job.pages_total = result["pages_checked"]
        job.pages_with_issues = result["pages_with_issues"]
        job.result = stored
        db.commit()
    except Exception as e:
        job = db.get(PageAuditJob, job_id)
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
    finally:
        db.close()
        progress_db.close()


@router.post("/{client_id}/site-audit-pages/start")
def start_page_audit_job(
    client_id: uuid.UUID,
    limit: int = 200000,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kicks off a background crawl of every URL in the site's sitemap and
    returns a job id to poll — avoids blocking on a single long request."""
    client = _get_owned_client(client_id, db, current_user)
    limit = max(1, min(limit, 200000))
    job = PageAuditJob(client_id=client_id, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(target=_run_page_audit_job, args=(job.id, client.website_url, limit), daemon=True).start()
    return {"job_id": job.id}


@router.get("/{client_id}/site-audit-pages/{job_id}")
def get_page_audit_job(
    client_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_client(client_id, db, current_user)
    job = db.get(PageAuditJob, job_id)
    if not job or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "id": job.id,
        "status": job.status,
        "pages_checked": job.pages_checked,
        "pages_total": job.pages_total,
        "pages_with_issues": job.pages_with_issues,
        "error": job.error,
        "result": job.result if job.status == "done" else None,
    }


@router.get("/{client_id}/schema-validation")
def schema_validation(
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Whole-site schema.org coverage + missing required properties, tallied
    from the most recently completed Page Audit job — no fresh crawl, reuses
    whatever full-site data that background job already collected."""
    _get_owned_client(client_id, db, current_user)
    job = (
        db.query(PageAuditJob)
        .filter(PageAuditJob.client_id == client_id, PageAuditJob.status == "done")
        .order_by(PageAuditJob.created_at.desc())
        .first()
    )
    if not job or not job.result:
        raise HTTPException(status_code=404, detail="No completed Page Audit yet — run one first")
    return aggregate_schema_validation(job.result.get("pages", []))


@router.post("/{client_id}/pagespeed")
def pagespeed(
    client_id: uuid.UUID,
    strategy: str = "mobile",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = _get_owned_client(client_id, db, current_user)
    if not settings.google_psi_api_key:
        raise HTTPException(status_code=400, detail="PageSpeed Insights API key is not configured")
    if strategy not in ("mobile", "desktop"):
        raise HTTPException(status_code=400, detail="strategy must be 'mobile' or 'desktop'")
    try:
        return run_pagespeed(client.website_url, strategy=strategy)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"PageSpeed Insights request failed: {e}")


@router.get("/{client_id}/company-overview")
def company_overview(
    client_id: uuid.UUID,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Crawls the client's About/Products/legal pages and asks Gemini to
    extract a structured overview (name, description, products, KPIs,
    registration info) for the report's client-overview slide.

    Cached on the client after the first successful extraction and reused
    on every later call (this endpoint is hit both by the preview/edit UI
    and, via the cache, effectively backs report generation too) — this
    content rarely changes and repeated Gemini calls were burning through
    the free-tier quota for no benefit. Pass force=true (the UI's "Refresh"
    button) to bypass the cache, re-crawl, and overwrite it."""
    client = _get_owned_client(client_id, db, current_user)
    if not force and client.company_overview_cache:
        return client.company_overview_cache
    result = extract_company_overview(client.website_url)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    client.company_overview_cache = result
    client.company_overview_cached_at = datetime.now(timezone.utc)
    db.commit()
    return result


@router.get("/{client_id}/product-catalogue")
def product_catalogue(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Crawls nav menus, sitemap.xml, and product/collection pages to list
    the client's products/solutions catalogue. Deterministic — no LLM."""
    client = _get_owned_client(client_id, db, current_user)
    result = crawl_product_catalogue(client.website_url)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


@router.get("/{client_id}/tech-stack")
def tech_stack(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Fingerprints the client's tech stack and hosting from response headers,
    DNS/PTR records, and HTML markers — no credentials needed."""
    client = _get_owned_client(client_id, db, current_user)
    result = detect_tech_stack(client.website_url)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


def _load_credentials(client_id: uuid.UUID, db: Session):
    connection = db.query(GoogleConnection).filter(GoogleConnection.client_id == client_id).first()
    if not connection:
        return None
    try:
        creds = google_oauth.credentials_from_stored(
            decrypt(connection.encrypted_access_token), decrypt(connection.encrypted_refresh_token)
        )
        connection.encrypted_access_token = encrypt(creds.token)
        db.commit()
        return creds
    except Exception:
        # Stale/revoked/undecryptable tokens shouldn't take down the whole
        # report — analytics is one optional section among many.
        return None


def _filter_competitor_keywords(client: Client, data: dict) -> None:
    """Classifies every competitor keyword (Highly relevant / Potentially
    relevant / Exclude — see keyword_relevance_service) and strips excluded
    rows from competitor_positions and competitor_analysis["keyword_gap_
    rows"] in place, before anything downstream (PPTX slides, Next Steps
    findings, competitor narratives) ever sees them. Raw uploaded data in
    SemrushImport is untouched — only what gets rendered is filtered.
    "Potentially relevant" keywords are kept (not just "highly relevant") —
    the existing volume-sort + row caps on those slides already surface
    only the highest-value rows once excludes are stripped out."""
    competitor_positions: dict[str, list[dict]] = data.get("competitor_positions") or {}
    competitor_analysis = data.get("competitor_analysis") or {}
    keyword_gap_rows = competitor_analysis.get("keyword_gap_rows") or []
    if not competitor_positions and not keyword_gap_rows:
        return

    client_domain = client.website_url.replace("https://", "").replace("http://", "").rstrip("/")
    brand_tokens = {_brand_token(client_domain)}
    brand_tokens.update(_brand_token(d) for d in competitor_positions.keys())
    brand_tokens.update(_brand_token(r.get("competitor_domain") or "") for r in keyword_gap_rows)
    brand_tokens.discard("")

    all_keywords = [r.get("keyword", "") for rows in competitor_positions.values() for r in rows]
    all_keywords += [r.get("keyword", "") for r in keyword_gap_rows]
    if not all_keywords:
        return

    classifications = classify_keywords(client.name, client_domain, brand_tokens, all_keywords)
    if not classifications:
        return
    keep = {"highly_relevant", "potentially_relevant"}

    for domain, rows in list(competitor_positions.items()):
        competitor_positions[domain] = [
            r for r in rows if classifications.get((r.get("keyword") or "").lower(), "potentially_relevant") in keep
        ]

    if keyword_gap_rows:
        competitor_analysis["keyword_gap_rows"] = [
            r for r in keyword_gap_rows
            if classifications.get((r.get("keyword") or "").lower(), "potentially_relevant") in keep
        ]


def _generate_competitor_narratives(
    client: Client,
    data: dict,
    max_competitors: int = 5,
    on_progress: Callable[[str, int], None] | None = None,
    content_issues: list[str] | None = None,
) -> dict[str, dict]:
    """AI-generated "Areas of Focus" narrative per competitor domain,
    grounded in whatever data was actually uploaded for that domain (Domain
    Overview stats, keyword positions, the rule-based gap findings that
    mention it by name). Capped at max_competitors — an unbounded
    competitor list would make the one batched AI call below unbounded too
    (see generate_competitor_narratives_batch, which covers every
    competitor in a single call rather than one call each). content_issues
    (optional, mutated in place) collects a human-readable failure reason
    per competitor that didn't get a narrative, for the report's own
    diagnostics slide."""
    progress = on_progress or (lambda _stage, _pct: None)
    if content_issues is None:
        content_issues = []
    competitor_rows = data.get("competitor_rows") or []
    competitor_positions = data.get("competitor_positions") or {}
    gap_issues = (data.get("competitor_analysis") or {}).get("issues") or []

    client_domain = client.website_url.replace("https://", "").replace("http://", "").rstrip("/")

    def _norm(d: str) -> str:
        return d.strip().lower().removeprefix("www.")

    # Own-site domain must be excluded here — comparing against the raw
    # website_url (a full URL, e.g. "https://www.ejtoyco.com/") never
    # matched a bare row domain (e.g. "ejtoyco.com"), so the client's own
    # Domain Overview row was previously slipping through as a "competitor."
    domains = [d for d in competitor_positions.keys() if _norm(d) != _norm(client_domain)]
    for row in competitor_rows:
        d = row.get("domain")
        if d and d not in domains and _norm(d) != _norm(client_domain):
            domains.append(d)
    domains = domains[:max_competitors]
    if not domains:
        return {}

    def _as_number(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    # Homepage fetches are plain network I/O with no AI-rate-limit concern,
    # unlike the narrative generation below — pulled out of that serial loop
    # and run concurrently first so N-1 fetch durations overlap instead of
    # each one stacking onto the already-serial AI critical path.
    homepage_texts: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(domains))) as pool:
        futures = {pool.submit(fetch_homepage_text, d): d for d in domains}
        for future, domain in futures.items():
            try:
                homepage_texts[domain] = future.result()
            except Exception:
                homepage_texts[domain] = None

    # Gather each domain's grounding facts (pure data assembly, no AI call)
    # — a domain with nothing grounded to write from is dropped entirely
    # rather than sent to the AI to invent from.
    competitors_facts: dict[str, dict] = {}
    for domain in domains:
        row = next((r for r in competitor_rows if r.get("domain") == domain), None)
        top_keywords = sorted(
            competitor_positions.get(domain, []), key=lambda r: _as_number(r.get("search_volume")), reverse=True
        )[:8]
        relevant_gaps = [
            i["summary"] for i in gap_issues
            if i.get("domain") and _norm(i["domain"]) == _norm(domain)
        ]
        homepage_text = homepage_texts.get(domain)
        if not row and not top_keywords and not relevant_gaps and not homepage_text:
            continue
        # Cheap evidence signal for the opportunity_analysis prompt — counts
        # of what PAGE TYPE this competitor's own ranking keywords call for,
        # so "has comparison pages" / "has a blog" is grounded in real
        # keyword data instead of guessed blind from homepage_text alone.
        # No new AI/network call: reuses the same classifier the Content
        # SEO Next Steps slide already runs over the client's own keywords.
        ranking_page_types: dict[str, int] = {}
        for r in competitor_positions.get(domain, []):
            category = _classify_keyword_page_category(r.get("keyword") or "")
            if category:
                ranking_page_types[category] = ranking_page_types.get(category, 0) + 1
        competitors_facts[domain] = {
            "domain_stats": row,
            "top_ranking_keywords": [
                {"keyword": r.get("keyword"), "position": r.get("position"), "search_volume": r.get("search_volume")}
                for r in top_keywords
            ],
            "ranking_page_types": ranking_page_types,
            "gaps_vs_this_competitor": relevant_gaps,
            "homepage_url": f"https://{domain}",
            "homepage_text": homepage_text,
        }

    narratives = {}
    if competitors_facts:
        # One AI call for every competitor at once instead of one call per
        # competitor — competitor narratives were routinely the largest
        # chunk of a report's total AI-call footprint (up to 5 of 7-8
        # calls), and this app's free-tier AI quota (not raw processing
        # time) is the actual binding constraint on report generation.
        # Trade-off accepted deliberately: if this one call fails outright,
        # every competitor's narrative is lost together instead of just
        # one — see competitor_narrative_service's module docstring.
        progress(f"Analyzing {len(competitors_facts)} competitor(s) together...", 65)
        results = generate_competitor_narratives_batch(client.name, client_domain, competitors_facts)
        for domain, result in results.items():
            if "error" in result:
                logger.warning(
                    "Competitor narrative failed for %s (client %s): %s | raw: %s",
                    domain, client.id, result["error"], result.get("raw"),
                )
                content_issues.append(f"Competitor narrative for {domain}: {result['error']}")
            else:
                narratives[domain] = result

    # Best-effort homepage screenshot per competitor, for visual grounding
    # on the narrative slide (matches the manual reference deck). Run
    # sequentially, in this thread, deliberately NOT inside the ThreadPool-
    # Executor above — Playwright's sync API isn't reliably safe to invoke
    # from multiple worker threads at once, and a single browser instance
    # reused across domains here is already cheap relative to the AI calls
    # above. A screenshot that fails (bot-blocked, timeout, unreachable)
    # just means that competitor's slide renders without one — never an
    # error surfaced anywhere in the report.
    if narratives:
        progress("Capturing competitor homepage screenshots...", 82)
    try:
        screenshots = capture_homepage_screenshots(list(narratives.keys()))
    except Exception:
        screenshots = {}
    for domain, shot in screenshots.items():
        if domain in narratives:
            narratives[domain]["screenshot"] = shot

    return narratives


def _build_next_steps_findings(data: dict, competitor_narratives: dict[str, dict]) -> dict:
    """Assembles the bounded, summarized fact-set the Next Steps AI prompt
    reasons over — reuses data already gathered elsewhere in the report
    (never re-fetches anything) so the AI has real products/competitors/
    numbers to name instead of inventing generic advice. Kept summarized
    (top N only, no raw per-page dumps) to stay inside the prompt's 12,000-
    char budget in next_steps_service."""

    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    company_overview = data.get("company_overview") or {}

    competitors = []
    for row in (data.get("competitor_rows") or [])[:5]:
        domain = row.get("domain")
        if not domain:
            continue
        narrative = competitor_narratives.get(domain) or {}
        competitors.append({
            "domain": domain,
            "authority_score": row.get("authority_score"),
            "backlinks_total": row.get("backlinks_total"),
            "organic_traffic": row.get("organic_traffic"),
            # Real on-site tactics this competitor actually uses, already
            # AI-extracted for the "What X Does Well" slide — reusing it here
            # is exactly what makes GEO/Conversion bullets business-specific
            # instead of generic ("Switch to Hex"-style specificity).
            "best_at": narrative.get("best_at") if "error" not in narrative else None,
        })

    keyword_clusters: dict[str, dict] = {}
    for r in data.get("keyword_rows") or []:
        label = (r.get("cluster") or "").strip()
        if not label:
            continue
        entry = keyword_clusters.setdefault(label, {"count": 0, "volume": 0.0})
        entry["count"] += 1
        entry["volume"] += _num(r.get("search_volume"))
    top_clusters = sorted(keyword_clusters.items(), key=lambda kv: -kv[1]["volume"])[:10]

    structured_data_rows = data.get("structured_data_rows") or []
    structured_data_summary = None
    if structured_data_rows:
        total = len(structured_data_rows)
        structured_data_summary = {
            "total_pages": total,
            "local_business_pages": sum(1 for r in structured_data_rows if _num(r.get("local_business_items")) > 0),
            "faq_pages": sum(1 for r in structured_data_rows if _num(r.get("faq_items")) > 0),
            "product_pages": sum(1 for r in structured_data_rows if _num(r.get("product_items")) > 0),
            "review_pages": sum(1 for r in structured_data_rows if _num(r.get("review_items")) > 0),
        }

    analytics = data.get("analytics") or {}
    top_pages = ((analytics.get("top_pages") or {}).get("rows") or [])[:5]
    traffic_rows = (analytics.get("traffic_overview") or {}).get("rows") or []
    bounce_rate = (
        sum(_num(r.get("bounce_rate")) for r in traffic_rows) / len(traffic_rows) if traffic_rows else None
    )

    ux_findings = data.get("ux_findings") or {}

    return {
        "company_overview": {
            "summary": company_overview.get("summary"),
            "industries_served": company_overview.get("industries_served"),
            "icp": company_overview.get("icp"),
            "solutions": company_overview.get("solutions"),
        } if company_overview else None,
        "competitors": competitors or None,
        "keyword_clusters": [
            {"label": label, "keyword_count": v["count"], "combined_monthly_searches": round(v["volume"])}
            for label, v in top_clusters
        ] or None,
        "core_problem": data.get("core_problem"),
        "structured_data_coverage": structured_data_summary,
        "backlink_summary": data.get("backlink_summary"),
        "own_domain_rating": data.get("own_domain_rating"),
        "own_backlink_row_count": data.get("backlink_row_count"),
        "top_traffic_pages": [
            {"path": p.get("path"), "pageviews": p.get("page_views")} for p in top_pages
        ] or None,
        "bounce_rate_pct": round(bounce_rate, 1) if bounce_rate is not None else None,
        "domain_strategy": data.get("domain_strategy"),
        "ux_findings": ux_findings if not ux_findings.get("no_ux_pass_done") and not ux_findings.get("error") else None,
    }


def _gather_report_data(
    client: Client,
    db: Session,
    client_id: uuid.UUID,
    include_analytics: bool,
    include_pagespeed: bool,
    include_company_overview: bool,
    company_overview_override: dict | None,
    competitor_analysis_override: dict | None,
    ux_notes: str | None = None,
    on_progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Runs every check for this client and returns the plain-dict payload
    that build_report() consumes — shared by the preview (JSON, for the user
    to review/edit) and generate-report (same data, built into a PPTX) so the
    two never drift apart.

    If company_overview_override / competitor_analysis_override are given
    (the user's edited version of what the preview returned), they're used
    as-is instead of re-crawling/re-analyzing.

    on_progress (optional) is called with a short human-readable stage
    string at each major checkpoint — used by the background job runner to
    show real progress instead of a plain spinner. There's no single
    meaningful "% complete" here (a PageSpeed Insights call and a crawl
    aren't comparable units of work), so this reports the current stage by
    name rather than fabricating a percentage."""
    progress = on_progress or (lambda _stage, _pct: None)
    # Real failure reasons for any AI-dependent section that didn't come
    # through this run (keyword clustering, Core Problem, competitor
    # narratives, Next Steps) — surfaced on the report's own last slide
    # instead of only a server log line, since whoever downloads the report
    # often has no access to server logs to see why a section is missing.
    content_issues: list[str] = []
    company_overview_result = None
    if company_overview_override is not None:
        company_overview_result = company_overview_override
        client.company_overview_cache = company_overview_result
        client.company_overview_cached_at = datetime.now(timezone.utc)
        db.commit()
    elif include_company_overview and client.company_overview_cache:
        # Reuses the cached extraction instead of calling Gemini/Claude
        # again on every report regeneration — this content rarely changes,
        # and repeated calls were burning through Gemini's free-tier quota
        # for no benefit. Only refreshed via the explicit refresh endpoint.
        company_overview_result = client.company_overview_cache
    elif include_company_overview and (settings.gemini_api_key or settings.claude_api_key):
        progress("Reading company overview...", 5)
        result = extract_company_overview(client.website_url)
        if "error" not in result:
            company_overview_result = result
            catalogue = crawl_product_catalogue(client.website_url)
            catalogue_names = [p["name"] for p in catalogue.get("products", [])]
            if catalogue_names and not company_overview_result.get("products"):
                company_overview_result["products"] = catalogue_names
            client.company_overview_cache = company_overview_result
            client.company_overview_cached_at = datetime.now(timezone.utc)
            db.commit()

    # site_audit/page_audit/tech_stack each fetch the site independently and
    # none needs another's result — previously run one after another, now
    # run concurrently so their fetch times overlap instead of stacking.
    # page_audit uses the async concurrent crawler (already used by the
    # background full-crawl job, CONCURRENCY-at-a-time, no artificial delay)
    # instead of the old one-at-a-time loop with a hardcoded time.sleep(0.3)
    # after every page — that alone was 6+ seconds of pure sleep for a
    # 20-page crawl, run synchronously on every single report generation.
    progress("Crawling site and checking tech stack...", 15)
    with ThreadPoolExecutor(max_workers=3) as pool:
        site_audit_future = pool.submit(run_site_audit, client.website_url)
        page_audit_future = pool.submit(asyncio.run, run_multi_page_audit_async(client.website_url, page_limit=20))
        tech_stack_future = pool.submit(detect_tech_stack, client.website_url)
        site_audit_result = site_audit_future.result()
        page_audit_result = page_audit_future.result()
        tech_stack_result = tech_stack_future.result()
    if "error" in tech_stack_result:
        tech_stack_result = None

    psi_mobile = psi_desktop = None
    if include_pagespeed and settings.google_psi_api_key:
        progress("Running PageSpeed Insights...", 30)
        with ThreadPoolExecutor(max_workers=2) as pool:
            mobile_future = pool.submit(run_pagespeed, client.website_url, "mobile")
            desktop_future = pool.submit(run_pagespeed, client.website_url, "desktop")
            try:
                psi_mobile = mobile_future.result()
            except Exception:
                logger.exception("PageSpeed Insights mobile run failed for %s", client.website_url)
                psi_mobile = None
            try:
                psi_desktop = desktop_future.result()
            except Exception:
                logger.exception("PageSpeed Insights desktop run failed for %s", client.website_url)
                psi_desktop = None

    analytics = None
    if include_analytics and (client.ga4_property_id or client.gsc_site_url):
        creds = _load_credentials(client_id, db)
        if creds:
            progress("Pulling Analytics & Search Console data...", 45)
            from datetime import date, timedelta

            analytics = {}
            date_range = {}
            ga4_start = (date.today() - timedelta(days=30)).isoformat()
            ga4_end = date.today().isoformat()
            # Search Console data lags ~2-3 days behind — a range whose end
            # date is more recent than that reliably comes back empty (not
            # partial), so clamp regardless of "today".
            gsc_start = (date.today() - timedelta(days=30)).isoformat()
            gsc_end = (date.today() - timedelta(days=3)).isoformat()

            # These 6 calls are independent of each other and of anything
            # else in this function (traffic_spike is the one exception — it
            # needs traffic_overview's own result, so it stays sequential
            # afterward) — running them concurrently instead of one-by-one
            # saves several seconds of pure Google API round-trip latency
            # that were previously just stacking up serially on every report.
            jobs = {}
            with ThreadPoolExecutor(max_workers=6) as pool:
                if client.ga4_property_id:
                    jobs["traffic_overview"] = pool.submit(
                        ga4_service.get_traffic_overview, creds, client.ga4_property_id, "30daysAgo", "today"
                    )
                    jobs["top_pages"] = pool.submit(
                        ga4_service.get_top_pages, creds, client.ga4_property_id, "30daysAgo", "today", limit=15
                    )
                    jobs["traffic_sources"] = pool.submit(
                        ga4_service.get_traffic_sources, creds, client.ga4_property_id, "30daysAgo", "today"
                    )
                    jobs["page_performance"] = pool.submit(
                        ga4_service.get_page_performance, creds, client.ga4_property_id, "30daysAgo", "today"
                    )
                if client.gsc_site_url:
                    jobs["search_queries"] = pool.submit(
                        gsc_service.get_search_analytics, creds, client.gsc_site_url, gsc_start, gsc_end, row_limit=20
                    )
                    jobs["page_clicks"] = pool.submit(
                        gsc_service.get_page_clicks, creds, client.gsc_site_url, gsc_start, gsc_end, row_limit=1000
                    )
                for key, future in jobs.items():
                    try:
                        analytics[key] = future.result()
                    except HttpError:
                        pass

            if client.ga4_property_id:
                date_range["ga4_start"], date_range["ga4_end"] = ga4_start, ga4_end
                # Separate try: needs Google Signals/demographics enabled on
                # the property (age/gender), which the calls above don't —
                # a permission/config error here shouldn't wipe out the
                # traffic_overview/top_pages/etc that already succeeded.
                try:
                    if analytics.get("traffic_overview"):
                        spike = ga4_service.get_traffic_spike_breakdown(
                            creds, client.ga4_property_id, analytics["traffic_overview"]["rows"]
                        )
                        if spike:
                            analytics["traffic_spike"] = spike
                except HttpError:
                    pass
            if client.gsc_site_url:
                date_range["gsc_start"], date_range["gsc_end"] = gsc_start, gsc_end
            if date_range:
                analytics["date_range"] = date_range

    all_imports = db.query(SemrushImport).filter(SemrushImport.client_id == client_id).all()

    def _all_rows(import_type: str, own_only: bool = False) -> list[dict]:
        """Every row from every upload of this type — not just the latest —
        so a second Keyword Gap or Organic Competitors file isn't dropped."""
        rows = []
        for r in all_imports:
            if r.import_type != import_type:
                continue
            if own_only and not r.is_own_site:
                continue
            rows.extend(r.parsed_data.get("rows", []))
        return rows

    # Target Keywords slide is the client's own keyword research — exclude
    # competitor-labeled keyword_gap uploads (those feed the separate
    # "keyword gap opportunities" finding in semrush_analysis_service instead,
    # which intentionally wants both own + competitor rows).
    keyword_rows_all = _all_rows("keyword_gap", own_only=True)
    # Real Semrush Keyword Gap exports carry no Cluster/Topic column at all
    # (confirmed against a real client file) — the manual reference decks'
    # grouped-by-topic keyword tables come from a different, clustered
    # Semrush export nobody's uploaded here. When no row already has a real
    # cluster value, cluster them ourselves via AI — pure classification of
    # keywords that are already there, nothing invented — so Target
    # Keywords still renders grouped instead of one flat table. Capped at
    # the 100 highest-volume keywords to keep the prompt bounded; any
    # keyword beyond that just renders without a cluster label, same as
    # when no clustering happens at all.
    if keyword_rows_all and not any((r.get("cluster") or "").strip() for r in keyword_rows_all):
        def _kw_volume(r: dict) -> float:
            try:
                return float(r.get("search_volume") or 0)
            except (TypeError, ValueError):
                return 0.0

        seen_kw = set()
        unique_keywords = []
        for r in sorted(keyword_rows_all, key=_kw_volume, reverse=True):
            kw = r.get("keyword")
            if kw and kw not in seen_kw:
                seen_kw.add(kw)
                unique_keywords.append(kw)
        try:
            cluster_map = generate_keyword_clusters(unique_keywords[:100])
        except Exception as e:
            logger.warning("Keyword clustering failed for client %s: %s", client_id, e)
            content_issues.append(f"Keyword clustering (Target Keywords topic grouping): {e}")
            cluster_map = {}
        for r in keyword_rows_all:
            label = cluster_map.get(r.get("keyword"))
            if label:
                r["cluster"] = label

    own_backlink_rows = _all_rows("backlinks", own_only=True)
    # Semrush Site Audit's own issue-type rollup (Issue/Failed checks/Total
    # checks) — a real multi-page crawl result, richer than our own
    # homepage-only + 20-page checks. When present, the SEO Issues slide
    # prefers this over site_audit_result["issues"] / page_audit issues.
    site_audit_issues_rows = _all_rows("site_audit_issues", own_only=True)
    # Semrush Site Audit's own per-page structured-data (schema markup)
    # export — feeds the Structured Data slide's "which rich-result types
    # are missing site-wide" findings. Own-site only, same as the other
    # Site Audit family exports above — structured data isn't something we
    # compare against competitors anywhere in the report.
    structured_data_rows = _all_rows("structured_data", own_only=True)
    # Semrush Site Audit's own per-page export — same rows semrush_analysis_
    # service already reads for the non-200/canonical/sitemap findings, but
    # threaded through directly here too so the Website Structure slide can
    # roll them up by top-level directory (Directory/URLs/Issues), matching
    # the manual report's "Site Structure" table.
    site_audit_pages_rows = _all_rows("site_audit_pages", own_only=True)
    # Semrush Site Audit's own crawl-health summary (Site Health %, AI Search
    # Health %, Blocked/Redirect/Have issues/Broken/Healthy page counts) — a
    # real full-site crawl, replaces our own homepage + 20-page approximation
    # on the Site Health slide when uploaded.
    site_audit_overview_rows = _all_rows("site_audit_overview", own_only=True)
    site_audit_overview = site_audit_overview_rows[-1] if site_audit_overview_rows else None
    # Semrush Backlink List PDF's summary stats (Authority Score, Referring
    # Domains, Total Backlinks, Referring IPs, Follow/Nofollow/Sponsored/UGC
    # link attributes) — richer/more authoritative than what the Backlink
    # Profile slide otherwise computes from a possibly-partial backlinks CSV.
    backlink_summary_rows = _all_rows("backlink_summary", own_only=True)
    backlink_summary = backlink_summary_rows[-1] if backlink_summary_rows else None

    # Competitor Analysis comparison table: prefer Domain Overview rows (own +
    # competitors) when uploaded — they carry DR/backlinks/top-countries/
    # branded-split that Organic Competitors exports don't. Own site's row
    # goes first since single-domain exports don't repeat the domain name.
    own_website_domain = (client.website_url or "").replace("https://", "").replace("http://", "").rstrip("/")
    domain_overview_rows = []
    for r in all_imports:
        if r.import_type != "domain_overview":
            continue
        label = own_website_domain if r.is_own_site else (r.domain_label or "competitor")
        for row in r.parsed_data.get("rows", []):
            # For the own site, always use the client's own domain as the
            # label — a PDF-parsed row carries its own "domain" field (e.g.
            # "startek.com") which can disagree with client.website_url on
            # "www." and break the own-row-sorts-first logic below.
            domain = label if r.is_own_site else (row.get("domain") or label)
            domain_overview_rows.append({**row, "domain": domain})
    domain_overview_rows.sort(key=lambda row: row["domain"] != own_website_domain)

    # Domain Overview PDF is always pulled against a single country's
    # database (confirmed: every export headed "US | Domain | ..."), never
    # Worldwide. Semrush's separate "Overview Trend" CSV export carries an
    # explicit Database column — built here (before the synthetic-row
    # fallback below, which needs it) instead of down where it's consumed.
    worldwide_by_domain: dict[str, tuple[str, dict]] = {}
    for r in all_imports:
        if r.import_type != "overview_trend":
            continue
        label = own_website_domain if r.is_own_site else (r.domain_label or "")
        for row in r.parsed_data.get("rows", []):
            if not label or str(row.get("database", "")).strip().lower() != "worldwide":
                continue
            worldwide_by_domain[_normalize_domain(label)] = (label, row)

    # Fallback for a competitor whose Domain Overview PDF can't be
    # downloaded (Semrush free-tier export limits) but who DOES have an
    # Overview Trend (Worldwide) upload and/or a raw Backlinks CSV — those
    # alone are enough for a partial Competitor Analysis row (Global
    # traffic/keywords, paid traffic, backlink count) instead of no row at
    # all. Real per-country traffic, Top Countries, and Branded/Non-Branded
    # split genuinely aren't in either file — those columns just stay blank
    # for a synthesized row, same as any other missing field elsewhere in
    # this table.
    covered_domains = {_normalize_domain(row["domain"]) for row in domain_overview_rows}
    backlinks_total_by_domain: dict[str, int] = {}
    for r in all_imports:
        if r.import_type != "backlinks" or r.is_own_site or not r.domain_label:
            continue
        norm = _normalize_domain(r.domain_label)
        backlinks_total_by_domain[norm] = backlinks_total_by_domain.get(norm, 0) + r.parsed_data.get("row_count", 0)

    for norm_domain, (label, wd_row) in worldwide_by_domain.items():
        if norm_domain in covered_domains:
            continue
        synthetic_row: dict = {"domain": label}
        if wd_row.get("paid_traffic") is not None:
            synthetic_row["paid_traffic"] = wd_row["paid_traffic"]
        if norm_domain in backlinks_total_by_domain:
            synthetic_row["backlinks_total"] = backlinks_total_by_domain[norm_domain]
        domain_overview_rows.append(synthetic_row)
        covered_domains.add(norm_domain)
    domain_overview_rows.sort(key=lambda row: row["domain"] != own_website_domain)

    # DR column in the Competitor Analysis table comes ONLY from manually
    # entered Domain Rating (see DomainRating model) — user decision
    # 2026-08-28: Semrush's Authority Score is no longer used to fill this
    # column at all, even as a fallback. Ahrefs has no free bulk/API access
    # (only a free single-domain manual lookup), so this is typed in by
    # hand per domain rather than pulled automatically. Explicitly clear
    # any authority_score a domain_overview CSV row might already carry
    # (DOMAIN_OVERVIEW_COLUMN_ALIASES maps an "authority score" column on
    # bulk exports) so no Semrush-sourced value can leak through.
    for row in domain_overview_rows:
        row["authority_score"] = None
    manual_dr_rows = db.query(DomainRating).filter(DomainRating.client_id == client_id).all()
    manual_dr_by_domain = {_normalize_domain(r.domain): r.dr for r in manual_dr_rows}
    for row in domain_overview_rows:
        match = manual_dr_by_domain.get(_normalize_domain(row["domain"]))
        if match is not None:
            row["authority_score"] = match
    # Same manual DR, for the Backlink Profile slide's own stat card (that
    # slide is about the client's own site only, not a comparison table).
    own_domain_rating = manual_dr_by_domain.get(_normalize_domain(own_website_domain))

    # Fold Worldwide traffic/keywords onto every row — matched by domain,
    # same way DR is above — using the worldwide_by_domain dict already
    # built earlier (before the synthetic-row fallback). Covers real
    # Domain Overview rows and the synthetic fallback rows alike.
    for row in domain_overview_rows:
        match = worldwide_by_domain.get(_normalize_domain(row["domain"]))
        if match:
            _, wd = match
            row["organic_traffic_worldwide"] = wd.get("organic_traffic")
            row["organic_keywords_worldwide"] = wd.get("organic_keywords")
            row["worldwide_as_of_date"] = wd.get("trend_date")

    competitor_rows_all = domain_overview_rows or _all_rows("organic_competitors")

    # Organic Research > Positions export, per competitor domain — feeds the
    # "Competitor Keywords: {domain}" ranking-table slides.
    competitor_positions: dict[str, list[dict]] = {}
    for r in all_imports:
        if r.import_type != "organic_positions" or r.is_own_site:
            continue
        label = r.domain_label or "competitor"
        competitor_positions.setdefault(label, []).extend(r.parsed_data.get("rows", []))
    own_backlink_row_count = sum(
        r.parsed_data.get("row_count", 0) for r in all_imports if r.import_type == "backlinks" and r.is_own_site
    )
    if competitor_analysis_override is not None:
        competitor_analysis_result = competitor_analysis_override
    else:
        competitor_analysis_result = analyze_semrush_data([
            {
                "import_type": r.import_type,
                "is_own_site": r.is_own_site,
                "domain_label": r.domain_label,
                "created_at": r.created_at,
                "parsed_data": r.parsed_data,
            }
            for r in all_imports
        ], own_domain=own_website_domain)

    domain_strategy_result = check_domain_strategy(
        client.website_url, (company_overview_result or {}).get("target_country")
    )

    if ux_notes and ux_notes.strip() and (settings.gemini_api_key or settings.claude_api_key):
        ux_findings_result = generate_ux_findings(client.name, client.website_url, ux_notes)
    else:
        ux_findings_result = static_no_ux_pass()

    # One diagnostic thesis synthesizing everything else already gathered —
    # deliberately NOT cached (unlike Company Overview): this reflects
    # current metrics/issues, and a stale cached diagnosis would be
    # actively wrong once a client fixes something.
    core_problem_result = None
    if settings.gemini_api_key or settings.claude_api_key:
        core_problem_findings = {
            "homepage_issues": site_audit_result.get("issues", []),
            "pages_checked": (page_audit_result or {}).get("pages_checked"),
            "pages_with_issues": (page_audit_result or {}).get("pages_with_issues"),
            "sample_page_level_issues": [
                {"url": p.get("url"), "issues": p.get("issues")}
                for p in (page_audit_result or {}).get("pages", [])
                if p.get("issues")
            ][:15],
            "backlink_summary": backlink_summary,
            "own_backlink_row_count": own_backlink_row_count,
            "competitor_gap_findings": (competitor_analysis_result or {}).get("issues", []),
            "target_keyword_count": len(keyword_rows_all) if keyword_rows_all else 0,
        }
        core_problem_candidate = generate_core_problem(core_problem_findings)
        if "error" not in core_problem_candidate:
            core_problem_result = core_problem_candidate
        else:
            logger.warning("Core Problem generation failed for client %s: %s", client.id, core_problem_candidate["error"])
            content_issues.append(f"Core Problem slide: {core_problem_candidate['error']}")

    # Whole-site schema coverage, from whatever full-site Page Audit job the
    # user last ran (if any) — not the fresh 20-page page_audit_result above,
    # which is too small a sample for a sitewide schema claim.
    latest_page_audit_job = (
        db.query(PageAuditJob)
        .filter(PageAuditJob.client_id == client_id, PageAuditJob.status == "done")
        .order_by(PageAuditJob.created_at.desc())
        .first()
    )
    schema_validation_result = (
        aggregate_schema_validation(latest_page_audit_job.result.get("pages", []))
        if latest_page_audit_job and latest_page_audit_job.result
        else None
    )

    return {
        "site_audit": site_audit_result,
        "page_audit": page_audit_result,
        "schema_validation": schema_validation_result,
        "site_audit_issues": site_audit_issues_rows or None,
        "structured_data_rows": structured_data_rows or None,
        "site_audit_pages_rows": site_audit_pages_rows or None,
        "site_audit_overview": site_audit_overview,
        "backlink_summary": backlink_summary,
        "own_domain_rating": own_domain_rating,
        "core_problem": core_problem_result,
        "psi_mobile": psi_mobile,
        "psi_desktop": psi_desktop,
        "analytics": analytics,
        "competitor_rows": competitor_rows_all or None,
        "keyword_rows": keyword_rows_all or None,
        "backlink_rows": own_backlink_rows or None,
        "backlink_row_count": own_backlink_row_count,
        "competitor_positions": competitor_positions or None,
        "brand_color_hex": site_audit_result.get("brand_color"),
        "company_overview": company_overview_result,
        "tech_stack": tech_stack_result,
        "competitor_analysis": competitor_analysis_result,
        "domain_strategy": domain_strategy_result,
        "ux_findings": ux_findings_result,
        "content_generation_issues": content_issues,
    }


@router.post("/{client_id}/report-preview")
def report_preview(
    client_id: uuid.UUID,
    include_analytics: bool = True,
    include_pagespeed: bool = True,
    include_company_overview: bool = True,
    company_overview_override: dict | None = Body(default=None),
    competitor_analysis_override: dict | None = Body(default=None),
    ux_notes: str | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Runs every check and returns the report content as JSON instead of a
    PPTX — the user reviews/edits the text sections, then POSTs the edited
    version back to /generate-report via company_overview_override /
    competitor_analysis_override so the download matches what they saw."""
    client = _get_owned_client(client_id, db, current_user)
    data = _gather_report_data(
        client, db, client_id, include_analytics, include_pagespeed, include_company_overview,
        company_overview_override, competitor_analysis_override, ux_notes,
    )
    return {"client_name": client.name, "website_url": client.website_url, **data}


def _build_pptx_for_client(
    client: Client,
    db: Session,
    client_id: uuid.UUID,
    include_analytics: bool,
    include_pagespeed: bool,
    include_company_overview: bool,
    company_overview_override: dict | None,
    competitor_analysis_override: dict | None,
    ux_notes: str | None,
    on_progress: Callable[[str, int], None] | None = None,
) -> tuple[bytes, str, list[str]]:
    """Runs the available checks for this client and assembles the PPTX
    bytes — shared by the synchronous /generate-report endpoint and the
    background job runner below. The third return value lists any
    AI-dependent section that failed this run (keyword clustering, Core
    Problem, a competitor narrative, Next Steps) — deliberately never
    rendered into the PPTX itself (a client-facing deliverable is no place
    for "Groq request failed: 429"), surfaced instead through the job
    status API so the agency user sees it before handing the file over."""
    progress = on_progress or (lambda _stage, _pct: None)
    data = _gather_report_data(
        client, db, client_id, include_analytics, include_pagespeed, include_company_overview,
        company_overview_override, competitor_analysis_override, ux_notes, on_progress,
    )

    logo_bytes = None
    logo_url = (data.get("site_audit") or {}).get("logo_url")
    if logo_url:
        try:
            logo_bytes = fetch_logo_bytes(logo_url)
        except Exception:
            logger.exception("Logo fetch failed for client %s (%s) — continuing without it", client_id, logo_url)

    # Same list object _gather_report_data already put in data["content_
    # generation_issues"] — mutated in place here too so **data below
    # carries every AI-dependent failure (keyword clustering, Core Problem,
    # each competitor, Next Steps) through to build_report without a
    # colliding duplicate keyword argument.
    content_issues = data.get("content_generation_issues")
    if content_issues is None:
        content_issues = []
        data["content_generation_issues"] = content_issues

    # Strip excluded competitor keywords (brand, nav/login, careers, typos,
    # unrelated industries) before anything downstream — PPTX slides, the
    # ranking_page_types signal below, Next Steps findings — ever sees them.
    _filter_competitor_keywords(client, data)

    competitor_narratives = _generate_competitor_narratives(
        client, data, on_progress=on_progress, content_issues=content_issues
    )

    # Bespoke, business-aware Next Steps advice (real products/competitors/
    # numbers, sections that don't fit the business dropped entirely) in
    # place of the fixed checklist — see next_steps_service for why. Only
    # attempted when an AI key is configured (same gate as Core Problem);
    # falls back to the static per-category slides automatically inside
    # build_report when this is None or a category comes back empty.
    next_steps_ai = None
    if settings.gemini_api_key or settings.groq_api_key or settings.claude_api_key:
        progress("Writing tailored recommendations...", 90)
        client_domain = client.website_url.replace("https://", "").replace("http://", "").rstrip("/")
        findings = _build_next_steps_findings(data, competitor_narratives)
        next_steps_candidate = generate_next_steps(client.name, client_domain, findings)
        if "error" not in next_steps_candidate:
            next_steps_ai = next_steps_candidate
        else:
            logger.warning("Next Steps generation failed for client %s: %s", client.id, next_steps_candidate["error"])
            content_issues.append(f"Next Steps recommendations: {next_steps_candidate['error']}")

    # Popped out before spreading — build_report has no such parameter (see
    # the docstring above on why this never goes into the PPTX itself).
    data.pop("content_generation_issues", None)

    progress("Building presentation...", 96)
    try:
        pptx_bytes = build_report(
            client_name=client.name,
            website_url=client.website_url,
            logo_bytes=logo_bytes,
            competitor_narratives=competitor_narratives,
            next_steps_ai=next_steps_ai,
            **data,
        )
    except Exception:
        logger.exception("PPTX build failed for client %s", client_id)
        raise HTTPException(status_code=500, detail="Report generation failed while building the PPTX.")

    filename = f"{client.name.replace(' ', '-')}-seo-audit.pptx"
    return pptx_bytes, filename, content_issues


@router.post("/{client_id}/generate-report")
def generate_report(
    client_id: uuid.UUID,
    include_analytics: bool = True,
    include_pagespeed: bool = True,
    include_company_overview: bool = True,
    company_overview_override: dict | None = Body(default=None),
    competitor_analysis_override: dict | None = Body(default=None),
    ux_notes: str | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Synchronous PPTX download — one call, one response. Kept for backward
    compatibility; slow enough runs (PageSpeed Insights, AI narratives) can
    outlast the hosting gateway's request timeout, so prefer
    /generate-report/start for new callers."""
    client = _get_owned_client(client_id, db, current_user)
    # content_generation_issues has nowhere to go on this raw-file response
    # (unlike the /start job, which surfaces it via its JSON status) — this
    # legacy synchronous path is kept for backward compatibility only, not
    # used by the current frontend (which always uses /start + polling).
    pptx_bytes, filename, _content_issues = _build_pptx_for_client(
        client, db, client_id, include_analytics, include_pagespeed, include_company_overview,
        company_overview_override, competitor_analysis_override, ux_notes,
    )
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _run_generate_report_job(
    job_id: uuid.UUID,
    client_id: uuid.UUID,
    include_analytics: bool,
    include_pagespeed: bool,
    include_company_overview: bool,
    company_overview_override: dict | None,
    competitor_analysis_override: dict | None,
    ux_notes: str | None,
):
    """Builds the PPTX in a background thread with its own DB session, so a
    slow build (PageSpeed Insights, AI narratives, crawls) never has an HTTP
    request waiting on it past a gateway's timeout."""
    db = SessionLocal()
    progress_db = SessionLocal()
    try:
        job = db.get(ReportGenerationJob, job_id)
        job.status = "running"
        db.commit()

        # Separate session for progress updates (same pattern as
        # _run_page_audit_job's PageAuditJob progress callback) — keeps
        # these frequent small commits independent of the main session's
        # transaction, which stays open for the whole build.
        def progress(stage: str, pct: int):
            j = progress_db.get(ReportGenerationJob, job_id)
            if j:
                j.progress_stage = stage
                j.progress_pct = pct
                progress_db.commit()

        client = db.get(Client, client_id)
        pptx_bytes, filename, content_issues = _build_pptx_for_client(
            client, db, client_id, include_analytics, include_pagespeed, include_company_overview,
            company_overview_override, competitor_analysis_override, ux_notes, progress,
        )

        job = db.get(ReportGenerationJob, job_id)
        job.status = "done"
        job.filename = filename
        job.pptx_bytes = pptx_bytes
        job.progress_stage = None
        job.progress_pct = 100
        # Never rendered into the PPTX itself (see _build_pptx_for_client's
        # docstring) — stored here so the agency user sees which sections
        # failed and why, right in the app, before deciding whether to
        # regenerate or send the file as-is.
        job.content_generation_issues = content_issues or None
        db.commit()
    except Exception as e:
        job = db.get(ReportGenerationJob, job_id)
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
    finally:
        db.close()
        progress_db.close()


@router.post("/{client_id}/generate-report/start")
def start_generate_report_job(
    client_id: uuid.UUID,
    include_analytics: bool = True,
    include_pagespeed: bool = True,
    include_company_overview: bool = True,
    company_overview_override: dict | None = Body(default=None),
    competitor_analysis_override: dict | None = Body(default=None),
    ux_notes: str | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Kicks off a background PPTX build and returns a job id to poll —
    avoids blocking on a single long request that could outlast the hosting
    gateway's timeout."""
    _get_owned_client(client_id, db, current_user)
    job = ReportGenerationJob(client_id=client_id, status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(
        target=_run_generate_report_job,
        args=(
            job.id, client_id, include_analytics, include_pagespeed, include_company_overview,
            company_overview_override, competitor_analysis_override, ux_notes,
        ),
        daemon=True,
    ).start()
    return {"job_id": job.id}


@router.get("/{client_id}/generate-report/{job_id}")
def get_generate_report_job(
    client_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_client(client_id, db, current_user)
    job = db.get(ReportGenerationJob, job_id)
    if not job or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Job not found")
    # A background job's worker process can die mid-build (deploy restart,
    # OOM, crash) with nothing left alive to ever mark the row "done" or
    # "failed" — confirmed real: a job frozen at "Analyzing competitor 2 of
    # 4" / 65% for 30+ minutes, far past any realistic worst-case for that
    # one step (Groq's own call+retry tops out around 2-3 minutes). Treat a
    # "running" job whose progress hasn't moved in STALE_JOB_MINUTES as
    # dead rather than let the frontend poll a frozen percentage forever.
    if job.status == "running" and datetime.now(timezone.utc) - job.updated_at > timedelta(minutes=STALE_JOB_MINUTES):
        job.status = "failed"
        job.error = (
            "Report generation appears to have stalled or crashed server-side "
            f"(no progress for over {STALE_JOB_MINUTES} minutes) — please try again."
        )
        db.commit()
    return {
        "id": job.id,
        "status": job.status,
        "error": job.error,
        "progress_stage": job.progress_stage,
        "progress_pct": job.progress_pct,
        "content_generation_issues": job.content_generation_issues,
    }


@router.get("/{client_id}/generate-report/{job_id}/download")
def download_generate_report_job(
    client_id: uuid.UUID,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_client(client_id, db, current_user)
    job = db.get(ReportGenerationJob, job_id)
    if not job or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"Report not ready yet (status: {job.status})")
    return Response(
        content=job.pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )

