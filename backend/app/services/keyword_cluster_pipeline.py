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
is called after intent + page_category AND ranking enrichment are already
set on every row (since 2026-09-24 — current_position/current_url feed the
primary-keyword pick, priority score and §23 existing-page matching, where
the page already ranking for a cluster is its target).

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
from collections import Counter
import re

from app.services.business_theme_service import UNCLASSIFIED_THEME, generate_business_themes
from app.services.keyword_relevance_service import build_page_index, is_junk_keyword, is_live_target_page, match_existing_page_for_cluster
from app.services.keyword_semantic_cluster_service import generate_phase2_candidate_clusters, generate_phase3_validated_clusters
from app.services.keyword_intelligence_service import (
    KeywordIntelligenceCache,
    annotate_keyword_rows,
    apply_cluster_intelligence,
    build_rule_groups,
    normalize_keyword,
    ranking_page_target,
    rule_group_name,
)
from app.services.keyword_strategy_service import score_summaries, summaries_from_keyword_rows
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
# real competitor mention, or whose relevance_status is a career/recruitment
# query, must be routed to its own fixed bucket and never enter semantic
# (business-theme) clustering at all — not just excluded from consideration,
# not blended into a normal-looking topic cluster either. Confirmed exactly
# how the BharatBenz "Tata automotive overview" bug happened: an AI-fail-open
# "Unknown / Needs Review" row got clustered together with confident rows
# into a real-looking business-theme cluster instead of being visibly
# isolated, because nothing stopped an ambiguous row from competing for a
# normal cluster slot. Three fixed labels, never AI-named, so they can never
# collide with (or hide inside) a real topic cluster name.
_NEEDS_REVIEW_CLUSTER_LABEL = "Needs Review — Relevance Unconfirmed"
_COMPETITOR_ROUTE_CLUSTER_LABEL = "Competitor / Comparison Opportunities"
_COMPETITOR_ROUTE_STATUSES = {"Competitor Comparison Opportunity", "Relevant Competitor Intent"}
# Career/recruitment rows (spec's CAREER routing) — _filter_keyword_rows
# (site_audit.py) is the only relevance-filter caller that keeps these
# instead of dropping them outright, specifically so they have a real
# destination here rather than vanishing silently.
_CAREER_ROUTE_CLUSTER_LABEL = "Jobs / Careers"
_CAREER_ROUTE_STATUS = "Career / Recruitment Query"
# Geo routing, per the user's explicit instruction (2026-09-21) to follow
# the pasted spec as literally as possible — the spec's own pseudocode
# computes geo_status in Phase 1 but only lists it as a hard clustering-
# skip in this one requested extension, not in its base rule set (only
# OFF_TOPIC/AMBIGUOUS/COMPETITOR/CAREER skip clustering there).
# assign_geo_status (keyword_relevance_service.py) only ever sets
# "Geographic Mismatch" or None — it never distinguishes IN_MARKET from
# NO_GEO_SIGNAL, so both map to "stays clusterable" here; only a real,
# positive mismatch routes away.
_GEO_ROUTE_CLUSTER_LABEL = "Geographic Mismatch / Out of Market"
_GEO_MISMATCH_STATUS = "Geographic Mismatch"
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
        if relevance == _CAREER_ROUTE_STATUS:
            r["cluster"] = _CAREER_ROUTE_CLUSTER_LABEL
            r["cluster_source"] = "routing"
            r["cluster_status"] = "Validated"
            continue
        if is_junk_keyword(r.get("keyword") or ""):
            # §21: a hash/URL/code fragment is never a page — and must never
            # name one (it became a Geopits cluster). Kept, in review.
            r["cluster"] = _NEEDS_REVIEW_CLUSTER_LABEL
            r["cluster_source"] = "routing"
            r["cluster_status"] = "Needs Review: not a real search query (code, hash or URL fragment)"
            continue
        if relevance == "Unknown / Needs Review" and reason != _UNJUDGED_REASON:
            r["cluster"] = _NEEDS_REVIEW_CLUSTER_LABEL
            r["cluster_source"] = "routing"
            r["cluster_status"] = f"Needs Review: {reason}" if reason else "Needs Review"
            continue
        if competitor_status in _COMPETITOR_ROUTE_STATUSES:
            r["cluster"] = _COMPETITOR_ROUTE_CLUSTER_LABEL
            r["cluster_source"] = "routing"
            r["cluster_status"] = "Validated"
            continue
        if r.get("geo_status") == _GEO_MISMATCH_STATUS:
            r["cluster"] = _GEO_ROUTE_CLUSTER_LABEL
            r["cluster_source"] = "routing"
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


