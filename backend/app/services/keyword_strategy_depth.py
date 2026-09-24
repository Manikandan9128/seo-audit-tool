"""Universal SEO Keyword engine — strategy depth (2026-09-24). The prompt
sections that need the website model (keyword_site_model) on top of the
cluster strategy (keyword_strategy_service). Deterministic, no AI call.
See docs/keyword_engine_spec_coverage.md for the section-by-section map.
"""

import math
import re
from collections import Counter

from app.services.keyword_intelligence_service import (
    _EXCLUDED_RELEVANCE_STATUSES,
    _demand,
    _num,
    display_phrase,
    modifier_types,
    normalize_keyword,
    smart_title,
)
from app.services.keyword_relevance_service import classify_page_type
from app.services.keyword_site_model import geo_served, keyword_audience_from_site

# ---------------------------------------------------------------------------
# per keyword: §6 entity relationship, §11/§12 site-aware audience + geo,
# §54 language / traffic
# ---------------------------------------------------------------------------

_SCRIPTS = [
    ("Hindi", re.compile(r"[ऀ-ॿ]")), ("Tamil", re.compile(r"[஀-௿]")),
    ("Arabic", re.compile(r"[؀-ۿ]")), ("Chinese", re.compile(r"[一-鿿]")),
    ("Cyrillic", re.compile(r"[Ѐ-ӿ]")),
]


def keyword_language(keyword: str) -> str:
    for name, pattern in _SCRIPTS:
        if pattern.search(keyword or ""):
            return name
    return "English / Latin script" if re.search(r"[a-z]", (keyword or "").lower()) else "Unknown"


def _names_in(text: str, names) -> list[str]:
    t = f" {' '.join(re.findall(r'[a-z0-9]+', (text or '').lower()))} "
    return [n for n in names or () if len(n) >= 3 and f" {' '.join(re.findall(r'[a-z0-9]+', n))} " in t]


def entity_relationship(row: dict, graph: dict | None) -> str | None:
    """§6 how the keyword's entity relates to another: service -> industry,
    product -> attribute, problem -> solution, product/service ->
    audience, service -> location, product -> alternative."""
    kw = row.get("keyword") or ""
    graph = graph or {}
    kinds = modifier_types(kw)
    if row.get("detected_intent") == "Comparison":
        return "product -> alternative"
    if row.get("entity_type") == "Problem":
        return "problem -> solution"
    base = "service" if row.get("entity_type") == "Service" else "product"
    if _names_in(kw, graph.get("industries")):
        return f"{base} -> industry"
    if row.get("audience") or "Audience" in kinds:
        return f"{base} -> audience"
    if row.get("geography") or "Geographic" in kinds:
        return f"{base} -> location"
    if "Product / Attribute" in kinds:
        return "product -> attribute"
    if _names_in(kw, graph.get("use_cases")):
        return f"{base} -> use case"
    return None


def annotate_rows_with_site(summaries: list[dict], site_model: dict | None, page_clicks: dict | None) -> None:
    graph = (site_model or {}).get("graph") or {}
    clicks = {k.rstrip("/").lower(): v for k, v in (page_clicks or {}).items()}
    for s in summaries:
        for r in s["rows"]:
            if not r.get("audience"):
                r["audience"] = keyword_audience_from_site(r.get("keyword") or "", site_model)
            r["geo_served"] = geo_served(r.get("geography"), site_model)
            r["entity_relationship"] = entity_relationship(r, graph)
            r["language"] = keyword_language(r.get("keyword") or "")
            url = (r.get("current_url") or "").rstrip("/").lower()
            r["existing_url_traffic"] = clicks.get(url) if url else None


# ---------------------------------------------------------------------------
# §32 topical authority per cluster (fed into feasibility)
# ---------------------------------------------------------------------------

def topical_authority(summaries: list[dict], site_model: dict | None) -> None:
    """How many of the site's own live pages already cover the cluster's
    topic words — 0 pages = 0.0, 10+ pages = 1.0. Stamped on every row."""
    page_tokens = (site_model or {}).get("page_tokens") or {}
    for s in summaries:
        core = Counter(t for r in s["rows"] for t in normalize_keyword(r.get("keyword") or "")["core_tokens"])
        top = {t for t, _n in core.most_common(2)}
        n = sum(1 for toks in page_tokens.values() if top and top <= toks) if top else 0
        s["topical_pages"] = n
        for r in s["rows"]:
            r["topical_authority"] = round(min(1.0, n / 10), 2)


