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
_assign_core_category_and_priority).

Modifier/Attribute Detection (Universal SEO Audit Engine spec, same date,
sections 7-11 + 46's "CRITICAL ARCHITECTURE RULE"): semantic_topic and
modifier are now real intermediate fields computed BEFORE clustering
(_strip_modifiers), not left to the clustering AI call's own judgment —
"product pricing"/"product features"/"product benefits" all strip down to
the same core phrase "product" deterministically, so the candidate-
clustering AI call only ever sees ONE representative keyword per distinct
core phrase per bucket, never the raw modifier-laden phrase — the AI
structurally cannot invent "Product Pricing" as its own cluster name for
a bucket that collapses to one topic group, because "pricing" is stripped
before the AI's input is even built. A bucket whose every keyword shares
one core phrase skips the AI entirely and uses the (title-cased) core
phrase itself as the cluster name — real evidence, not an AI guess.
"""

import logging
import re

from app.services.business_theme_service import UNCLASSIFIED_THEME, generate_business_themes
from app.services.keyword_cluster_service import generate_batched_candidate_clusters
from app.services.keyword_relevance_service import match_existing_page_for_cluster
from app.services.priority_model import compute_priority_score, evidence_confidence_to_score

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


# Modifier/attribute words (spec section 8's own list) — describe the
# search need/content treatment/commercial qualifier, never the topic
# itself. Matched as whole words only (see _strip_modifiers), so this
# never accidentally eats part of a real multi-word topic.
_MODIFIER_WORDS = {
    "pricing", "price", "cost", "plans", "plan", "features", "feature", "benefits", "benefit",
    "types", "type", "options", "option", "reviews", "review", "examples", "example", "overview",
    "information", "info", "basics", "basic", "fundamentals", "fundamental", "guide", "guides",
    "how-to", "howto", "best", "top", "tools", "tool", "providers", "provider", "companies", "company",
    "manufacturers", "manufacturer", "services", "service", "solutions", "solution", "platforms",
    "platform", "software", "calculators", "calculator", "templates", "template", "resources", "resource",
}


# Coarse attribute_type bucket per modifier word (spec section 43's
# `attribute_type` field) — describes WHAT KIND of modifier the keyword
# carries, not just that one exists. Purely descriptive of the word itself,
# no AI, no invented category.
_MODIFIER_TYPE = {
    "pricing": "Commercial", "price": "Commercial", "cost": "Commercial", "plans": "Commercial", "plan": "Commercial",
    "reviews": "Evaluative", "review": "Evaluative", "best": "Evaluative", "top": "Evaluative",
    "types": "Evaluative", "type": "Evaluative", "options": "Evaluative", "option": "Evaluative",
    "examples": "Evaluative", "example": "Evaluative",
    "information": "Informational", "info": "Informational", "basics": "Informational", "basic": "Informational",
    "fundamentals": "Informational", "fundamental": "Informational", "guide": "Informational", "guides": "Informational",
    "how-to": "Informational", "howto": "Informational", "overview": "Informational",
    "features": "Feature", "feature": "Feature", "benefits": "Feature", "benefit": "Feature",
    "tools": "Format", "tool": "Format", "calculators": "Format", "calculator": "Format",
    "templates": "Format", "template": "Format", "resources": "Format", "resource": "Format",
    "software": "Format", "platforms": "Format", "platform": "Format",
    "providers": "Provider", "provider": "Provider", "companies": "Provider", "company": "Provider",
    "manufacturers": "Provider", "manufacturer": "Provider", "services": "Provider", "service": "Provider",
    "solutions": "Provider", "solution": "Provider",
}


def _attribute_type(modifier_words: list[str]) -> str | None:
    """Spec section 43's `attribute_type` — the dominant modifier category
    for a row's `modifier` words (first one found, since a real keyword
    rarely mixes categories; e.g. "product pricing guide" -> Commercial).
    None when the keyword carries no modifier at all."""
    for w in modifier_words:
        if w in _MODIFIER_TYPE:
            return _MODIFIER_TYPE[w]
    return None


def _strip_modifiers(keyword: str) -> tuple[str, list[str]]:
    """Splits a keyword into its core semantic-topic phrase and whichever
    modifier/attribute words it carries (spec sections 7-9) — pure
    deterministic tokenization/list-matching, no AI, no invention: real
    words from the real keyword, nothing added. "product pricing plans" ->
    ("product", ["pricing", "plans"]). If every word is a modifier (no
    anchor noun left at all — e.g. the keyword IS just "pricing"), the
    core phrase falls back to the full original keyword rather than an
    empty string, so it never silently collapses into some other
    unrelated all-modifier keyword's group."""
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", keyword.lower())
    core_words = [w for w in words if w not in _MODIFIER_WORDS]
    modifier_words = [w for w in words if w in _MODIFIER_WORDS]
    core_phrase = " ".join(core_words) if core_words else keyword.strip().lower()
    return core_phrase, modifier_words


