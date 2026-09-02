import httpx

from app.config import settings

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# Mobile Lighthouse runs simulate network/CPU throttling and routinely take
# noticeably longer than desktop — 90s was tight enough that mobile timed out
# far more often than desktop even with a retry, silently dropping the Mobile
# card on the Website Performance slide.
TIMEOUT = 150.0

# Already surfaced separately as core_web_vitals — excluded from the generic
# issue list below so a slow LCP/CLS doesn't also show up as a duplicate
# "diagnostic" row with no extra information.
_METRIC_AUDIT_IDS = {
    "largest-contentful-paint", "cumulative-layout-shift", "interaction-to-next-paint",
    "total-blocking-time", "first-contentful-paint", "speed-index",
}


def _extract_issues(audits: dict, limit: int = 8) -> list[dict]:
    """Real Lighthouse audits.<id> the PSI dashboard itself lists under
    "Opportunities"/"Diagnostics" — failing (score < 0.9), scored
    (scoreDisplayMode binary/numeric, not the purely-informative ones like
    screenshots), non-metric audits. Sorted worst-impact first: real
    millisecond savings when Lighthouse reports one, else by score."""
    issues = []
    for audit_id, audit in audits.items():
        if audit_id in _METRIC_AUDIT_IDS:
            continue
        score = audit.get("score")
        if score is None or score >= 0.9:
            continue
        if audit.get("scoreDisplayMode") not in ("binary", "numeric"):
            continue
        savings_ms = ((audit.get("details") or {}).get("overallSavingsMs")) or 0
        issues.append({
            "title": audit.get("title", audit_id),
            "impact": audit.get("displayValue") or audit.get("description", "")[:140],
            "savings_ms": savings_ms,
            "score": score,
        })
    issues.sort(key=lambda x: (-x["savings_ms"], x["score"]))
    return issues[:limit]


def run_pagespeed(url: str, strategy: str = "mobile", retries: int = 2) -> dict:
    """strategy: 'mobile' or 'desktop'. Retries on timeout and on a 5xx from
    PSI itself — its own Lighthouse run is slow and flaky enough that both
    transient timeouts and transient server errors aren't unusual."""
    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "seo", "accessibility", "best-practices"],
        "key": settings.google_psi_api_key,
    }
    try:
        resp = httpx.get(PSI_ENDPOINT, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
            raise
        if retries > 0:
            return run_pagespeed(url, strategy=strategy, retries=retries - 1)
        raise
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
        "issues": _extract_issues(audits),
    }
