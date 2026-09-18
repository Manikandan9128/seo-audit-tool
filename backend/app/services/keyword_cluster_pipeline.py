"""Business-theme-first, intent-aware, page-type-aware keyword clustering —
the FINAL PIPELINE the lead specified (2026-09-19):

Merge + Dedup -> Relevance Filter -> Intent -> Page Category -> Business
Theme -> Candidate Clustering -> Cluster Validation -> Automatic Splitting
-> Primary/Secondary Selection -> Ranking Enrichment -> Existing Page
Matching -> Cannibalization Check -> Final Cluster Output -> PPT renderer.

This module owns everything from Business Theme onward (candidate
clustering through cannibalization check). Merge/dedup, relevance
filtering, intent classification, page-category classification, and
ranking enrichment are unchanged and stay in site_audit.py — this module
is called after intent + page_category are already set on every row, and
before ranking enrichment (ranking enrichment doesn't affect clustering
decisions, only which existing current_position/current_url a row carries).

Core design decision: cluster VALIDATION and AUTOMATIC SPLITTING are not a
separate pass that inspects finished clusters for problems — they're
built into how candidate clusters are formed in the first place. Every
keyword is first bucketed by the exact (business_theme, search_intent,
page_category) triplet, a hard boundary no AI call can cross, so a cluster
can never end up mixing themes, intents, or page formats regardless of
what any AI response says. Only within one already-homogeneous bucket is
an AI call used, and only to split it further into real per-page
sub-topics (the "candidate clustering" step, D/E in the spec: semantic
relationship, keyword patterns/modifiers). This makes "validation" and
"auto-split" structural guarantees rather than best-effort checks.

SERP overlap (spec's criterion F) is intentionally never used — this
codebase has no SERP dataset for any client, and inventing SERP overlap
signal would violate the spec's explicit "never invent SERP overlap" rule.
"""

import logging

from app.services.business_theme_service import UNCLASSIFIED_THEME, generate_business_themes
from app.services.keyword_cluster_service import generate_candidate_clusters
from app.services.keyword_relevance_service import match_existing_page_for_cluster

logger = logging.getLogger(__name__)

