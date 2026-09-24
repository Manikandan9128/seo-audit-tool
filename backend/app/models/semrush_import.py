import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, LargeBinary, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base


class SemrushImport(Base):
    """A parsed Semrush CSV/Excel export (backlinks, keyword gap, competitor overview) for a client."""

    __tablename__ = "semrush_imports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    import_type: Mapped[str] = mapped_column(String, nullable=False)  # backlinks | organic_competitors | keyword_gap
    is_own_site: Mapped[bool] = mapped_column(nullable=False, default=True)
    domain_label: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. a competitor's domain, when is_own_site is False
    parsed_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The uploaded file's exact bytes, for the Download button. Deferred so
    # the report pipeline and list endpoints, which load every import, never
    # pull these in. NULL for files uploaded before this column existed; the
    # download endpoint rebuilds a CSV from parsed_data for those.
    original_file: Mapped[bytes | None] = deferred(mapped_column(LargeBinary, nullable=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
