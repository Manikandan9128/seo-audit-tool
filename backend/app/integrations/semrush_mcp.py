"""Server-side Semrush MCP integration: OAuth 2.1 (PKCE, public client,
dynamic client registration) plus a Streamable HTTP MCP client for
https://mcp.semrush.com/v2/mcp.

No Semrush API key is involved — the user signs in to Semrush once from
Settings and the resulting OAuth tokens are stored Fernet-encrypted in
app_settings (same pattern as the Google Sheets OAuth tokens), app-wide
rather than per-user, like every other integration in Settings. Tokens
never leave the server: routes only ever return status/booleans and tool
results.

Every OAuth endpoint is discovered at runtime, not hardcoded, following
the MCP authorization spec — verified live 2026-09-23:
  1. The MCP endpoint answers 401 with
     `WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/v2/mcp"`.
  2. That protected-resource metadata names the authorization server
     (https://oauth.semrush.com) and the `mcp.access` scope.
  3. The authorization server's /.well-known/oauth-authorization-server
     metadata gives the authorize/token/registration endpoints, PKCE
     (S256), `token_endpoint_auth_methods_supported: ["none"]` (public
     client, no client secret) and a dynamic client registration endpoint.
So there's nothing to register by hand in a Semrush dashboard: the first
Connect registers this app's callback URL via RFC 7591 and stores the
returned client_id, re-registering if the callback URL changes (e.g. local
dev vs the production host)."""

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.orm import Session

from app.integrations.crypto import decrypt, encrypt
from app.models.app_setting import AppSetting

SEMRUSH_MCP_URL = "https://mcp.semrush.com/v2/mcp"
PROTECTED_RESOURCE_METADATA_URL = "https://mcp.semrush.com/.well-known/oauth-protected-resource/v2/mcp"
# offline_access is listed in the authorization server's scopes_supported
# and is what gets a refresh token issued alongside the access token.
OAUTH_SCOPES = "mcp.access offline_access"
CLIENT_NAME = "SEO Audit Tool"

PENDING_STATE_TTL_SECONDS = 600
# Refresh this long before the access token's stated expiry, so a token
# doesn't expire mid-request.
TOKEN_REFRESH_MARGIN_SECONDS = 60
MCP_TIMEOUT_SECONDS = 60

# app_settings keys. Tokens and the pending PKCE verifier are encrypted;
# the client registration is public-client metadata (no secret) but is
# stored encrypted too for uniformity.
_CLIENT_KEY = "semrush_mcp_client"
_TOKENS_KEY = "semrush_mcp_tokens"
_PENDING_KEY = "semrush_mcp_oauth_pending"

_refresh_lock = threading.Lock()
_metadata_cache: dict | None = None


class SemrushError(Exception):
    """Base for every user-facing Semrush failure. `code` is a stable slug
    the frontend can key off; the message is safe to show as-is (never
    contains tokens)."""

    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SemrushNotConnected(SemrushError):
    code = "not_connected"


class SemrushOAuthDenied(SemrushError):
    code = "oauth_denied"


class SemrushInvalidState(SemrushError):
    code = "invalid_state"


class SemrushSessionExpired(SemrushError):
    code = "expired_session"


class SemrushAuthFailed(SemrushError):
    code = "auth_failed"


class SemrushPermissionDenied(SemrushError):
    code = "missing_permissions"


class SemrushAccountUnavailable(SemrushError):
    code = "account_unavailable"


class SemrushRateLimited(SemrushError):
    code = "rate_limited"


class SemrushConnectionFailed(SemrushError):
    code = "connection_failed"


# ---------------------------------------------------------------- storage


def _load(db: Session, key: str) -> dict | None:
    row = db.get(AppSetting, key)
    if not row or not row.value:
        return None
    try:
        return json.loads(decrypt(row.value))
    except Exception:
        return None


