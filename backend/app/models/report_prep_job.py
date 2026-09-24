import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReportPrepJob(Base):
    """One "Generate Report" run on the client page: the per-section checks
    (company overview, site audit, PageSpeed, tech stack, analytics, All
    Pages crawl kickoff) run server-side in a background thread instead of
    as browser requests, so the run survives the user navigating away or
    refreshing. Separate from ReportGenerationJob, which builds the PPTX.

    `sections` is {section_key: {"status": pending|running|done|failed,
    "data": <that section's endpoint response>, "error": str|None}} — the
    page reads each section's data from here to show its preview."""

    __tablename__ = "report_prep_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending | running | done | failed
    sections: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    progress_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
