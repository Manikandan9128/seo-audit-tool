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
    display_phrase,
    normalize_keyword,
    singularize,
    smart_title,
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


def _feasibility(kd, site_authority, topical: float | None = None) -> float | None:
    """§32: KD is not an absolute barrier — it's read against the site's
    own authority (DR/Authority Score, 0-100) when known: a DR-60 site
    finds KD 50 far easier than a DR-20 site does."""
    if kd in (None, ""):
        return None
    base = (100 - _num(kd)) / 100
    if site_authority not in (None, "") and _num(site_authority) > 0:
        base += (_num(site_authority) - _num(kd)) / 200
    # §32 topical authority: pages the site already has on this topic
    # make it easier to rank (up to +0.15 at 10+ pages).
    if topical:
        base += 0.15 * _num(topical)
    return max(0.0, min(1.0, base))


def keyword_opportunity(r: dict, max_demand: float, strategic: float | None = None, site_authority=None) -> dict:
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
        "ranking_feasibility": _feasibility(kd, site_authority, r.get("topical_authority")),
        "competitor_gap": _competitor_gap(r),
        "existing_authority": _existing_authority(r),
        "strategic_importance": strategic,
    }
    used = {k: v for k, v in factors.items() if v is not None}
    total_weight = sum(OPPORTUNITY_WEIGHTS[k] for k in used)
    score = sum(OPPORTUNITY_WEIGHTS[k] * max(0.0, min(1.0, v)) for k, v in used.items()) / total_weight if total_weight else 0
    return {"score": round(score * 100), "factors": factors}


def cluster_opportunity(rows: list[dict], max_demand: float, strategic: float | None = None, site_authority=None) -> int:
    """A cluster's opportunity = the mean of its best five keywords (a page
    is judged by the searches it can realistically win, not diluted by a
    long tail of weak variants)."""
    scores = sorted((keyword_opportunity(r, max_demand, strategic, site_authority)["score"] for r in rows), reverse=True)[:5]
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


def score_summaries(summaries: list[dict], site_authority=None) -> None:
    """Stamps opportunity (0-100) + roadmap_priority on every summary, and
    opportunity_score on every keyword row, in place."""
    max_demand = max((_demand(r) for s in summaries for r in s["rows"]), default=0.0)
    for s in summaries:
        for r in s["rows"]:
            r["opportunity_score"] = keyword_opportunity(r, max_demand, s.get("strategic"), site_authority)["score"]
        s["opportunity"] = cluster_opportunity(s["rows"], max_demand, s.get("strategic"), site_authority)
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


def build_topic_model(summaries: list[dict], max_parents: int | None = 8) -> list[dict]:
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
            "parent": smart_title(parent),
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
            "outliers": sample.get("cluster_outliers") or [],
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
            "outliers": c.get("outliers") or [],
            "strategic": None,
            "_cluster": c,
        })
    return summaries


def apply_strategy_to_manual_clusters(
    clusters: list[dict], keyword_ranking: dict[str, tuple] | None = None, context: dict | None = None,
) -> dict | None:
    """Builds the full strategy for the manual path and writes each
    cluster's opportunity, roadmap priority, cluster/page type, content-gap
    type and §53 decision back onto it (for its slide)."""
    summaries = summaries_from_manual_clusters(clusters, keyword_ranking)
    strategy = build_full_keyword_strategy(summaries, context)
    for s in summaries:
        c = s.pop("_cluster")
        for key in ("opportunity", "roadmap_priority", "cluster_type", "page_type", "content_gap_type",
                    "decision", "decision_reason", "parent_topic", "cluster_id", "business_rule", "audience",
                    "geography", "difficulty", "search_demand", "business_value"):
            c[key] = s.get(key)
        if s.get("primary_keyword") and s["primary_keyword"] != c.get("primary_keyword"):
            c["primary_keyword"] = s["primary_keyword"]
            c["user_need"] = next((r.get("user_need") for r in s["rows"] if r.get("keyword") == s["primary_keyword"]),
                                  c.get("user_need"))
        if s.get("page_type"):
            c["recommended_page_type"] = s["page_type"]
    return strategy


# ===========================================================================
# 2026-09-24 spec-coverage additions — see docs/keyword_engine_spec_coverage.md
# ===========================================================================

import re as _re

from app.services.keyword_relevance_service import classify_page_type, is_live_target_page, keyword_brand_type

_TIER_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Human Review": 3}


