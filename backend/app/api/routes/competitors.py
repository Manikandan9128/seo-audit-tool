import uuid

from fastapi import APIRouter, Body, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.client import Client
from app.models.domain_rating import DomainRating
from app.models.semrush_import import SemrushImport
from app.models.user import User
from app.services.semrush_analysis_service import analyze as analyze_semrush_data, _normalize_domain
from app.services.semrush_ai_summary_service import generate_ai_summary
from app.services.semrush_parser import parse_semrush_file

router = APIRouter(prefix="/clients", tags=["competitors"])


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
    _get_owned_client(client_id, db, current_user)
    content = await file.read()
    try:
        import_type, parsed_data = parse_semrush_file(file.filename or "upload.csv", content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not parse file: {e}")

    record = SemrushImport(
        client_id=client_id,
        uploaded_by_user_id=current_user.id,
        original_filename=file.filename or "upload.csv",
        import_type=import_type,
        is_own_site=is_own_site,
        domain_label=domain_label or None,
        parsed_data=parsed_data,
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