# Same bound as the existing clustering/intent/page-matching AI calls
# elsewhere in this pipeline (site_audit.py) — keeps every AI call in the
# keyword pipeline working from the same top-N-by-volume candidate pool
# instead of adding a new, inconsistent cap. Also caps candidate-clustering
# prompt size — see _build_candidate_clusters' docstring for why this cap
# is load-bearing there, not just a nice-to-have.
_BUSINESS_THEME_CANDIDATE_CAP = 100

# External lead's Phase 1 routing spec (2026-09-21): a row whose relevance
# was genuinely judged AMBIGUOUS, or whose competitor_status marks it as a
# real competitor mention, must be routed to its own fixed bucket and never
# enter semantic (business-theme) clustering at all — not just excluded
# from consideration, not blended into a normal-looking topic cluster
# either. Confirmed exactly how the BharatBenz "Tata automotive overview"
# bug happened: an AI-fail-open "Unknown / Needs Review" row got clustered
# together with confident rows into a real-looking business-theme cluster
# instead of being visibly isolated, because nothing stopped an ambiguous
# row from competing for a normal cluster slot. Two fixed labels, never
# AI-named, so they can never collide with (or hide inside) a real topic
# cluster name.
_NEEDS_REVIEW_CLUSTER_LABEL = "Needs Review — Relevance Unconfirmed"
_COMPETITOR_ROUTE_CLUSTER_LABEL = "Competitor / Comparison Opportunities"
_COMPETITOR_ROUTE_STATUSES = {"Competitor Comparison Opportunity", "Relevant Competitor Intent"}
# _filter_keyword_rows stamps this exact reason on a row that was simply
# never sent to the AI classifier at all (outside its top-N-by-volume
# candidate pool) — a deliberate "leave as-is, don't judge it" case, not a
# real ambiguity verdict. Only a row the classifier (or its fail-open path)
# actually rendered a verdict on for is real AMBIGUOUS evidence; routing
# every uncapped low-volume row here too would silently stop them from
# ever being clustered at all, a real regression the pasted spec never asked for.
_UNJUDGED_REASON = "Keyword outside the classified candidate pool."


def _route_non_clusterable_rows(rows: list[dict]) -> list[dict]:
    """Splits `rows` into (routed rows, still-clusterable rows), stamping
    `cluster`/`cluster_status` directly on every routed row so it's never
    silently dropped — just permanently kept out of normal business-theme
    clustering. Returns the still-clusterable subset for the caller to pass
    into `_assign_business_themes`/`_build_candidate_clusters`; routed rows
    are left out of both (no business_theme is ever computed for them) but
    still flow through every later step in `build_final_keyword_clusters`
    (primary/secondary, existing-page matching, priority scoring) since
    those all key off `cluster` being non-empty and already tolerate an
    unset business_theme."""
    clusterable = []
    for r in rows:
        relevance = r.get("relevance_status")
        reason = r.get("relevance_reason")
        competitor_status = r.get("competitor_status")
        if relevance == "Unknown / Needs Review" and reason != _UNJUDGED_REASON:
            r["cluster"] = _NEEDS_REVIEW_CLUSTER_LABEL
            r["cluster_status"] = f"Needs Review: {reason}" if reason else "Needs Review"
            continue
        if competitor_status in _COMPETITOR_ROUTE_STATUSES:
            r["cluster"] = _COMPETITOR_ROUTE_CLUSTER_LABEL
            r["cluster_status"] = "Validated"
            continue
        clusterable.append(r)
    return clusterable


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _demand_proxy(r: dict) -> float:
    """Same reasoning as site_audit.py's _demand_proxy — a GSC-only row
    (spec sections 1-3 merge) has no real Semrush search_volume at all, so
    it falls back to its own real gsc_clicks for candidate-pool ranking
    only, never for cluster priority/demand math itself (_cluster_score
    stays search_volume-only). A row with real search_volume is unaffected."""
    return max(_num(r.get("search_volume")), _num(r.get("gsc_clicks")))


