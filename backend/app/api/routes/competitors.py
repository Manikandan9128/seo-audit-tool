import csv
import io
import json
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.client import Client
from app.models.domain_rating import DomainRating
from app.models.semrush_import import SemrushImport
from app.models.user import User
from app.services.semrush_analysis_service import analyze as analyze_semrush_data, _normalize_domain
from app.services.semrush_ai_summary_service import generate_ai_summary
from app.services.semrush_parser import parse_semrush_file
from app.services.geopulse_parser import parse_geopulse_file
from app.services.manual_keyword_cluster_parser import parse_manual_keyword_cluster_file

router = APIRouter(prefix="/clients", tags=["competitors"])

_GEOPULSE_MAX_UPLOAD_BYTES = 25 * 1_048_576  # 25 MB — generous for a real data export


def _get_owned_client(client_id: uuid.UUID, db: Session, user: User) -> Client:
    client = db.get(Client, client_id)
    if not client or client.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


@router.post("/{client_id}/semrush-upload")
async def upload_semrush_file(
    client_id: uuid.UUID,
    file: UploadFile,
    is_own_site: bool = Form(True),
    domain_label: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = _get_owned_client(client_id, db, current_user)
    content = await file.read()
    try:
        import_type, parsed_data = parse_semrush_file(file.filename or "upload.csv", content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    # Keyword Gap is the one import type that self-identifies its compared
    # domains (parse_semrush_file stores each row's "domain_positions" dict
    # keyed by every domain column found in the export). When uploaded as
    # is_own_site=True, the client's own domain MUST be one of those keys —
    # if it isn't, this is a Keyword Gap export for a different site entirely
    # (confirmed real incident: a Tata Motors passenger-car keyword export
    # got uploaded to a commercial-truck client's is_own_site slot, silently
    # replacing every "Target Keywords" slide with unrelated keywords — the
    # report pipeline has no way to tell "wrong domain" from "real data" once
    # it's in the table, so this has to be caught here, at upload, generically
    # for any client rather than patched per-incident).
    if import_type == "keyword_gap" and is_own_site:
        own_norm = _normalize_domain(
            (client.website_url or "").replace("https://", "").replace("http://", "").rstrip("/")
        )

        def _same_site(a: str, b: str) -> bool:
            # Exact match, or either is a subdomain of the other (e.g. the
            # client's own site shows up in the export as "shop.bharatbenz.com"
            # or "trucks.bharatbenz.com" rather than the bare root domain —
            # real Semrush exports mix both forms depending on what was
            # actually queried) — a bare "." + root suffix check so it can't
            # also match an unrelated domain that merely ends with the same
            # letters (e.g. "notbharatbenz.com").
            return a == b or a.endswith("." + b) or b.endswith("." + a)

        file_domains = set()
        for row in parsed_data.get("rows", []):
            file_domains.update((row.get("domain_positions") or {}).keys())
        file_domains_norm = {_normalize_domain(d) for d in file_domains}
        if file_domains_norm and not any(_same_site(own_norm, d) for d in file_domains_norm):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"This Keyword Gap export doesn't include {client.website_url} as one of its "
                    f"compared domains (found: {', '.join(sorted(file_domains))}). This looks like "
                    "a file from a different site — re-check the export, or if this is genuinely "
                    "meant as a competitor's file, upload it with \"is your own site\" unchecked."
                ),
            )

    record = SemrushImport(
        client_id=client_id,
        uploaded_by_user_id=current_user.id,
        original_filename=file.filename or "upload.csv",
        import_type=import_type,
        is_own_site=is_own_site,
        domain_label=domain_label or None,
        parsed_data=parsed_data,
        original_file=content,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    # Surfaced so a Domain Overview PDF exported from the wrong Semrush
    # database (confirmed real incident: a client's 4 competitor PDFs were
    # accidentally exported from Guyana instead of US, silently producing
    # empty/near-zero traffic numbers with no error anywhere) is visible
    # right here at upload time instead of only showing up as wrong-looking
    # numbers in the finished report.
    database = None
    if import_type == "domain_overview" and parsed_data.get("rows"):
        database = parsed_data["rows"][0].get("database")
    return {
        "id": record.id,
        "import_type": record.import_type,
        "row_count": parsed_data["row_count"],
        "original_filename": record.original_filename,
        "is_own_site": record.is_own_site,
        "domain_label": record.domain_label,
        "database": database,
    }


@router.post("/{client_id}/geopulse-upload")
async def upload_geopulse_file(
    client_id: uuid.UUID,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stores a GeoPulse (client's own AI-visibility tool) export, any file
    format, as a SemrushImport row with import_type="geopulse" — reuses the
    existing generic import table/list/delete endpoints rather than a new
    model. The report-generation route feeds the extracted text to
    geopulse_ai_service to ground the AEO/GEO slide content."""
    _get_owned_client(client_id, db, current_user)
    content = await file.read()
    if len(content) > _GEOPULSE_MAX_UPLOAD_BYTES:
        # Fail fast with a real message instead of letting a huge file
        # (especially a many-page PDF pdfplumber has to scan) risk a slow
        # timeout that surfaces in the browser as a bare "upload failed"
        # with no detail at all — confirmed real 2026-09-25.
        raise HTTPException(
            status_code=400,
            detail=f"File is {len(content) / 1_048_576:.1f} MB — GeoPulse exports over "
            f"{_GEOPULSE_MAX_UPLOAD_BYTES // 1_048_576} MB aren't accepted. Export a smaller "
            "date range or section if your GeoPulse tool supports it.",
        )
    try:
        parsed_data = parse_geopulse_file(file.filename or "upload", content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")

    record = SemrushImport(
        client_id=client_id,
        uploaded_by_user_id=current_user.id,
        original_filename=file.filename or "upload",
        import_type="geopulse",
        is_own_site=True,
        parsed_data=parsed_data,
        original_file=content,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "import_type": record.import_type,
        "row_count": parsed_data["row_count"],
        "original_filename": record.original_filename,
    }


@router.post("/{client_id}/keyword-cluster-upload")
async def upload_manual_keyword_cluster_file(
    client_id: uuid.UUID,
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stores an SEO strategist's own hand-built keyword clustering
    (CSV/XLSX with a Keyword column and a Cluster column) as a SemrushImport
    row with import_type="keyword_cluster_manual" — same generic-import-table
    reuse as geopulse-upload above. Whenever at least one of these exists for
    a client, keyword_cluster_pipeline.build_final_keyword_clusters uses it
    as the source of truth for Target Keywords clustering instead of running
    the AI Phase 2/3 pipeline at all (site_audit.py wires this at report-
    generation time) — the AI pipeline is the fallback for when no manual
    file has been uploaded, never a second opinion layered on top of one
    that has."""
    _get_owned_client(client_id, db, current_user)
    content = await file.read()
    try:
        parsed_data = parse_manual_keyword_cluster_file(file.filename or "upload.csv", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read file: {e}")

    record = SemrushImport(
        client_id=client_id,
        uploaded_by_user_id=current_user.id,
        original_filename=file.filename or "upload.csv",
        import_type="keyword_cluster_manual",
        is_own_site=True,
        parsed_data=parsed_data,
        original_file=content,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "import_type": record.import_type,
        "row_count": parsed_data["row_count"],
        "original_filename": record.original_filename,
    }


@router.get("/{client_id}/semrush-imports")
def list_semrush_imports(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_owned_client(client_id, db, current_user)
    records = (
        db.query(SemrushImport)
        .filter(SemrushImport.client_id == client_id)
        .order_by(SemrushImport.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "import_type": r.import_type,
            "original_filename": r.original_filename,
            "row_count": r.parsed_data.get("row_count", 0),
            "created_at": r.created_at,
            "is_own_site": r.is_own_site,
            "domain_label": r.domain_label,
        }
        for r in records
    ]


@router.get("/{client_id}/semrush-analysis")
def semrush_analysis(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Compares uploaded own-site vs. competitor Semrush data and returns
    concrete gaps (traffic, keywords, backlinks) with recommendations."""
    client = _get_owned_client(client_id, db, current_user)
    records = (
        db.query(SemrushImport)
        .filter(SemrushImport.client_id == client_id)
        .order_by(SemrushImport.created_at.desc())
        .all()
    )
    payload = [
        {
            "import_type": r.import_type,
            "is_own_site": r.is_own_site,
            "domain_label": r.domain_label,
            "created_at": r.created_at,
            "parsed_data": r.parsed_data,
        }
        for r in records
    ]
    own_domain = (client.website_url or "").replace("https://", "").replace("http://", "").rstrip("/")
    return analyze_semrush_data(payload, own_domain=own_domain)


@router.get("/{client_id}/semrush-ai-summary")
def semrush_ai_summary(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Runs the rule-based analysis, then asks Gemini to turn the findings
    into a narrative executive summary + prioritized action list."""
    client = _get_owned_client(client_id, db, current_user)
    records = (
        db.query(SemrushImport)
        .filter(SemrushImport.client_id == client_id)
        .order_by(SemrushImport.created_at.desc())
        .all()
    )
    payload = [
        {
            "import_type": r.import_type,
            "is_own_site": r.is_own_site,
            "domain_label": r.domain_label,
            "created_at": r.created_at,
            "parsed_data": r.parsed_data,
        }
        for r in records
    ]
    own_domain = (client.website_url or "").replace("https://", "").replace("http://", "").rstrip("/")
    analysis = analyze_semrush_data(payload, own_domain=own_domain)
    return generate_ai_summary(client.name, client.website_url, analysis)


@router.get("/{client_id}/semrush-imports/{import_id}")
def get_semrush_import(
    client_id: uuid.UUID, import_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    _get_owned_client(client_id, db, current_user)
    record = db.get(SemrushImport, import_id)
    if not record or record.client_id != client_id:
        raise HTTPException(status_code=404, detail="Import not found")
    return {
        "id": record.id,
        "import_type": record.import_type,
        "original_filename": record.original_filename,
        "parsed_data": record.parsed_data,
    }


def _rows_to_csv(rows: list) -> bytes:
    """parsed_data rows back to a CSV, for imports uploaded before the
    original file was kept. Header is every key seen across the rows, in
    first-seen order; nested values (e.g. keyword_gap's domain_positions)
    are written as JSON."""
    header: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            header.extend(k for k in row if k not in header)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        if not isinstance(row, dict):
            continue
        writer.writerow([
            json.dumps(v) if isinstance(v, (dict, list)) else ("" if v is None else v)
            for v in (row.get(k) for k in header)
        ])
    return buf.getvalue().encode("utf-8-sig")


@router.get("/{client_id}/semrush-imports/{import_id}/download")
def download_semrush_import(
    client_id: uuid.UUID, import_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """The uploaded file exactly as it was uploaded. Imports from before
    original_file existed come back as a CSV rebuilt from parsed_data, named
    "<name> (rebuilt).csv" so nobody mistakes it for the original export."""
    _get_owned_client(client_id, db, current_user)
    record = db.get(SemrushImport, import_id)
    if not record or record.client_id != client_id:
        raise HTTPException(status_code=404, detail="Import not found")
    if record.original_file is not None:
        content, filename = record.original_file, record.original_filename
    else:
        rows = (record.parsed_data or {}).get("rows") or []
        if not any(isinstance(r, dict) for r in rows):
            raise HTTPException(status_code=404, detail="This file was uploaded before downloads were supported and has no rows to rebuild.")
        content, filename = _rows_to_csv(rows), f"{Path(record.original_filename).stem} (rebuilt).csv"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.delete("/{client_id}/semrush-imports/{import_id}")
def delete_semrush_import(
    client_id: uuid.UUID, import_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    _get_owned_client(client_id, db, current_user)
    record = db.get(SemrushImport, import_id)
    if not record or record.client_id != client_id:
        raise HTTPException(status_code=404, detail="Import not found")
    db.delete(record)
    db.commit()
    return {"ok": True}


@router.get("/{client_id}/domain-ratings")
def list_domain_ratings(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Manually-entered DR (e.g. from Ahrefs' free Authority Checker) per
    domain — own site or a competitor. Overrides Semrush's Authority Score
    in the Competitor Analysis table for any domain listed here."""
    _get_owned_client(client_id, db, current_user)
    rows = (
        db.query(DomainRating)
        .filter(DomainRating.client_id == client_id)
        .order_by(DomainRating.domain)
        .all()
    )
    return [{"id": r.id, "domain": r.domain, "dr": r.dr} for r in rows]


@router.put("/{client_id}/domain-ratings")
def upsert_domain_rating(
    client_id: uuid.UUID,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Body: {"domain": "example.com", "dr": 42}. Upserts by normalized
    domain (www./case/trailing-slash insensitive, same matching as the
    Semrush DR/Worldwide merges) so re-saving the same domain updates it
    in place instead of creating a duplicate row."""
    _get_owned_client(client_id, db, current_user)
    domain = str(payload.get("domain", "")).strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domain is required")
    try:
        dr = int(payload.get("dr"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="DR must be a number")

    target = _normalize_domain(domain)
    existing = (
        db.query(DomainRating)
        .filter(DomainRating.client_id == client_id)
        .all()
    )
    match = next((r for r in existing if _normalize_domain(r.domain) == target), None)
    if match:
        match.domain = domain
        match.dr = dr
    else:
        match = DomainRating(client_id=client_id, domain=domain, dr=dr)
        db.add(match)
    db.commit()
    db.refresh(match)
    return {"id": match.id, "domain": match.domain, "dr": match.dr}


@router.delete("/{client_id}/domain-ratings/{rating_id}")
def delete_domain_rating(
    client_id: uuid.UUID,
    rating_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_client(client_id, db, current_user)
    row = db.get(DomainRating, rating_id)
    if not row or row.client_id != client_id:
        raise HTTPException(status_code=404, detail="Domain rating not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
