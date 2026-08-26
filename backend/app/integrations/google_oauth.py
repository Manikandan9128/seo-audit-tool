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

_state_serializer = URLSafeTimedSerializer(settings.jwt_secret, salt="google-oauth-state")


def _client_config(redirect_uri: str) -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
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
