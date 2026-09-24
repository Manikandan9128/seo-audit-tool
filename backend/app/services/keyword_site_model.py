"""Universal SEO Keyword engine — website model (2026-09-24).

Deterministic, no AI call, built once per report from the crawl
(Semrush Site Audit pages) + the company overview + Search Console page
clicks. Everything the keyword strategy needs to know about the SITE:

  §3   entity graph: business -> products / services / industries /
       audiences / locations / use cases
  §4   per-URL analysis: page type, primary entity, topic, audience,
       intent, funnel stage, geography, content depth, internal-link
       role, business purpose, traffic
  §11  audiences the site itself serves (industry / "for X" pages + ICP)
  §12  service areas below country level (location pages + overview)
  §24  pages with near-identical titles (duplicate-page cannibalization)
  §57  the site's own URL sections, for suggested new-page URLs
"""

import re
from collections import Counter

from app.services.keyword_intelligence_service import (
    _GEO_WORDS,
    _STOP_WORDS,
    display_phrase,
    funnel_stage,
    keyword_audience,
    normalize_keyword,
    smart_title,
)
from app.services.keyword_relevance_service import classify_page_type, is_live_target_page

_SECTION_KIND = {
    "product": "products", "products": "products", "models": "products", "model": "products",
    "service": "services", "services": "services", "solutions": "services", "solution": "services",
    "consulting": "services", "what-we-do": "services",
    "industries": "industries", "industry": "industries", "sectors": "industries", "verticals": "industries",
    "locations": "locations", "location": "locations", "branches": "locations", "offices": "locations",
    "dealers": "locations", "stores": "locations", "cities": "locations", "areas": "locations",
    "use-cases": "use_cases", "use-case": "use_cases", "usecases": "use_cases", "applications": "use_cases",
    "for": "audiences", "who-we-serve": "audiences", "customers": "audiences",
    "features": "features", "feature": "features", "integrations": "integrations", "integration": "integrations",
    "docs": "documentation", "documentation": "documentation", "help": "documentation", "support-center": "documentation",
    "technologies": "technologies", "technology": "technologies", "partners": "technologies",
}
_PAGE_INTENT = {
    "commercial": "Commercial", "blog": "Informational", "comparison": "Comparison", "location": "Local",
    "home": "Navigational", "utility": "Navigational", "other": "Commercial",
}
_PAGE_PURPOSE = {
    "commercial": "Convert buyers (product/service page)", "blog": "Educate and pass readers to product pages",
    "comparison": "Win buyers comparing options", "location": "Local enquiries and visits",
    "home": "Brand entry point", "utility": "Support / account", "other": "Supporting page",
}


def _path(url: str) -> str:
    return re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+", "", (url or "").strip(), flags=re.I).split("?")[0].split("#")[0]


def _slug_name(seg: str) -> str:
    seg = re.sub(r"\.[a-z]{2,5}$", "", seg.lower())
    seg = re.sub(r"[-_]?\d+$", "", seg)
    return seg.replace("-", " ").replace("_", " ").strip()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _target_page_authority(backlink_rows: list[dict] | None) -> dict[str, dict]:
    """§23 per-URL authority — grouped from the SAME Backlinks export
    already parsed (target_url + the linking page's own "Page ascore"),
    not a new data source: how many distinct domains link to this URL, and
    their average authority score."""
    by_target: dict[str, dict] = {}
    for r in backlink_rows or []:
        target = (r.get("target_url") or "").rstrip("/").lower()
        if not target:
            continue
        domain = re.sub(r"^www\.", "", re.sub(r"^[a-z][a-z0-9+.-]*://", "", (r.get("source_url") or "").lower()).split("/")[0])
        entry = by_target.setdefault(target, {"domains": set(), "scores": []})
        if domain:
            entry["domains"].add(domain)
        score = r.get("domain_score")
        if score not in (None, ""):
            try:
                entry["scores"].append(float(score))
            except (TypeError, ValueError):
                pass
    return {
        url: {"referring_backlinks": len(e["domains"]),
              "authority_score": round(sum(e["scores"]) / len(e["scores"])) if e["scores"] else None}
        for url, e in by_target.items()
    }


