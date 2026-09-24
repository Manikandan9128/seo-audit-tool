"""Universal SEO Keyword engine — strategy layer (2026-09-24). Everything
here runs AFTER clusters and their target pages are decided, is
deterministic (no AI call, no extra report time) and works the same for
the client's manual cluster sheet and the automatic Semrush/GSC path:

  §31/§33  Opportunity Score per keyword and cluster (configurable weights,
           search volume only one signal among eight)
  §66-K    Priority roadmap tier: High / Medium / Low / Human Review
  §18      Topic hierarchy: Parent topic -> cluster (page)
  §38      Topical coverage per parent topic: Strong / Partial / Weak /
           Missing, plus which intents have no page yet
  §37      Internal-linking relationships between clusters of one topic
  §56      Page map: URL -> clusters, primary keywords, action, risk

Clusters are passed in as plain "cluster summaries" so both paths share
one implementation:
  {"name", "rows" (annotated keyword rows), "target_url", "match_strength",
   "confidence", "confidence_level", "primary_keyword", "dominant_family",
   "recommended_action", "strategic": 0-1 | None}
"""

import math
from collections import Counter

from app.services.keyword_intelligence_service import (
    _EXCLUDED_RELEVANCE_STATUSES,
    _demand,
    _num,
    normalize_keyword,
    singularize,
)

# §31 starting model. The spec says these "must be configurable" and are
# "not universal SEO laws" — change them here, nowhere else. SERP
# opportunity has no data source yet (Semrush API deferred): its weight is
# redistributed proportionally over the others rather than scored as a
# made-up neutral value, and every result says so in its factors.
OPPORTUNITY_WEIGHTS = {
    "business_relevance": 0.30,
    "intent_value": 0.20,
    "search_demand": 0.15,
    "serp_opportunity": 0.10,
    "ranking_feasibility": 0.10,
    "competitor_gap": 0.05,
    "existing_authority": 0.05,
    "strategic_importance": 0.05,
}

_RELEVANCE_VALUE = {
    "Core Relevant": 1.0, "Relevant": 0.85, "Validated (Manual)": 0.8,
    "Competitor Comparison Opportunity": 0.7, "Relevant Competitor Intent": 0.7,
    "Adjacent / Potential": 0.5, "Unknown / Needs Review": 0.5,
}
_INTENT_VALUE = {
    "Transactional": 1.0, "Commercial": 0.85, "Commercial Investigation": 0.85, "Comparison": 0.8,
    "Local": 0.8, "Informational": 0.45, "Navigational": 0.2,
}

# Roadmap tier thresholds on the 0-100 cluster opportunity.
_HIGH_OPPORTUNITY = 62
_MEDIUM_OPPORTUNITY = 45


def _relevance_value(r: dict) -> float:
    status = r.get("relevance_status")
    if status in _EXCLUDED_RELEVANCE_STATUSES:
        return 0.1
    return _RELEVANCE_VALUE.get(status or "", 0.6)  # no verdict = not evidence either way


def _competitor_gap(r: dict) -> float | None:
    """1.0 when a compared competitor ranks top-10 and the client doesn't;
    None (no evidence) when the row carries no competitor positions."""
    positions = r.get("domain_positions") or {}
    if not positions:
        return None
    own = _num(r.get("current_position"))
    competitor_top10 = any(0 < _num(p) <= 10 for p in positions.values())
    if not competitor_top10:
        return 0.3
    return 0.2 if 0 < own <= 10 else 1.0


def _existing_authority(r: dict) -> float:
    pos = _num(r.get("current_position"))
    if pos <= 0:
        return 0.0
    if pos <= 10:
        return 1.0
    if pos <= 20:
        return 0.7
    return 0.4 if pos <= 50 else 0.2


def keyword_opportunity(r: dict, max_demand: float, strategic: float | None = None) -> dict:
    """§31 Opportunity Score (0-100) for one keyword, with its per-factor
    breakdown. A factor with no evidence (SERP always; competitor gap when
    no gap data; strategic importance when no core-category signal) is
    left out and its weight shared by the rest — never guessed."""
    kd = r.get("keyword_difficulty")
    factors = {
        "business_relevance": _relevance_value(r),
        "intent_value": _INTENT_VALUE.get(r.get("detected_intent") or "", 0.6),
        # log scale: a 100/mo keyword isn't worth 1/1000th of a 100k one
        "search_demand": (math.log10(1 + _demand(r)) / math.log10(1 + max_demand)) if max_demand > 0 else 0.0,
        "serp_opportunity": None,
        "ranking_feasibility": (100 - _num(kd)) / 100 if kd not in (None, "") else None,
        "competitor_gap": _competitor_gap(r),
        "existing_authority": _existing_authority(r),
        "strategic_importance": strategic,
    }
    used = {k: v for k, v in factors.items() if v is not None}
    total_weight = sum(OPPORTUNITY_WEIGHTS[k] for k in used)
    score = sum(OPPORTUNITY_WEIGHTS[k] * max(0.0, min(1.0, v)) for k, v in used.items()) / total_weight if total_weight else 0
    return {"score": round(score * 100), "factors": factors}


