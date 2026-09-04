"""Attaches a target-URL recommendation to every SEO finding (keyword gap,
technical issue, content opportunity) instead of leaving "what page does this
even apply to" for the reader to work out.

Resolution follows a fixed 7-level hierarchy, most-authoritative source
first — the first level that produces a real answer wins, everything below
it is never consulted for that recommendation:

1. Existing ranking URL from Semrush (own-site Organic Positions export —
   the keyword already ranks somewhere, that page is unambiguously "the"
   target)
2. GSC query-to-page relationship (Search Console's own query x page
   crosstab — real click/impression evidence, not an export someone had to
   remember to upload)
3. Existing crawl URL (a crawled page's title/path already covers this
   topic, even though Semrush/GSC have no ranking data for it yet)
4. Canonical URL (the matched page defers its ranking signals elsewhere —
   redirect the recommendation to the real target)
5. Search intent (Semrush's Intent column, when present, decides
   informational vs. commercial page shape)
6. Page type (keyword text alone decides page shape when intent is
   missing — same word-list classifier the Content SEO slide uses)
7. Keyword-to-URL relevance (last resort: loose token overlap against every
   crawled page — enough to suggest an internal link, not enough to claim
   the page already covers the topic)

Nothing here calls out to Semrush/GSC/the crawler directly — build_context()
takes already-fetched rows (the report route already pulls all of these for
other slides) and every resolve_* function is pure over that context, so this
is independently testable and never adds a network round-trip of its own.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.services.keyword_relevance_service import _classify_keyword_page_category

# Every recommendation must use one of these 7 actions — see module docstring.
OPTIMIZE_EXISTING = "OPTIMIZE_EXISTING"
CREATE_NEW = "CREATE_NEW"
MERGE = "MERGE"
REDIRECT = "REDIRECT"
INTERNAL_LINK = "INTERNAL_LINK"
UPDATE_TEMPLATE = "UPDATE_TEMPLATE"
SITEWIDE = "SITEWIDE"

_EMPTY_MAPPING = {
    "target_url": None,
    "url_action": None,
    "target_page_type": None,
    "current_ranking_keyword": None,
    "current_position": None,
}

_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "with", "is", "are",
    "what", "how", "why", "vs", "versus", "your", "you", "best", "top",
}

_CANONICAL_URL_RE = re.compile(r"https?://[^\s,;]+")

# Coarse path-based page-type labels for a URL that already exists — distinct
# from _classify_keyword_page_category, which classifies a *keyword* that has
# no page yet. Checked in order; first match wins.
_PAGE_TYPE_PATH_SIGNALS = [
    ("Blog / Article", ["/blog", "/article", "/news", "/insights", "/resources"]),
    ("Comparison / Alternative", ["vs-", "-vs-", "/alternatives", "/comparison"]),
    ("Pricing", ["/pricing", "/plans"]),
    ("Product / Service", ["/product", "/service", "/solutions", "/features"]),
    ("Landing Page", ["/lp/", "/landing"]),
    ("About / Company", ["/about", "/company", "/team"]),
    ("Contact", ["/contact"]),
    ("Category / Listing", ["/category", "/collections"]),
]


def _num(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _normalize_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    return u.rstrip("/") or u


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def infer_page_type_from_url(url: str, title: str | None = None) -> str | None:
    path = (urlparse(url).path or "").lower()
    if path in ("", "/"):
        return "Homepage"
    for label, signals in _PAGE_TYPE_PATH_SIGNALS:
        if any(sig in path for sig in signals):
            return label
    # No path signal — fall back to the keyword-shape classifier against the
    # page's own title, since that's the best topic signal left.
    if title:
        return _classify_keyword_page_category(title)
    return None


def build_context(
    *,
    own_domain: str | None = None,
    own_organic_positions_rows: list[dict] | None = None,
    gsc_query_page_rows: list[dict] | None = None,
    crawl_pages: list[dict] | None = None,
    canonicalization_by_url: dict[str, str] | None = None,
) -> dict:
    """Assembles the lookup tables used by every resolve_* call for one
    report — call once per report, not once per recommendation.

    own_organic_positions_rows: rows from a Semrush "Organic Research >
        Positions" export for the client's OWN domain — {keyword, position,
        url, ...}.
    gsc_query_page_rows: rows from a GSC Search Analytics query dimensioned
        by BOTH query and page — {query, page, position, clicks,
        impressions}. Distinct from the query-only / page-only pulls used
        elsewhere in the report (those can't tell you which page a query
        actually ranks on).
    crawl_pages: merged list of every known page on the site — Semrush Site
        Audit "Crawled Pages" rows AND/OR our own crawler's pages — as
        {url, title}. Order doesn't matter; later duplicates of the same
        URL overwrite earlier ones.
    canonicalization_by_url: {url: raw Semrush "Canonicalization" cell} —
        used to redirect a match to its real canonical target when the
        matched page defers its own ranking signals elsewhere.
    """
    ranking_by_keyword: dict[str, dict] = {}
    for r in own_organic_positions_rows or []:
        kw = (r.get("keyword") or "").strip().lower()
        url = _normalize_url(r.get("url"))
        if not kw or not url:
            continue
        pos = _num(r.get("position"))
        existing = ranking_by_keyword.get(kw)
        if existing is None or (pos is not None and (existing["position"] is None or pos < existing["position"])):
            ranking_by_keyword[kw] = {"url": url, "position": pos}

    query_to_page: dict[str, dict] = {}
    for r in gsc_query_page_rows or []:
        q = (r.get("query") or "").strip().lower()
        page = _normalize_url(r.get("page"))
        if not q or not page:
            continue
        pos = _num(r.get("position"))
        existing = query_to_page.get(q)
        if existing is None or (r.get("impressions") or 0) > existing.get("_impressions", -1):
            query_to_page[q] = {"url": page, "position": pos, "_impressions": r.get("impressions") or 0}

    pages: dict[str, dict] = {}
    for p in crawl_pages or []:
        url = _normalize_url(p.get("url"))
        if not url:
            continue
        pages[url] = {"url": url, "title": p.get("title") or ""}

    return {
        "own_domain": own_domain,
        "ranking_by_keyword": ranking_by_keyword,
        "query_to_page": query_to_page,
        "pages": pages,
        "canonicalization_by_url": {
            _normalize_url(u): v for u, v in (canonicalization_by_url or {}).items() if u
        },
    }


def _canonical_target(context: dict, url: str) -> str | None:
    raw = context["canonicalization_by_url"].get(url)
    if not raw or "self" in str(raw).lower():
        return None
    m = _CANONICAL_URL_RE.search(str(raw))
    return _normalize_url(m.group(0)) if m else None


def _best_crawl_match(context: dict, keyword_tokens: set[str], require_full_overlap: bool) -> str | None:
    """require_full_overlap=True is level 3 (strong: every keyword token
    appears in the page's title) — require_full_overlap=False is level 7
    (weak: any overlap at all, best-scoring page wins)."""
    if not keyword_tokens:
        return None
    best_url, best_score = None, 0
    for url, page in context["pages"].items():
        title_tokens = _tokens(page["title"]) | _tokens(urlparse(url).path.replace("-", " ").replace("/", " "))
        overlap = keyword_tokens & title_tokens
        if not overlap:
            continue
        if require_full_overlap:
            if overlap == keyword_tokens:
                return url
            continue
        if len(overlap) > best_score:
            best_url, best_score = url, len(overlap)
    return best_url


def resolve_for_keyword(context: dict, keyword: str, intent: str | None = None) -> dict:
    """The 7-level hierarchy for one keyword-shaped recommendation (a
    keyword gap, a content opportunity). Returns {target_url, url_action,
    target_page_type, current_ranking_keyword, current_position}."""
    if not keyword:
        return dict(_EMPTY_MAPPING)
    kw_lower = keyword.strip().lower()

    # Level 1 — existing ranking URL from Semrush.
    hit = context["ranking_by_keyword"].get(kw_lower)
    if hit:
        target = hit["url"]
        canonical = _canonical_target(context, target)
        return {
            "target_url": canonical or target,
            "url_action": MERGE if canonical else OPTIMIZE_EXISTING,
            "target_page_type": infer_page_type_from_url(canonical or target, context["pages"].get(target, {}).get("title")),
            "current_ranking_keyword": keyword,
            "current_position": hit["position"],
        }

    # Level 2 — GSC query-to-page relationship.
    hit = context["query_to_page"].get(kw_lower)
    if hit:
        target = hit["url"]
        canonical = _canonical_target(context, target)
        return {
            "target_url": canonical or target,
            "url_action": MERGE if canonical else OPTIMIZE_EXISTING,
            "target_page_type": infer_page_type_from_url(canonical or target, context["pages"].get(target, {}).get("title")),
            "current_ranking_keyword": keyword,
            "current_position": hit["position"],
        }

    kw_tokens = _tokens(keyword)

    # Level 3 — existing crawl URL (strong topical match, no ranking data yet).
    target = _best_crawl_match(context, kw_tokens, require_full_overlap=True)
    if target:
        # Level 4 — canonical URL, only meaningful once a match exists to redirect.
        canonical = _canonical_target(context, target)
        return {
            "target_url": canonical or target,
            "url_action": MERGE if canonical else OPTIMIZE_EXISTING,
            "target_page_type": infer_page_type_from_url(canonical or target, context["pages"].get(target, {}).get("title")),
            "current_ranking_keyword": None,
            "current_position": None,
        }

    # Level 5/6 — search intent, falling back to page-type-from-keyword-text
    # (both handled inside _classify_keyword_page_category itself).
    page_type = _classify_keyword_page_category(keyword, intent)
    if page_type:
        return {
            "target_url": None,
            "url_action": CREATE_NEW,
            "target_page_type": page_type,
            "current_ranking_keyword": None,
            "current_position": None,
        }

    # Level 7 — keyword-to-URL relevance, last resort. Weak enough that the
    # right action is "link to it," not "this page already covers it."
    target = _best_crawl_match(context, kw_tokens, require_full_overlap=False)
    if target:
        return {
            "target_url": target,
            "url_action": INTERNAL_LINK,
            "target_page_type": infer_page_type_from_url(target, context["pages"].get(target, {}).get("title")),
            "current_ranking_keyword": None,
            "current_position": None,
        }

    return dict(_EMPTY_MAPPING)


def resolve_for_pages(context: dict, page_urls: list[str], url_action: str) -> dict:
    """For technical/on-page issues that are ALREADY page-scoped (a crawl
    already named the affected URLs) — no keyword hierarchy needed, just
    action classification + page type. `url_action` is supplied by the
    caller (see _TECHNICAL_ISSUE_ACTIONS in semrush_analysis_service.py)
    since a non-200 page needs REDIRECT, a schema gap needs UPDATE_TEMPLATE,
    etc. — the action follows the ISSUE KIND, not anything resolvable here."""
    urls = [_normalize_url(u) for u in page_urls if u]
    urls = [u for u in urls if u]
    if not urls:
        return dict(_EMPTY_MAPPING)
    sample = urls[0]
    page_type = infer_page_type_from_url(sample, context["pages"].get(sample, {}).get("title"))
    return {
        "target_url": urls[0] if len(urls) == 1 else urls,
        "url_action": url_action,
        "target_page_type": page_type,
        "current_ranking_keyword": None,
        "current_position": None,
    }


def resolve_sitewide(target_page_type: str | None = "Domain-wide") -> dict:
    """For domain-level recommendations with no single target page (backlink
    gap, overall traffic/keyword-count gap vs. a competitor)."""
    return {
        "target_url": None,
        "url_action": SITEWIDE,
        "target_page_type": target_page_type,
        "current_ranking_keyword": None,
        "current_position": None,
    }
