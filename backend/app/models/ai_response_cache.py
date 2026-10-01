from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AiResponseCache(Base):
    """A paid-provider AI answer that a report step accepted, keyed by a hash
    of everything that determines it (provider, model, effort, step, the exact
    prompt and any images). The same question asked again - a regenerate with
    unchanged data - is answered from here at no cost. Change anything in the
    question and the key changes, so a fresh answer is requested."""

    __tablename__ = "ai_response_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    module: Mapped[str] = mapped_column(String, nullable=False, default="other")
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    # What the original (paid) call used - shown as "saved" on a cache hit.
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