def _save(db: Session, key: str, data: dict) -> None:
    value = encrypt(json.dumps(data))
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def _delete(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        db.delete(row)
        db.commit()


# -------------------------------------------------------------- discovery


def _auth_server_metadata() -> dict:
    """Protected-resource metadata → authorization server → its RFC 8414
    metadata. Cached for the process lifetime; these documents are static."""
    global _metadata_cache
    if _metadata_cache is not None:
        return _metadata_cache
    try:
        resource = httpx.get(PROTECTED_RESOURCE_METADATA_URL, timeout=15)
        resource.raise_for_status()
        issuer = resource.json()["authorization_servers"][0].rstrip("/")
        metadata = httpx.get(f"{issuer}/.well-known/oauth-authorization-server", timeout=15)
        metadata.raise_for_status()
        data = metadata.json()
    except Exception as e:
        raise SemrushConnectionFailed(f"Couldn't read Semrush's OAuth metadata: {str(e)[:200]}") from e
    for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not data.get(field):
            raise SemrushConnectionFailed(f"Semrush's OAuth metadata is missing {field}")
    _metadata_cache = data
    return data


def _registered_client_id(db: Session, redirect_uri: str) -> str:
    """Returns this app's Semrush OAuth client_id for `redirect_uri`,
    registering one via dynamic client registration the first time (or
    when the callback URL changed since the last registration)."""
    existing = _load(db, _CLIENT_KEY)
    if existing and existing.get("redirect_uri") == redirect_uri and existing.get("client_id"):
        return existing["client_id"]
    metadata = _auth_server_metadata()
    try:
        response = httpx.post(
            metadata["registration_endpoint"],
            json={
                "client_name": CLIENT_NAME,
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": OAUTH_SCOPES,
            },
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise SemrushConnectionFailed(f"Couldn't reach Semrush to register this app: {str(e)[:200]}") from e
    if response.status_code >= 400:
        raise SemrushConnectionFailed(
            f"Semrush rejected this app's client registration ({response.status_code}): {response.text[:200]}"
        )
    client_id = response.json()["client_id"]
    _save(db, _CLIENT_KEY, {"client_id": client_id, "redirect_uri": redirect_uri})
    return client_id


# ------------------------------------------------------------------ OAuth


def connect_semrush(db: Session, redirect_uri: str, user_id: str) -> str:
    """Starts the OAuth flow: registers the client if needed, stores a
    single-use state + PKCE verifier server-side, and returns the Semrush
    authorization URL to send the browser to.

    Only one pending authorization is kept (the integration is app-wide):
    starting a new Connect invalidates any earlier unfinished one."""
    metadata = _auth_server_metadata()
    client_id = _registered_client_id(db, redirect_uri)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _save(db, _PENDING_KEY, {
        "state": state,
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "user_id": user_id,
        "expires_at": time.time() + PENDING_STATE_TTL_SECONDS,
    })
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # RFC 8707 resource indicator, required by the MCP authorization
        # spec so the token is audience-bound to this MCP server.
        "resource": SEMRUSH_MCP_URL,
    })
    return f"{metadata['authorization_endpoint']}?{query}"


def complete_semrush_oauth(db: Session, state: str | None, code: str | None, error: str | None, error_description: str | None) -> None:
    """Handles the OAuth callback. Validates state against the stored
    pending authorization (constant-time compare, expiry, single use)
    BEFORE looking at anything else, then exchanges the code."""
    pending = _load(db, _PENDING_KEY)
    if not state or not pending or not secrets.compare_digest(state, pending.get("state", "")):
        raise SemrushInvalidState("Invalid OAuth state — start the Semrush connection again from Settings.")
    # Single use: consumed whether the rest succeeds or not.
    _delete(db, _PENDING_KEY)
    if time.time() > pending["expires_at"]:
        raise SemrushSessionExpired("The Semrush sign-in took too long and expired — click Connect Semrush again.")
    if error:
        if error == "access_denied":
            raise SemrushOAuthDenied("Semrush access was denied — the connection wasn't authorized.")
        raise SemrushAuthFailed(f"Semrush sign-in failed: {error_description or error}")
    if not code:
        raise SemrushAuthFailed("Semrush didn't return an authorization code.")

    metadata = _auth_server_metadata()
    try:
        response = httpx.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending["redirect_uri"],
                "client_id": pending["client_id"],
                "code_verifier": pending["verifier"],
                "resource": SEMRUSH_MCP_URL,
            },
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise SemrushConnectionFailed(f"Couldn't reach Semrush to finish signing in: {str(e)[:200]}") from e
    if response.status_code >= 400:
        raise SemrushAuthFailed(f"Semrush rejected the sign-in ({response.status_code}): {_oauth_error_text(response)}")
    _store_token_response(db, response.json(), pending["client_id"], previous_refresh_token=None, connected_at=time.time())