# ---------------------------------------------------------------------------
# §19-20 primary keyword score
# ---------------------------------------------------------------------------

PRIMARY_WEIGHTS = {
    "business_relevance": 0.25, "intent_fit": 0.20, "page_fit": 0.15, "search_demand": 0.15,
    "conversion_potential": 0.10, "existing_authority": 0.10, "strategic_importance": 0.05,
}
_REL = {"Core Relevant": 1.0, "Relevant": 0.85, "Validated (Manual)": 0.8, "Adjacent / Potential": 0.5}
_CONV = {"Transactional": 1.0, "Commercial": 0.85, "Commercial Investigation": 0.85, "Comparison": 0.8, "Local": 0.8,
         "Informational": 0.45, "Navigational": 0.2}


def primary_keyword_scores(summaries: list[dict], site_model: dict | None) -> None:
    """§20 Primary Keyword Score (0-100) per keyword; the cluster's primary
    is the top scorer — except in an AI-validated cluster, where the AI's
    own primary pick is kept and the score is informational."""
    page_tokens = (site_model or {}).get("page_tokens") or {}
    max_d = max((_demand(r) for s in summaries for r in s["rows"]), default=0.0)
    for s in summaries:
        family = s.get("dominant_family")
        target_toks = page_tokens.get(s.get("target_url") or "", set())
        for r in s["rows"]:
            toks = set(normalize_keyword(r.get("keyword") or "")["core_tokens"])
            pos = _num(r.get("current_position"))
            f = {
                "business_relevance": 0.1 if r.get("relevance_status") in _EXCLUDED_RELEVANCE_STATUSES
                else _REL.get(r.get("relevance_status") or "", 0.6),
                "intent_fit": 1.0 if r.get("intent_family") == family else 0.3,
                "page_fit": (len(toks & target_toks) / len(toks)) if toks and target_toks else 0.5,
                "search_demand": math.log10(1 + _demand(r)) / math.log10(1 + max_d) if max_d > 0 else 0.0,
                "conversion_potential": _CONV.get(r.get("detected_intent") or "", 0.6),
                "existing_authority": 1.0 if 0 < pos <= 10 else 0.6 if 0 < pos <= 20 else 0.3 if pos > 0 else 0.0,
                "strategic_importance": s.get("strategic") if s.get("strategic") is not None else 0.5,
            }
            r["primary_score"] = round(100 * sum(PRIMARY_WEIGHTS[k] * v for k, v in f.items()))
        if s["rows"] and s["rows"][0].get("cluster_source") != "ai":
            best = max(s["rows"], key=lambda r: (r["primary_score"], _demand(r)))
            for r in s["rows"]:
                r["primary_or_secondary"] = "Primary" if r is best else "Secondary"
            s["primary_keyword"] = best.get("keyword")


# ---------------------------------------------------------------------------
# §23 target-page evidence, §14 intent similarity, §18 hierarchy levels
# ---------------------------------------------------------------------------

def target_evidence(summaries: list[dict], site_model: dict | None) -> None:
    pages = {p["url"].rstrip("/").lower(): p for p in (site_model or {}).get("pages") or []}
    for s in summaries:
        p = pages.get((s.get("target_url") or "").rstrip("/").lower())
        if not p:
            s["target_evidence"] = None
            continue
        conv = "Conversion page" if p["page_type"] in ("commercial", "location") else "Supporting page"
        s["target_evidence"] = {
            "page_type": p["page_type"], "traffic_clicks": p["traffic_clicks"], "crawl_depth": p["crawl_depth"],
            "incoming_internal_links": p["incoming_internal_links"], "content_depth": p["content_depth"],
            "conversion_role": conv, "authority_score": p.get("authority_score"),
            "referring_backlinks": p.get("referring_backlinks"),
        }


def _intent_vector(rows: list[dict]) -> dict[str, float]:
    c = Counter(r.get("intent_family") or "Commercial" for r in rows)
    n = sum(c.values()) or 1
    return {k: v / n for k, v in c.items()}


def intent_similarity(a: list[dict], b: list[dict]) -> float:
    """§14 — cosine similarity of two clusters' intent distributions."""
    va, vb = _intent_vector(a), _intent_vector(b)
    dot = sum(va.get(k, 0) * vb.get(k, 0) for k in set(va) | set(vb))
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return round(dot / (na * nb), 2) if na and nb else 0.0


