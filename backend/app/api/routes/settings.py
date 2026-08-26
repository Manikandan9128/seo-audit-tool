from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.services.app_settings_service import (
    masked_claude_api_key,
    masked_gemini_api_key,
    set_claude_api_key,
    set_gemini_api_key,
    test_claude_key,
    test_gemini_key,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class GeminiKeyIn(BaseModel):
    gemini_api_key: str


class ClaudeKeyIn(BaseModel):
    claude_api_key: str


@router.get("")
def get_settings(current_user: User = Depends(get_current_user)):
    gemini_masked = masked_gemini_api_key()
    claude_masked = masked_claude_api_key()
    return {
        "gemini_api_key_set": gemini_masked is not None,
        "gemini_api_key_masked": gemini_masked,
        "claude_api_key_set": claude_masked is not None,
        "claude_api_key_masked": claude_masked,
    }


@router.put("/gemini-api-key")
def update_gemini_api_key(
    payload: GeminiKeyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the key, then immediately makes one real Gemini call to confirm
    it actually works — so a bad/rate-limited key is caught right here
    instead of failing later on some client's report."""
    set_gemini_api_key(db, payload.gemini_api_key)
    test = test_gemini_key()
    return {
        "gemini_api_key_set": True,
        "gemini_api_key_masked": masked_gemini_api_key(),
        "test_ok": test["ok"],
        "test_message": test["message"],
    }


@router.post("/gemini-api-key/test")
def test_gemini_api_key(current_user: User = Depends(get_current_user)):
    """Re-runs the connectivity test on demand, without changing the key."""
    test = test_gemini_key()
    return {"test_ok": test["ok"], "test_message": test["message"]}


@router.put("/claude-api-key")
def update_claude_api_key(
    payload: ClaudeKeyIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves the key, then immediately makes one real Claude call to confirm
    it actually works. Company Overview extraction and the AI Summary use
    Gemini first and fall back to Claude — either key alone is enough."""
    set_claude_api_key(db, payload.claude_api_key)
    test = test_claude_key()
    return {
        "claude_api_key_set": True,
        "claude_api_key_masked": masked_claude_api_key(),
        "test_ok": test["ok"],
        "test_message": test["message"],
    }


@router.post("/claude-api-key/test")
def test_claude_api_key(current_user: User = Depends(get_current_user)):
    """Re-runs the connectivity test on demand, without changing the key."""
    test = test_claude_key()
    return {"test_ok": test["ok"], "test_message": test["message"]}
