"""Global Recommendation Registry — Universal SEO Audit Engine spec
(2026-09-20) section 38: every recommendation carries a stable
Recommendation ID + Category + Evidence + URL + Priority + Action, and the
SAME recommendation must never repeat across modules (Technical, Content,
Programmatic, Conversion, AEO, GEO, Competitor, Keyword Strategy).

This module doesn't generate any new recommendations itself — every
module keeps deciding its own content, per spec section 47 ("the renderer
must not make decisions... all decisions must be completed upstream", the
same discipline applied one layer up here: this dedupes what other modules
already decided, it never re-judges relevance/priority/action itself). It
collects already-built, real per-module recommendation lists and merges
cross-module duplicates that share the same URL and the same canonical
action type — the concrete failure mode the spec describes (two modules
independently landing on the identical fix for the identical URL)."""

import hashlib

_CANONICAL_ACTION_PHRASES = [
    "optimize the existing page", "expand the existing page", "create a new page",
    "differentiate", "consolidate", "redirect / merge",
]


def _canonical_action(action_text: str) -> str | None:
    text = (action_text or "").lower()
    for phrase in _CANONICAL_ACTION_PHRASES:
        if phrase in text:
            return phrase
    return None


def _normalize_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def _recommendation_id(category: str, url: str, action: str) -> str:
    raw = f"{category}|{_normalize_url(url)}|{(action or '').strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def deduplicate_recommendations(recommendations: list[dict]) -> list[dict]:
    """Merges recommendations sharing the same URL + canonical action type
    across DIFFERENT categories — the real section-38 violation pattern.
    A recommendation with no recognizable URL, or whose action text
    doesn't match a canonical phrase, is never merged (not confident
    enough to call it a duplicate) — it always survives untouched. Two
    recommendations from the SAME category sharing a URL+action also both
    survive (e.g. two distinct technical issues that each happen to
    recommend "optimize the existing page" aren't the cross-module
    duplicate this function targets).

    The surviving merged entry keeps the highest-priority (lowest
    `priority` number) instance's own fields and gains `also_raised_in`:
    the other categories that independently flagged the same thing — real
    corroborating evidence, never hidden. Every returned recommendation
    gets a stable `recommendation_id` (spec section 38's own required
    field) if it didn't already carry one."""
    groups: dict[tuple[str, str], list[dict]] = {}
    passthrough: list[dict] = []
    for rec in recommendations:
        url = _normalize_url(rec.get("url"))
        canonical = _canonical_action(rec.get("action"))
        if not url or not canonical:
            passthrough.append(rec)
            continue
        groups.setdefault((url, canonical), []).append(rec)

    merged: list[dict] = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        categories = sorted({r.get("category") for r in group if r.get("category")})
        if len(categories) <= 1:
            merged.extend(group)
            continue
        best = min(group, key=lambda r: r["priority"] if r.get("priority") is not None else float("inf"))
        best = dict(best)
        best["also_raised_in"] = [c for c in categories if c != best.get("category")]
        merged.append(best)

    result = merged + passthrough
    for rec in result:
        rec.setdefault("recommendation_id", _recommendation_id(rec.get("category", ""), rec.get("url", ""), rec.get("action", "")))
    return result


_KEYWORD_STRATEGY_ACTION_TEXT = {
    "Optimize Existing Page": "optimize the existing page",
    "Expand Existing Page": "expand the existing page",
    "Differentiate": "create a new page and differentiate it from the closest existing match",
    "Create New Page": "create a new page",
}


def build_keyword_strategy_recommendations(cluster_rows: list[dict] | None) -> list[dict]:
    """One Keyword Strategy recommendation per validated cluster, built
    from the SAME existing_page_action/existing_page_url evidence the
    Target Keywords slide and Content SEO Next Steps already use — never a
    new judgment, just this registry's own view of that same data, so a
    genuine cross-module duplicate against Technical recommendations is
    actually catchable by deduplicate_recommendations above."""
    if not cluster_rows:
        return []
    cluster_size: dict[str, int] = {}
    representative: dict[str, dict] = {}
    for r in cluster_rows:
        label = (r.get("cluster") or "").strip()
        if not label:
            continue
        cluster_size[label] = cluster_size.get(label, 0) + 1
        representative.setdefault(label, r)

    recommendations = []
    for label, r in representative.items():
        action_key = r.get("existing_page_action")
        if not action_key:
            continue
        url = r.get("existing_page_url")
        base = _KEYWORD_STRATEGY_ACTION_TEXT.get(action_key, action_key.lower() if action_key else "")
        action_text = f"{base} ({url})" if url and "(" not in base else base
        recommendations.append({
            "category": "Keyword Strategy",
            "issue": f'Keyword cluster "{label}" needs a content decision',
            "evidence": f"{cluster_size[label]} keyword(s) tracked in this cluster",
            "url": url,
            "action": action_text,
            "priority": r.get("cluster_priority"),
        })
    return recommendations
