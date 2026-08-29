import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportGenerationJob(Base):
    """Tracks an in-progress or finished PPTX report build for a client.

    Report generation runs slow steps (PageSpeed Insights Lighthouse runs,
    AI narrative calls, crawls) that can legitimately take minutes — far
    longer than a hosting gateway lets a single HTTP request stay open. This
    lets the build run in the background while the client polls for status,
    the same pattern already used for PageAuditJob's multi-page crawls."""

    __tablename__ = "report_generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending | running | done | failed
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    pptx_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
