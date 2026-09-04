import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # Human-readable current step ("Running PageSpeed Insights...",
    # "Analyzing competitor 2 of 4..."), paired with a rough percentage
    # (progress_pct) — hand-assigned per stage based on typical relative
    # duration (PageSpeed Insights and a per-competitor AI call aren't
    # comparable units of work, so this is an estimate, not a measured
    # byte-count), same as most "estimated progress" bars. Good enough for
    # "about how much longer" without pretending to be exact.
    progress_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    pptx_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    # Real failure reason per AI-dependent section that didn't come through
    # this run (keyword clustering, Core Problem, a competitor narrative,
    # Next Steps) — e.g. a rate limit or quota message. Deliberately NEVER
    # rendered into the PPTX itself (a client-facing deliverable is no
    # place for "Groq request failed: 429") — surfaced here instead so the
    # agency user sees it in the app before deciding whether to regenerate
    # or send the file as-is.
    content_generation_issues: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
