import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.integrations import google_oauth
from app.integrations.crypto import encrypt, decrypt
from app.models.client import Client
from app.models.google_connection import GoogleConnection
from app.models.user import User
from app.schemas.client import ClientSelectProperties, GA4PropertyOut, GSCSiteOut
from app.services import ga4_service, gsc_service

router = APIRouter(prefix="/clients", tags=["google"])


def _get_owned_client(client_id: uuid.UUID, db: Session, user: User) -> Client:
    client = db.get(Client, client_id)
    if not client or client.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def _callback_redirect_uri(request: Request) -> str:
    # Derived from the Host header the browser actually used, so this works
    # whether the request came in via localhost or a tunnel (e.g. ngrok) — as
    # long as that exact origin is also registered as an Authorized redirect
    # URI in Google Cloud Console. Tunnels terminate TLS upstream and forward
    # plain HTTP locally, so trust X-Forwarded-Proto for the scheme.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}/api/clients/google/callback"


@router.get("/{client_id}/google/connect")
def connect_google(
    client_id: uuid.UUID, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    _get_owned_client(client_id, db, current_user)
    redirect_uri = _callback_redirect_uri(request)
    auth_url = google_oauth.build_auth_url(client_id=str(client_id), redirect_uri=redirect_uri)
    return {"auth_url": auth_url}


@router.get("/google/callback")
def google_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    try:
        client_id = uuid.UUID(google_oauth.parse_state(state))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    redirect_uri = _callback_redirect_uri(request)
    creds = google_oauth.exchange_code(code, redirect_uri)

    from googleapiclient.discovery import build as gbuild
    oauth2 = gbuild("oauth2", "v2", credentials=creds)
    userinfo = oauth2.userinfo().get().execute()
    google_email = userinfo.get("email", "unknown")

    connection = db.query(GoogleConnection).filter(GoogleConnection.client_id == client_id).first()
    expiry = creds.expiry.replace(tzinfo=timezone.utc) if creds.expiry else None
    if connection is None:
        connection = GoogleConnection(
            client_id=client_id,
            connected_by_user_id=client.owner_user_id,
            encrypted_access_token=encrypt(creds.token),
            encrypted_refresh_token=encrypt(creds.refresh_token or ""),
            token_expiry=expiry,
            scopes=list(creds.scopes or []),
            google_account_email=google_email,
        )
        db.add(connection)
    else:
        connection.encrypted_access_token = encrypt(creds.token)
        if creds.refresh_token:
            connection.encrypted_refresh_token = encrypt(creds.refresh_token)
        connection.token_expiry = expiry
        connection.scopes = list(creds.scopes or [])
        connection.google_account_email = google_email
    db.commit()

    _auto_select_properties(client, creds)
    db.commit()

    granted_scopes = set(creds.scopes or [])
    required_scopes = {
        "https://www.googleapis.com/auth/analytics.readonly",
        "https://www.googleapis.com/auth/webmasters.readonly",
    }
    missing_scopes = required_scopes - granted_scopes

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))

    status = "google_connected=1" if not missing_scopes else "google_connected=1&missing_scopes=1"
    frontend_url = f"{scheme}://{host}/clients/{client_id}?{status}"
    return RedirectResponse(url=frontend_url)


def _google_error_message(e: HttpError) -> str:
    if e.resp.status == 403:
        return "Connected Google account doesn't have permission for this property/site."
    return f"Google API error ({e.resp.status})"


def _domain_from_url(url: str) -> str:
    return url.replace("https://", "").replace("http://", "").rstrip("/").removeprefix("www.").lower()


def _auto_select_properties(client: Client, creds) -> None:
    """Best-effort: pick the GA4 property / GSC site matching this client's
    domain automatically, or the sole result if the account has just one, so
    the user isn't forced to pick manually after every connect."""
    site_domain = _domain_from_url(client.website_url or "")

    if not client.gsc_site_url:
        try:
            sites = gsc_service.list_sites(creds)
        except Exception:
            sites = []
        match = next((s for s in sites if site_domain and site_domain in s["site_url"].lower()), None)
        if match:
            client.gsc_site_url = match["site_url"]
        elif len(sites) == 1:
            client.gsc_site_url = sites[0]["site_url"]

    if not client.ga4_property_id:
        try:
            props = ga4_service.list_properties(creds)
        except Exception:
            props = []
        match = next(
            (p for p in props if site_domain and site_domain.split(".")[0] in p["display_name"].lower()), None
        )
        if match:
            client.ga4_property_id = match["name"]
        elif len(props) == 1:
            client.ga4_property_id = props[0]["name"]


