import httpx

from app.config import settings

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Mobile Lighthouse runs simulate network/CPU throttling and routinely take
# noticeably longer than desktop — 90s was tight enough that mobile timed out
# far more often than desktop even with a retry, silently dropping the Mobile
# card on the Website Performance slide.
TIMEOUT = 150.0


def run_pagespeed(url: str, strategy: str = "mobile", retries: int = 2) -> dict:
    """strategy: 'mobile' or 'desktop'. Retries once on timeout — PSI's own
    Lighthouse run is slow enough that transient timeouts aren't unusual."""
    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "seo", "accessibility", "best-practices"],
        "key": settings.google_psi_api_key,
    }
    try:
        resp = httpx.get(PSI_ENDPOINT, params=params, timeout=TIMEOUT)
    except httpx.TimeoutException:
        if retries > 0:
            return run_pagespeed(url, strategy=strategy, retries=retries - 1)
        raise
    resp.raise_for_status()
    data = resp.json()

    lighthouse = data.get("lighthouseResult", {})
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})

    def score(cat_key: str) -> int | None:
        cat = categories.get(cat_key)
        if not cat or cat.get("score") is None:
            return None
        return round(cat["score"] * 100)

    def metric(audit_key: str) -> str | None:
        audit = audits.get(audit_key)
        return audit.get("displayValue") if audit else None

    return {
        "strategy": strategy,
        "scores": {
            "performance": score("performance"),
            "seo": score("seo"),
            "accessibility": score("accessibility"),
            "best_practices": score("best-practices"),
        },
        "core_web_vitals": {
            "largest_contentful_paint": metric("largest-contentful-paint"),
            "cumulative_layout_shift": metric("cumulative-layout-shift"),
            "interaction_to_next_paint": metric("interaction-to-next-paint") or metric("total-blocking-time"),
            "first_contentful_paint": metric("first-contentful-paint"),
        },
    }