def cluster_opportunity(rows: list[dict], max_demand: float, strategic: float | None = None) -> int:
    """A cluster's opportunity = the mean of its best five keywords (a page
    is judged by the searches it can realistically win, not diluted by a
    long tail of weak variants)."""
    scores = sorted((keyword_opportunity(r, max_demand, strategic)["score"] for r in rows), reverse=True)[:5]
    return round(sum(scores) / len(scores)) if scores else 0


def roadmap_priority(
    opportunity: int, confidence_level: str | None, rows: list[dict], high_cutoff: int = _HIGH_OPPORTUNITY,
) -> str:
    """§66-K / §62: Human Review when the evidence is weak (Low confidence,
    or most keywords flagged), else High / Medium / Low by opportunity.
    `high_cutoff` lets the caller keep "High" for the genuinely strongest
    clusters of THIS client (a roadmap where everything is High is no
    roadmap)."""
    flagged = sum(1 for r in rows if r.get("relevance_status") in _EXCLUDED_RELEVANCE_STATUSES)
    if confidence_level == "Low" or (rows and flagged / len(rows) > 0.5):
        return "Human Review"
    if opportunity >= max(_HIGH_OPPORTUNITY, high_cutoff):
        return "High"
    return "Medium" if opportunity >= _MEDIUM_OPPORTUNITY else "Low"


def score_summaries(summaries: list[dict]) -> None:
    """Stamps opportunity (0-100) + roadmap_priority on every summary, and
    opportunity_score on every keyword row, in place."""
    max_demand = max((_demand(r) for s in summaries for r in s["rows"]), default=0.0)
    for s in summaries:
        for r in s["rows"]:
            r["opportunity_score"] = keyword_opportunity(r, max_demand, s.get("strategic"))["score"]
        s["opportunity"] = cluster_opportunity(s["rows"], max_demand, s.get("strategic"))
    # "High" = above the absolute floor AND in this client's top third.
    ranked = sorted((s["opportunity"] for s in summaries), reverse=True)
    top_third_cutoff = ranked[max(0, math.ceil(len(ranked) / 3) - 1)] if ranked else _HIGH_OPPORTUNITY
    for s in summaries:
        s["roadmap_priority"] = roadmap_priority(s["opportunity"], s.get("confidence_level"), s["rows"], top_third_cutoff)
        for r in s["rows"]:
            r["cluster_opportunity"] = s["opportunity"]
            r["roadmap_priority"] = s["roadmap_priority"]


# ---------------------------------------------------------------------------
# §18 / §38 / §37 — topic model
# ---------------------------------------------------------------------------

def _cluster_tokens(s: dict) -> tuple[Counter, float, Counter]:
    """Demand-weighted core-entity tokens of a cluster's keywords, the
    cluster's total demand (each keyword counted once), and how much demand
    has each token as its HEAD noun (last core word: "school bus" -> bus)."""
    weights: Counter = Counter()
    heads: Counter = Counter()
    total = 0.0
    for r in s["rows"]:
        w = max(_demand(r), 1.0)
        total += w
        norm = normalize_keyword(r.get("keyword") or "")
        for t in norm["core_tokens"]:
            weights[t] += w
        words = norm["core_phrase"].split()
        if words:
            heads[singularize(words[-1])] += w
    return weights, total, heads


def _parent_token(tokens: Counter, total: float, cluster_spread: Counter, heads: Counter | None = None) -> str | None:
    """The cluster's parent entity: among tokens present in keywords
    carrying at least half of the cluster's demand, the one shared by the
    most clusters overall — "truck" over "part" for a Truck Parts cluster,
    so it sits under Truck."""
    if not total or not tokens:
        return None
    core = [t for t, w in tokens.items() if w / total >= 0.5 and len(t) > 2]
    if not core:
        core = [tokens.most_common(1)[0][0]]
    heads = heads or Counter()
    # Ties (e.g. "school bus": both words in one cluster) go to the head
    # noun, then alphabetically — never to set iteration order.
    return max(core, key=lambda t: (cluster_spread[t], heads[t], tokens[t], t))


def _page_label(s: dict) -> str:
    return s["target_url"] if s.get("target_url") else f"new page: {s['name']}"


