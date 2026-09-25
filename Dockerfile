FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
    libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Playwright's browser binary download (186MB+ from Google's CDN) is the
# slowest, most failure-prone step in this whole build. Installed here,
# keyed only on this line's own text, BEFORE the app code below — so a
# routine code change never invalidates this layer and re-triggers the
# download. Confirmed real (2026-09-25): with playwright install AFTER
# COPY backend/app, every single commit re-downloaded Chrome for Testing
# fresh; a slow/flaky CDN night turned that into repeated stuck/failed
# deploys and real site downtime. Keep this version in sync with
# backend/pyproject.toml's own playwright pin.
RUN pip install --no-cache-dir "playwright>=1.47" && playwright install --with-deps chromium

COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/app ./backend/app
RUN pip install --no-cache-dir ./backend

COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/alembic ./backend/alembic
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

WORKDIR /app/backend
ENV PORT=8000
EXPOSE 8000

CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