def build_site_model(site_audit_pages_rows: list[dict] | None, company_overview: dict | None = None,
                     page_clicks: dict[str, float] | None = None, backlink_rows: list[dict] | None = None) -> dict:
    """Returns {"graph": {...}, "pages": [...], "audiences": [...],
    "service_areas": [...], "sections": {page_type: "/section/"},
    "duplicate_titles": [...], "page_tokens": {url: set}}."""
    overview = company_overview or {}
    graph: dict[str, set] = {k: set() for k in (
        "products", "services", "industries", "audiences", "locations", "use_cases", "features", "integrations",
        "documentation", "technologies")}
    for key, field in (("products", "products"), ("services", "solutions"), ("industries", "industries")):
        for v in overview.get(field) or []:
            if v and len(str(v)) <= 60:
                graph[key].add(str(v).strip().lower())
    for v in overview.get("primary_buyers") or []:
        if v:
            graph["audiences"].add(str(v).strip().lower())

    live = [r for r in site_audit_pages_rows or [] if r.get("page_url") and is_live_target_page(r)]
    section_counter: dict[str, Counter] = {}
    pages = []
    clicks = {k.rstrip("/").lower(): v for k, v in (page_clicks or {}).items()}
    authority = _target_page_authority(backlink_rows)
    for r in live:
        url = r["page_url"]
        segs = [s for s in _path(url).lower().split("/") if s]
        for i, seg in enumerate(segs[:-1]):
            kind = _SECTION_KIND.get(seg)
            if kind:
                name = _slug_name(segs[i + 1])
                if name and not name.isdigit():
                    graph[kind].add(name)
        if segs and segs[0].startswith("for-"):
            graph["audiences"].add(_slug_name(segs[0][4:]))
        ptype = classify_page_type(url)
        if len(segs) >= 2:
            section_counter.setdefault(ptype, Counter())[f"/{segs[0]}/"] += 1
        title = r.get("page_title") or (_slug_name(segs[-1]) if segs else "home")
        text = f"{title} {' '.join(segs)}".lower()
        entity = display_phrase(title) if title else ""
        intent = _PAGE_INTENT.get(ptype, "Commercial")
        geo = sorted({w for w in re.findall(r"[a-z]+", text) if w in _GEO_WORDS})
        words = _num(r.get("word_count"))
        inlinks = r.get("incoming_internal_links")
        depth = r.get("crawl_depth")
        pages.append({
            "url": url, "page_type": ptype, "primary_entity": smart_title(entity)[:80],
            "topic": _topic_token(title), "audience": keyword_audience(text),
            "intent": intent, "funnel_stage": funnel_stage(title, intent if intent != "Commercial" else None),
            "geography": ", ".join(geo) or None,
            "content_depth": ("Deep" if words >= 1500 else "Medium" if words >= 600 else "Thin") if words else "Unknown",
            "incoming_internal_links": int(_num(inlinks)) if inlinks not in (None, "") else None,
            "crawl_depth": int(_num(depth)) if depth not in (None, "") else None,
            "internal_link_role": "Hub" if len(segs) <= 1 and ptype != "home" else ("Home" if ptype == "home" else "Leaf"),
            "business_purpose": _PAGE_PURPOSE.get(ptype, "Supporting page"),
            "traffic_clicks": clicks.get(url.rstrip("/").lower()),
            "referring_backlinks": authority.get(url.rstrip("/").lower(), {}).get("referring_backlinks"),
            "authority_score": authority.get(url.rstrip("/").lower(), {}).get("authority_score"),
        })

    # A section page with children is a hub, not a leaf.
    parents = Counter("/" + "/".join([s for s in _path(p["url"]).lower().split("/") if s][:-1]) for p in pages)
    for p in pages:
        if parents.get(_path(p["url"]).lower().rstrip("/"), 0) >= 2:
            p["internal_link_role"] = "Hub"

    audiences = sorted(graph["audiences"] | graph["industries"])
    service_areas = sorted(graph["locations"] | {str(v).lower() for v in overview.get("locations") or [] if v})
    sections = {t: c.most_common(1)[0][0] for t, c in section_counter.items() if c}
    return {
        "graph": {k: sorted(v)[:60] for k, v in graph.items()},
        "business": overview.get("company_name") or overview.get("name"),
        "pages": pages,
        "audiences": audiences,
        "service_areas": service_areas,
        "target_country": overview.get("target_country"),
        "sections": sections,
        "duplicate_titles": _duplicate_titles(pages),
        "page_tokens": {p["url"]: set(normalize_keyword(f"{p['primary_entity']} {_path(p['url'])}")["core_tokens"])
                        for p in pages},
    }


