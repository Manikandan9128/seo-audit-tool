from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.integrations import semrush_mcp
from app.models.user import User

router = APIRouter(prefix="/integrations/semrush", tags=["integrations"])


class ToolCallIn(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


def _origin(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{scheme}://{host}"


def _redirect_uri(request: Request) -> str:
    return f"{_origin(request)}/api/integrations/semrush/callback"


# Never 401 here: the frontend's axios interceptor treats any 401 as "app
# login expired" and logs the user out, which a Semrush-side auth problem
# isn't.
_STATUS_BY_CODE = {
    "not_connected": 400,
    "invalid_state": 400,
    "oauth_denied": 400,
    "expired_session": 400,
    "auth_failed": 400,
    "missing_permissions": 403,
    "account_unavailable": 402,
    "rate_limited": 429,
    "connection_failed": 502,
}


def _http_error(e: semrush_mcp.SemrushError) -> HTTPException:
    return HTTPException(status_code=_STATUS_BY_CODE.get(e.code, 502), detail=e.message)


@router.get("/connect")
def semrush_connect(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Returns the Semrush authorization URL for the frontend to navigate
    to (same shape as the Google Sheets connect route — the browser can't
    carry the app's Bearer token on a plain navigation, so this is fetched
    with it and the redirect happens client-side)."""
    try:
        auth_url = semrush_mcp.connect_semrush(db, _redirect_uri(request), str(current_user.id))
    except semrush_mcp.SemrushError as e:
        raise _http_error(e)
    return {"auth_url": auth_url}


@router.get("/callback")
def semrush_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """Semrush redirects the browser here. Unauthenticated by necessity (a
    top-level redirect carries no Bearer token) — the single-use,
    server-stored state is what ties it to a Connect started by a signed-in
    user. Always redirects back to Settings with a status flag, never
    tokens."""
    try:
        semrush_mcp.complete_semrush_oauth(db, state, code, error, error_description)
    except semrush_mcp.SemrushError as e:
        return RedirectResponse(url=f"{_origin(request)}/settings?semrush_error={e.code}")
    return RedirectResponse(url=f"{_origin(request)}/settings?semrush_connected=1")


@router.get("/status")
def semrush_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return semrush_mcp.semrush_status(db)


@router.post("/test")
def semrush_test(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    test = semrush_mcp.test_semrush_connection(db)
    return {"test_ok": test["ok"], "test_message": test["message"], "code": test["code"]}


@router.post("/disconnect")
def semrush_disconnect(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    semrush_mcp.disconnect_semrush(db)
    return {"ok": True}


@router.get("/tools")
def semrush_tools(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return {"tools": semrush_mcp.get_semrush_tools(db)}
    except semrush_mcp.SemrushError as e:
        raise _http_error(e)


@router.post("/tools/call")
def semrush_tool_call(payload: ToolCallIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Calls one Semrush MCP tool server-side. Report tools spend the
    connected Semrush account's API units."""
    try:
        return semrush_mcp.call_semrush_tool(db, payload.name, payload.arguments)
    except semrush_mcp.SemrushError as e:
        raise _http_error(e)