def _oauth_error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        # Standard OAuth errors use error/error_description; Semrush's token
        # endpoint also answers with {"code": ..., "message": ...}.
        return str(body.get("error_description") or body.get("error") or body.get("message") or body)[:200]
    except Exception:
        return response.text[:200]


def _store_token_response(db: Session, body: dict, client_id: str, previous_refresh_token: str | None, connected_at: float) -> dict:
    tokens = {
        "access_token": body["access_token"],
        # Refresh responses may omit refresh_token when it isn't rotated.
        "refresh_token": body.get("refresh_token") or previous_refresh_token,
        "expires_at": time.time() + int(body["expires_in"]) if body.get("expires_in") else None,
        "scope": body.get("scope"),
        "client_id": client_id,
        "connected_at": connected_at,
    }
    _save(db, _TOKENS_KEY, tokens)
    return tokens


def _refresh(db: Session, tokens: dict) -> dict:
    if not tokens.get("refresh_token"):
        raise SemrushSessionExpired("Semrush session expired and no refresh token was issued — reconnect Semrush in Settings.")
    metadata = _auth_server_metadata()
    try:
        response = httpx.post(
            metadata["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": tokens["client_id"],
                "resource": SEMRUSH_MCP_URL,
            },
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise SemrushConnectionFailed(f"Couldn't reach Semrush to refresh the session: {str(e)[:200]}") from e
    if response.status_code >= 400:
        raise SemrushSessionExpired(
            f"Semrush session expired or was revoked — reconnect Semrush in Settings. ({_oauth_error_text(response)})"
        )
    return _store_token_response(db, response.json(), tokens["client_id"], tokens["refresh_token"], tokens.get("connected_at") or time.time())


def _access_token(db: Session, force_refresh: bool = False) -> str:
    with _refresh_lock:
        tokens = _load(db, _TOKENS_KEY)
        if not tokens:
            raise SemrushNotConnected("Semrush isn't connected — click Connect Semrush in Settings.")
        expires_at = tokens.get("expires_at")
        if force_refresh or (expires_at and time.time() > expires_at - TOKEN_REFRESH_MARGIN_SECONDS):
            tokens = _refresh(db, tokens)
        return tokens["access_token"]


def semrush_status(db: Session) -> dict:
    """Connection status for the Settings page — no token material."""
    tokens = _load(db, _TOKENS_KEY)
    if not tokens:
        return {"connected": False}
    return {"connected": True, "connected_at": tokens.get("connected_at"), "scope": tokens.get("scope")}


def disconnect_semrush(db: Session) -> None:
    """Revokes the refresh token at Semrush (best effort) and deletes the
    stored tokens. The client registration is kept for the next Connect."""
    tokens = _load(db, _TOKENS_KEY)
    if tokens and tokens.get("refresh_token"):
        try:
            revocation = _auth_server_metadata().get("revocation_endpoint")
            if revocation:
                httpx.post(
                    revocation,
                    data={"token": tokens["refresh_token"], "token_type_hint": "refresh_token", "client_id": tokens["client_id"]},
                    timeout=10,
                )
        except Exception:
            pass
    _delete(db, _TOKENS_KEY)
    _delete(db, _PENDING_KEY)


# -------------------------------------------------------------------- MCP


@dataclass
class _HttpStatus:
    code: int | None = None
    retry_after: str | None = None


def _raise_for_http_status(status: _HttpStatus) -> None:
    if status.code == 401:
        raise SemrushAuthFailed("Semrush rejected the stored credentials — reconnect Semrush in Settings.")
    if status.code == 403:
        raise SemrushPermissionDenied(
            "Semrush denied access — this Semrush account may lack the permissions or subscription the MCP server needs."
        )
    if status.code == 429:
        wait = f" Retry after {status.retry_after}s." if status.retry_after else ""
        raise SemrushRateLimited(f"Semrush rate limit hit.{wait}")
    if status.code == 402:
        raise SemrushAccountUnavailable("Semrush says this account has no available API units or subscription for MCP.")


async def _with_client(access_token: str, fn):
    """Opens one Streamable HTTP MCP session with the bearer token, runs
    `fn(client)`, and closes it. The SDK wraps HTTP failures in a generic
    MCPError, so an httpx response hook records the last non-2xx status to
    map 401/403/429 to specific errors."""
    status = _HttpStatus()

    async def on_response(response: httpx2.Response) -> None:
        if response.status_code >= 400:
            status.code = response.status_code
            status.retry_after = response.headers.get("retry-after")

    try:
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=MCP_TIMEOUT_SECONDS,
            event_hooks={"response": [on_response]},
        ) as http_client:
            async with Client(
                streamable_http_client(SEMRUSH_MCP_URL, http_client=http_client),
                read_timeout_seconds=MCP_TIMEOUT_SECONDS,
            ) as client:
                return await fn(client)
    except SemrushError:
        raise
    except BaseException as e:  # ExceptionGroup from the SDK's task groups
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        _raise_for_http_status(status)
        raise SemrushConnectionFailed(f"Semrush MCP connection failed: {_first_error(e)[:300]}") from e