def _unique_keywords_by_volume(rows: list[dict]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for r in sorted(rows, key=_demand_proxy, reverse=True):
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
    strips modifiers to a core semantic_topic phrase within each bucket
    (spec sections 7-9 — see _strip_modifiers), then sub-splits each
    bucket's distinct topic phrases via AI into real per-page clusters.
    Sets `cluster` on every row with a non-empty keyword; a row is left
    unclustered (`cluster` = "") only when there isn't enough evidence to
    group it with anything — spec's explicit fallback, never a forced
    group.

    Modifier stripping happens BEFORE the AI ever sees anything (spec
    section 46's "CRITICAL ARCHITECTURE RULE" — semantic_topic/modifier
    must be real fields the clustering engine CONSUMES, not something a
    prompt asks the AI to figure out): "product", "product pricing",
    "product features", "product benefits" all strip to the same core
    phrase "product", so a bucket built entirely from modifier variants of
    ONE topic never reaches the AI at all — it's labeled by that shared
    core phrase directly, real evidence, not an AI guess. Only bucket
    topic phrases that are GENUINELY DIFFERENT after stripping go to the
    AI, which decides only real topic-level merges/splits (e.g. is "6x4
    truck" its own opportunity distinct from "heavy truck"), never a
    modifier-vs-topic judgment call the AI could get wrong.

    Bucketing and modifier-stripping both run over every row (cheap, no
    AI). The AI sub-split is still ONE batched call covering every
    bucket's distinct topic phrases at once, not one call per bucket —
    confirmed real (2026-09-19): a client with many distinct buckets
    turned into that many sequential Groq calls, each also queued behind
    Groq's shared per-minute token budget alongside this same report's
    other AI calls, chaining past the 15-minute stale-job threshold and
    killing the whole report. That single call's input is capped at the
    same _BUSINESS_THEME_CANDIDATE_CAP keywords business theme
    classification already used, for the same reason: bounded AI cost
    regardless of how many thousand keyword rows a real export has. A
    topic group whose representative keyword falls outside that cap still
    gets its bucket's theme/topic-based default cluster (or stays
    unclustered for an Unclassified bucket) — never silently dropped, just
    not AI-sub-split."""
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        keyword = (r.get("keyword") or "").strip()
        if not keyword:
            continue
        core_phrase, modifier_words = _strip_modifiers(keyword)
        r["semantic_topic"] = core_phrase.title() if core_phrase else keyword
        r["modifier"] = ", ".join(modifier_words) if modifier_words else ""
        r["attribute_type"] = _attribute_type(modifier_words)
        theme = (r.get("business_theme") or UNCLASSIFIED_THEME).strip() or UNCLASSIFIED_THEME
        # Spec section 43's `main_entity` — the dynamic business entity this
        # keyword belongs to (spec section 2's entity model: brand/product/
        # service/industry/etc.). business_theme is this pipeline's own real,
        # evidence-based entity classification (Business Theme Discovery,
        # already AI-classified from the client's actual site content) — the
        # closest already-computed field to a true entity extraction, reused
        # here rather than adding a second, redundant AI entity-extraction
        # call for the same keyword universe.
        r["main_entity"] = theme if theme != UNCLASSIFIED_THEME else None
        intent = (r.get("intent") or "").strip() or "Unknown Intent"
        category = (r.get("page_category") or "").strip() or "Unspecified Format"
        buckets.setdefault((theme, intent, category), []).append(r)

    # Global cap keeps the one batched AI call bounded regardless of how
    # many buckets or total rows exist — mirrors _assign_business_themes'
    # own top-N-by-volume cap.
    capped_keywords = set(_unique_keywords_by_volume(rows)[:_BUSINESS_THEME_CANDIDATE_CAP])

    # Topic groups within each bucket: rows sharing the same modifier-
    # stripped core phrase (case-insensitive) are structurally the SAME
    # candidate topic — grouped here BEFORE the AI call even exists, so
    # "product pricing" and "product features" are already one group by
    # the time any AI involvement happens.
    bucket_topic_groups: dict[tuple[str, str, str], dict[str, list[dict]]] = {}
    for bucket_key, bucket_rows in buckets.items():
        topic_groups: dict[str, list[dict]] = {}
        for r in bucket_rows:
            topic_groups.setdefault(r["semantic_topic"].lower(), []).append(r)
        bucket_topic_groups[bucket_key] = topic_groups

    def _representative(topic_rows: list[dict]) -> str:
        return max(topic_rows, key=lambda r: _num(r.get("search_volume"))).get("keyword")

    groups: list[tuple[str, list[str]]] = []
    for bucket_key, topic_groups in bucket_topic_groups.items():
        theme, intent, category = bucket_key
        if len(topic_groups) <= 1:
            continue  # whole bucket is one topic — no AI needed, see below
        representatives = [kw for kw in (_representative(tr) for tr in topic_groups.values()) if kw in capped_keywords]
        if len(representatives) <= 1:
            continue
        if theme != UNCLASSIFIED_THEME:
            context_label = f'business theme "{theme}", search intent "{intent}", recommended page format "{category}"'
        else:
            context_label = f'search intent "{intent}", recommended page format "{category}" (business theme unknown)'
        groups.append((context_label, representatives))

    sub_label_map: dict[str, str] = {}
    if groups:
        try:
            sub_label_map = generate_batched_candidate_clusters(groups)
        except Exception as e:
            logger.warning("Batched candidate clustering failed: %s", e)
            sub_label_map = {}

    label_owner: dict[str, tuple[str, str, str]] = {}

    for bucket_key, topic_groups in bucket_topic_groups.items():
        theme, intent, _category = bucket_key

        # A known business theme is itself real evidence a page-level group
        # exists, so a bucket the AI didn't (or couldn't) sub-split still
        # gets ONE coherent cluster labeled by theme — every keyword in it
        # already shares theme + intent + page format. An unknown
        # ("Unclassified") theme carries no such evidence, so with no AI
        # sub-split result those keywords stay unclustered rather than
        # forcing them into a fake shared group.
        theme_default_label = theme if theme != UNCLASSIFIED_THEME else None

        for topic_key, topic_rows in topic_groups.items():
            representative = _representative(topic_rows)

            if len(topic_groups) == 1:
                # Whole bucket already collapsed to one topic phrase —
                # modifier-stripping alone proved every keyword here is the
                # same candidate topic (spec section 46), so no AI call was
                # even made for it. Naming still prefers the known business
                # theme (same discipline as before this feature existed) —
                # a real theme name is more business-meaningful than a
                # literal keyword phrase, and an Unclassified theme still
                # means "not enough evidence to name a cluster" rather than
                # falling back to whatever text one keyword happens to be.
                label = theme_default_label
            else:
                label = sub_label_map.get(representative)
                if label and _is_catchall_cluster_name(label):
                    # Cluster Name Validation (spec steps 15-17): reject a
                    # catch-all/generic-only AI-returned name outright.
                    label = None
                label = label or theme_default_label

            for r in topic_rows:
                if not label or _is_catchall_cluster_name(label):
                    r["cluster"] = ""
                    r["cluster_status"] = "Unvalidated"
                    continue
                label_key = label
                if label_key in label_owner and label_owner[label_key] != bucket_key:
                    # Same short label text independently chosen for a
                    # genuinely different (theme, intent, category) bucket —
                    # disambiguate so it doesn't silently merge two different
                    # page opportunities under one displayed cluster name.
                    # This is the structural safety net that makes the
                    # single shared AI call above safe even if it ignores
                    # the "never combine different groups" instruction.
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


# Existing Page Matching action menu (Universal SEO Audit Engine spec,
# section 20) — the default, match-strength-driven action before any
# cannibalization override. "Consolidate"/"Redirect / Merge" are
# deliberately NOT here: those only make sense once _apply_cannibalization_
# check finds MULTIPLE clusters resolving to the same URL, not from a
# single cluster's own match strength alone.
_EXISTING_PAGE_ACTION_BY_STRENGTH = {
    "strong": "Optimize Existing Page",
    "partial": "Expand Existing Page",
    "weak": "Differentiate",
    "none": "Create New Page",
}


def _apply_existing_page_matching(rows: list[dict], site_audit_pages_rows: list[dict] | None) -> None:
    """Spec step 11 — matched at the CLUSTER level (every keyword in the
    cluster contributes signal), not per individual keyword, then the same
    match is applied to every row in that cluster so downstream consumers
    (PPT renderer, Content SEO Next Steps slide, cannibalization check
    below) all see one consistent existing-page decision per cluster —
    including existing_page_action, so the renderer never has to re-derive
    "update vs. create" itself (spec section 47: the PPT renderer is
    presentation-only, every decision must already be made upstream)."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)

    for _label, cluster_rows in clusters.items():
        keywords = [r.get("keyword") for r in cluster_rows if r.get("keyword")]
        match = match_existing_page_for_cluster(keywords, site_audit_pages_rows)
        strength = match["match_strength"] if match else "none"
        for r in cluster_rows:
            if match:
                r["existing_page_url"] = match["url"]
                r["existing_page_title"] = match["title"]
                r["existing_page_match_strength"] = strength
            else:
                r["existing_page_url"] = None
                r["existing_page_title"] = None
                r["existing_page_match_strength"] = "none"
            r["existing_page_action"] = _EXISTING_PAGE_ACTION_BY_STRENGTH[strength]


def _apply_cannibalization_check(rows: list[dict]) -> None:
    """Spec step 12 — flags when two or more DIFFERENT final clusters
    resolve to the same existing URL as a real (strong/partial) match,
    meaning that one page would otherwise be asked to satisfy two distinct
    page opportunities at once. A "weak"/no match never counts toward
    cannibalization — too little evidence that URL is really the target
    for either cluster.

    Also overrides existing_page_action for the affected clusters (spec
    section 21: "recommend Consolidation, Differentiation, Primary URL
    selection... do not automatically create another page") — the cluster
    with the strongest match (then highest combined volume) is picked as
    the URL's Primary owner and keeps "Consolidate"; every other cluster
    sharing that URL is told to differentiate or redirect/merge into the
    primary, instead of every sibling cluster independently reading
    "Optimize Existing Page" for the identical URL."""
    cluster_url: dict[str, str] = {}
    cluster_match_strength: dict[str, str] = {}
    cluster_volume: dict[str, float] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if not label:
            continue
        cluster_volume[label] = cluster_volume.get(label, 0.0) + _num(r.get("search_volume"))
        url = r.get("existing_page_url")
        strength = r.get("existing_page_match_strength")
        if url and strength in ("strong", "partial") and label not in cluster_url:
            cluster_url[label] = url
            cluster_match_strength[label] = strength

    url_clusters: dict[str, set[str]] = {}
    for label, url in cluster_url.items():
        url_clusters.setdefault(url, set()).add(label)

    # Primary URL selection: strongest match wins, combined search volume
    # breaks a tie — real, already-computed evidence, nothing invented.
    primary_cluster_for_url: dict[str, str] = {}
    for url, siblings in url_clusters.items():
        if len(siblings) > 1:
            primary_cluster_for_url[url] = max(
                siblings, key=lambda c: (cluster_match_strength.get(c) == "strong", cluster_volume.get(c, 0.0)),
            )

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
            primary_cluster = primary_cluster_for_url.get(url)
            if label == primary_cluster:
                r["existing_page_action"] = f"Consolidate — Primary URL for this topic ({url})"
            else:
                r["existing_page_action"] = f'Differentiate or Redirect / Merge into "{primary_cluster}"'
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


def _assign_evidence_confidence(rows: list[dict]) -> None:
    """Spec section 41 — evidence_confidence (High/Medium/Low) per row,
    summarizing how much REAL evidence backs its cluster: a known business
    theme (not Unclassified), a real ranking signal, and a strong/partial
    existing-page match are each independently corroborating evidence.
    Never a new judgment call — purely derived from fields every earlier
    step in this pipeline already set. An unclustered row (no real
    evidence to group it with anything) is always Low."""
    for r in rows:
        if not (r.get("cluster") or "").strip():
            r["evidence_confidence"] = "Low"
            continue
        theme_known = (r.get("business_theme") or UNCLASSIFIED_THEME) != UNCLASSIFIED_THEME
        strong_page_match = r.get("existing_page_match_strength") in ("strong", "partial")
        has_ranking = r.get("current_position") not in (None, "")
        signals = sum([theme_known, strong_page_match, has_ranking])
        r["evidence_confidence"] = "High" if signals >= 2 else ("Medium" if signals == 1 else "Low")


_EFFORT_BY_EXISTING_PAGE_ACTION = {
    "Optimize Existing Page": 0.2,
    "Expand Existing Page": 0.4,
    "Differentiate": 0.6,
    "Create New Page": 0.8,
}
_INTENT_STRENGTH = {
    "transactional": 1.0, "commercial": 1.0, "commercial investigation": 0.8,
    "comparison": 0.7, "local": 0.6, "informational": 0.4, "navigational": 0.2,
}


def _assign_priority_score(rows: list[dict]) -> None:
    """Spec section 40 — Unified Priority Model, using every earlier
    field this pipeline already computed (never a new judgment call, never
    volume alone). Additive: sets `priority_score`/`priority_factors`
    alongside the existing `cluster_priority` rank, which stays the
    slide's own actual sort key — see priority_model.py's module docstring
    for why this doesn't replace it."""
    for r in rows:
        clustered = bool((r.get("cluster") or "").strip())
        is_core = clustered and r.get("business_theme") and r.get("business_theme") == r.get("core_category")
        business_relevance = 1.0 if is_core else (0.6 if clustered else 0.0)

        volume = _num(r.get("search_volume"))
        search_demand = min(volume / 1000.0, 1.0)

        position = r.get("current_position")
        try:
            position_f = float(position) if position not in (None, "") else None
        except (TypeError, ValueError):
            position_f = None
        current_visibility = 1.0 if (position_f is not None and position_f <= 10) else (0.5 if position_f is not None else 0.0)
        if position_f is None:
            ranking_opportunity = 0.6  # not ranking at all — real opportunity, lower certainty than a known quick-win zone
        elif 4 <= position_f <= 20:
            ranking_opportunity = 1.0  # page-1/2 quick-win zone
        else:
            ranking_opportunity = 0.2  # already top-3 (little headroom) or very deep (long climb)

        intent_strength = _INTENT_STRENGTH.get((r.get("intent") or "").strip().lower(), 0.3)
        cpc = _num(r.get("cpc"))
        commercial_value = min(cpc / 10.0, 1.0) if cpc else intent_strength

        effort = _EFFORT_BY_EXISTING_PAGE_ACTION.get(r.get("existing_page_action") or "", 0.5)
        evidence_confidence = evidence_confidence_to_score(r.get("evidence_confidence"))

        result = compute_priority_score(
            business_relevance=business_relevance,
            search_demand=search_demand,
            current_visibility=current_visibility,
            ranking_opportunity=ranking_opportunity,
            intent_strength=intent_strength,
            commercial_value=commercial_value,
            conversion_potential=0.5,  # no per-keyword conversion/funnel data available — neutral, never guessed higher
            technical_severity=0.0,  # not applicable to a keyword row
            effort=effort,
            evidence_confidence=evidence_confidence,
        )
        r["priority_score"] = result["score"]
        r["priority_factors"] = result["factors"]


def _final_cluster_acceptance_check(rows: list[dict]) -> None:
    """Spec section 44 — Final Cluster Acceptance Check, enforced as an
    actual gate rather than left implicit across the earlier steps that
    already guarantee most of the checklist structurally (bucketing by
    (business_theme, intent, page_category) makes the one-page/over-merge/
    over-fragmentation/parent-child/intent/page-type items structural
    guarantees, not best-effort checks — see the module docstring).

    This pass is the single place that VERIFIES a final cluster before it
    can reach the PPT renderer and demotes (never silently keeps) any
    cluster failing a checkable item — catching a bug elsewhere in the
    pipeline letting an incomplete cluster slip through, not re-litigating
    decisions already correctly made upstream. Checked here specifically:
    cluster name isn't generic/catch-all (re-verified defensively — the
    disambiguation suffix a same-named cluster in a different bucket gets,
    e.g. "Pricing (Commercial)", is stripped first so it's judged on its
    real name), a primary keyword was actually selected, and existing-page
    matching actually ran (existing_page_action always gets set to
    something by _apply_existing_page_matching — an empty value here means
    that step never ran for this cluster, a real pipeline defect, not a
    legitimate "no data" case)."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)

    for label, cluster_rows in clusters.items():
        failures = []
        bare_name = label.split(" (")[0]
        if _is_catchall_cluster_name(bare_name):
            failures.append("generic/catch-all cluster name")
        if not any((r.get("primary_or_secondary") or "") == "Primary" for r in cluster_rows):
            failures.append("no primary keyword selected")
        if any(not (r.get("existing_page_action") or "").strip() for r in cluster_rows):
            failures.append("existing-page match never evaluated")
        if failures:
            logger.warning(
                "Cluster %r rejected at final acceptance check (%s) — %d keyword(s) demoted to unclustered",
                label, "; ".join(failures), len(cluster_rows),
            )
            for r in cluster_rows:
                r["cluster"] = ""
                r["cluster_status"] = f"Rejected: {'; '.join(failures)}"
                r["primary_or_secondary"] = None
                r["cluster_priority"] = None
                r["core_category"] = None


def build_final_keyword_clusters(
    rows: list[dict],
    client_name: str,
    client_description: str | None,
    site_audit_pages_rows: list[dict] | None,
) -> list[dict]:
    """Runs Non-Clusterable Routing (AMBIGUOUS/competitor-flavored rows —
    see `_route_non_clusterable_rows`) -> Business Theme -> Candidate
    Clustering -> Validation/Auto-Split (structural, see module docstring)
    -> Core Category + Cluster Prioritization -> Primary/Secondary ->
    Existing Page Matching -> Cannibalization Check -> Final Cluster
    Acceptance Check -> Evidence Confidence -> Unified Priority Score (spec
    section 40, additive — see priority_model.py), mutating and returning
    `rows`. Caller must already have
    `intent` and `page_category` set on every row (FINAL
    PIPELINE steps 3-4) before calling this — this function only reads
    those fields, never sets them. Ranking enrichment (current_position/
    current_url) may run before or after this call; nothing here reads or
    overwrites it except to help pick a primary keyword and score cluster
    priority. Fails safe: if every AI call in here fails, every row simply
    keeps `cluster` = "" (unclustered) and the caller's existing flat-table
    fallback renders instead — no cluster is ever hallucinated."""
    if not rows:
        return rows

    clusterable_rows = _route_non_clusterable_rows(rows)
    _assign_business_themes(clusterable_rows, client_name, client_description)
    _build_candidate_clusters(clusterable_rows)
    _assign_core_category_and_priority(rows)
    _select_primary_secondary(rows)
    _apply_existing_page_matching(rows, site_audit_pages_rows)
    _apply_cannibalization_check(rows)
    _final_cluster_acceptance_check(rows)
    _assign_evidence_confidence(rows)
    _assign_priority_score(rows)
    return rows