def _annotate_semantic_fields(rows: list[dict]) -> dict[str, dict]:
    """Modifier stripping to a core semantic_topic phrase (spec sections
    7-9 — see _strip_modifiers) runs deterministically over every row,
    independent of whether clustering itself ends up manual or AI-driven —
    real evidence fields, never left to either judgment call. Returns
    {keyword text: row} for the caller's own lookups."""
    keyword_rows_by_text: dict[str, dict] = {}
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
        keyword_rows_by_text[keyword] = r
    return keyword_rows_by_text


def _apply_manual_clusters(rows: list[dict], manual_cluster_map: dict[str, dict]) -> None:
    """User's explicit instruction (2026-09-21): a client's own hand-built
    keyword clustering file (manual_keyword_cluster_parser.py) is the FIRST
    preference for Target Keywords clustering, with the AI Phase 2/3
    pipeline as the fallback only when no manual file has been uploaded —
    never a second opinion layered on top of a manual assignment that
    exists. `manual_cluster_map` is {keyword.lower(): {"cluster",
    "primary_or_secondary"}}. A keyword the manual file doesn't cover is
    left cluster="" (unclustered) — the same explicit "never force a
    group" fallback the AI pipeline itself uses, not silently guessed."""
    for r in rows:
        keyword = (r.get("keyword") or "").strip()
        entry = manual_cluster_map.get(keyword.lower()) if keyword else None
        if not entry or not entry.get("cluster"):
            r.setdefault("cluster", "")
            continue
        r["cluster"] = entry["cluster"]
        r["cluster_status"] = "Validated (Manual)"
        r["cluster_source"] = "manual"
        if entry.get("primary_or_secondary"):
            r["primary_or_secondary"] = entry["primary_or_secondary"]


