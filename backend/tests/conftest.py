"""Dummy env vars so importing app modules doesn't crash on Settings()
validation in CI (no real .env there). Values are placeholders only —
nothing here talks to a real database or API; the tests in this suite
exercise pure functions that don't touch settings at runtime."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "test-encryption-key")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost/callback")
