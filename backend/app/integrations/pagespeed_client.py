import math
import re
from urllib.parse import urlparse

import httpx

from app.config import settings

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BACKTICK_RE = re.compile(r"`([^`]+)`")

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


# Lighthouse's own performance-category weights + scoring-curve control
# points (median -> score 50, p10 -> score 90), from Lighthouse's published
# `metrics.json`/`audit.js` scoring model (v10+, same for mobile & desktop).
# Google can revise these between Lighthouse versions; re-check against
# `lighthouseResult.lighthouseVersion` in the PSI response if scores here
# ever look off from PSI's own dashboard.
_METRIC_CURVES = {
    "largest-contentful-paint": {"label": "LCP", "weight": 0.25, "median": 4000, "p10": 2500},
    "total-blocking-time": {"label": "TBT", "weight": 0.30, "median": 600, "p10": 200},
    "cumulative-layout-shift": {"label": "CLS", "weight": 0.25, "median": 0.25, "p10": 0.1},
    "first-contentful-paint": {"label": "FCP", "weight": 0.10, "median": 3000, "p10": 1800},
    "speed-index": {"label": "Speed Index", "weight": 0.10, "median": 5800, "p10": 3387},
}
# Φ⁻¹(0.9) — the standard-normal quantile that pins the p10 control point to
# score 90 in Lighthouse's log-normal curve.
_P10_Z = 1.2816


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _lighthouse_metric_score(value: float, median: float, p10: float) -> int:
    """Reimplementation of Lighthouse's log-normal metric scoring curve
    (core/lib/statistics.js getLogNormalScore) — the same math behind the
    public Lighthouse Scoring Calculator. `value` at p10 scores ~90, at
    median scores ~50."""
    if value <= 0:
        return 100
    sigma = math.log(p10 / median) / -_P10_Z
    standardized = (math.log(value) - math.log(median)) / sigma
    return round(max(0.0, min(1.0, 1 - _normal_cdf(standardized))) * 100)


def _score_breakdown(audits: dict) -> list[dict]:
    """Per-metric current value, weight, and score — the ingredients behind
    the single Performance score, broken out so a report can show which
    metric is actually costing the most points."""
    breakdown = []
    for audit_id, curve in _METRIC_CURVES.items():
        audit = audits.get(audit_id)
        value = audit.get("numericValue") if audit else None
        if value is None:
            continue
        score = _lighthouse_metric_score(value, curve["median"], curve["p10"])
        breakdown.append({
            "id": audit_id,
            "label": curve["label"],
            "value": value,
            "display_value": audit.get("displayValue"),
            "weight": curve["weight"],
            "median": curve["median"],
            "p10": curve["p10"],
            "score": score,
            "weighted_points": round(score * curve["weight"], 1),
        })
    return breakdown


def _overall_score(breakdown: list[dict], overrides: dict | None = None) -> int:
    """Weighted-sum Performance score, optionally with one or more metrics'
    values swapped out (e.g. to a 'good' p10 threshold) to project what the
    score would become if just those metrics were fixed."""
    overrides = overrides or {}
    total = 0.0
    for m in breakdown:
        if m["id"] in overrides:
            score = _lighthouse_metric_score(overrides[m["id"]], m["median"], m["p10"])
        else:
            score = m["score"]
        total += score * m["weight"]
    return round(total)


def _quick_wins(breakdown: list[dict], current_score: int) -> list[dict]:
    """Per-metric 'what if this alone hit its good threshold' projection,
    ranked by actual score impact — the Lighthouse Scoring Calculator's core
    trick, applied metric-by-metric instead of by hand.

    The delta is computed self-consistently in our own weighted-sum model
    (own 'before' vs own 'after', same formula both sides) rather than
    against `current_score` directly — `current_score` is Google's real API
    score, which can differ slightly from our independently-rounded
    reimplementation, and diffing across those two baselines can produce a
    nonsensical negative delta for a genuine improvement. The delta is then
    applied on top of the real `current_score` so the displayed projection
    still anchors to the number the client sees on their own PSI dashboard."""
    own_before = _overall_score(breakdown)
    wins = []
    for m in breakdown:
        own_after = _overall_score(breakdown, {m["id"]: m["p10"]})
        delta = own_after - own_before
        wins.append({**m, "score_if_fixed": min(100, current_score + delta), "score_delta": delta})
    wins.sort(key=lambda w: -w["score_delta"])
    return wins