def _build_candidate_clusters(
    rows: list[dict], keyword_rows_by_text: dict[str, dict], client_description: str | None = None,
    cache: KeywordIntelligenceCache | None = None,
) -> None:
    """Universal SEO Keyword Intelligence engine (2026-09-23) on top of the
    external lead's Phase 2/3 spec (2026-09-21).

    Step 1 — rule-based same-page groups over EVERY row
    (keyword_intelligence_service.build_rule_groups: core entity + intent
    family, guarded parent attach), deterministic and instant. This is what
    removed the old top-100-keywords cap: previously only the 100
    highest-volume keywords were ever clustered and everything else fell
    into "Other / Ungrouped" (Lumber: 1,831 of them).

    Step 2 — the SAME two AI calls as before (Phase 2 semantic grouping,
    Phase 3 validate/split/merge/name), but over the top
    _BUSINESS_THEME_CANDIDATE_CAP groups' representative keywords instead
    of the top 100 raw keywords, so each AI decision now covers a whole
    variant group. The AI result is cached per client (`cache`), keyed by
    the exact representative set + business context, so a regeneration on
    unchanged data makes zero AI calls.

    Step 3 — every group the AI didn't place (beyond the cap, AI
    unavailable, or a catch-all name the AI returned) becomes its own
    rule-based cluster, named from its shared core entity and marked
    cluster_source="rule" so confidence scoring and the slide say it wasn't
    AI-validated. No keyword is ever left in an "Ungrouped" bucket just
    because the AI didn't get to it."""
    groups = build_rule_groups(rows)
    if not groups:
        return
    group_by_rep: dict[str, dict] = {}
    for g in groups:
        rep_kw = (g["representative"].get("keyword") or "").strip()
        if rep_kw:
            group_by_rep[rep_kw] = g
    ai_groups = [g for g in groups if (g["representative"].get("keyword") or "").strip()][:_BUSINESS_THEME_CANDIDATE_CAP]

    keyword_meta = []
    for g in ai_groups:
        r = g["representative"]
        kw = r["keyword"].strip()
        theme = (r.get("business_theme") or UNCLASSIFIED_THEME).strip() or UNCLASSIFIED_THEME
        intent = (r.get("intent") or r.get("detected_intent") or "").strip() or "Unknown Intent"
        category = (r.get("page_category") or "").strip() or "Unspecified Format"
        source_cluster = None if theme == UNCLASSIFIED_THEME else f'business theme "{theme}", intent "{intent}", page format "{category}"'
        variants = [m.get("keyword") for m in g["rows"] if m is not r and m.get("keyword")][:3]
        keyword_meta.append({
            "keyword": kw, "search_volume": r.get("search_volume"),
            "keyword_difficulty": r.get("keyword_difficulty"), "source_cluster": source_cluster,
            "variants": variants, "group_size": len(g["rows"]),
        })

    final_clusters: list[dict] = []
    cache_key = KeywordIntelligenceCache.cluster_key(keyword_meta, client_description) if cache is not None else None
    cached = cache.get_clusters(cache_key) if cache is not None and keyword_meta else None
    if cached:
        final_clusters = cached
    elif keyword_meta:
        try:
            candidate_clusters, _unmapped = generate_phase2_candidate_clusters(keyword_meta, client_description)
        except Exception as e:
            logger.warning("Phase 2 candidate clustering raised: %s", e)
            candidate_clusters = {}

        if candidate_clusters:
            # Phase 3's own input shape wants each candidate cluster's
            # keywords enriched with intent (Step 2's intent/page-type
            # compatibility check), not just the bare keyword strings.
            enriched_candidates = {}
            for cid, info in candidate_clusters.items():
                kw_objs = []
                for kw in info["keywords"]:
                    r = keyword_rows_by_text.get(kw)
                    kw_objs.append({
                        "keyword": kw, "search_volume": r.get("search_volume") if r else None,
                        "keyword_difficulty": r.get("keyword_difficulty") if r else None,
                        "intent": (r.get("intent") or r.get("detected_intent")) if r else None,
                    })
                enriched_candidates[cid] = {**info, "keywords": kw_objs}
            try:
                final_clusters = generate_phase3_validated_clusters(enriched_candidates)
            except Exception as e:
                logger.warning("Phase 3 cluster validation raised: %s", e)
                final_clusters = []
            if cache is not None and final_clusters:
                cache.put_clusters(cache_key, final_clusters)

    placed: set[int] = set()
    seen_names: dict[str, int] = {}

    def _unique(name: str) -> str:
        seen_names[name] = seen_names.get(name, 0) + 1
        # Two independently-finalized clusters landed on the exact same
        # name — disambiguate so they don't silently merge on the slide.
        return name if seen_names[name] == 1 else f"{name} ({seen_names[name]})"

    for final in final_clusters:
        name = final.get("cluster_name") or ""
        status = final.get("cluster_status") or "Validated"
        if not name or _is_catchall_cluster_name(name):
            # Cluster Name Validation (spec steps 15-17): a catch-all/
            # generic AI name is rejected; its groups fall through to the
            # rule-based step below instead of vanishing.
            continue
        member_groups = [group_by_rep[kw] for kw in final.get("member_keywords") or [] if kw in group_by_rep]
        member_groups = [g for g in member_groups if id(g) not in placed]
        if not member_groups:
            continue
        display_name = _unique(name)
        primary_kw = final.get("primary_keyword")
        for g in member_groups:
            placed.add(id(g))
            for r in g["rows"]:
                r["cluster"] = display_name
                r["cluster_status"] = status
                r["cluster_source"] = "ai"
                r["primary_or_secondary"] = "Primary" if r.get("keyword") == primary_kw else "Secondary"

    for g in groups:
        if id(g) in placed:
            continue
        display_name = _unique(rule_group_name(g))
        for r in g["rows"]:
            r["cluster"] = display_name
            r["cluster_status"] = "Rule-based (not AI-validated)"
            r["cluster_source"] = "rule"


_RELEVANCE_RANK = {"Core Relevant": 3, "Relevant": 2, "Adjacent / Potential": 1}