def deepen_topics(topics: list[dict], summaries: list[dict]) -> None:
    """§18 subtopic + intent levels, §14 intent-similar sibling pairs, and
    §38 topical-authority breakdown, added onto each topic in place."""
    by_name = {s["name"]: s for s in summaries}
    for t in topics:
        members = [by_name[n] for n in t["clusters"] if n in by_name]
        parent = (t["parent"] or "").lower()
        hierarchy: dict[str, dict[str, list[str]]] = {}
        for s in members:
            words = [w for w in display_phrase(s["name"].split(" — ")[0]).lower().split() if w not in parent.split()]
            sub = smart_title(" ".join(words)) or "Core"
            hierarchy.setdefault(sub, {}).setdefault(s.get("dominant_family") or "Commercial", []).append(s["name"])
        t["hierarchy"] = hierarchy
        pairs = []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                sim = intent_similarity(a["rows"], b["rows"])
                if sim >= 0.9:
                    pairs.append({"a": a["name"], "b": b["name"], "intent_similarity": sim})
        t["intent_similar_pairs"] = pairs[:5]
        covered = [s for s in members if s.get("match_strength") in ("strong", "partial")]
        t["entity_coverage"] = sorted({r.get("entity_type") for s in covered for r in s["rows"] if r.get("entity_type")})
        t["audience_coverage"] = sorted({r.get("audience") for s in covered for r in s["rows"] if r.get("audience")})
        t["commercial_covered"] = sum(1 for s in covered if (s.get("dominant_family") or "Commercial") == "Commercial")
        t["informational_covered"] = sum(1 for s in covered if s.get("dominant_family") == "Informational")
        t["supporting_content"] = t["informational_covered"] > 0
        t["internal_links"] = len(t.get("links") or [])
        score = (t["covered"] / t["total"] if t["total"] else 0) * 0.5 + (0.25 if t["supporting_content"] else 0) \
            + (0.25 if t["commercial_covered"] else 0)
        t["authority_level"] = "Strong" if score >= 0.75 else "Partial" if score >= 0.4 else "Weak"


# ---------------------------------------------------------------------------
# §37 extra link types
# ---------------------------------------------------------------------------

def extra_links(topics: list[dict], summaries: list[dict]) -> None:
    """Category -> Product, Problem -> Solution, Audience -> Service and
    Service -> Guide links inside a topic (the §37 relationship types the
    first pass didn't produce)."""
    by_name = {s["name"]: s for s in summaries}
    label = lambda s: s["target_url"] if s.get("target_url") else f"new page: {s['name']}"  # noqa: E731
    for t in topics:
        members = [by_name[n] for n in t["clusters"] if n in by_name]
        have = {(l["from_cluster"], l["to_cluster"]) for l in t.get("links") or []}
        core = next((s for s in members if s.get("cluster_type") == "Core Topic"), None)
        services = [s for s in members if s.get("cluster_type") in ("Core Topic", "Service Topic", "Product Topic", "Commercial Topic")]
        new = []
        for s in members:
            ctype = s.get("cluster_type")
            if core and s is not core and ctype == "Product Topic" and core.get("page_type") == "Category Page":
                new.append((core, s, "Category → Product"))
            if ctype == "Problem/Solution Topic" and services:
                new.append((s, services[0] if services[0] is not s else (services[1] if len(services) > 1 else s), "Problem → Solution"))
            if ctype == "Audience Topic" and core and s is not core:
                new.append((s, core, "Audience → Product/Service"))
            if ctype in ("Supporting Topic", "Informational Topic") and core:
                new.append((core, s, "Service → Guide"))
        for a, b, rel in new:
            if a is b or (a["name"], b["name"]) in have:
                continue
            if a.get("target_url") and a.get("target_url") == b.get("target_url"):
                continue
            have.add((a["name"], b["name"]))
            t.setdefault("links", []).append({"from": label(a), "to": label(b), "relation": rel,
                                              "from_cluster": a["name"], "to_cluster": b["name"]})


# ---------------------------------------------------------------------------
# §46-§52 business-model and competitor/local rules
# ---------------------------------------------------------------------------

_FEATURE_RE = re.compile(r"\b(feature|features|functionality|capabilit(y|ies))\b")
_INTEGRATION_RE = re.compile(r"\b(integration|integrations|integrate|connector|connectors|plugin|api)\b")
_DOCS_RE = re.compile(r"\b(docs|documentation|setup|set up|configure|configuration|install|installation|tutorial)\b")
_BUYING_GUIDE_RE = re.compile(r"\b(best|how to choose|buying guide|which .* to buy|top \d+)\b")


