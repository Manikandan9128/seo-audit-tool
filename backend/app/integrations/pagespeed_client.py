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


# 2026-09-21 spec: no custom/reimplemented Lighthouse scoring curve, and no
# "if this metric were fixed, the overall score would become Y" projection
# anywhere in this module — a per-metric improvement is never presented as
# a guaranteed overall Performance score change. Good Threshold values below
# are Google's own published "good" cutoffs (web.dev/articles/lcp,
# web.dev/articles/cls, web.dev/articles/tbt, etc.) — the same numbers the
# real PSI dashboard itself draws its green/orange/red bands at — not a
# threshold this tool invented.
_METRIC_GOOD_THRESHOLDS = {
    "largest-contentful-paint": {"label": "LCP", "good_threshold": 2500},
    "total-blocking-time": {"label": "TBT", "good_threshold": 200},
    "cumulative-layout-shift": {"label": "CLS", "good_threshold": 0.1},
    "first-contentful-paint": {"label": "FCP", "good_threshold": 1800},
    "speed-index": {"label": "Speed Index", "good_threshold": 3387},
}


def _metric_status(score: float | None) -> str | None:
    """Status from PSI's OWN per-audit score (score>=0.9 is Lighthouse's
    real 'Good'/green band, >=0.5 'Needs Improvement'/orange, else 'Poor'/
    red — the same bands the PSI dashboard itself colors these audits
    with), never a custom classification."""
    if score is None:
        return None
    pct = score * 100
    if pct >= 90:
        return "Good"
    if pct >= 50:
        return "Needs Improvement"
    return "Poor"


def _metric_table(audits: dict) -> list[dict]:
    """Metric | Current | Good Threshold | Status rows (2026-09-21 spec
    section 2) — every value read straight from PSI's own Lighthouse audit
    for that metric (numericValue, displayValue, score). No weighting, no
    'if fixed' projection, no overall-score arithmetic."""
    rows = []
    for audit_id, meta in _METRIC_GOOD_THRESHOLDS.items():
        audit = audits.get(audit_id)
        if audit is None or audit.get("numericValue") is None:
            continue
        rows.append({
            "id": audit_id,
            "label": meta["label"],
            "value": audit["numericValue"],
            "display_value": audit.get("displayValue"),
            "good_threshold": meta["good_threshold"],
            "status": _metric_status(audit.get("score")),
        })
    return rows


def _field_metric(metrics: dict, *keys: str) -> dict | None:
    for key in keys:
        entry = metrics.get(key)
        if entry:
            return {"percentile": entry.get("percentile"), "category": entry.get("category")}
    return None


def _cwv_field_data(psi_response: dict) -> dict | None:
    """Real CrUX field data (2026-09-21 spec sections 4/11) — actual
    Chrome-User-Experience-Report traffic from real visitors, never a
    Lighthouse lab-run number. `loadingExperience` is per-URL field data;
    `originLoadingExperience` is CrUX's own site-wide fallback for when the
    URL itself doesn't have enough real-user traffic to report alone.
    Neither present (new/low-traffic site — CrUX genuinely has nothing) ->
    None, and the caller must say so explicitly rather than claim Core Web
    Vitals passed or failed."""
    field = psi_response.get("loadingExperience") or {}
    is_origin_fallback = False
    if not field.get("metrics"):
        field = psi_response.get("originLoadingExperience") or {}
        is_origin_fallback = bool(field.get("metrics"))
    metrics = field.get("metrics") or {}
    if not metrics:
        return None
    return {
        "is_origin_fallback": is_origin_fallback,
        "overall_category": field.get("overall_category"),
        "lcp": _field_metric(metrics, "LARGEST_CONTENTFUL_PAINT_MS"),
        # INP replaced FID as the CWV responsiveness metric in March 2024;
        # EXPERIMENTAL_INTERACTION_TO_NEXT_PAINT covers CrUX responses that
        # still use the pre-GA field name. Never derived from TBT (a lab
        # metric) — if CrUX has neither key, this is None, full stop.
        "inp": _field_metric(metrics, "INTERACTION_TO_NEXT_PAINT", "EXPERIMENTAL_INTERACTION_TO_NEXT_PAINT"),
        "cls": _field_metric(metrics, "CUMULATIVE_LAYOUT_SHIFT_SCORE"),
    }


def _perf_audit_groups(categories: dict) -> dict[str, str]:
    """audit_id -> group ('load-opportunities'/'diagnostics'/...), straight
    from the Performance category's own auditRefs — PSI's real UI grouping,
    not a classification this tool invented."""
    refs = ((categories.get("performance") or {}).get("auditRefs")) or []
    return {r["id"]: r.get("group") for r in refs if r.get("id")}


def _format_savings(details: dict) -> str | None:
    ms = details.get("overallSavingsMs")
    kib = details.get("overallSavingsBytes")
    parts = []
    if ms:
        parts.append(f"{ms / 1000:.1f}s" if ms >= 1000 else f"{round(ms)}ms")
    if kib:
        parts.append(f"{kib / 1024:.0f} KiB")
    return " / ".join(parts) if parts else None


