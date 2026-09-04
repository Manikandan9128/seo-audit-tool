from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.auth.exceptions import RefreshError

from app.api.routes import auth, clients, competitors, google_oauth, settings as settings_routes, site_audit
from app.db.session import SessionLocal
from app.services.app_settings_service import load_overrides_into_settings

app = FastAPI(title="SEO Audit Tool API")


@app.exception_handler(RefreshError)
def _google_refresh_error_handler(request: Request, exc: RefreshError):
    # Google's Credentials.refresh() only runs lazily, on the first actual
    # API call that needs a fresh token — not when credentials are loaded
    # from storage — so a revoked/expired refresh token (invalid_grant)
    # surfaces deep inside whichever service call happens to need it
    # (ga4_service.list_properties, gsc_service.list_sites, an analytics
    # pull, etc.), not at the one shared _load_credentials() call site.
    # Confirmed live: every one of those routes crashed with a bare 500
    # for this. One handler here covers all of them instead of wrapping
    # each call site individually.
    return JSONResponse(
        status_code=400,
        content={"detail": f"Google account connection is invalid or expired — reconnect it: {exc}"},
    )


@app.on_event("startup")
def _load_settings_overrides():
    db = SessionLocal()
    try:
        load_overrides_into_settings(db)
    finally:
        db.close()

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):5173|https://.*\.ngrok-free\.(dev|app)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(clients.router, prefix="/api")
app.include_router(google_oauth.router, prefix="/api")
app.include_router(site_audit.router, prefix="/api")
app.include_router(competitors.router, prefix="/api")
app.include_router(settings_routes.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
