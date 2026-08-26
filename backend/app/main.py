from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, clients, competitors, google_oauth, settings as settings_routes, site_audit
from app.db.session import SessionLocal
from app.services.app_settings_service import load_overrides_into_settings

app = FastAPI(title="SEO Audit Tool API")


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