def _extract_opportunities(audits: dict, audit_groups: dict, limit: int = 8) -> list[dict]:
    """Real PSI 'Opportunities' only (2026-09-21 spec section 6) — audits
    Lighthouse itself grouped under load-opportunities (its own PSI-UI
    section), and only when PSI actually quantified a saving for this run.
    Never a manufactured "compress images"/"remove unused JS" line for a
    best-practice PSI didn't flag with a real number here."""
    opps = []
    for audit_id, group in audit_groups.items():
        if group != "load-opportunities":
            continue
        audit = audits.get(audit_id)
        if not audit:
            continue
        score = audit.get("score")
        if score is not None and score >= 0.9:
            continue
        details = audit.get("details") or {}
        savings = _format_savings(details)
        if not savings:
            continue
        opps.append({
            "title": _clean_lighthouse_text(audit.get("title", audit_id)),
            "savings": savings,
            "savings_ms": details.get("overallSavingsMs") or 0,
        })
    opps.sort(key=lambda o: -o["savings_ms"])
    return opps[:limit]


# Rule 8 — a small fixed set of well-known PSI diagnostic audits. Only ever
# surfaced when PSI actually returned a real value for THIS run.
_DIAGNOSTIC_AUDIT_IDS = {
    "mainthread-work-breakdown": "Long main-thread tasks",
    "bootup-time": "Total JavaScript execution time",
    "total-byte-weight": "Total network payload",
    "render-blocking-resources": "Render-blocking resources",
    "unused-javascript": "Unused JavaScript",
    "unused-css-rules": "Unused CSS",
    "network-requests": "Request count",
}


def _extract_diagnostics(audits: dict, audit_groups: dict) -> list[dict]:
    """network-requests carries no displayValue of its own — its real
    diagnostic value here is the actual request count from its own
    details.items, never a manufactured figure. Rule 9 (avoid repetition):
    an audit PSI itself grouped under load-opportunities (render-blocking-
    resources, unused-javascript, unused-css-rules commonly are, when they
    carry a real saving) is skipped here — it already appears once, in
    Top PSI Opportunities."""
    out = []
    for audit_id, label in _DIAGNOSTIC_AUDIT_IDS.items():
        if audit_groups.get(audit_id) == "load-opportunities":
            continue
        audit = audits.get(audit_id)
        if not audit:
            continue
        if audit_id == "network-requests":
            items = ((audit.get("details") or {}).get("items")) or []
            if not items:
                continue
            value = f"{len(items)} requests"
        else:
            value = audit.get("displayValue")
            if not value:
                continue
        out.append({"id": audit_id, "label": label, "value": value})
    return out


_LCP_PHASE_LABELS = {
    "ttfb": "TTFB", "loaddelay": "Resource load delay",
    "loadtime": "Resource load duration", "renderdelay": "Element render delay",
}


def _extract_lcp_breakdown(audits: dict) -> list[dict] | None:
    """Rule 7 — Lighthouse's own LCP phase table (largest-contentful-
    paint-element audit's phase breakdown), only when PSI's response for
    this run actually includes one in a shape this can positively match to
    a known phase label. Different Lighthouse versions have varied this
    audit's exact detail shape; this never guesses at an unrecognized one
    — returns None (section omitted entirely) rather than invent a phase
    split PSI didn't actually provide."""
    audit = audits.get("largest-contentful-paint-element")
    if not audit:
        return None
    rows = []
    for item in ((audit.get("details") or {}).get("items")) or []:
        phase_raw = str(item.get("phase") or item.get("label") or "").strip().lower().replace(" ", "").replace("_", "")
        label = _LCP_PHASE_LABELS.get(phase_raw)
        timing = item.get("timing")
        if not label or timing is None:
            continue
        rows.append({"phase": label, "timing_ms": timing})
    return rows or None


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
        "total_script_count": len(scripts),
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

    # A 200 OK can still come back with an empty/incomplete Lighthouse
    # result (no performance category scored at all) — confirmed real on a
    # heavy-JS site: two back-to-back calls with identical params, one
    # returned a real score, the other returned 200 with categories={}
    # entirely. That never triggered the retry above (no exception, no
    # 5xx status), so a single flaky run silently produced "Not run" on
    # the Website Performance slide even though a retry moments later
    # would very likely have succeeded (as it did here).
    if retries > 0 and categories.get("performance", {}).get("score") is None:
        return run_pagespeed(url, strategy=strategy, retries=retries - 1, timeout=timeout)

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

    audit_groups = _perf_audit_groups(categories)

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
        # 2026-09-21 spec: this is Google's own real Performance score,
        # never a recomputed/projected one — no "if fixed" arithmetic
        # anywhere in this module.
        "current_score": score("performance"),
        "metric_table": _metric_table(audits),
        "field_data": _cwv_field_data(data),
        "opportunities": _extract_opportunities(audits, audit_groups),
        "diagnostics": _extract_diagnostics(audits, audit_groups),
        "lcp_breakdown": _extract_lcp_breakdown(audits),
        "script_weight": _extract_script_weight(audits, url),
        "script_treemap": _extract_treemap(audits),
    }