def _select_primary_secondary(rows: list[dict]) -> None:
    """Primary/secondary selection AFTER clusters are final (spec step 9).
    Every row within one final cluster already shares business theme,
    search intent, and page category by construction, so those three
    factors can't differentiate a primary keyword within the cluster — the
    real, available differentiators are commercial intent, whether the
    client already has ranking signal (a realistic ranking opportunity),
    and search volume, in that priority order. Never just "highest
    volume wins" on its own.

    Fallback only: Phase 3's own validation call (spec Step 5, 2026-09-21)
    already makes this selection for a normally-clustered row (stamped by
    keyword_cluster_pipeline._build_candidate_clusters) — never automatically
    highest-volume, a strategist-level judgment call this heuristic can't
    fully replicate. A cluster where every row already has a real
    primary_or_secondary value is left untouched; this only fills the gap
    for a cluster with none set at all (e.g. the fixed Needs-Review/
    Competitor/Career routing buckets, which never go through Phase 3)."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)

    for _label, cluster_rows in clusters.items():
        if any((r.get("primary_or_secondary") or "").strip() == "Primary" for r in cluster_rows):
            continue
        # Universal SEO engine §19-20: business relevance, then intent fit
        # (the keyword matches the cluster's dominant intent family, so it
        # represents the page's core need), then commercial intent and a
        # real ranking signal — search volume is only the last tiebreaker.
        families = Counter(r.get("intent_family") for r in cluster_rows if r.get("intent_family"))
        dominant_family = families.most_common(1)[0][0] if families else None

        def _score(r: dict) -> tuple:
            intent = (r.get("intent") or "").lower()
            relevance = _RELEVANCE_RANK.get(r.get("relevance_status") or "", 0)
            intent_fit = 1 if dominant_family and r.get("intent_family") == dominant_family else 0
            # "Commercial Investigation" is commercial too ("data management
            # services" must beat "outsourcing data management" on demand).
            commercial = 1 if intent in ("commercial", "transactional", "commercial investigation") \
                or r.get("intent_family") == "Commercial" else 0
            has_ranking_signal = 1 if r.get("current_position") not in (None, "") or r.get("position") not in (None, "") else 0
            return (relevance, intent_fit, commercial, has_ranking_signal, _num(r.get("search_volume")))

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

    page_index = build_page_index(site_audit_pages_rows)
    dead_urls = {
        r.get("page_url") for r in site_audit_pages_rows or [] if r.get("page_url") and not is_live_target_page(r)
    }
    titles = {(r.get("page_url") or "").rstrip("/"): r.get("page_title") for r in site_audit_pages_rows or []}
    for _label, cluster_rows in clusters.items():
        keywords = [r.get("keyword") for r in cluster_rows if r.get("keyword")]
        # Clusters never mix page formats (a hard clustering boundary), so
        # the most common page_category is the cluster's own — passed so
        # the matcher can refuse a page whose type contradicts it.
        categories = Counter((r.get("page_category") or "") for r in cluster_rows if r.get("page_category"))
        page_category = categories.most_common(1)[0][0] if categories else None
        # §23 "existing rankings": the page Google already ranks for these
        # keywords beats any title/URL word overlap.
        ranking = ranking_page_target(cluster_rows, None, dead_urls)
        if ranking:
            match = {"url": ranking["url"], "title": titles.get(ranking["url"].rstrip("/")), "match_strength": ranking["match_strength"]}
        else:
            match = match_existing_page_for_cluster(keywords, site_audit_pages_rows, page_category, page_index=page_index)
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


# A core word in more than this share of all clusters (and in 3+) is a
# site-wide head term ("sql" on a database agency) — sharing it is not
# evidence two clusters are the same need.
_GENERIC_TOKEN_SHARE = 0.2


def _merge_same_page_clusters(rows: list[dict]) -> None:
    """§39/§41/§43 merge test, run after existing-page matching: two
    clusters that (a) resolved to the SAME existing page (strong/partial
    match), (b) share the same intent family, and (c) share a real topic
    word (not a site-wide head term) are one search need split in two —
    Geopits got "Index Sql — Guides" and "Indexing Database — Guides" as
    separate target pages for the same blog post. At least one side must be
    rule-based: two AI-validated clusters were already judged separate by
    the AI's own merge test and are left alone (their overlap still shows
    as cannibalization). The higher-demand cluster keeps its name, primary
    keyword and target decision."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label and r.get("cluster_source") != "routing":
            clusters.setdefault(label, []).append(r)
    if len(clusters) < 2:
        return

    raw_tokens = {
        label: {t for r in crow for t in normalize_keyword(r.get("keyword") or "")["core_tokens"]}
        for label, crow in clusters.items()
    }
    vocab = set().union(*raw_tokens.values())

    def _stem(t: str) -> str:
        # data-driven -ing lemma: "indexing" -> "index" only when "index"
        # itself occurs in this client's keywords.
        if t.endswith("ing") and len(t) > 5:
            for base in (t[:-3], t[:-3] + "e"):
                if base in vocab:
                    return base
        return t

    tokens = {label: {_stem(t) for t in toks} for label, toks in raw_tokens.items()}
    spread = Counter(t for toks in tokens.values() for t in toks)
    generic = {t for t, n in spread.items() if n >= 3 and n / len(clusters) > _GENERIC_TOKEN_SHARE}

    def _page_key(crow: list[dict]):
        sample = crow[0]
        if sample.get("existing_page_match_strength") not in ("strong", "partial") or not sample.get("existing_page_url"):
            return None
        families = Counter(r.get("intent_family") for r in crow if r.get("intent_family"))
        family = families.most_common(1)[0][0] if families else None
        return (sample["existing_page_url"].rstrip("/").lower(), family)

    by_page: dict[tuple, list[str]] = {}
    for label, crow in clusters.items():
        key = _page_key(crow)
        if key:
            by_page.setdefault(key, []).append(label)

    demand = {label: sum(_demand_proxy(r) for r in crow) for label, crow in clusters.items()}
    for labels in by_page.values():
        if len(labels) < 2:
            continue
        labels.sort(key=lambda lb: (-demand[lb], lb))
        merged: list[str] = []
        for label in labels:
            target = next(
                (m for m in merged
                 if (tokens[m] & tokens[label]) - generic
                 and "rule" in {clusters[m][0].get("cluster_source"), clusters[label][0].get("cluster_source")}),
                None,
            )
            if target is None:
                merged.append(label)
                continue
            winner = clusters[target]
            for r in clusters[label]:
                r["cluster"] = target
                r["cluster_source"] = winner[0].get("cluster_source")
                r["cluster_status"] = winner[0].get("cluster_status")
                r["primary_or_secondary"] = "Secondary"
                for key in ("existing_page_url", "existing_page_title", "existing_page_match_strength",
                            "existing_page_action", "cluster_priority", "core_category"):
                    r[key] = winner[0].get(key)
            winner.extend(clusters[label])


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
    # Universal SEO engine §24: cannibalization needs pages genuinely
    # competing for one need — a single-keyword rule-based group (the
    # engine now clusters every keyword) sharing a page with a real cluster
    # is not evidence of that, so it never counts as a competing cluster.
    cluster_sizes = Counter((r.get("cluster") or "").strip() for r in rows)
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if not label:
            continue
        if r.get("cluster_source") == "rule" and cluster_sizes[label] < 2:
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
    manual_cluster_map: dict[str, dict] | None = None,
    cache: KeywordIntelligenceCache | None = None,
) -> list[dict]:
    """Runs (Manual Clustering, when `manual_cluster_map` is non-empty — see
    `_apply_manual_clusters` — OR, as the fallback, Non-Clusterable Routing
    [AMBIGUOUS/competitor-flavored rows, see `_route_non_clusterable_rows`]
    -> Business Theme -> Candidate Clustering) -> Validation/Auto-Split
    (structural, see module docstring) -> Core Category + Cluster
    Prioritization -> Primary/Secondary -> Existing Page Matching ->
    Cannibalization Check -> Final Cluster Acceptance Check -> Evidence
    Confidence -> Unified Priority Score (spec section 40, additive — see
    priority_model.py), mutating and returning `rows`.

    `manual_cluster_map` (site_audit.py, built from a client's own
    "keyword_cluster_manual" uploads) is the user's explicit first
    preference (2026-09-21): when present, it's authoritative and the AI
    Phase 2/3 pipeline never runs at all — not a second opinion layered on
    top, strictly a fallback for when no manual file exists.

    Caller must already have `intent` and `page_category` set on every row
    (FINAL PIPELINE steps 3-4) before calling this — this function only
    reads those fields, never sets them. Ranking enrichment
    (current_position/current_url) may run before or after this call;
    nothing here reads or overwrites it except to help pick a primary
    keyword and score cluster priority. Fails safe: if every AI call in
    here fails (AI-fallback mode only), every row simply keeps `cluster` =
    "" (unclustered) and the caller's existing flat-table fallback renders
    instead — no cluster is ever hallucinated."""
    if not rows:
        return rows

    keyword_rows_by_text = _annotate_semantic_fields(rows)
    annotate_keyword_rows(rows)
    if manual_cluster_map:
        _apply_manual_clusters(rows, manual_cluster_map)
    else:
        clusterable_rows = _route_non_clusterable_rows(rows)
        _assign_business_themes(clusterable_rows, client_name, client_description)
        _build_candidate_clusters(clusterable_rows, keyword_rows_by_text, client_description, cache)
    _assign_core_category_and_priority(rows)
    _select_primary_secondary(rows)
    _apply_existing_page_matching(rows, site_audit_pages_rows)
    _merge_same_page_clusters(rows)
    _apply_cannibalization_check(rows)
    _final_cluster_acceptance_check(rows)
    _assign_evidence_confidence(rows)
    _assign_priority_score(rows)
    apply_cluster_intelligence(rows)
    # §31/§66-K: opportunity score + roadmap tier per keyword and cluster,
    # for the Content SEO / page-map / roadmap consumers downstream.
    score_summaries(summaries_from_keyword_rows(rows))
    for r in rows:
        # Internal grouping keys — never part of the row data handed on.
        r.pop("_core_key", None)
        r.pop("_core_tokens", None)
    return rows