def apply_business_rules(summaries: list[dict], site_model: dict | None, business_model: dict | None,
                         competitor_brands: set | None) -> None:
    """§46 competitor/alternative checks, §47 local-value check, §48
    e-commerce, §49 service, §50 publisher, §51 SaaS, §52 multi-industry
    rules. Adjusts page_type / decision / decision_reason in place and
    records `business_rule` (which rule fired)."""
    models = set((business_model or {}).get("models") or [])
    graph = (site_model or {}).get("graph") or {}
    offer_names = set(graph.get("services") or []) | set(graph.get("products") or [])
    offer_tokens = {t for n in offer_names for t in normalize_keyword(n)["core_tokens"]}
    site_tokens = set().union(*((site_model or {}).get("page_tokens") or {"_": set()}).values())
    has_local = bool((site_model or {}).get("service_areas")) or any(
        p["page_type"] == "location" for p in (site_model or {}).get("pages") or [])
    industries = graph.get("industries") or []
    for s in summaries:
        rows, family = s["rows"], s.get("dominant_family") or "Commercial"
        text = " ".join((r.get("keyword") or "").lower() for r in rows)
        demand = sum(_demand(r) for r in rows)
        s["business_rule"] = None
        # §46 — a comparison naming a competitor needs a real equivalent offer
        if family == "Comparison" and any(r.get("brand_type") in ("Competitor Brand", "Mixed Brand") for r in rows):
            brands = set(competitor_brands or ())
            topic = {t for r in rows for t in normalize_keyword(r.get("keyword") or "")["core_tokens"]} - brands
            if topic and not (topic & (offer_tokens or site_tokens)):
                s["decision"], s["decision_reason"] = "REVIEW", "No equivalent product/service found on the site for this comparison — confirm the client really offers an alternative."
            else:
                s["decision_reason"] = (s.get("decision_reason") or "") + " Keep claims factual and don't use the competitor's trademarks misleadingly."
            s["business_rule"] = "§46 competitor comparison check"
        # §47 — a location page needs real local presence
        if family == "Local" and not has_local and s.get("decision") == "NEW URL REQUIRED":
            s["decision"], s["decision_reason"] = "REVIEW", "No location pages or listed service areas on the site — a location page would have no unique local value yet."
            s["business_rule"] = "§47 local value check"
        # §51 SaaS: feature / integration / documentation pages
        if models & {"SaaS / Subscription", "Product"}:
            if _INTEGRATION_RE.search(text):
                s["page_type"], s["business_rule"] = "Integration Page", "§51 SaaS integration"
            elif _DOCS_RE.search(text) and family == "Informational":
                s["page_type"], s["business_rule"] = "Documentation", "§51 SaaS documentation"
            elif _FEATURE_RE.search(text):
                s["page_type"], s["business_rule"] = "Feature Page", "§51 SaaS feature"
        # §48 e-commerce: category / subcategory / attribute / buying guide
        if "E-commerce" in models:
            kinds = Counter(k for r in rows for k in modifier_types(r.get("keyword") or ""))
            if family == "Informational" and _BUYING_GUIDE_RE.search(text):
                s["page_type"], s["business_rule"] = "Buying Guide", "§48 e-commerce buying guide"
            elif kinds.get("Product / Attribute", 0) > len(rows) / 2:
                s["page_type"], s["business_rule"] = "Attribute / Filter Page", "§48 e-commerce attribute"
            elif s.get("cluster_type") in ("Product Topic", "Commercial Topic") and s.get("page_type") in ("Product Page", "Product / Service Page"):
                s["page_type"], s["business_rule"] = "Subcategory Page", "§48 e-commerce subcategory"
        # §49 / §52 service x industry
        hit = next((i for i in industries if _names_in(text, [i])), None)
        if hit and family == "Commercial" and "Service" in models:
            s["page_type"], s["business_rule"] = "Industry Service Page", "§49/§52 service x industry"
            if demand < 100 and s.get("decision") == "NEW URL REQUIRED":
                s["decision"] = "EXISTING URL — SECONDARY TARGET"
                s["decision_reason"] = f"Only {int(demand)} searches/month for {smart_title(hit)} — cover it as a section on the main service page, not its own page."
        # §50 publisher: freshness over new year pages
        if "Publisher / Content-led" in models and family == "Informational" and any(r.get("temporal") for r in rows):
            s["decision_reason"] = (s.get("decision_reason") or "") + " Refresh the existing article each year instead of a new page."
            s["business_rule"] = s["business_rule"] or "§50 publisher freshness"