def _majority(rows: list[dict], key: str, value) -> bool:
    return bool(rows) and sum(1 for r in rows if r.get(key) == value) / len(rows) > 0.5


def _family_share(rows: list[dict]) -> tuple[str, float]:
    families = Counter(r.get("intent_family") or "Commercial" for r in rows)
    if not families:
        return "Commercial", 1.0
    family, n = families.most_common(1)[0]
    return family, n / len(rows)


# §3 / §66-A website model ---------------------------------------------------

_MODEL_SIGNALS = [
    ("SaaS / Subscription", _re.compile(r"/(pricing|plans|signup|sign-up|free-trial|trial|demo|request-a-demo|login|app)(/|$)")),
    # E-commerce needs real shop mechanics. A /products/<name> page alone
    # is a company presenting its own products (Geopits: /products/geoops,
    # its own software) — that's "Product", not an online store.
    ("E-commerce", _re.compile(r"/(cart|basket|checkout|shop|store|collections?|add-to-cart|my-cart)(/|$)")),
    ("Product", _re.compile(r"/(products?|product-range|models?)/[^/]+")),
    ("Service", _re.compile(r"/(services?|solutions?|consulting|what-we-do)(/|$)")),
    ("Local / Multi-location", _re.compile(r"/(locations?|branches|dealers?|dealer-locator|stores?|near-me|service-cent(er|re)s?)(/|$)")),
    ("Marketplace", _re.compile(r"/(marketplace|sellers?|vendors?|listings?)(/|$)")),
    ("B2B", _re.compile(r"/(industries|industry|enterprise|partners?|case-studies|customers)(/|$)")),
]