_LINK_RELATION = {
    "Informational": "Guide → Product/Service",
    "Comparison": "Comparison → Product/Service",
    "Local": "Location → Service",
}


def build_topic_model(summaries: list[dict], max_parents: int = 8) -> list[dict]:
    """§18 hierarchy + §38 coverage + §37 internal links. Returns parents
    sorted by total opportunity:
      {"parent", "clusters": [summary names], "coverage": Strong|Partial|
       Weak|Missing, "covered": n, "total": n, "intents_without_page":
       [families], "links": [{"from", "to", "relation"}], "demand"}"""
    token_sets = {s["name"]: _cluster_tokens(s) for s in summaries}
    spread: Counter = Counter()
    for tokens, _total, _heads in token_sets.values():
        spread.update(set(tokens))
    parents: dict[str, list[dict]] = {}
    for s in summaries:
        tokens, total, heads = token_sets[s["name"]]
        parent = _parent_token(tokens, total, spread, heads) or s["name"].lower()
        parents.setdefault(parent, []).append(s)

    model = []
    for parent, members in parents.items():
        covered = [s for s in members if s.get("match_strength") in ("strong", "partial")]
        share = len(covered) / len(members)
        coverage = "Strong" if share >= 0.75 else "Partial" if share >= 0.4 else "Weak" if covered else "Missing"
        families = {s.get("dominant_family") or "Commercial" for s in members}
        covered_families = {s.get("dominant_family") or "Commercial" for s in covered}

        # §37: a parent hub exists only when a cluster really targets the
        # bare parent entity ("truck"/"trucks") — every other cluster of the
        # topic is its subtopic and links up to it. With no such hub,
        # sibling product clusters are NOT linked to each other just for
        # sharing a word (§37 "not solely because keywords share words");
        # only guide/comparison/location pages link to the strongest
        # product page of the topic, the relationship the spec names.
        commercial = [s for s in members if (s.get("dominant_family") or "Commercial") == "Commercial"]
        hub = max(
            (s for s in commercial if any(normalize_keyword(r.get("keyword") or "")["core_key"] == parent for r in s["rows"])),
            key=lambda s: sum(_demand(r) for r in s["rows"]), default=None,
        )
        product = hub or max(commercial, key=lambda s: sum(_demand(r) for r in s["rows"]), default=None)
        links = []
        for s in members:
            family = s.get("dominant_family") or "Commercial"
            if hub is not None and s is not hub:
                to, relation = hub, _LINK_RELATION.get(family, "Subtopic → Parent hub")
            elif family in _LINK_RELATION and product is not None and s is not product:
                to, relation = product, _LINK_RELATION[family]
            else:
                continue
            if s.get("target_url") and s.get("target_url") == to.get("target_url"):
                # Both clusters resolved to the SAME existing page — a
                # self-link is no link; the page map flags this overlap.
                continue
            links.append({
                "from": _page_label(s), "to": _page_label(to), "relation": relation,
                "from_cluster": s["name"], "to_cluster": to["name"],
            })
        model.append({
            "parent": parent.title(),
            "clusters": [s["name"] for s in sorted(members, key=lambda s: -(s.get("opportunity") or 0))],
            "coverage": coverage, "covered": len(covered), "total": len(members),
            "intents_without_page": sorted(families - covered_families),
            "links": links,
            "opportunity": max((s.get("opportunity") or 0) for s in members),
            "demand": sum(_demand(r) for s in members for r in s["rows"]),
        })
    model.sort(key=lambda p: (-p["opportunity"], -p["demand"]))
    return model[:max_parents]


# ---------------------------------------------------------------------------
# §56 — page map
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Human Review": 3}


def build_page_map(summaries: list[dict]) -> list[dict]:
    """One entry per target page — an existing URL (strong/partial match)
    or a new page per cluster with none. Two or more clusters on one URL
    is flagged as a cannibalization risk (§24/§56). Sorted by priority."""
    by_url: dict[str, list[dict]] = {}
    for s in summaries:
        key = s["target_url"] if s.get("target_url") else f"new:{s['name']}"
        by_url.setdefault(key, []).append(s)
    pages = []
    for key, members in by_url.items():
        best = min(members, key=lambda s: (_PRIORITY_ORDER.get(s.get("roadmap_priority"), 9), -(s.get("opportunity") or 0)))
        pages.append({
            "url": None if key.startswith("new:") else key,
            "clusters": [s["name"] for s in members],
            "primary_keywords": [s.get("primary_keyword") for s in members if s.get("primary_keyword")],
            "intent": best.get("dominant_family") or "Commercial",
            "action": best.get("recommended_action"),
            "priority": best.get("roadmap_priority"),
            "opportunity": best.get("opportunity") or 0,
            "cannibalization_risk": len(members) > 1,
        })
    pages.sort(key=lambda p: (_PRIORITY_ORDER.get(p["priority"], 9), -p["opportunity"]))
    return pages


