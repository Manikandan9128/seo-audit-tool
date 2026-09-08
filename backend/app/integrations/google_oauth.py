import os

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from itsdangerous import URLSafeTimedSerializer
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# Separate scope set for the app-owned "who creates competitor keyword
# Sheets" connection (see app/services/google_sheets_service.py) — a
# distinct OAuth identity from the per-client GA4/GSC connection above, not
# tied to any one client. Using the human's own OAuth login instead of a
# bare service account sidesteps a real, confirmed-live limitation: a plain
# personal Gmail account has no Workspace features (no Domain-wide
# Delegation, no Shared Drives), so a service account creating files even
# inside a folder that Gmail account shared with it can still fail with
# "storage quota exceeded" — a known Google Drive API quirk unrelated to
# how much space is actually free. Creating the Sheet directly under the
# human's own OAuth identity avoids the whole class of problem.
SHEETS_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

_state_serializer = URLSafeTimedSerializer(settings.jwt_secret, salt="google-oauth-state")
_sheets_state_serializer = URLSafeTimedSerializer(settings.jwt_secret, salt="google-sheets-oauth-state")


def _client_config(redirect_uri: str, client_id: str | None = None, client_secret: str | None = None) -> dict:
    return {
        "web": {
            "client_id": client_id or settings.google_client_id,
            "client_secret": client_secret or settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def build_auth_url(client_id: str, redirect_uri: str) -> str:
    flow = Flow.from_client_config(
        _client_config(redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri, autogenerate_code_verifier=False
    )
    state = _state_serializer.dumps({"client_id": client_id})
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    return auth_url


def parse_state(state: str, max_age_seconds: int = 600) -> str:
    data = _state_serializer.loads(state, max_age=max_age_seconds)
    return data["client_id"]


def exchange_code(code: str, redirect_uri: str) -> Credentials:
    flow = Flow.from_client_config(
        _client_config(redirect_uri), scopes=SCOPES, redirect_uri=redirect_uri, autogenerate_code_verifier=False
    )
    flow.fetch_token(code=code)
    return flow.credentials


def credentials_from_stored(access_token: str, refresh_token: str) -> Credentials:
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )
    if not creds.valid:
        creds.refresh(GoogleRequest())
    return creds


class NoSheetsOAuthClientConfigured(Exception):
    pass


def _sheets_client_id_secret() -> tuple[str, str]:
    if not settings.google_sheets_oauth_client_id or not settings.google_sheets_oauth_client_secret:
        raise NoSheetsOAuthClientConfigured(
            "No Sheets OAuth client configured — add its Client ID and Secret in Settings"
        )
    return settings.google_sheets_oauth_client_id, settings.google_sheets_oauth_client_secret


def build_sheets_auth_url(redirect_uri: str) -> str:
    client_id, client_secret = _sheets_client_id_secret()
    flow = Flow.from_client_config(
        _client_config(redirect_uri, client_id, client_secret),
        scopes=SHEETS_OAUTH_SCOPES, redirect_uri=redirect_uri, autogenerate_code_verifier=False,
    )
    state = _sheets_state_serializer.dumps({"purpose": "sheets"})
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)
    return auth_url


def parse_sheets_state(state: str, max_age_seconds: int = 600) -> None:
    _sheets_state_serializer.loads(state, max_age=max_age_seconds)


def exchange_sheets_code(code: str, redirect_uri: str) -> Credentials:
    client_id, client_secret = _sheets_client_id_secret()
    flow = Flow.from_client_config(
        _client_config(redirect_uri, client_id, client_secret),
        scopes=SHEETS_OAUTH_SCOPES, redirect_uri=redirect_uri, autogenerate_code_verifier=False,
    )
    flow.fetch_token(code=code)
    return flow.credentials


def sheets_credentials_from_stored(access_token: str, refresh_token: str) -> Credentials:
    client_id, client_secret = _sheets_client_id_secret()
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SHEETS_OAUTH_SCOPES,
    )
    if not creds.valid:
        creds.refresh(GoogleRequest())
    return creds
