import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PageAuditJob(Base):
    """Tracks an in-progress or finished multi-page crawl for a client, so
    large sites (thousands of URLs) can run in the background instead of
    blocking a single HTTP request."""

    __tablename__ = "page_audit_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending | running | done | failed
    pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_with_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
