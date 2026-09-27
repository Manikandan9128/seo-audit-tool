import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DomainRating(Base):
    """A manually-entered Domain Rating for one domain under one client —
    own site or a competitor. Fallback only (2026-09-27): DR is pulled
    live from Ahrefs' free public Domain Rating API by default (see
    app.services.ahrefs_service); a row here is only used when that call
    fails for the domain (no Ahrefs key configured, rate limited, unknown
    domain, etc). Never comes from Semrush's Authority Score — that's
    explicitly cleared for the DR column in the Competitor Analysis
    table."""

    __tablename__ = "domain_ratings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    dr: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