def _topic_token(title: str) -> str | None:
    tokens = [t for t in normalize_keyword(title or "")["core_phrase"].split() if t not in _STOP_WORDS]
    return tokens[-1] if tokens else None


def _duplicate_titles(pages: list[dict], limit: int = 15) -> list[dict]:
    """§24/§58 — live pages of the SAME type whose titles are near-identical
    (token Jaccard >= 0.8, boilerplate words shared by >15% of titles
    removed). Preferred page = more clicks, then shallower."""
    toks = {p["url"]: set(normalize_keyword(p["primary_entity"] or "")["core_tokens"]) for p in pages}
    spread = Counter(t for s in toks.values() for t in s)
    common = {t for t, n in spread.items() if n > max(4, 0.15 * len(pages))}
    out = []
    by_type: dict[str, list[dict]] = {}
    for p in pages:
        if p["page_type"] not in ("home", "utility"):
            by_type.setdefault(p["page_type"], []).append(p)
    for group in by_type.values():
        for i, a in enumerate(group):
            fa, ta = toks[a["url"]], toks[a["url"]] - common
            if len(ta) < 2:
                continue
            for b in group[i + 1:]:
                fb, tb = toks[b["url"]], toks[b["url"]] - common
                if len(tb) < 2:
                    continue
                # Near-identical full titles, or one title's distinctive
                # topic (3+ words) wholly inside the other's ("Dynamic Data
                # Masking" / "... in SQL Server"). Two pages sharing only
                # a brand/platform word ("Google Cloud SQL" / "Google Cloud
                # Platform") are not duplicates.
                sim = len(fa & fb) / len(fa | fb)
                subset = (ta <= tb or tb <= ta) and min(len(ta), len(tb)) >= 3
                if sim >= 0.75 or subset:
                    pref, other = sorted((a, b), key=lambda p: (-(p["traffic_clicks"] or 0), p["crawl_depth"] or 99, len(p["url"])))
                    out.append({"preferred_url": pref["url"], "other_url": other["url"], "similarity": round(sim, 2),
                                "page_type": a["page_type"]})
                    if len(out) >= limit:
                        return out
    return out


def keyword_audience_from_site(keyword: str, site_model: dict | None) -> str | None:
    """§11 — an audience/industry the SITE serves, named in the keyword
    ("payroll software for construction" on a site with /industries/
    construction). None when the keyword names none of them."""
    if not site_model:
        return None
    text = f" {' '.join(re.findall(r'[a-z0-9]+', (keyword or '').lower()))} "
    for aud in site_model.get("audiences") or []:
        a = " ".join(re.findall(r"[a-z0-9]+", aud))
        if len(a) >= 4 and f" {a} " in text:
            return smart_title(a)
    return None


def geo_served(keyword_geo: str | None, site_model: dict | None) -> str | None:
    """§12 — whether a location named in the keyword is one the business
    lists (a location page / overview location), is only covered by its
    country-level target, or isn't listed at all."""
    if not keyword_geo or not site_model:
        return None
    areas = " ".join(site_model.get("service_areas") or [])
    places = [g.strip() for g in keyword_geo.split(",") if g.strip()]
    if any(p in areas for p in places):
        return "Served (listed location)"
    country = (site_model.get("target_country") or "").lower()
    if country and ("global" in country or any(p in country for p in places)):
        return "Served (country-level)"
    return "Not listed — check before targeting"