def _load_credentials(client_id: uuid.UUID, db: Session):
    connection = db.query(GoogleConnection).filter(GoogleConnection.client_id == client_id).first()
    if not connection:
        raise HTTPException(status_code=400, detail="Google account not connected for this client")
    creds = google_oauth.credentials_from_stored(
        decrypt(connection.encrypted_access_token), decrypt(connection.encrypted_refresh_token)
    )
    connection.encrypted_access_token = encrypt(creds.token)
    if creds.expiry:
        connection.token_expiry = creds.expiry.replace(tzinfo=timezone.utc)
    db.commit()
    return creds


@router.get("/{client_id}/ga4/properties", response_model=list[GA4PropertyOut])
def ga4_properties(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_owned_client(client_id, db, current_user)
    creds = _load_credentials(client_id, db)
    props = ga4_service.list_properties(creds)
    return [GA4PropertyOut(name=p["name"], display_name=p["display_name"]) for p in props]


@router.get("/{client_id}/gsc/sites", response_model=list[GSCSiteOut])
def gsc_sites(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _get_owned_client(client_id, db, current_user)
    creds = _load_credentials(client_id, db)
    sites = gsc_service.list_sites(creds)
    return [GSCSiteOut(site_url=s["site_url"], permission_level=s["permission_level"]) for s in sites]


@router.post("/{client_id}/select-properties")
def select_properties(
    client_id: uuid.UUID,
    payload: ClientSelectProperties,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = _get_owned_client(client_id, db, current_user)
    if payload.ga4_property_id is not None:
        client.ga4_property_id = payload.ga4_property_id
    if payload.gsc_site_url is not None:
        client.gsc_site_url = payload.gsc_site_url
    db.commit()
    return {"ok": True}


@router.get("/{client_id}/ga4/raw-overview")
def ga4_raw_overview(
    client_id: uuid.UUID,
    start_date: str = "30daysAgo",
    end_date: str = "today",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = _get_owned_client(client_id, db, current_user)
    if not client.ga4_property_id:
        raise HTTPException(status_code=400, detail="No GA4 property selected for this client")
    creds = _load_credentials(client_id, db)
    return ga4_service.get_traffic_overview(creds, client.ga4_property_id, start_date, end_date)


@router.get("/{client_id}/gsc/raw-query-data")
def gsc_raw_query_data(
    client_id: uuid.UUID,
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = _get_owned_client(client_id, db, current_user)
    if not client.gsc_site_url:
        raise HTTPException(status_code=400, detail="No Search Console site selected for this client")
    creds = _load_credentials(client_id, db)
    return gsc_service.get_search_analytics(creds, client.gsc_site_url, start_date, end_date)


@router.get("/{client_id}/analytics-report")
def analytics_report(
    client_id: uuid.UUID,
    start_date: str = "30daysAgo",
    end_date: str = "today",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Combined GA4 + Search Console report: traffic overview, top pages,
    traffic sources, and top search queries — in one call for the report UI."""
    client = _get_owned_client(client_id, db, current_user)
    creds = _load_credentials(client_id, db)

    result: dict = {"date_range": {"start": start_date, "end": end_date}}
    result["errors"] = {}

    if client.ga4_property_id:
        try:
            result["traffic_overview"] = ga4_service.get_traffic_overview(creds, client.ga4_property_id, start_date, end_date)
            result["top_pages"] = ga4_service.get_top_pages(creds, client.ga4_property_id, start_date, end_date, limit=15)
            result["traffic_sources"] = ga4_service.get_traffic_sources(creds, client.ga4_property_id, start_date, end_date)
            result["page_performance"] = ga4_service.get_page_performance(creds, client.ga4_property_id, start_date, end_date)
        except HttpError as e:
            result["traffic_overview"] = None
            result["top_pages"] = None
            result["traffic_sources"] = None
            result["page_performance"] = None
            result["errors"]["ga4"] = _google_error_message(e)
    else:
        result["traffic_overview"] = None
        result["top_pages"] = None
        result["traffic_sources"] = None
        result["page_performance"] = None

    if client.gsc_site_url:
        gsc_start = start_date if start_date != "30daysAgo" else _iso_days_ago(30)
        # Search Console data lags ~2-3 days behind — a range whose end date
        # is more recent than that reliably comes back empty (not partial),
        # so clamp regardless of what the caller asked for.
        requested_end = end_date if end_date != "today" else _iso_days_ago(0)
        gsc_lag_cutoff = _iso_days_ago(3)
        gsc_end = min(requested_end, gsc_lag_cutoff)
        if gsc_end < gsc_start:
            gsc_start = gsc_end
        try:
            result["search_queries"] = gsc_service.get_search_analytics(creds, client.gsc_site_url, gsc_start, gsc_end, row_limit=20)
        except HttpError as e:
            result["search_queries"] = None
            result["errors"]["gsc"] = _google_error_message(e)
    else:
        result["search_queries"] = None

    return result


def _iso_days_ago(n: int) -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=n)).isoformat()
