"""Runtime-editable settings (Gemini + Groq + Claude API keys) backed by the
app_settings table, so the user can change them from the UI without editing
.env or restarting the server. Any one key alone is enough — see
app.integrations.text_ai_client for the fallback logic that picks whichever
is configured."""

import httpx
from anthropic import Anthropic
from google import genai
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.gemini_errors import friendly_gemini_error
from app.integrations.text_ai_client import GROQ_API_URL, GROQ_MODEL
from app.models.app_setting import AppSetting

GEMINI_API_KEY = "gemini_api_key"
GEMINI_MODEL = "gemini-3.6-flash"
GROQ_API_KEY = "groq_api_key"
CLAUDE_API_KEY = "claude_api_key"
CLAUDE_MODEL = "claude-sonnet-5"


def load_overrides_into_settings(db: Session) -> None:
    """Called on app startup — applies any DB-stored overrides on top of the
    .env values so previously-saved keys survive a restart."""
    row = db.get(AppSetting, GEMINI_API_KEY)
    if row and row.value:
        settings.gemini_api_key = row.value
    row = db.get(AppSetting, GROQ_API_KEY)
    if row and row.value:
        settings.groq_api_key = row.value
    row = db.get(AppSetting, CLAUDE_API_KEY)
    if row and row.value:
        settings.claude_api_key = row.value


def _set_key(db: Session, setting_key: str, value: str) -> str:
    value = value.strip()
    row = db.get(AppSetting, setting_key)
    if row:
        row.value = value
    else:
        row = AppSetting(key=setting_key, value=value)
        db.add(row)
    db.commit()
    return value


def _mask(key: str | None) -> str | None:
    if not key:
        return None
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * (len(key) - 8)}{key[-4:]}"


def set_gemini_api_key(db: Session, value: str) -> None:
    settings.gemini_api_key = _set_key(db, GEMINI_API_KEY, value)


def test_gemini_key() -> dict:
    """Makes one minimal real call to confirm the currently-configured key
    actually works — not just that it was saved. Returns {ok, message}."""
    if not settings.gemini_api_key:
        return {"ok": False, "message": "No Gemini API key configured"}
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents="Reply with just: OK")
        text = (response.text or "").strip()
        return {"ok": True, "message": f"Key works — model replied: {text[:80] or '(empty)'}"}
    except Exception as e:
        return {"ok": False, "message": friendly_gemini_error(e)}


def masked_gemini_api_key() -> str | None:
    return _mask(settings.gemini_api_key)


def set_groq_api_key(db: Session, value: str) -> None:
    settings.groq_api_key = _set_key(db, GROQ_API_KEY, value)


def test_groq_key() -> dict:
    """Makes one minimal real call to confirm the currently-configured key
    actually works — not just that it was saved. Returns {ok, message}."""
    if not settings.groq_api_key:
        return {"ok": False, "message": "No Groq API key configured"}
    try:
        response = httpx.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": "Reply with just: OK"}],
                "max_tokens": 20,
            },
            timeout=30,
        )
        response.raise_for_status()
        text = (response.json()["choices"][0]["message"]["content"] or "").strip()
        return {"ok": True, "message": f"Key works — model replied: {text[:80] or '(empty)'}"}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return {"ok": False, "message": "Groq rejected this API key — check it was copied correctly and hasn't been revoked."}
        if e.response.status_code == 429:
            return {"ok": False, "message": "Groq rate limit hit — wait a bit and try again."}
        return {"ok": False, "message": f"Groq request failed: {str(e)[:300]}"}
    except Exception as e:
        return {"ok": False, "message": f"Groq request failed: {str(e)[:300]}"}


def masked_groq_api_key() -> str | None:
    return _mask(settings.groq_api_key)


def set_claude_api_key(db: Session, value: str) -> None:
    settings.claude_api_key = _set_key(db, CLAUDE_API_KEY, value)


def test_claude_key() -> dict:
    """Makes one minimal real call to confirm the currently-configured key
    actually works — not just that it was saved. Returns {ok, message}."""
    if not settings.claude_api_key:
        return {"ok": False, "message": "No Claude API key configured"}
    try:
        client = Anthropic(api_key=settings.claude_api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=20, messages=[{"role": "user", "content": "Reply with just: OK"}]
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return {"ok": True, "message": f"Key works — model replied: {text[:80] or '(empty)'}"}
    except Exception as e:
        text = str(e)
        if "401" in text or "authentication" in text.lower():
            return {"ok": False, "message": "Claude rejected this API key — check it was copied correctly and hasn't been revoked."}
        if "429" in text or "rate" in text.lower():
            return {"ok": False, "message": "Claude rate limit hit — wait a bit and try again."}
        return {"ok": False, "message": f"Claude request failed: {text[:300]}"}


def masked_claude_api_key() -> str | None:
    return _mask(settings.claude_api_key)
