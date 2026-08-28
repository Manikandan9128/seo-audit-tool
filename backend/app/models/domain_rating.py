import uuid
from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DomainRating(Base):
    """A manually-entered Domain Rating for one domain under one client —
    own site or a competitor. Ahrefs (the source the user wants DR from)
    has no free bulk/API access, only a free single-domain manual lookup
    tool — so this is typed in by hand rather than pulled automatically.
    Overrides whatever Semrush's Authority Score would otherwise show for
    that domain in the Competitor Analysis table."""

    __tablename__ = "domain_ratings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    dr: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