def _first_error(e: BaseException) -> str:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return str(e) or type(e).__name__


def _run(db: Session, fn):
    """Runs an MCP operation with a valid token; on a 401 refreshes once
    and retries (the stored expiry can be wrong if the token was revoked
    or the server shortened its lifetime)."""
    try:
        return asyncio.run(_with_client(_access_token(db), fn))
    except SemrushAuthFailed:
        return asyncio.run(_with_client(_access_token(db, force_refresh=True), fn))


def get_semrush_tools(db: Session) -> list[dict]:
    """Lists the tools the Semrush MCP server exposes (name, description,
    input schema). Doesn't spend Semrush API units."""

    async def fn(client: Client):
        tools, cursor = [], None
        while True:
            result = await client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = result.next_cursor
            if not cursor:
                return tools

    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in _run(db, fn)
    ]


# Error codes Semrush documents for its MCP tool results (a JSON error
# object with a `code` field in place of report data).
_TOOL_ERROR_CODES = {
    "no_subscription": SemrushAccountUnavailable,
    "no_api_units": SemrushAccountUnavailable,
    "rate_limit": SemrushRateLimited,
}


def call_semrush_tool(db: Session, name: str, arguments: dict | None = None) -> dict:
    """Calls one Semrush MCP tool and returns {"content": [...text/json
    parts...], "structured": ...}. Semrush-side errors (no subscription,
    out of API units, rate limit) raise the matching SemrushError."""

    async def fn(client: Client):
        return await client.call_tool(name, arguments or {})

    result = _run(db, fn)
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    if result.is_error:
        message = " ".join(texts)[:500] or "Semrush tool call failed"
        try:
            error = json.loads(texts[0]) if texts else {}
        except (ValueError, TypeError):
            error = {}
        exc_type = _TOOL_ERROR_CODES.get(error.get("code") if isinstance(error, dict) else None, SemrushError)
        raise exc_type((error.get("message") if isinstance(error, dict) else None) or message)
    return {"content": texts, "structured": result.structured_content}


def test_semrush_connection(db: Session) -> dict:
    """Opens a real authenticated MCP session and lists tools. Returns
    {ok, message}."""
    try:
        tools = get_semrush_tools(db)
    except SemrushError as e:
        return {"ok": False, "message": e.message, "code": e.code}
    return {"ok": True, "message": f"Connected to Semrush MCP — {len(tools)} tools available.", "code": "ok"}
