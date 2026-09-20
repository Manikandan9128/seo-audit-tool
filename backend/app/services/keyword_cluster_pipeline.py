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

2026-09-20 spec additions on top of the above: Cluster Name Validation
(reject a catch-all/generic-only cluster name rather than render it —
see _is_catchall_cluster_name) and Core Category Prioritization (the
final cluster order must lead with the client's strongest core commercial
opportunity, never simply the highest-combined-volume cluster — see
_assign_core_category_and_priority). Semantic Topic/Entity and Keyword
Modifier Classification (spec steps 7-8) are deliberately NOT implemented
as a separate AI pass here — the existing Business Theme + candidate-
clustering AI call already collapses same-topic modifier variants
("Certified Payroll Basics/Fundamentals/Overview") into one cluster (see
keyword_cluster_service.generate_batched_candidate_clusters' own
instruction to that effect), and adding a dedicated third AI classification
pass purely to store an internal-only field the PPT never renders (spec
step 27 doesn't list semantic_topic in the rendered table) wasn't judged
worth its added AI cost/latency risk (see this pipeline's own "one batched
call, not one per bucket" incident above) — flagged here rather than
silently claimed as done.
"""

import logging
import re

from app.services.business_theme_service import UNCLASSIFIED_THEME, generate_business_themes
from app.services.keyword_cluster_service import generate_batched_candidate_clusters
from app.services.keyword_relevance_service import match_existing_page_for_cluster

logger = logging.getLogger(__name__)

# Cluster Name Validation (spec steps 15-17): a name that's ONLY a generic
# content-format/catch-all label, never a real SEO topic. Checked against
# the whole normalized label, never a substring — a real specific name
# that happens to contain a generic word (e.g. "Daimler Companies") is
# never caught here, per spec's explicit generic-word exception (step 16):
# a generic word only disqualifies a name when it IS the whole name.
_CATCHALL_CLUSTER_NAMES = {
    "overview", "info", "information", "details", "miscellaneous", "general", "general topics",
    "various", "various topics", "other", "other topics", "ungrouped", "ungrouped keywords",
    "general information", "misc", "misc topics", "tech info", "platform info", "general info",
}


def _is_catchall_cluster_name(label: str) -> bool:
    normalized = re.sub(r"\s+", " ", label or "").strip().lower()
    if not normalized:
        return True
    if normalized in _CATCHALL_CLUSTER_NAMES:
        return True
    # "Miscellaneous Database Topics" style names — spec's own worked
    # example: reject regardless of whatever anchor noun follows, since a
    # label built around "miscellaneous"/"ungrouped" as its organizing word
    # is a catch-all bucket by construction, not a real SEO topic.
    if "miscellaneous" in normalized or "ungrouped" in normalized:
        return True
    return False

# Same bound as the existing clustering/intent/page-matching AI calls
# elsewhere in this pipeline (site_audit.py) — keeps every AI call in the
# keyword pipeline working from the same top-N-by-volume candidate pool
# instead of adding a new, inconsistent cap. Also caps candidate-clustering
# prompt size — see _build_candidate_clusters' docstring for why this cap
# is load-bearing there, not just a nice-to-have.
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
    group.

    Bucketing itself runs over every row (cheap, no AI). The AI sub-split
    is ONE batched call covering every bucket at once, not one call per
    bucket — confirmed real (2026-09-19): a client with many distinct
    buckets turned into that many sequential Groq calls, each also queued
    behind Groq's shared per-minute token budget alongside this same
    report's other AI calls, chaining past the 15-minute stale-job
    threshold and killing the whole report. That single call's input is
    capped at the same _BUSINESS_THEME_CANDIDATE_CAP keywords business
    theme classification already used, for the same reason: bounded AI
    cost regardless of how many thousand keyword rows a real export has.
    A row whose keyword falls outside that cap still gets its bucket's
    theme-based default cluster (or stays unclustered for an Unclassified
    bucket) — never silently dropped, just not AI-sub-split."""
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        if not (r.get("keyword") or "").strip():
            continue
        theme = (r.get("business_theme") or UNCLASSIFIED_THEME).strip() or UNCLASSIFIED_THEME
        intent = (r.get("intent") or "").strip() or "Unknown Intent"
        category = (r.get("page_category") or "").strip() or "Unspecified Format"
        buckets.setdefault((theme, intent, category), []).append(r)

    # Global cap keeps the one batched AI call bounded regardless of how
    # many buckets or total rows exist — mirrors _assign_business_themes'
    # own top-N-by-volume cap.
    capped_keywords = set(_unique_keywords_by_volume(rows)[:_BUSINESS_THEME_CANDIDATE_CAP])

    groups: list[tuple[str, list[str]]] = []
    for bucket_key, bucket_rows in buckets.items():
        theme, intent, category = bucket_key
        unique_kw = [kw for kw in _unique_keywords_by_volume(bucket_rows) if kw in capped_keywords]
        if len(unique_kw) <= 1:
            continue
        if theme != UNCLASSIFIED_THEME:
            context_label = f'business theme "{theme}", search intent "{intent}", recommended page format "{category}"'
        else:
            context_label = f'search intent "{intent}", recommended page format "{category}" (business theme unknown)'
        groups.append((context_label, unique_kw))

    sub_label_map: dict[str, str] = {}
    if groups:
        try:
            sub_label_map = generate_batched_candidate_clusters(groups)
        except Exception as e:
            logger.warning("Batched candidate clustering failed: %s", e)
            sub_label_map = {}

    label_owner: dict[str, tuple[str, str, str]] = {}

    for bucket_key, bucket_rows in buckets.items():
        theme, intent, _category = bucket_key

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
            label = sub_label_map.get(kw)
            if label and _is_catchall_cluster_name(label):
                # Cluster Name Validation (spec steps 15-17): reject a
                # catch-all/generic-only AI-returned name outright — fall
                # back to the theme label (still real evidence) rather
                # than rendering "Overview" or "Miscellaneous X Topics".
                label = None
            label = label or default_label
            if not label or _is_catchall_cluster_name(label):
                r["cluster"] = ""
                r["cluster_status"] = "Unvalidated"
                continue
            label_key = label
            if label_key in label_owner and label_owner[label_key] != bucket_key:
                # Same short label text independently chosen for a
                # genuinely different (theme, intent, category) bucket —
                # disambiguate so it doesn't silently merge two different
                # page opportunities under one displayed cluster name. This
                # is the structural safety net that makes the single
                # shared AI call above safe even if it ignores the "never
                # combine different groups" instruction.
                label_key = f"{label} ({intent})"
            label_owner[label_key] = bucket_key
            r["cluster"] = label_key
            r["cluster_status"] = "Validated"


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


def _cluster_score(cluster_rows: list[dict]) -> tuple[float, int, float]:
    """Composite priority signal for one cluster (spec steps 19-20):
    (commercial-intent share, has-real-ranking-signal, total search
    volume) — in that order, so search demand is only ever a tiebreaker,
    never the primary sort key. Every input here is a real field an
    earlier pipeline step already set; nothing is invented."""
    if not cluster_rows:
        return (0.0, 0, 0.0)
    commercial = sum(
        1 for r in cluster_rows
        if (r.get("intent") or "").strip().lower() in ("commercial", "commercial investigation", "transactional")
    )
    commercial_share = commercial / len(cluster_rows)
    has_ranking = any(r.get("current_position") not in (None, "") for r in cluster_rows)
    total_volume = sum(_num(r.get("search_volume")) for r in cluster_rows)
    return (commercial_share, 1 if has_ranking else 0, total_volume)


def _assign_core_category_and_priority(rows: list[dict]) -> None:
    """Spec steps 18-20 — Core Category Identification + Core Cluster
    Prioritization. Deterministic, no new AI call: reuses business_theme,
    search_intent, search_volume, and current_position, every one of them
    already set by an earlier step.

    Step 18 (core_category): the business theme whose clusters carry the
    client's strongest aggregate commercial+ranking+demand signal — never
    guessed, and left unset (core_category_status="Requires Validation")
    when every theme is Unclassified (no real business-context evidence at
    all, not just a low score).

    Step 20 (cluster_priority, 1 = highest): clusters belonging to
    core_category sort ahead of every other theme's clusters; within that,
    and within every other theme, clusters rank by the same
    commercial/ranking/demand composite from _cluster_score — never by raw
    search volume alone (explicitly banned, step 20)."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)

    if not clusters:
        for r in rows:
            r["core_category"] = None
            r["core_category_status"] = "Requires Validation"
            r["cluster_priority"] = None
        return

    theme_score: dict[str, float] = {}
    for cluster_rows in clusters.values():
        theme = (cluster_rows[0].get("business_theme") or UNCLASSIFIED_THEME).strip() or UNCLASSIFIED_THEME
        if theme == UNCLASSIFIED_THEME:
            continue
        commercial_share, has_ranking, total_volume = _cluster_score(cluster_rows)
        # +1 on volume so a real but small (zero-volume-data) cluster with
        # commercial intent/ranking signal still contributes something,
        # rather than multiplying out to a flat zero.
        theme_score[theme] = theme_score.get(theme, 0.0) + (commercial_share * 2 + has_ranking) * (total_volume + 1)

    core_category = max(theme_score, key=theme_score.get) if theme_score else None

    ranked_labels = sorted(
        clusters.keys(),
        key=lambda label: (
            1 if (clusters[label][0].get("business_theme") or "").strip() == core_category else 0,
            *_cluster_score(clusters[label]),
        ),
        reverse=True,
    )
    priority_by_label = {label: i + 1 for i, label in enumerate(ranked_labels)}

    for r in rows:
        label = (r.get("cluster") or "").strip()
        r["core_category"] = core_category
        r["core_category_status"] = None if core_category else "Requires Validation"
        r["cluster_priority"] = priority_by_label.get(label) if label else None


def build_final_keyword_clusters(
    rows: list[dict],
    client_name: str,
    client_description: str | None,
    site_audit_pages_rows: list[dict] | None,
) -> list[dict]:
    """Runs Business Theme -> Candidate Clustering -> Validation/Auto-Split
    (structural, see module docstring) -> Core Category + Cluster
    Prioritization -> Primary/Secondary -> Existing Page Matching ->
    Cannibalization Check, mutating and returning `rows`. Caller must
    already have `intent` and `page_category` set on every row (FINAL
    PIPELINE steps 3-4) before calling this — this function only reads
    those fields, never sets them. Ranking enrichment (current_position/
    current_url) may run before or after this call; nothing here reads or
    overwrites it except to help pick a primary keyword and score cluster
    priority. Fails safe: if every AI call in here fails, every row simply
    keeps `cluster` = "" (unclustered) and the caller's existing flat-table
    fallback renders instead — no cluster is ever hallucinated."""
    if not rows:
        return rows

    _assign_business_themes(rows, client_name, client_description)
    _build_candidate_clusters(rows)
    _assign_core_category_and_priority(rows)
    _select_primary_secondary(rows)
    _apply_existing_page_matching(rows, site_audit_pages_rows)
    _apply_cannibalization_check(rows)
    return rows
