"""Finds where a client's brand is ALREADY cited externally — directories,
press, review sites — grounding the Brand Citation Opportunities slide in
real search results instead of just a generic list of directories to
submit to. Two free data sources, no AI call:

- Brave Search API (https://api.search.brave.com) — 2,000 free queries/month,
  no card required. Gated on settings.brave_api_key; silently skipped
  (same discipline as every optional section in this app) when no key is
  configured, so this ships now and activates the moment a key is added.
- Wikipedia's search API — free, unlimited, no key. Answers the one
  specific GEO signal the SPOTONIX reference deck calls out by name:
  does this brand have a Wikipedia/Wikidata entity AI engines can cite.
"""

import re
from urllib.parse import urlparse

import httpx

from app.config import settings

USER_AGENT = "SEOAuditTool/1.0 (+https://seoaudittool.local/bot-info)"
TIMEOUT = 8.0

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/api.php"


def _own_domain(url: str) -> str:
    return urlparse(url if url.startswith("http") else f"https://{url}").netloc.lower().removeprefix("www.")


def search_brand_mentions(brand_name: str, client_domain: str, max_results: int = 6) -> list[dict] | None:
    """Returns [{"title", "url", "description"}] for real web results
    mentioning the brand, excluding the client's own site (that's not a
    citation, it's the site itself) — or None if no Brave API key is
    configured or the request failed outright. Never raises; a failed
    lookup here should never take down report generation."""
    if not settings.brave_api_key:
        return None

    own = _own_domain(client_domain)
    try:
        resp = httpx.get(
            BRAVE_SEARCH_URL,
            params={"q": f'"{brand_name}"', "count": max_results + 5},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": settings.brave_api_key,
                "User-Agent": USER_AGENT,
            },
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    results = (data.get("web") or {}).get("results") or []
    mentions = []
    for r in results:
        url = r.get("url") or ""
        if not url or _own_domain(url) == own:
            continue
        mentions.append({
            "title": r.get("title") or url,
            "url": url,
            "description": re.sub(r"<[^>]+>", "", r.get("description") or ""),
        })
        if len(mentions) >= max_results:
            break
    return mentions


def check_wikipedia_presence(brand_name: str) -> dict | None:
    """Returns {"title", "url"} for the best-matching Wikipedia page if one
    exists for this brand name, else None (no page found, or the request
    failed — treated the same way, since either means "nothing to cite").
    Free, unlimited, no key — always attempted regardless of Brave
    availability."""
    try:
        resp = httpx.get(
            WIKIPEDIA_SEARCH_URL,
            params={
                "action": "query", "list": "search", "srsearch": brand_name,
                "format": "json", "srlimit": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    hits = (data.get("query") or {}).get("search") or []
    if not hits:
        return None
    title = hits[0].get("title")
    if not title:
        return None
    # A search "hit" is fuzzy-matched by MediaWiki and can be a same-word
    # but unrelated page (e.g. a common surname) — only treat it as a real
    # citation when the brand name actually appears in the matched title,
    # not just somewhere in that page's body text.
    if brand_name.strip().lower() not in title.lower():
        return None
    return {"title": title, "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}
