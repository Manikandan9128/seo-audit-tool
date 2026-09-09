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


def _extract_treemap(audits: dict, limit: int = 40) -> list[dict]:
    """Lighthouse's own treemap (googlechrome.github.io/lighthouse/treemap)
    is built from the script-treemap-data audit — one root node per JS
    resource, each with nested source-map children. PSI only populates it
    when the page actually has scripts to map, so this is often empty for
    script-light pages; callers must handle that."""
    nodes = ((audits.get("script-treemap-data") or {}).get("details") or {}).get("nodes") or []

    def flatten(node: dict, name: str) -> dict:
        return {
            "name": name,
            "resource_bytes": node.get("resourceBytes", 0),
            "unused_bytes": node.get("unusedBytes", 0),
            "children": [flatten(c, c.get("name", "")) for c in (node.get("children") or [])],
        }

    flat = [flatten(n, n.get("name", "")) for n in nodes]
    flat.sort(key=lambda n: -n["resource_bytes"])
    return flat[:limit]


def run_pagespeed(url: str, strategy: str = "mobile", retries: int = 1, timeout: float = TIMEOUT) -> dict:
    """strategy: 'mobile' or 'desktop'. Retries on timeout and on a 5xx from
    PSI itself — its own Lighthouse run is slow and flaky enough that both
    transient timeouts and transient server errors aren't unusual.
    `timeout` defaults to the module constant but is overridable per call —
    a caller sitting behind a synchronous request/response (a browser
    waiting on this endpoint through a gateway with its own timeout, e.g.
    ngrok's 60s default) needs a much tighter budget than the background
    report-generation path, which already applies its own 340s outer
    deadline on top of this and can afford the full retry."""
    params = {
        "url": url,
        "strategy": strategy,
        "category": ["performance", "seo", "accessibility", "best-practices"],
        "key": settings.google_psi_api_key,
    }
    try:
        resp = httpx.get(PSI_ENDPOINT, params=params, timeout=timeout)
        resp.raise_for_status()
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
            raise
        if retries > 0:
            return run_pagespeed(url, strategy=strategy, retries=retries - 1, timeout=timeout)
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

    def raw_score(cat_key: str) -> float | None:
        # Lighthouse's own exact 0-1 score, unrounded — the PSI dashboard's
        # 0-100 badge is round(raw * 100), which loses precision (0.905 and
        # 0.914 both show as "91").
        cat = categories.get(cat_key)
        return cat.get("score") if cat else None

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
        "raw_scores": {
            "performance": raw_score("performance"),
            "seo": raw_score("seo"),
            "accessibility": raw_score("accessibility"),
            "best_practices": raw_score("best-practices"),
        },
        "core_web_vitals": {
            "largest_contentful_paint": metric("largest-contentful-paint"),
            "cumulative_layout_shift": metric("cumulative-layout-shift"),
            "interaction_to_next_paint": metric("interaction-to-next-paint") or metric("total-blocking-time"),
            "first_contentful_paint": metric("first-contentful-paint"),
        },
        "issues": _extract_issues(audits),
        "script_treemap": _extract_treemap(audits),
    }