# ---------------------------------------------------------------------------
# §55 cluster output, §57 content gaps, §58 similarity, §59 programmatic,
# §62 extra review types, §54 programmatic flag
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def cluster_outputs(summaries: list[dict]) -> None:
    for s in summaries:
        rows = s["rows"]
        kds = [_num(r.get("keyword_difficulty")) for r in rows if r.get("keyword_difficulty") not in (None, "")]
        s["audience"] = Counter(r.get("audience") for r in rows if r.get("audience")).most_common(1)[0][0] \
            if any(r.get("audience") for r in rows) else None
        s["geography"] = Counter(r.get("geography") for r in rows if r.get("geography")).most_common(1)[0][0] \
            if any(r.get("geography") for r in rows) else None
        s["difficulty"] = round(sum(kds) / len(kds)) if kds else None
        s["search_demand"] = int(sum(_demand(r) for r in rows))
        rel = [_REL.get(r.get("relevance_status") or "", 0.6) for r in rows]
        conv = [_CONV.get(r.get("detected_intent") or "", 0.6) for r in rows]
        s["business_value"] = round(100 * (sum(rel) / len(rel)) * (sum(conv) / len(conv))) if rows else 0


def content_gaps(summaries: list[dict], site_model: dict | None) -> list[dict]:
    """§57 — one entry per cluster with no page yet."""
    sections = (site_model or {}).get("sections") or {}
    type_section = {"Guide / Blog Article": "blog", "Glossary": "blog", "FAQ": "blog", "Comparison Page": "comparison",
                    "Alternative Page": "comparison", "Location Page": "location"}
    out = []
    for s in summaries:
        if not s.get("content_gap_type"):
            continue
        rows = s["rows"]
        comp = []
        for r in rows:
            for dom, pos in (r.get("domain_positions") or {}).items():
                if 0 < _num(pos) <= 20:
                    comp.append((dom, int(_num(pos)), r.get("keyword")))
        comp.sort(key=lambda c: c[1])
        section = sections.get(type_section.get(s.get("page_type") or "", "commercial")) or "/"
        out.append({
            "gap_type": s["content_gap_type"], "cluster": s["name"], "topic": s.get("parent_topic"),
            "intent": s.get("dominant_family"),
            "business_relevance": round(sum(_REL.get(r.get("relevance_status") or "", 0.6) for r in rows) / len(rows), 2),
            "competitor_evidence": f"{comp[0][0]} ranks #{comp[0][1]} for \"{comp[0][2]}\"" if comp else "No competitor ranking data",
            "search_demand": int(sum(_demand(r) for r in rows)),
            "page_type": s.get("page_type"),
            "suggested_url": f"{section.rstrip('/')}/{_slug(display_phrase(s.get('primary_keyword') or s['name']))}",
            "primary_keyword": s.get("primary_keyword"),
            "supporting_keywords": [r["keyword"] for r in rows if r.get("keyword") != s.get("primary_keyword")][:6],
            "priority": s.get("roadmap_priority"),
            "reason": f"{s['content_gap_type']}: {s.get('decision_reason') or 'no existing page covers this search need'}",
        })
    return out


def cannibalization_similarity(cannibalization: list[dict], site_model: dict | None) -> list[dict]:
    """§58 — similarity score for each Search Console pair, plus the
    near-identical-title page pairs from the crawl."""
    toks = (site_model or {}).get("page_tokens") or {}

    def _toks(url):
        return toks.get(url) or set(normalize_keyword(re.sub(r"[/\-_]", " ", url))["core_tokens"])

    for c in cannibalization:
        a, b = _toks(c["preferred_url"]), _toks(c["other_urls"][0]) if c.get("other_urls") else set()
        c["similarity"] = round(len(a & b) / len(a | b), 2) if a | b else 0.0
        c.setdefault("source", "Search Console")
    for d in (site_model or {}).get("duplicate_titles") or []:
        cannibalization.append({
            "query": "(near-identical page titles)", "preferred_url": d["preferred_url"], "other_urls": [d["other_url"]],
            "risk": "Medium", "action": "Merge" if d["similarity"] >= 0.9 else "Differentiate", "impressions": 0,
            "similarity": d["similarity"], "source": "Duplicate titles",
            "evidence": f"Two {d['page_type']} pages with {int(d['similarity'] * 100)}% title overlap.",
        })
    return cannibalization