def _combined_projection(breakdown: list[dict], quick_wins: list[dict], current_score: int, top_n: int = 2) -> dict:
    top = [w for w in quick_wins if w["score_delta"] > 0][:top_n]
    overrides = {w["id"]: w["p10"] for w in top}
    own_before = _overall_score(breakdown)
    own_after = _overall_score(breakdown, overrides)
    delta = own_after - own_before
    return {
        "metrics": [w["label"] for w in top],
        "score_before": current_score,
        "score_after": min(100, current_score + delta),
    }


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _extract_script_weight(audits: dict, site_url: str, limit: int = 5) -> dict | None:
    """Script-weight breakdown for the report's treemap-derived slide, built
    from two Lighthouse audits, each used only for the one figure it
    measures cleanly:

    - `script-treemap-data` top-level nodes -> per-file transferred bytes
      (encodedBytes) and first- vs third-party split by domain. Only the
      top-level `encodedBytes` is used; nested child nodes are NOT summed —
      real PSI responses show `unusedBytes` appearing inconsistently at
      different tree depths (sometimes only on an intermediate grouping
      node, sometimes on leaves, sometimes absent), so hand-summing it risks
      double-counting or silently dropping bytes.
    - `unused-javascript`'s own `details.items` for wasted-byte/percent per
      resource — Lighthouse's own purpose-built, unambiguous figure for
      exactly this, so no aggregation of our own is needed for waste."""
    treemap = audits.get("script-treemap-data")
    nodes = ((treemap or {}).get("details") or {}).get("nodes") or []
    if not nodes:
        return None

    site_domain = _domain(site_url)
    scripts = []
    total_bytes = 0
    third_party_bytes = 0
    for node in nodes:
        encoded = node.get("encodedBytes") or 0
        url = node.get("name", "")
        total_bytes += encoded
        is_third_party = bool(url) and _domain(url) != site_domain
        if is_third_party:
            third_party_bytes += encoded
        scripts.append({
            "url": url, "encoded_bytes": encoded,
            "resource_bytes": node.get("resourceBytes") or 0,
            "is_third_party": is_third_party,
        })
    scripts.sort(key=lambda s: -s["encoded_bytes"])

    waste_items = []
    unused_js = audits.get("unused-javascript")
    for item in ((unused_js or {}).get("details") or {}).get("items", []):
        url = item.get("url", "")
        waste_items.append({
            "url": url,
            "total_bytes": item.get("totalBytes") or 0,
            "wasted_bytes": item.get("wastedBytes") or 0,
            "wasted_percent": item.get("wastedPercent") or 0,
            "is_third_party": bool(url) and _domain(url) != site_domain,
        })
    waste_items.sort(key=lambda w: -w["wasted_bytes"])

    return {
        "total_js_bytes": total_bytes,
        "third_party_bytes": third_party_bytes,
        "third_party_pct": round(third_party_bytes / total_bytes * 100) if total_bytes else 0,
        "top_scripts": scripts[:limit],
        "top_waste": waste_items[:limit],
    }


def _clean_lighthouse_text(text: str) -> str:
    """Lighthouse audit descriptions carry raw Markdown (`[Learn more](url)`,
    `` `offsetWidth` ``) meant for their own web dashboard — rendered
    verbatim into a slide it shows as literal brackets/parens/backticks
    instead of prose. Strips both, keeping the link's visible label."""
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    return _BACKTICK_RE.sub(r"\1", text)


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
            "impact": audit.get("displayValue") or _clean_lighthouse_text(audit.get("description", "")),
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

    performance_score = score("performance")
    breakdown = _score_breakdown(audits)
    # Anchor "current" to the real score Google's own API returned (not our
    # recomputed weighted sum, which is a faithful but independently-rounded
    # reimplementation of their formula) — projections/deltas below are then
    # relative to the number the client's actual PSI dashboard shows, so the
    # report never contradicts what they can go verify themselves.
    current_score = performance_score if performance_score is not None else _overall_score(breakdown)
    quick_wins = _quick_wins(breakdown, current_score) if breakdown else []
    combined_projection = _combined_projection(breakdown, quick_wins, current_score) if quick_wins else None

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
        "current_score": current_score,
        "score_breakdown": breakdown,
        "quick_wins": quick_wins,
        "combined_projection": combined_projection,
        "script_weight": _extract_script_weight(audits, url),
        "core_web_vitals": {
            "largest_contentful_paint": metric("largest-contentful-paint"),
            "cumulative_layout_shift": metric("cumulative-layout-shift"),
            "interaction_to_next_paint": metric("interaction-to-next-paint") or metric("total-blocking-time"),
            "first_contentful_paint": metric("first-contentful-paint"),
        },
        "issues": _extract_issues(audits),
        "script_treemap": _extract_treemap(audits),
    }
