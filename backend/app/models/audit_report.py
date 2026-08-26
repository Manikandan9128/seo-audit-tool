import uuid
from datetime import date, datetime

from sqlalchemy import String, DateTime, Date, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditReport(Base):
    __tablename__ = "audit_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    date_range_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_range_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    raw_data_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    analysis_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pdf_file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    pptx_file_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
