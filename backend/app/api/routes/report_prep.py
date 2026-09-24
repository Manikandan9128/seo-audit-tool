"""Generate Report as a server-side job.

The client page's "Generate Report" button used to fire each section's
request from the browser (company overview, site audit, PageSpeed, tech
stack, analytics, All Pages crawl kickoff), so navigating away or refreshing
cancelled the run and reset the button. This module runs the same route
functions in a background thread instead and keeps each section's result
on a ReportPrepJob row, which the page polls and reads back after a
navigation or refresh.

Each section calls the exact endpoint function the browser used to call,
with the same arguments, so what gets saved (SiteAuditRun, the company
overview cache, the All Pages crawl job) and what the page shows are
unchanged. The PPTX build (/generate-report/start) is separate and
untouched: it never read these previews, only the same server-side caches.
"""

import concurrent.futures
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.routes import google_oauth as google_oauth_routes
from app.api.routes import site_audit as site_audit_routes
from app.db.session import SessionLocal
from app.models.client import Client
from app.models.report_prep_job import ReportPrepJob
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clients", tags=["report-prep"])

# The longest section is PageSpeed (two 50s Lighthouse runs in parallel) or
# the company overview crawl + AI call — a few minutes at worst. The job row
# is touched at least every HEARTBEAT_SECONDS while its thread is alive, so
# STALE_MINUTES without an update means the thread is gone (deploy restart,
# crash), not that a section is slow.
STALE_MINUTES = 15
HEARTBEAT_SECONDS = 30

SECTION_KEYS = ("overview", "site_audit", "all_pages", "pagespeed", "tech_stack", "analytics")


def _get_owned_client(client_id: uuid.UUID, db: Session, user: User) -> Client:
    client = db.get(Client, client_id)
    if not client or client.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


# ---------- sections ----------
# Each takes its own DB session and the owning user, calls the same route
# function the browser used to call, and returns what the page needs to
# show that section.

def _section_overview(client_id, db, user, _params):
    overview = site_audit_routes.company_overview(client_id, force=False, db=db, current_user=user)
    try:
        catalogue = site_audit_routes.product_catalogue(client_id, db=db, current_user=user)
    except Exception:
        catalogue = {"products": []}  # the page treated a failed catalogue as empty too
    return {"overview": overview, "catalogue": catalogue}


def _section_site_audit(client_id, db, user, _params):
    site_audit_routes.site_audit(client_id, db=db, current_user=user)
    return {}  # saved as a SiteAuditRun; the page re-reads its history list


def _section_all_pages(client_id, db, user, _params):
    # Kicks off the background crawl and returns right away, not awaited —
    # same as before (the PPTX build uses the latest *completed* crawl).
    return site_audit_routes.start_page_audit_job(client_id, limit=200000, db=db, current_user=user)


def _section_pagespeed(client_id, db, user, _params):
    user_id = user.id

    def one(strategy):
        s = SessionLocal()
        try:
            return site_audit_routes.pagespeed(client_id, strategy=strategy, db=s, current_user=s.get(User, user_id))
        finally:
            s.close()

    # Both strategies in parallel, and either failing fails the section —
    # same as the page's Promise.all.
    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        mobile, desktop = ex.submit(one, "mobile"), ex.submit(one, "desktop")
        return {"mobile": mobile.result(), "desktop": desktop.result()}


def _section_tech_stack(client_id, db, user, _params):
    return site_audit_routes.tech_stack(client_id, db=db, current_user=user)


def _section_analytics(client_id, db, user, params):
    return google_oauth_routes.analytics_report(
        client_id, start_date=params["analytics_start"], end_date=params["analytics_end"], db=db, current_user=user,
    )


SECTION_RUNNERS = {
    "overview": _section_overview,
    "site_audit": _section_site_audit,
    "all_pages": _section_all_pages,
    "pagespeed": _section_pagespeed,
    "tech_stack": _section_tech_stack,
    "analytics": _section_analytics,
}


