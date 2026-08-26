import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

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
from app.models.client import Client
from app.models.google_connection import GoogleConnection
from app.models.page_audit_job import PageAuditJob
from app.models.semrush_import import SemrushImport
from app.models.site_audit_run import SiteAuditRun
from app.models.user import User
from app.reporting.pptx_builder import build_report
from app.services import ga4_service, gsc_service
from app.services.company_overview_service import extract_company_overview
from app.services.competitor_narrative_service import generate_competitor_narrative
from app.services.logo_service import fetch_logo_bytes
from app.services.product_catalogue_service import crawl_product_catalogue
from app.services.semrush_analysis_service import analyze as analyze_semrush_data
from app.services.tech_stack_service import detect_tech_stack
from app.services.technical_seo_service import run_multi_page_audit, run_multi_page_audit_async, run_site_audit

router = APIRouter(prefix="/clients", tags=["site-audit"])


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
    return run_multi_page_audit(client.website_url, page_limit=limit)


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
def company_overview(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Crawls the client's About/Products/legal pages and asks Gemini to
    extract a structured overview (name, description, products, KPIs,
    registration info) for the report's client-overview slide."""
    client = _get_owned_client(client_id, db, current_user)
    result = extract_company_overview(client.website_url)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
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


def _generate_competitor_narratives(client: Client, data: dict, max_competitors: int = 5) -> dict[str, dict]:
    """One AI-generated "Areas of Focus" narrative per competitor domain,
    grounded in whatever data was actually uploaded for that domain (Domain
    Overview stats, keyword positions, the rule-based gap findings that
    mention it by name). Capped at max_competitors — each one is a real AI
    call, so an unbounded competitor list would make report generation slow
    and expensive for no proportional benefit."""
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

    narratives = {}
    for domain in domains:
        row = next((r for r in competitor_rows if r.get("domain") == domain), None)
        top_keywords = sorted(
            competitor_positions.get(domain, []), key=lambda r: _as_number(r.get("search_volume")), reverse=True
        )[:8]
        relevant_gaps = [i["summary"] for i in gap_issues if domain in i.get("summary", "")]
        facts = {
            "domain_stats": row,
            "top_ranking_keywords": [
                {"keyword": r.get("keyword"), "position": r.get("position"), "search_volume": r.get("search_volume")}
                for r in top_keywords
            ],
            "gaps_vs_this_competitor": relevant_gaps,
        }
        if not row and not top_keywords and not relevant_gaps:
            continue  # nothing grounded to write from — skip rather than let the AI invent
        try:
            narratives[domain] = generate_competitor_narrative(client.name, client_domain, domain, facts)
        except Exception:
            # A single competitor's AI narrative failing (rate limit, API
            # error, timeout) shouldn't take down the whole report.
            continue
    return narratives


def _gather_report_data(
    client: Client,
    db: Session,
    client_id: uuid.UUID,
    include_analytics: bool,
    include_pagespeed: bool,
    include_company_overview: bool,
    company_overview_override: dict | None,
    competitor_analysis_override: dict | None,
) -> dict:
    """Runs every check for this client and returns the plain-dict payload
    that build_report() consumes — shared by the preview (JSON, for the user
    to review/edit) and generate-report (same data, built into a PPTX) so the
    two never drift apart.

    If company_overview_override / competitor_analysis_override are given
    (the user's edited version of what the preview returned), they're used
    as-is instead of re-crawling/re-analyzing."""
    company_overview_result = None
    if company_overview_override is not None:
        company_overview_result = company_overview_override
    elif include_company_overview and (settings.gemini_api_key or settings.claude_api_key):
        result = extract_company_overview(client.website_url)
        if "error" not in result:
            company_overview_result = result
            catalogue = crawl_product_catalogue(client.website_url)
            catalogue_names = [p["name"] for p in catalogue.get("products", [])]
            if catalogue_names and not company_overview_result.get("products"):
                company_overview_result["products"] = catalogue_names

    site_audit_result = run_site_audit(client.website_url)
    page_audit_result = run_multi_page_audit(client.website_url, page_limit=20)

    tech_stack_result = detect_tech_stack(client.website_url)
    if "error" in tech_stack_result:
        tech_stack_result = None

    psi_mobile = psi_desktop = None
    if include_pagespeed and settings.google_psi_api_key:
        with ThreadPoolExecutor(max_workers=2) as pool:
            mobile_future = pool.submit(run_pagespeed, client.website_url, "mobile")
            desktop_future = pool.submit(run_pagespeed, client.website_url, "desktop")
            try:
                psi_mobile = mobile_future.result()
            except Exception:
                psi_mobile = None
            try:
                psi_desktop = desktop_future.result()
            except Exception:
                psi_desktop = None

    analytics = None
    if include_analytics and (client.ga4_property_id or client.gsc_site_url):
        creds = _load_credentials(client_id, db)
        if creds:
            analytics = {}
            if client.ga4_property_id:
                try:
                    analytics["traffic_overview"] = ga4_service.get_traffic_overview(
                        creds, client.ga4_property_id, "30daysAgo", "today"
                    )
                    analytics["top_pages"] = ga4_service.get_top_pages(
                        creds, client.ga4_property_id, "30daysAgo", "today", limit=15
                    )
                    analytics["traffic_sources"] = ga4_service.get_traffic_sources(
                        creds, client.ga4_property_id, "30daysAgo", "today"
                    )
                    analytics["page_performance"] = ga4_service.get_page_performance(
                        creds, client.ga4_property_id, "30daysAgo", "today"
                    )
                except HttpError:
                    pass
            if client.gsc_site_url:
                from datetime import date, timedelta

                # Search Console data lags ~2-3 days behind — a range whose
                # end date is more recent than that reliably comes back
                # empty (not partial), so clamp regardless of "today".
                gsc_start = (date.today() - timedelta(days=30)).isoformat()
                gsc_end = (date.today() - timedelta(days=3)).isoformat()
                try:
                    analytics["search_queries"] = gsc_service.get_search_analytics(
                        creds, client.gsc_site_url, gsc_start, gsc_end, row_limit=20
                    )
                except HttpError:
                    pass

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
    own_backlink_rows = _all_rows("backlinks", own_only=True)

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
            domain_overview_rows.append({**row, "domain": row.get("domain") or label})
    domain_overview_rows.sort(key=lambda row: row["domain"] != own_website_domain)

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
        ])

    return {
        "site_audit": site_audit_result,
        "page_audit": page_audit_result,
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
    }