def build_keyword_strategy(summaries: list[dict]) -> dict | None:
    """Scores the summaries, then builds the topic model and page map.
    None when there are no clusters to reason about."""
    summaries = [s for s in summaries if s.get("rows")]
    if not summaries:
        return None
    score_summaries(summaries)
    return {
        "topics": build_topic_model(summaries),
        "page_map": build_page_map(summaries),
        "roadmap": {
            tier: [s["name"] for s in sorted(summaries, key=lambda s: -(s.get("opportunity") or 0))
                   if s["roadmap_priority"] == tier]
            for tier in ("High", "Medium", "Low", "Human Review")
        },
        "weights": dict(OPPORTUNITY_WEIGHTS),
    }


# ---------------------------------------------------------------------------
# Summaries from each path
# ---------------------------------------------------------------------------

def summaries_from_keyword_rows(rows: list[dict]) -> list[dict]:
    """Automatic path (and the pipeline's own manual-map run): one summary
    per real cluster. Routing buckets (jobs, out-of-market, needs-review,
    competitor/comparison holding pen) are exclusions, never pages."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label and r.get("cluster_source") != "routing":
            clusters.setdefault(label, []).append(r)
    summaries = []
    for label, crow in clusters.items():
        sample = crow[0]
        primary = next((r for r in crow if r.get("primary_or_secondary") == "Primary"), None) \
            or max(crow, key=_demand)
        families = Counter(r.get("intent_family") for r in crow if r.get("intent_family"))
        strength = sample.get("existing_page_match_strength")
        priority_rank = sample.get("cluster_priority")
        summaries.append({
            "name": label, "rows": crow,
            "target_url": sample.get("existing_page_url") if strength in ("strong", "partial") else None,
            "match_strength": strength,
            "confidence": sample.get("cluster_confidence"),
            "confidence_level": sample.get("cluster_confidence_level"),
            "primary_keyword": primary.get("keyword"),
            "dominant_family": families.most_common(1)[0][0] if families else "Commercial",
            "recommended_action": sample.get("recommended_action"),
            # Core-category rank from the pipeline (1 = the client's core
            # commercial topic) is the only strategic signal we have.
            "strategic": (1.0 if priority_rank <= 3 else 0.5) if isinstance(priority_rank, int) else None,
        })
    return summaries


def summaries_from_manual_clusters(clusters: list[dict], keyword_ranking: dict[str, tuple] | None = None) -> list[dict]:
    """Manual-sheet path: one summary per selected (already enriched)
    cluster, over annotated COPIES of its keywords — the sheet's own values
    are never touched."""
    from app.services.keyword_intelligence_service import annotate_keyword_rows

    summaries = []
    for c in clusters:
        rows = [dict(k) for k in c.get("keywords") or []]
        annotate_keyword_rows(rows)
        for r in rows:
            r.pop("_core_key", None)
            r.pop("_core_tokens", None)
            if keyword_ranking and r.get("current_position") in (None, ""):
                pos, url = keyword_ranking.get((r.get("keyword") or "").strip().lower(), (None, None))
                if pos not in (None, ""):
                    r["current_position"], r["current_url"] = pos, url
        families = Counter(r.get("intent_family") for r in rows if r.get("intent_family"))
        summaries.append({
            "name": c["cluster"], "rows": rows,
            "target_url": c.get("target_url"), "match_strength": c.get("match_strength"),
            "confidence": c.get("confidence"), "confidence_level": c.get("confidence_level"),
            "primary_keyword": c.get("primary_keyword"),
            "dominant_family": families.most_common(1)[0][0] if families else "Commercial",
            "recommended_action": c.get("recommended_action"),
            "strategic": None,
            "_cluster": c,
        })
    return summaries


def apply_strategy_to_manual_clusters(clusters: list[dict], keyword_ranking: dict[str, tuple] | None = None) -> dict | None:
    """Builds the strategy for the manual path and writes each cluster's
    opportunity + roadmap_priority back onto it (for its slide)."""
    summaries = summaries_from_manual_clusters(clusters, keyword_ranking)
    strategy = build_keyword_strategy(summaries)
    for s in summaries:
        c = s.pop("_cluster")
        c["opportunity"] = s.get("opportunity")
        c["roadmap_priority"] = s.get("roadmap_priority")
    return strategy