# Same bound as the existing clustering/intent/page-matching AI calls
# elsewhere in this pipeline (site_audit.py) — keeps every AI call in the
# keyword pipeline working from the same top-N-by-volume candidate pool
# instead of adding a new, inconsistent cap.
_BUSINESS_THEME_CANDIDATE_CAP = 100


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _unique_keywords_by_volume(rows: list[dict]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for r in sorted(rows, key=lambda r: _num(r.get("search_volume")), reverse=True):
        kw = r.get("keyword")
        if kw and kw not in seen:
            seen.add(kw)
            ordered.append(kw)
    return ordered


def _assign_business_themes(rows: list[dict], client_name: str, client_description: str | None) -> None:
    if any((r.get("business_theme") or "").strip() for r in rows):
        for r in rows:
            if not (r.get("business_theme") or "").strip():
                r["business_theme"] = UNCLASSIFIED_THEME
        return

    candidates = _unique_keywords_by_volume(rows)[:_BUSINESS_THEME_CANDIDATE_CAP]
    try:
        theme_map = generate_business_themes(client_name, client_description, candidates)
    except Exception as e:  # AI call is best-effort; never block the report on it
        logger.warning("Business theme classification failed: %s", e)
        theme_map = {}
    for r in rows:
        r["business_theme"] = theme_map.get(r.get("keyword"), UNCLASSIFIED_THEME) or UNCLASSIFIED_THEME


def _build_candidate_clusters(rows: list[dict]) -> None:
    """Buckets every row by (business_theme, search_intent, page_category),
    then sub-splits each bucket via AI into real per-page clusters. Sets
    `cluster` on every row with a non-empty keyword; a row is left
    unclustered (`cluster` = "") only when there isn't enough evidence to
    group it with anything — spec's explicit fallback, never a forced
    group."""
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        if not (r.get("keyword") or "").strip():
            continue
        theme = (r.get("business_theme") or UNCLASSIFIED_THEME).strip() or UNCLASSIFIED_THEME
        intent = (r.get("intent") or "").strip() or "Unknown Intent"
        category = (r.get("page_category") or "").strip() or "Unspecified Format"
        buckets.setdefault((theme, intent, category), []).append(r)

    label_owner: dict[str, tuple[str, str, str]] = {}

    for bucket_key, bucket_rows in buckets.items():
        theme, intent, category = bucket_key
        unique_kw = _unique_keywords_by_volume(bucket_rows)

        sub_label_map: dict[str, str] = {}
        if len(unique_kw) > 1:
            if theme != UNCLASSIFIED_THEME:
                context_hint = f'business theme "{theme}", search intent "{intent}", and recommended page format "{category}"'
            else:
                context_hint = f'search intent "{intent}" and recommended page format "{category}" (business theme could not be determined)'
            try:
                sub_label_map = generate_candidate_clusters(unique_kw, context_hint)
            except Exception as e:
                logger.warning("Candidate clustering failed for bucket %s: %s", bucket_key, e)
                sub_label_map = {}

        # A known business theme is itself real evidence a page-level group
        # exists, so a bucket the AI didn't (or couldn't) sub-split still
        # gets ONE coherent cluster labeled by theme — every keyword in it
        # already shares theme + intent + page format. An unknown
        # ("Unclassified") theme carries no such evidence, so with no AI
        # sub-split result those keywords stay unclustered rather than
        # forcing them into a fake shared group.
        default_label = theme if theme != UNCLASSIFIED_THEME else None

        for r in bucket_rows:
            kw = r.get("keyword")
            label = sub_label_map.get(kw) or default_label
            if not label:
                r["cluster"] = ""
                continue
            label_key = label
            if label_key in label_owner and label_owner[label_key] != bucket_key:
                # Same short label text independently chosen for a
                # genuinely different (theme, intent, category) bucket —
                # disambiguate so it doesn't silently merge two different
                # page opportunities under one displayed cluster name.
                label_key = f"{label} ({intent})"
            label_owner[label_key] = bucket_key
            r["cluster"] = label_key


def _select_primary_secondary(rows: list[dict]) -> None:
    """Primary/secondary selection AFTER clusters are final (spec step 9).
    Every row within one final cluster already shares business theme,
    search intent, and page category by construction, so those three
    factors can't differentiate a primary keyword within the cluster — the
    real, available differentiators are commercial intent, whether the
    client already has ranking signal (a realistic ranking opportunity),
    and search volume, in that priority order. Never just "highest
    volume wins" on its own."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)

    def _score(r: dict) -> tuple:
        intent = (r.get("intent") or "").lower()
        commercial = 1 if intent in ("commercial", "transactional") else 0
        has_ranking_signal = 1 if r.get("current_position") not in (None, "") or r.get("position") not in (None, "") else 0
        return (commercial, has_ranking_signal, _num(r.get("search_volume")))

    for _label, cluster_rows in clusters.items():
        primary = max(cluster_rows, key=_score)
        for r in cluster_rows:
            r["primary_or_secondary"] = "Primary" if r is primary else "Secondary"


def _apply_existing_page_matching(rows: list[dict], site_audit_pages_rows: list[dict] | None) -> None:
    """Spec step 11 — matched at the CLUSTER level (every keyword in the
    cluster contributes signal), not per individual keyword, then the same
    match is applied to every row in that cluster so downstream consumers
    (PPT renderer, Content SEO Next Steps slide, cannibalization check
    below) all see one consistent existing-page decision per cluster."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)

    for _label, cluster_rows in clusters.items():
        keywords = [r.get("keyword") for r in cluster_rows if r.get("keyword")]
        match = match_existing_page_for_cluster(keywords, site_audit_pages_rows)
        for r in cluster_rows:
            if match:
                r["existing_page_url"] = match["url"]
                r["existing_page_title"] = match["title"]
                r["existing_page_match_strength"] = match["match_strength"]
            else:
                r["existing_page_url"] = None
                r["existing_page_title"] = None
                r["existing_page_match_strength"] = "none"


def _apply_cannibalization_check(rows: list[dict]) -> None:
    """Spec step 12 — flags when two or more DIFFERENT final clusters
    resolve to the same existing URL as a real (strong/partial) match,
    meaning that one page would otherwise be asked to satisfy two distinct
    page opportunities at once. A "weak"/no match never counts toward
    cannibalization — too little evidence that URL is really the target
    for either cluster."""
    cluster_url: dict[str, str] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        url = r.get("existing_page_url")
        strength = r.get("existing_page_match_strength")
        if label and url and strength in ("strong", "partial") and label not in cluster_url:
            cluster_url[label] = url

    url_clusters: dict[str, set[str]] = {}
    for label, url in cluster_url.items():
        url_clusters.setdefault(url, set()).add(label)

    for r in rows:
        label = (r.get("cluster") or "").strip()
        url = cluster_url.get(label)
        siblings = url_clusters.get(url) if url else None
        if siblings and len(siblings) > 1:
            others = sorted(c for c in siblings if c != label)
            r["cannibalization_status"] = (
                f"Potential cannibalization: {url} is also the best existing-page match for "
                f"{len(others)} other cluster(s) ({', '.join(others)}) — consolidate onto one page, "
                "differentiate the content, or confirm this is the right existing URL for this cluster."
            )
        else:
            r["cannibalization_status"] = None


def build_final_keyword_clusters(
    rows: list[dict],
    client_name: str,
    client_description: str | None,
    site_audit_pages_rows: list[dict] | None,
) -> list[dict]:
    """Runs Business Theme -> Candidate Clustering -> Validation/Auto-Split
    (structural, see module docstring) -> Primary/Secondary -> Existing
    Page Matching -> Cannibalization Check, mutating and returning `rows`.
    Caller must already have `intent` and `page_category` set on every row
    (FINAL PIPELINE steps 3-4) before calling this — this function only
    reads those fields, never sets them. Ranking enrichment
    (current_position/current_url) may run before or after this call;
    nothing here reads or overwrites it except to help pick a primary
    keyword. Fails safe: if every AI call in here fails, every row simply
    keeps `cluster` = "" (unclustered) and the caller's existing flat-table
    fallback renders instead — no cluster is ever hallucinated."""
    if not rows:
        return rows

    _assign_business_themes(rows, client_name, client_description)
    _build_candidate_clusters(rows)
    _select_primary_secondary(rows)
    _apply_existing_page_matching(rows, site_audit_pages_rows)
    _apply_cannibalization_check(rows)
    return rows
