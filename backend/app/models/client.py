import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    website_url: Mapped[str] = mapped_column(String, nullable=False)
    ga4_property_id: Mapped[str | None] = mapped_column(String, nullable=True)
    gsc_site_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # Cached Company Overview extraction (Gemini/Claude) — report generation
    # reuses this instead of re-calling the AI on every regeneration, which
    # was burning through Gemini's free-tier quota for content that rarely
    # changes. Cleared/replaced only via the explicit refresh endpoint.
    company_overview_cache: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    company_overview_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Keyword engine cache (keyword_intelligence_service.KeywordIntelligenceCache):
    # AI relevance verdicts per keyword + AI cluster results per input set,
    # so a report regeneration re-uses them instead of calling the AI again.
    keyword_intelligence_cache: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # §31/§63 — one row per cluster (keyed by a stable cluster_key, see
    # keyword_history_service.py), latest decision fields overwritten on
    # every report generation. `outcome` (accepted/rejected/None) is never
    # set by report generation itself — there's no review UI yet — it's a
    # manual DB flag someone sets directly; recalibrate() only nudges
    # opportunity scoring once enough outcomes exist for a group, so this
    # column is inert (no behavior change) until that happens.
    keyword_decision_history: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
