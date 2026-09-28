import uuid
from dataclasses import dataclass
from typing import Generator

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import SessionLocal
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    subject = decode_access_token(token)
    if subject is None:
        raise credentials_exception
    user = db.get(User, uuid.UUID(subject))
    if user is None:
        raise credentials_exception
    return user


@dataclass
class ReportAISelection:
    provider: str | None
    claude_model: str | None

    def require(self) -> str:
        """For endpoints whose whole job is AI generation (Generate,
        Preview, Download): refuse to start without a selection instead of
        choosing a provider on the user's behalf."""
        if not self.provider:
            raise HTTPException(
                status_code=400,
                detail="Select a Report AI Provider first — no provider is chosen automatically.",
            )
        return self.provider


def report_ai_selection(
    x_ai_provider: str | None = Header(default=None),
    x_claude_model: str | None = Header(default=None),
) -> ReportAISelection:
    """The Report AI Provider dropdown's value, sent by the frontend on every
    request (X-AI-Provider / X-Claude-Model headers). Route handlers pin it
    with text_ai_client.selected_provider_scope so every AI call in the
    request uses exactly that provider."""
    return validate_ai_selection(x_ai_provider, x_claude_model)


def validate_ai_selection(provider: str | None, claude_model: str | None) -> ReportAISelection:
    from app.integrations import text_ai_client

    provider = provider or None
    claude_model = claude_model or None
    if provider is not None and provider not in text_ai_client.PROVIDER_LABELS:
        raise HTTPException(
            status_code=400, detail=f"AI provider must be one of {sorted(text_ai_client.PROVIDER_LABELS)}",
        )
    if claude_model is not None and claude_model not in text_ai_client.CLAUDE_MODEL_CHOICES:
        raise HTTPException(
            status_code=400, detail=f"claude_model must be one of {sorted(text_ai_client.CLAUDE_MODEL_CHOICES)}",
        )
    return ReportAISelection(provider, claude_model)