def _run_section(key: str, client_id: uuid.UUID, user_id: uuid.UUID, params: dict) -> dict:
    db = SessionLocal()
    try:
        data = SECTION_RUNNERS[key](client_id, db, db.get(User, user_id), params)
        return {"status": "done", "data": jsonable_encoder(data), "error": None}
    except HTTPException as e:
        return {"status": "failed", "data": None, "error": str(e.detail)}
    except Exception as e:
        logger.exception("Generate Report section %s failed for client %s", key, client_id)
        return {"status": "failed", "data": None, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    finally:
        db.close()


def _run_prep_job(job_id: uuid.UUID, client_id: uuid.UUID, user_id: uuid.UUID, keys: list[str], params: dict) -> None:
    """Runs every requested section in parallel (as the browser did) and
    saves each result as it lands. Only this thread writes the row; waiting
    in HEARTBEAT_SECONDS slices keeps updated_at fresh while sections run."""
    db = SessionLocal()
    try:
        job = db.get(ReportPrepJob, job_id)
        sections = {k: {"status": "running", "data": None, "error": None} for k in keys}
        job.status, job.sections, job.progress_pct = "running", sections, 0
        db.commit()

        with concurrent.futures.ThreadPoolExecutor(len(keys)) as ex:
            futures = {ex.submit(_run_section, k, client_id, user_id, params): k for k in keys}
            pending = set(futures)
            while pending:
                finished, pending = concurrent.futures.wait(
                    pending, timeout=HEARTBEAT_SECONDS, return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for f in finished:
                    sections[futures[f]] = f.result()
                job.sections = dict(sections)  # new dict so SQLAlchemy sees the JSONB change
                job.progress_pct = round(100 * (len(keys) - len(pending)) / len(keys))
                job.updated_at = datetime.now(timezone.utc)
                db.commit()

        # A section failing is shown on that section, as before — the run
        # itself still counts as generated.
        job.status = "done"
        db.commit()
    except Exception as e:
        logger.exception("Generate Report job %s failed", job_id)
        db.rollback()
        job = db.get(ReportPrepJob, job_id)
        if job:
            job.status = "failed"
            job.error = f"Generate Report failed: {type(e).__name__}: {str(e)[:300]}"
            db.commit()
    finally:
        db.close()


# ---------- job lookup ----------

def _fail_if_stale(job: ReportPrepJob, db: Session, commit: bool = True) -> None:
    if job.status in ("pending", "running") and datetime.now(timezone.utc) - job.updated_at > timedelta(minutes=STALE_MINUTES):
        job.status = "failed"
        job.error = (
            "Generate Report appears to have stalled or crashed server-side "
            f"(no progress for over {STALE_MINUTES} minutes) — please try again."
        )
        # start_report_prep_job flushes only: committing there would release
        # its client row lock before it creates the new job.
        db.commit() if commit else db.flush()


def _latest_job(db: Session, client_id: uuid.UUID) -> ReportPrepJob | None:
    return (
        db.query(ReportPrepJob)
        .filter(ReportPrepJob.client_id == client_id)
        .order_by(ReportPrepJob.created_at.desc())
        .first()
    )


def _job_status(job: ReportPrepJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "sections": job.sections or {},
        "progress_pct": job.progress_pct,
        "error": job.error,
        "created_at": job.created_at,
    }


# ---------- endpoints ----------

@router.post("/{client_id}/report-prep/start")
def start_report_prep_job(
    client_id: uuid.UUID,
    sections: list[str] = Body(..., embed=True),
    analytics_start: str = Body("30daysAgo", embed=True),
    analytics_end: str = Body("today", embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Starts a Generate Report run, or returns the client's run already in
    progress (reused: true) — a double click, a second tab, or two requests
    at once never start two runs. The client row lock makes simultaneous
    requests take turns, so both can't see "no active run"."""
    _get_owned_client(client_id, db, current_user)
    keys = [k for k in SECTION_KEYS if k in set(sections)]
    unknown = set(sections) - set(SECTION_KEYS)
    if unknown or not keys:
        raise HTTPException(status_code=400, detail=f"sections must be a non-empty subset of {list(SECTION_KEYS)}")

    db.query(Client).filter(Client.id == client_id).with_for_update().one()
    latest = _latest_job(db, client_id)
    if latest:
        _fail_if_stale(latest, db, commit=False)
        if latest.status in ("pending", "running"):
            db.commit()
            return {"job_id": latest.id, "reused": True}

    job = ReportPrepJob(
        client_id=client_id, status="pending",
        sections={k: {"status": "pending", "data": None, "error": None} for k in keys},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    threading.Thread(
        target=_run_prep_job,
        args=(job.id, client_id, current_user.id, keys, {"analytics_start": analytics_start, "analytics_end": analytics_end}),
        daemon=True,
    ).start()
    return {"job_id": job.id, "reused": False}


@router.get("/{client_id}/report-prep/latest")
def get_latest_report_prep_job(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The client's most recent Generate Report run, or null — the page
    restores the button and section previews from this after a navigation
    or refresh. Declared before /{job_id} so "latest" isn't parsed as an id."""
    _get_owned_client(client_id, db, current_user)
    job = _latest_job(db, client_id)
    if not job:
        return None
    _fail_if_stale(job, db)
    return _job_status(job)


@router.get("/{client_id}/report-prep/{job_id}")
def get_report_prep_job(
    client_id: uuid.UUID, job_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    _get_owned_client(client_id, db, current_user)
    job = db.get(ReportPrepJob, job_id)
    if not job or job.client_id != client_id:
        raise HTTPException(status_code=404, detail="Job not found")
    _fail_if_stale(job, db)
    return _job_status(job)