_PATTERN_KINDS = ("Audience", "Geographic", "Product / Attribute", "Problem")


def programmatic_patterns(summaries: list[dict]) -> list[dict]:
    """§27/§59 — Entity × dimension patterns: a cluster whose keywords share
    one entity and vary by 3+ values of one modifier kind. Every pattern
    goes to human review (§62) and carries its SERP-validation gap."""
    out = []
    for s in summaries:
        rows = s["rows"]
        by_kind: dict[str, list[dict]] = {}
        for r in rows:
            for k in modifier_types(r.get("keyword") or ""):
                if k in _PATTERN_KINDS:
                    by_kind.setdefault(k, []).append(r)
        for kind, members in by_kind.items():
            if len(members) < 3:
                continue
            demand = sum(_demand(r) for r in members)
            avg = demand / len(members)
            out.append({
                "pattern": f"{s['name'].split(' — ')[0]} × {kind.lower()}", "dimension_a": s["name"].split(" — ")[0],
                "dimension_b": kind, "example_queries": [r["keyword"] for r in members[:4]], "search_demand": int(demand),
                "business_relevance": round(sum(_REL.get(r.get("relevance_status") or "", 0.6) for r in members) / len(members), 2),
                "serp_validation": "Not available (no SERP data source yet)",
                "uniqueness_potential": "High" if avg >= 100 else "Medium" if avg >= 30 else "Low",
                "content_requirements": {"Audience": "audience-specific use cases, examples, pricing",
                                         "Geographic": "real local details and proof",
                                         "Product / Attribute": "a spec/comparison table per variant",
                                         "Problem": "cause and fix steps per problem"}[kind],
                "risk": "Thin pages" if avg < 30 else "Low",
                "recommendation": "Build as templated pages after review" if avg >= 30 else "Cover as sections on the hub page",
                "cluster": s["name"],
            })
            for r in members:
                r["programmatic_flag"] = True
    out.sort(key=lambda p: -p["search_demand"])
    return out[:15]


def apply_history_recalibration(summaries: list[dict], multipliers: dict[tuple, float] | None) -> None:
    """§31 — nudges each cluster's opportunity score (and its keywords'
    opportunity_score) by its (cluster_type, business_rule) group's
    historical accept-rate multiplier. Every multiplier defaults to 1.0
    (no data yet, see keyword_history_service.recalibrate), so this is a
    no-op until real outcomes accumulate."""
    if not multipliers:
        return
    from app.services.keyword_history_service import multiplier_for
    for s in summaries:
        m = multiplier_for(s, multipliers)
        if m == 1.0:
            continue
        s["opportunity"] = round((s.get("opportunity") or 0) * m)
        for r in s["rows"]:
            r["opportunity_score"] = round((r.get("opportunity_score") or 0) * m)
            r["cluster_opportunity"] = s["opportunity"]


def extra_review_items(summaries: list[dict], patterns: list[dict], topics: list[dict] | None = None) -> list[dict]:
    """§62 — the review types the first queue didn't produce: programmatic
    opportunities, conflicting signals (Google ranks a page whose type
    contradicts the searches' intent), and §14 intent-similar sibling
    clusters that share no wording (the AI grouping step is what would
    normally merge them — see docs/keyword_engine_spec_coverage.md,
    'Known limit')."""
    items = [{"type": "Programmatic opportunity", "item": p["pattern"],
              "detail": f"{p['search_demand']:,}/mo; risk: {p['risk']}"} for p in patterns[:10]]
    for t in topics or []:
        for pair in t.get("intent_similar_pairs") or []:
            items.append({"type": "Possible same-need clusters", "item": f"{pair['a']} / {pair['b']}",
                          "detail": f"{int(pair['intent_similarity'] * 100)}% intent overlap — confirm whether these answer "
                                    "the same need and should share one page."})
    for s in sorted(summaries, key=lambda s: -(s.get("opportunity") or 0)):
        if s.get("roadmap_priority") not in ("High", "Medium"):
            continue
        family = s.get("dominant_family") or "Commercial"
        for r in s["rows"]:
            url = r.get("current_url")
            if not url:
                continue
            ptype = classify_page_type(url)
            if (family == "Commercial" and ptype in ("blog", "comparison")) or (family == "Informational" and ptype == "commercial"):
                items.append({"type": "Conflicting signals", "item": s["name"],
                              "detail": f"{family.lower()} searches, but Google ranks a {ptype} page ({url})"})
                break
    return items[:20]