@router.post("/{client_id}/report-preview")
def report_preview(
    client_id: uuid.UUID,
    include_analytics: bool = True,
    include_pagespeed: bool = True,
    include_company_overview: bool = True,
    company_overview_override: dict | None = Body(default=None),
    competitor_analysis_override: dict | None = Body(default=None),
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
        company_overview_override, competitor_analysis_override,
    )
    return {"client_name": client.name, "website_url": client.website_url, **data}


@router.post("/{client_id}/generate-report")
def generate_report(
    client_id: uuid.UUID,
    include_analytics: bool = True,
    include_pagespeed: bool = True,
    include_company_overview: bool = True,
    company_overview_override: dict | None = Body(default=None),
    competitor_analysis_override: dict | None = Body(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Runs the available checks for this client and assembles a PPTX audit
    deck styled like the agency sample report — one call, one download.

    If company_overview_override / competitor_analysis_override are given
    (the user's edited version of what /report-preview returned), they're
    used as-is instead of re-crawling/re-analyzing."""
    client = _get_owned_client(client_id, db, current_user)
    data = _gather_report_data(
        client, db, client_id, include_analytics, include_pagespeed, include_company_overview,
        company_overview_override, competitor_analysis_override,
    )

    logo_bytes = None
    logo_url = (data.get("site_audit") or {}).get("logo_url")
    if logo_url:
        logo_bytes = fetch_logo_bytes(logo_url)

    competitor_narratives = _generate_competitor_narratives(client, data)

    pptx_bytes = build_report(
        client_name=client.name,
        website_url=client.website_url,
        logo_bytes=logo_bytes,
        competitor_narratives=competitor_narratives,
        **data,
    )

    filename = f"{client.name.replace(' ', '-')}-seo-audit.pptx"
    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
