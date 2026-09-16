"""Dummy env vars so importing app modules doesn't crash on Settings()
validation in CI (no real .env there). Values are placeholders only —
nothing here talks to a real database or API; the tests in this suite
exercise pure functions that don't touch settings at runtime."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret")
# Must be a real 32-byte url-safe-base64 Fernet key, not an arbitrary
# string — app.integrations.crypto constructs a Fernet(...) from this at
# import time, so any test that imports a module importing crypto
# (e.g. app.api.routes.site_audit) crashed at collection with "Fernet key
# must be 32 url-safe base64-encoded bytes" until this was a valid key.
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "zH1Yv9k3n7pQxT2wL6cF8sD4rB0gJ5mN9uE7iA1oV0Y=")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost/callback")