def infer_business_model(site_audit_pages_rows: list[dict] | None, company_overview: dict | None = None) -> dict:
    """§3 business model(s), inferred from the site's own URL structure
    (plus the company overview's target market) — never forced into one
    category. Returns {"models": [...], "evidence": [...]}."""
    paths = []
    for row in site_audit_pages_rows or []:
        url = (row.get("page_url") or "").lower()
        if url and is_live_target_page(row):
            paths.append(_re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+", "", url) or "/")
    models, evidence = [], []
    for label, pattern in _MODEL_SIGNALS:
        hits = [p for p in paths if pattern.search(p)]
        if hits:
            models.append(label)
            evidence.append(f"{label}: {len(hits)} page(s) like {hits[0]}")
    blog = [p for p in paths if classify_page_type("https://x" + p) == "blog"]
    if paths and len(blog) / len(paths) >= 0.5:
        models.append("Publisher / Content-led")
        evidence.append(f"Publisher: {len(blog)} of {len(paths)} crawled pages are articles")
    market = " ".join(str(v) for v in [(company_overview or {}).get("target_market"),
                                       *((company_overview or {}).get("primary_buyers") or [])] if v).lower()
    if _re.search(r"\b(enterprise|business|b2b|mid-market|smb|companies|cto|cio|procurement)\b", market) and "B2B" not in models:
        models.append("B2B")
        evidence.append(f"B2B: target market/buyers — {market[:80]}")
    if _re.search(r"\b(consumer|consumers|individual|b2c|families|parents|students)\b", market):
        models.append("B2C")
        evidence.append(f"B2C: target market/buyers — {market[:80]}")
    return {"models": models or ["Undetermined"], "evidence": evidence}


# §29 cluster type / §22 page type / §25 content-gap type --------------------

_TOOL_RE = _re.compile(r"\b(calculator|calculators|checker|generator|estimator|converter|calculate)\b")
_TEMPLATE_RE = _re.compile(r"\b(template|templates|checklist|checklists|sample|samples|format)\b")
_GLOSSARY_RE = _re.compile(r"\b(meaning|definition|define|stands for|full form)\b")
_CASE_RE = _re.compile(r"\b(case study|case studies|success stories)\b")
_ALTERNATIVE_RE = _re.compile(r"\b(alternative|alternatives|competitors)\b")


def _share(rows: list[dict], pattern) -> float:
    return sum(1 for r in rows if pattern.search((r.get("keyword") or "").lower())) / len(rows) if rows else 0.0


_COMMERCIAL_EVIDENCE_TYPES = {"Service", "Product"}


def _has_commercial_evidence(s: dict) -> bool:
    """A cluster can be a topic's CORE page only when its keywords say the
    searcher wants to buy/hire something (a service/product word or a
    price/buy modifier) — not when an upstream tool merely labelled a bare
    concept "Commercial" ("stored procedures" on Geopits)."""
    rows = s["rows"]
    if not rows:
        return False
    # A bare product noun ("truck" for a truck maker) carries no modifier,
    # so the business-relevance verdict and the target page's own type
    # count as evidence too.
    if s.get("target_url") and classify_page_type(s["target_url"]) == "commercial":
        return True
    evidenced = sum(1 for r in rows if r.get("entity_type") in _COMMERCIAL_EVIDENCE_TYPES
                    or "Intent" in (r.get("modifier_type") or "")
                    or r.get("relevance_status") == "Core Relevant")
    return evidenced / len(rows) >= 0.5


def assign_cluster_types(summaries: list[dict], topics: list[dict], business_model: dict | None = None) -> None:
    """§29 cluster_type, §22 page_type and §25 content_gap_type per cluster
    summary, in place. Core/Supporting come from the topic model: the
    commercial page a topic is built on is its Core topic; guides and
    subtopics around it are Supporting."""
    parent_of = {name: t["parent"] for t in topics for name in t["clusters"]}
    core_names = set()
    by_parent: dict[str, list[dict]] = {}
    for s in summaries:
        by_parent.setdefault(parent_of.get(s["name"], s["name"]), []).append(s)
    for members in by_parent.values():
        commercial = [s for s in members if (s.get("dominant_family") or "Commercial") == "Commercial"
                      and _has_commercial_evidence(s)]
        if commercial:
            core_names.add(max(commercial, key=lambda s: (s.get("opportunity") or 0, s["name"]))["name"])
    models = set((business_model or {}).get("models") or [])

    for s in summaries:
        rows, family = s["rows"], s.get("dominant_family") or "Commercial"
        s["parent_topic"] = parent_of.get(s["name"])
        siblings = by_parent.get(s["parent_topic"] or s["name"], [s])
        has_commercial_sibling = any((m.get("dominant_family") or "Commercial") == "Commercial" and m is not s for m in siblings)
        brand = sum(1 for r in rows if r.get("brand_type") in ("Brand", "Mixed Brand")) / len(rows) > 0.5
        if brand:
            ctype = "Brand Topic"
        elif family == "Comparison":
            ctype = "Comparison Topic"
        elif family == "Local":
            ctype = "Local Topic"
        elif _majority(rows, "entity_type", "Problem"):
            ctype = "Problem/Solution Topic"
        elif sum(1 for r in rows if r.get("audience")) / len(rows) > 0.5:
            ctype = "Audience Topic"
        elif family == "Informational":
            ctype = "Supporting Topic" if has_commercial_sibling else "Informational Topic"
        elif s["name"] in core_names and len(siblings) > 1:
            ctype = "Core Topic"
        elif _majority(rows, "detected_intent", "Transactional"):
            ctype = "Transactional Topic"
        elif _majority(rows, "entity_type", "Service"):
            ctype = "Service Topic"
        elif _majority(rows, "entity_type", "Product"):
            ctype = "Product Topic"
        else:
            ctype = "Commercial Topic"
        s["cluster_type"] = ctype

        # §22 page type — the format the searches expect.
        if _share(rows, _TOOL_RE) > 0.3:
            ptype = "Tool / Calculator"
        elif _share(rows, _TEMPLATE_RE) > 0.3:
            ptype = "Template"
        elif _share(rows, _CASE_RE) > 0.3:
            ptype = "Case Study"
        elif family == "Comparison":
            ptype = "Alternative Page" if _share(rows, _ALTERNATIVE_RE) > 0.5 else "Comparison Page"
        elif family == "Local":
            ptype = "Location Page"
        elif family == "Navigational" or ctype == "Brand Topic":
            ptype = "Homepage / Brand Page"
        elif family == "Informational":
            words = [len((r.get("core_entity") or r.get("keyword") or "").split()) for r in rows]
            if _share(rows, _GLOSSARY_RE) > 0.4 and words and sum(words) / len(words) <= 3:
                ptype = "Glossary"
            elif sum(1 for r in rows if len((r.get("keyword") or "").split()) >= 6) / len(rows) > 0.5:
                ptype = "FAQ"
            else:
                ptype = "Guide / Blog Article"
        elif ctype == "Audience Topic":
            ptype = "Industry / Audience Page"
        elif ctype == "Core Topic":
            # §22/§48/§49: the pillar page's format follows the business —
            # a category page for products, a service page for services.
            if _majority(rows, "entity_type", "Service") or ("Service" in models and not models & {"E-commerce", "Product"}):
                ptype = "Service Page"
            elif _majority(rows, "entity_type", "Product") or models & {"E-commerce", "Product"}:
                ptype = "Category Page"
            else:
                ptype = "Landing Page"
        elif _majority(rows, "entity_type", "Service"):
            ptype = "Service Page"
        elif _majority(rows, "entity_type", "Product"):
            ptype = "Product Page"
        else:
            ptype = "Product / Service Page"
        s["page_type"] = ptype

        # §25 content-gap type — only for a cluster with no page yet.
        if s.get("target_url"):
            s["content_gap_type"] = None
        elif ctype == "Core Topic":
            s["content_gap_type"] = "Missing Core Page"
        elif family == "Comparison":
            s["content_gap_type"] = "Missing Comparison Page"
        elif family == "Local":
            s["content_gap_type"] = "Missing Location Page"
        elif ctype == "Audience Topic":
            s["content_gap_type"] = "Missing Audience Page"
        elif ctype == "Supporting Topic":
            s["content_gap_type"] = "Missing Supporting Content"
        elif family == "Informational":
            s["content_gap_type"] = "Missing Informational Content"
        elif has_commercial_sibling:
            s["content_gap_type"] = "Missing Subtopic"
        elif ctype in ("Service Topic", "Product Topic"):
            s["content_gap_type"] = "Missing Product/Service Page"
        else:
            s["content_gap_type"] = "Missing Commercial Content"


# §53 decision ------------------------------------------------------------------

def assign_decisions(summaries: list[dict], dead_urls: set[str] | None = None) -> None:
    """§53 — exactly one decision per cluster from the spec's eight:
    EXISTING URL — PRIMARY TARGET / EXISTING URL — SECONDARY TARGET /
    NEW URL REQUIRED / MERGE EXISTING URLS / RESTRUCTURE / REDIRECT /
    NO TARGET / REVIEW, with its reason."""
    dead = {(u or "").rstrip("/").lower() for u in dead_urls or ()}
    owner: dict[str, dict] = {}
    for s in sorted(summaries, key=lambda s: (_TIER_ORDER.get(s.get("roadmap_priority"), 9), -(s.get("opportunity") or 0))):
        url = (s.get("target_url") or "").rstrip("/").lower()
        base = s.get("recommended_action") or ""
        ranking_dead = next((r.get("current_url") for r in s["rows"]
                             if (r.get("current_url") or "").rstrip("/").lower() in dead
                             and (r.get("current_url") or "").rstrip("/").lower() != url), None)
        best_pos = min((_num(r.get("current_position")) for r in s["rows"] if _num(r.get("current_position")) > 0), default=0)
        if s.get("roadmap_priority") == "Human Review":
            decision, reason = "REVIEW", "Low confidence or mostly doubtful keywords — confirm before building."
        elif ranking_dead:
            decision = "REDIRECT"
            reason = f"Google still ranks {ranking_dead}, which now returns an error — 301 it to the target page."
        elif url and url in owner and owner[url] is not s:
            decision = "EXISTING URL — SECONDARY TARGET"
            s["_owner_family"] = owner[url].get("dominant_family")
            reason = f"Same page already serves \"{owner[url]['name']}\" — add this as a section, not a new page."
        elif url:
            owner.setdefault(url, s)
            if base.startswith("Merge"):
                decision, reason = "MERGE EXISTING URLS", "Several pages compete for this need — make this the one primary page."
            elif base.startswith("Restructure"):
                decision, reason = "RESTRUCTURE", "The page overlaps another cluster's page — differentiate or merge."
            elif 0 < best_pos <= 3 and s.get("match_strength") == "strong":
                decision, reason = "NO TARGET", f"Already ranks #{int(best_pos)} on the right page — monitor, no change needed."
            else:
                decision, reason = "EXISTING URL — PRIMARY TARGET", "This existing page is the closest real match."
        else:
            decision, reason = "NEW URL REQUIRED", "No existing page covers this search need."
        s["decision"], s["decision_reason"] = decision, reason


# §24 / §58 cannibalization from Search Console ---------------------------------

def _url_key(url: str) -> str:
    u = _re.sub(r"^[a-z][a-z0-9+.-]*://", "", (url or "").lower())
    return u.split("?")[0].split("#")[0].removeprefix("www.").rstrip("/")


def detect_cannibalization(page_query_rows: list[dict] | None, min_impressions: int = 50, limit: int = 15) -> list[dict]:
    """§24/§58 — a query where 2+ of the client's own pages both earn real
    impressions in Search Console (the spec's "multiple URLs ranking for
    the same query / traffic distributed across multiple pages"). Returns
    {"query", "preferred_url", "other_urls", "risk", "action", "evidence"},
    most impressions first. Actions follow §58: Canonicalize (same page,
    different URL form), Merge (same page type, weaker page barely
    earns), Differentiate (same type, both earn), Retarget (different page
    types — each should own its own intent), Keep Both (different types,
    both already top-10)."""
    by_query: dict[str, dict[str, dict]] = {}
    for r in page_query_rows or []:
        q = (r.get("query") or "").strip().lower()
        page = r.get("page") or ""
        if not q or not page:
            continue
        agg = by_query.setdefault(q, {}).setdefault(page, {"clicks": 0.0, "impressions": 0.0, "position": None})
        agg["clicks"] += _num(r.get("clicks"))
        agg["impressions"] += _num(r.get("impressions"))
        pos = _num(r.get("position"))
        if pos > 0:
            agg["position"] = pos if agg["position"] is None else min(agg["position"], pos)
    out = []
    for q, pages in by_query.items():
        real = {p: v for p, v in pages.items() if v["impressions"] >= 10}
        total = sum(v["impressions"] for v in real.values())
        if len(real) < 2 or total < min_impressions:
            continue
        ranked = sorted(real.items(), key=lambda kv: (-kv[1]["clicks"], -kv[1]["impressions"], kv[0]))
        (pref, pv), (other, ov) = ranked[0], ranked[1]
        same_type = classify_page_type(pref) == classify_page_type(other)
        if _url_key(pref) == _url_key(other):
            action = "Canonicalize"
        elif same_type and ov["impressions"] < 0.15 * pv["impressions"]:
            action = "Merge"
        elif same_type:
            action = "Differentiate"
        elif (pv["position"] or 99) <= 10 and (ov["position"] or 99) <= 10:
            action = "Keep Both"
        else:
            action = "Retarget"
        split = ov["impressions"] / total
        risk = "High" if split >= 0.25 and (ov["position"] or 99) <= 20 else "Medium" if split >= 0.1 else "Low"
        out.append({
            "query": q, "preferred_url": pref, "other_urls": [p for p, _v in ranked[1:]], "risk": risk,
            "action": action, "impressions": total,
            "evidence": (f"{len(real)} pages share {int(total):,} impressions; preferred has {int(pv['clicks'])} "
                         f"clicks at #{(pv['position'] or 0):.0f}, next has {int(ov['clicks'])} at #{(ov['position'] or 0):.0f}."),
        })
    out.sort(key=lambda c: ({"High": 0, "Medium": 1, "Low": 2}[c["risk"]], -c["impressions"]))
    return out[:limit]


# §62 review queue / §67 quality control -------------------------------------

_REVIEW_CAP_PER_TYPE = 10


def build_review_queue(summaries: list[dict], cannibalization: list[dict], routed_counts: dict | None = None) -> list[dict]:
    """§62 — the decisions a person should check, with the reason type.
    Only clusters on the roadmap's High/Medium tiers (or already sent to
    Human Review) are listed — a 50-searches/month Low cluster's page
    mapping isn't a decision anyone needs to make now — and at most
    _REVIEW_CAP_PER_TYPE items per type, highest opportunity first
    (Geopits listed 497 items, which nobody can review)."""
    queue = []
    ranked = sorted(summaries, key=lambda s: -(s.get("opportunity") or 0))
    for s in ranked:
        if s.get("roadmap_priority") not in ("High", "Medium", "Human Review"):
            continue
        rows = s["rows"]
        _family, share = _family_share(rows)
        flagged = [r["keyword"] for r in rows if r.get("relevance_status") in _EXCLUDED_RELEVANCE_STATUSES]
        # Ambiguous = no intent evidence at all: no modifier in the wording
        # AND no upstream (Semrush/sheet) intent label to fall back on.
        low_conf_intent = sum(1 for r in rows if (r.get("intent_confidence") or 100) <= 55
                              and not (r.get("intent") or "").strip()) / len(rows)
        if s.get("roadmap_priority") == "Human Review":
            queue.append({"type": "Low confidence cluster", "item": s["name"], "detail": s.get("decision_reason") or ""})
        if share < 0.6:
            queue.append({"type": "Mixed intent", "item": s["name"], "detail": f"only {share:.0%} of keywords share one intent"})
        if low_conf_intent > 0.5:
            queue.append({"type": "Ambiguous intent", "item": s["name"], "detail": "intent read from the bare term only (no modifier)"})
        if flagged:
            queue.append({"type": "Low business relevance", "item": s["name"], "detail": ", ".join(flagged[:3])})
        if s.get("match_strength") == "weak":
            queue.append({"type": "Unclear page mapping", "item": s["name"], "detail": "closest page is only a weak match"})
        if len(s.get("outliers") or []) >= 2:
            queue.append({"type": "Borderline cluster separation", "item": s["name"],
                          "detail": ", ".join((s.get("outliers") or [])[:3])})
        if s.get("decision") == "EXISTING URL — SECONDARY TARGET" and s.get("_owner_family") in (None, s.get("dominant_family")):
            queue.append({"type": "Potential cannibalization", "item": s["name"], "detail": s.get("decision_reason") or ""})
        # Only where the answer changes what gets built: a High-priority
        # cluster that needs a NEW page and whose wording doesn't say
        # whether it's a product or a service page.
        if (_majority(rows, "entity_type", "Product or Service") and s.get("roadmap_priority") == "High"
                and not s.get("target_url")):
            queue.append({"type": "Uncertain entity relationship", "item": s["name"],
                          "detail": "keywords don't say whether this is a product or a service"})
    capped, per_type = [], Counter()
    for q in queue:
        per_type[q["type"]] += 1
        if per_type[q["type"]] <= _REVIEW_CAP_PER_TYPE:
            capped.append(q)
    queue = capped
    for c in cannibalization:
        if c["risk"] != "Low":
            queue.append({"type": "Potential cannibalization", "item": c["query"],
                          "detail": f"{c['action']}: keep {c['preferred_url']}"})
    for label, n in (routed_counts or {}).items():
        if n:
            queue.append({"type": label, "item": f"{n} keyword(s)", "detail": "kept out of target pages, listed for review"})
    return queue


_INCOMPATIBLE = {"Informational": {"commercial", "home"}, "Comparison": {"home"}, "Commercial": {"blog"}}


def run_quality_checks(summaries: list[dict], cannibalization: list[dict], business_model: dict) -> list[dict]:
    """§67 — the 14 checks, each Pass / Warn / Not checked (with why)."""
    checks = []

    def add(n, name, problems, not_checked=None):
        if not_checked:
            checks.append({"check": n, "name": name, "status": "Not checked", "detail": not_checked})
        else:
            checks.append({"check": n, "name": name, "status": "Warn" if problems else "Pass",
                           "detail": "; ".join(problems[:4]) if problems else "OK"})

    active = [s for s in summaries if s.get("roadmap_priority") != "Human Review"]
    add(1, "Business relevance", [s["name"] for s in active
        if any(r.get("relevance_status") in _EXCLUDED_RELEVANCE_STATUSES for r in s["rows"])])
    add(2, "Intent coherence", [s["name"] for s in summaries if _family_share(s["rows"])[1] < 0.6])
    add(3, "SERP agreement", [], not_checked="no SERP data source yet (Semrush API deferred)")
    add(4, "Page satisfaction", [
        s["name"] for s in summaries if s.get("target_url")
        and classify_page_type(s["target_url"]) in _INCOMPATIBLE.get(s.get("dominant_family") or "", set())])
    add(5, "Over-clustering", [s["name"] for s in summaries if len(s["rows"]) > 25 and len(s.get("outliers") or []) >= 2])
    pages = Counter(((s.get("target_url") or "").rstrip("/").lower(), s.get("dominant_family")) for s in summaries if s.get("target_url"))
    add(6, "Over-splitting", [f"{n} clusters on {u}" for (u, _f), n in pages.items() if n > 1])
    add(7, "Cannibalization", [c["query"] for c in cannibalization if c["risk"] == "High"])
    add(8, "Architecture", [s["name"] for s in summaries if s.get("target_url")
                            and classify_page_type(s["target_url"]) in ("home", "utility") and s.get("cluster_type") != "Brand Topic"])
    models = set(business_model.get("models") or [])
    add(9, "Business model fit", [s["name"] for s in summaries
                                  if s.get("page_type") == "Product Page" and models == {"Service"}]
        + [s["name"] for s in summaries if s.get("page_type") == "Service Page" and models == {"E-commerce"}])
    add(10, "Search demand", [s["name"] for s in summaries if sum(_demand(r) for r in s["rows"]) <= 0])
    add(11, "SERP reality (page type ranks)", [], not_checked="no SERP data source yet")
    add(12, "Programmatic quality", [], not_checked="checked by the Programmatic SEO slide's own rules")
    add(13, "Explainability", [s["name"] for s in summaries if not s.get("decision_reason")])
    add(14, "Uncertainty flagged", [s["name"] for s in summaries
                                    if s.get("confidence_level") == "Low" and s.get("roadmap_priority") != "Human Review"])
    return checks


# §36 / §56 page map -------------------------------------------------------------

_BUSINESS_GOAL = {
    "Commercial": "Leads / sales from buyers ready to act",
    "Comparison": "Win buyers who are comparing options",
    "Local": "Local enquiries and visits",
    "Informational": "Awareness and trust — pass readers to the product/service page",
    "Navigational": "Serve existing customers",
}


def build_page_map_v2(summaries: list[dict], topics: list[dict], cannibalization: list[dict]) -> list[dict]:
    """§56 page map with §36 page purpose: one entry per target page (an
    existing URL, or a new page per cluster)."""
    links_in: dict[str, list[str]] = {}
    links_out: dict[str, list[str]] = {}
    for t in topics:
        for l in t["links"]:
            links_out.setdefault(l["from_cluster"], []).append(l["to_cluster"])
            links_in.setdefault(l["to_cluster"], []).append(l["from_cluster"])
    gsc_risk = {_url_key(c["preferred_url"]) for c in cannibalization} | {_url_key(u) for c in cannibalization for u in c["other_urls"]}
    by_url: dict[str, list[dict]] = {}
    for s in summaries:
        by_url.setdefault(s["target_url"] or f"new:{s['name']}", []).append(s)
    pages = []
    for key, members in by_url.items():
        best = min(members, key=lambda s: (_TIER_ORDER.get(s.get("roadmap_priority"), 9), -(s.get("opportunity") or 0)))
        family = best.get("dominant_family") or "Commercial"
        primary = next((r for r in best["rows"] if r.get("keyword") == best.get("primary_keyword")), best["rows"][0])
        positions = [_num(r.get("current_position")) for s in members for r in s["rows"] if _num(r.get("current_position")) > 0]
        url = None if key.startswith("new:") else key
        if url is None:
            status = "New page needed"
        elif positions:
            status = f"Existing page — ranks #{int(min(positions))} at best"
        else:
            status = "Existing page — not ranking for these keywords"
        pages.append({
            "url": url, "clusters": [s["name"] for s in members],
            "topic": best.get("parent_topic"),
            "primary_keyword": best.get("primary_keyword"),
            "secondary_keywords": [r["keyword"] for s in members for r in s["rows"] if r.get("keyword") != best.get("primary_keyword")][:8],
            "primary_intent": family,
            "secondary_intents": sorted({s.get("dominant_family") or "Commercial" for s in members} - {family}),
            "page_type": best.get("page_type"),
            "purpose": f"{best.get('page_type') or 'Page'} answering: {primary.get('user_need') or best.get('primary_keyword')}",
            "primary_entity": smart_title(display_phrase(best.get("primary_keyword") or best["name"])),
            "supporting_topics": sorted({c for s in members for c in links_in.get(s["name"], [])})[:6],
            "target_evidence": best.get("target_evidence"),
            "business_goal": _BUSINESS_GOAL.get(family, _BUSINESS_GOAL["Commercial"]),
            "audience": next((r.get("audience") for s in members for r in s["rows"] if r.get("audience")), None),
            "links_in": sorted({c for s in members for c in links_in.get(s["name"], [])}),
            "links_out": sorted({c for s in members for c in links_out.get(s["name"], [])}),
            "current_status": status,
            "decision": best.get("decision"),
            "action": best.get("decision_reason"),
            "content_gap": best.get("content_gap_type"),
            "cannibalization_risk": len(members) > 1 or (url is not None and _url_key(url) in gsc_risk),
            "priority": best.get("roadmap_priority"),
            "opportunity": best.get("opportunity") or 0,
        })
    pages.sort(key=lambda p: (_TIER_ORDER.get(p["priority"], 9), -p["opportunity"]))
    return pages


def build_full_keyword_strategy(summaries: list[dict], context: dict | None = None) -> dict | None:
    """Everything above in one pass, plus the §54 per-keyword fields
    stamped onto every row. `context` (all optional): site_authority (own
    DR), page_query_rows (GSC page x query), site_audit_pages_rows,
    company_overview, dead_urls, brands {"own","competitor","vendor"},
    routed_counts {label: n}."""
    ctx = context or {}
    summaries = [s for s in summaries if s.get("rows")]
    if not summaries:
        return None
    from app.services import keyword_strategy_depth as depth

    brands = ctx.get("brands") or {}
    site_model = ctx.get("site_model")
    for s in summaries:
        for r in s["rows"]:
            r["brand_type"] = keyword_brand_type(r.get("keyword") or "", brands.get("own"), brands.get("competitor"),
                                                 brands.get("vendor"))
    depth.annotate_rows_with_site(summaries, site_model, ctx.get("page_clicks"))
    depth.topical_authority(summaries, site_model)
    score_summaries(summaries, ctx.get("site_authority"))
    topics = build_topic_model(summaries, max_parents=None)
    business_model = infer_business_model(ctx.get("site_audit_pages_rows"), ctx.get("company_overview"))
    assign_cluster_types(summaries, topics, business_model)
    assign_decisions(summaries, ctx.get("dead_urls"))
    depth.apply_business_rules(summaries, site_model, business_model, brands.get("competitor"))
    depth.primary_keyword_scores(summaries, site_model)
    depth.target_evidence(summaries, site_model)
    depth.extra_links(topics, summaries)
    depth.deepen_topics(topics, summaries)
    depth.cluster_outputs(summaries)
    cannibalization = depth.cannibalization_similarity(detect_cannibalization(ctx.get("page_query_rows")), site_model)
    patterns = depth.programmatic_patterns(summaries)
    review_queue = build_review_queue(summaries, cannibalization, ctx.get("routed_counts")) \
        + depth.extra_review_items(summaries, patterns, topics)
    quality = run_quality_checks(summaries, cannibalization, business_model)
    gaps = depth.content_gaps(summaries, site_model)
    ordered = sorted(summaries, key=lambda s: (_TIER_ORDER.get(s.get("roadmap_priority"), 9), -(s.get("opportunity") or 0), s["name"]))
    for i, s in enumerate(ordered, 1):
        s["cluster_id"] = f"C{i:02d}"
        for r in s["rows"]:
            factors = keyword_opportunity(r, 1.0)["factors"]
            r.update({
                "cluster_id": s["cluster_id"], "cluster_type": s["cluster_type"], "parent_topic": s.get("parent_topic"),
                "recommended_page_type": s["page_type"], "content_gap_type": s.get("content_gap_type"),
                "decision": s["decision"], "decision_reason": s["decision_reason"],
                "business_relevance": round(factors["business_relevance"], 2),
                "conversion_potential": round(factors["intent_value"], 2),
                "competitor_gap": None if factors["competitor_gap"] is None else round(factors["competitor_gap"], 2),
                "content_gap_flag": not s.get("target_url"),
            })
            r.setdefault("programmatic_flag", False)
    return {
        "topics": topics,
        "page_map": build_page_map_v2(summaries, topics, cannibalization),
        "roadmap": {tier: [s["name"] for s in ordered if s["roadmap_priority"] == tier]
                    for tier in ("High", "Medium", "Low", "Human Review")},
        "clusters": [{
            "cluster_id": s["cluster_id"], "name": s["name"], "parent_topic": s.get("parent_topic"),
            "cluster_type": s["cluster_type"], "page_type": s["page_type"], "content_gap_type": s.get("content_gap_type"),
            "decision": s["decision"], "decision_reason": s["decision_reason"], "priority": s["roadmap_priority"],
            "opportunity": s.get("opportunity"), "target_url": s.get("target_url"), "primary_keyword": s.get("primary_keyword"),
            "audience": s.get("audience"), "geography": s.get("geography"), "difficulty": s.get("difficulty"),
            "search_demand": s.get("search_demand"), "business_value": s.get("business_value"),
            "confidence": s.get("confidence"), "intent": s.get("dominant_family"), "business_rule": s.get("business_rule"),
            "target_evidence": s.get("target_evidence"), "topical_pages": s.get("topical_pages"),
            "keyword_count": len(s["rows"]),
            "secondary_keywords": [r["keyword"] for r in s["rows"] if r.get("keyword") != s.get("primary_keyword")][:8],
        } for s in ordered],
        "content_gaps": gaps,
        "programmatic_patterns": patterns,
        "site": {
            "graph": (site_model or {}).get("graph"),
            "pages": ((site_model or {}).get("pages") or [])[:400],
            "service_areas": (site_model or {}).get("service_areas"),
        } if site_model else None,
        "cannibalization": cannibalization,
        "review_queue": review_queue,
        "quality_checks": quality,
        "business_model": business_model,
        "weights": dict(OPPORTUNITY_WEIGHTS),
    }
