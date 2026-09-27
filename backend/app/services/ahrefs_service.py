"""Ahrefs' free public Domain Rating endpoint — costs 0 API units, but
still needs a free APIv3 key (Ahrefs made auth mandatory 2026-08-10; it
launched keyless). Any failure (no key configured, bad domain, rate limit,
network error) returns None — callers fall back to the manually-entered
DomainRating row for that domain, see app.models.domain_rating."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

DOMAIN_RATING_URL = "https://api.ahrefs.com/v3/public/domain-rating-free"


def fetch_domain_rating(domain: str) -> int | None:
    if not domain or not settings.ahrefs_api_key:
        return None
    try:
        response = httpx.get(
            DOMAIN_RATING_URL,
            params={"target": domain},
            headers={"Authorization": f"Bearer {settings.ahrefs_api_key}"},
            timeout=15,
        )
        if response.status_code != 200:
            logger.warning("Ahrefs DR lookup failed for %s: %s %s", domain, response.status_code, response.text[:200])
            return None
        dr = (response.json().get("domain_rating") or {}).get("domain_rating")
        return round(dr) if dr is not None else None
    except Exception:
        logger.warning("Ahrefs DR lookup errored for %s", domain, exc_info=True)
        return None
