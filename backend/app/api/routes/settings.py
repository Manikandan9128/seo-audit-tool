from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.app_settings_service import (
    disconnect_sheets_oauth,
    get_sheets_oauth_client_id,
    get_sheets_oauth_email,
    masked_claude_api_key,
    masked_gemini_api_key,
    masked_groq_api_key,
    set_claude_api_key,
    set_gemini_api_key,
    set_groq_api_key,
    set_sheets_oauth_client,
    set_sheets_oauth_tokens,
    test_claude_key,
    test_gemini_key,
    test_groq_key,
    test_sheets_connection,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _sheets_oauth_redirect_uri(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}/api/settings/google-sheets-oauth/callback"


class GeminiKeyIn(BaseModel):
    gemini_api_key: str


class GroqKeyIn(BaseModel):
    groq_api_key: str


class ClaudeKeyIn(BaseModel):
    claude_api_key: str


class GoogleSheetsOAuthClientIn(BaseModel):
    google_sheets_oauth_client_id: str
    google_sheets_oauth_client_secret: str


@router.get("")
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    gemini_masked = masked_gemini_api_key()
    groq_masked = masked_groq_api_key()
    claude_masked = masked_claude_api_key()
    return {
        "gemini_api_key_set": gemini_masked is not None,
        "gemini_api_key_masked": gemini_masked,
        "groq_api_key_set": groq_masked is not None,
        "groq_api_key_masked": groq_masked,
        "claude_api_key_set": claude_masked is not None,
        "claude_api_key_masked": claude_masked,
        "google_sheets_oauth_email": get_sheets_oauth_email(db),
        "google_sheets_oauth_client_id": get_sheets_oauth_client_id(),
    }


@router.put("/gemini-api-key")
def update_gemini_api_key(
    payload: GeminiKeyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the key, then immediately makes one real Gemini call to confirm
    it actually works — so a bad/rate-limited key is caught right here
    instead of failing later on some client's report."""
    set_gemini_api_key(db, payload.gemini_api_key)
    test = test_gemini_key()
    return {
        "gemini_api_key_set": True,
        "gemini_api_key_masked": masked_gemini_api_key(),
        "test_ok": test["ok"],
        "test_message": test["message"],
    }


@router.post("/gemini-api-key/test")
def test_gemini_api_key(current_user: User = Depends(get_current_user)):
    """Re-runs the connectivity test on demand, without changing the key."""
    test = test_gemini_key()
    return {"test_ok": test["ok"], "test_message": test["message"]}


@router.put("/groq-api-key")
def update_groq_api_key(
    payload: GroqKeyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the key, then immediately makes one real Groq call to confirm
    it actually works — so a bad key is caught right here instead of
    failing later on some client's report. Groq's free tier has much
    higher per-minute limits than Gemini's, so it's tried first — covers
    the report-generation burst of ~8-10 AI calls that can trip Gemini's
    per-minute cap even on the same day the daily quota reset."""
    set_groq_api_key(db, payload.groq_api_key)
    test = test_groq_key()
    return {
        "groq_api_key_set": True,
        "groq_api_key_masked": masked_groq_api_key(),
        "test_ok": test["ok"],
        "test_message": test["message"],
    }


@router.post("/groq-api-key/test")
def test_groq_api_key(current_user: User = Depends(get_current_user)):
    """Re-runs the connectivity test on demand, without changing the key."""
    test = test_groq_key()
    return {"test_ok": test["ok"], "test_message": test["message"]}


@router.put("/claude-api-key")
def update_claude_api_key(
    payload: ClaudeKeyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the key, then immediately makes one real Claude call to confirm
    it actually works. Company Overview extraction and the AI Summary use
    Gemini first and fall back to Claude — either key alone is enough."""
    set_claude_api_key(db, payload.claude_api_key)
    test = test_claude_key()
    return {
        "claude_api_key_set": True,
        "claude_api_key_masked": masked_claude_api_key(),
        "test_ok": test["ok"],
        "test_message": test["message"],
    }


@router.post("/claude-api-key/test")
def test_claude_api_key(current_user: User = Depends(get_current_user)):
    """Re-runs the connectivity test on demand, without changing the key."""
    test = test_claude_key()
    return {"test_ok": test["ok"], "test_message": test["message"]}


@router.get("/google-sheets-oauth/connect")
def google_sheets_oauth_connect(request: Request, current_user: User = Depends(get_current_user)):
    """Connects the app to create competitor keyword Sheets directly under
    the user's own Google account (see google_sheets_service.py)."""
    from app.integrations import google_oauth

    redirect_uri = _sheets_oauth_redirect_uri(request)
    try:
        auth_url = google_oauth.build_sheets_auth_url(redirect_uri)
    except google_oauth.NoSheetsOAuthClientConfigured as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"auth_url": auth_url}


@router.put("/google-sheets-oauth-client")
def update_google_sheets_oauth_client(
    payload: GoogleSheetsOAuthClientIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A dedicated Web-application OAuth client for the Sheets connection —
    see config.py's google_sheets_oauth_client_id/secret docstring for why
    this is separate from the per-client GA4/GSC OAuth client."""
    set_sheets_oauth_client(db, payload.google_sheets_oauth_client_id, payload.google_sheets_oauth_client_secret)
    return {"google_sheets_oauth_client_id": get_sheets_oauth_client_id()}


@router.get("/google-sheets-oauth/callback")
def google_sheets_oauth_callback(code: str, state: str, request: Request, db: Session = Depends(get_db)):
    from app.integrations import google_oauth
    from googleapiclient.discovery import build as gbuild

    try:
        google_oauth.parse_sheets_state(state)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    redirect_uri = _sheets_oauth_redirect_uri(request)
    creds = google_oauth.exchange_sheets_code(code, redirect_uri)

    oauth2 = gbuild("oauth2", "v2", credentials=creds)
    userinfo = oauth2.userinfo().get().execute()
    email = userinfo.get("email", "unknown")

    set_sheets_oauth_tokens(db, creds.token, creds.refresh_token or "", email)

    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return RedirectResponse(url=f"{scheme}://{host}/settings?sheets_oauth_connected=1")


@router.post("/google-sheets-oauth/disconnect")
def google_sheets_oauth_disconnect(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    disconnect_sheets_oauth(db)
    return {"ok": True}


@router.post("/google-sheets-oauth/test")
def google_sheets_oauth_test(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    test = test_sheets_connection(db)
    return {"test_ok": test["ok"], "test_message": test["message"]}
