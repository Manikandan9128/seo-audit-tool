"""Finds where a client's brand is ALREADY cited externally — press,
directories, review sites — grounding the Brand Citation Opportunities
slide in real results instead of just a generic list of directories to
submit to. Two free data sources, no AI call, no API key, no card
required anywhere in the chain:

- Google News RSS (news.google.com/rss/search) — official RSS feed,
  free, unlimited, no key. Press-specific mentions.
- Wikipedia's search API — free, unlimited, no key. Answers the one
  specific GEO signal the SPOTONIX reference deck calls out by name:
  does this brand have a Wikipedia/Wikidata entity AI engines can cite.

Brave Search API was tried and dropped here (2026-09-03): it now
requires a credit card at signup even for the free plan, which this app
avoids categorically. DuckDuckGo's HTML search endpoint was also tried
and dropped the same day — live-tested and found bot-walled (returns a
CAPTCHA challenge page, not results) for non-browser requests, so it's
not usable server-side regardless of cost.
"""

import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 8.0

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/api.php"


def _own_domain(url: str) -> str:
    return urlparse(url if url.startswith("http") else f"https://{url}").netloc.lower().removeprefix("www.")


def search_brand_mentions(brand_name: str, client_domain: str, max_results: int = 6) -> list[dict] | None:
    """Returns [{"title", "url", "description"}] for real news results
    mentioning the brand, excluding the client's own site (that's not a
    citation, it's the site itself) — or None if the request failed
    outright. Never raises; a failed lookup here should never take down
    report generation."""
    own = _own_domain(client_domain)
    try:
        resp = httpx.get(
            GOOGLE_NEWS_RSS_URL,
            params={"q": f'"{brand_name}"', "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        root = ET.fromstring(resp.content)
    except (httpx.HTTPError, ET.ParseError, ValueError):
        return None

    mentions = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = item.find("source")
        source_url = source.get("url") if source is not None else None
        publisher = (source.text or "").strip() if source is not None else ""
        if not title or not link:
            continue
        if source_url and _own_domain(source_url) == own:
            continue
        mentions.append({"title": title, "url": link, "description": publisher})
        if len(mentions) >= max_results:
            break
    return mentions


def check_wikipedia_presence(brand_name: str) -> dict | None:
    """Returns {"title", "url"} for the best-matching Wikipedia page if one
    exists for this brand name, else None (no page found, or the request
    failed — treated the same way, since either means "nothing to cite").
    Free, unlimited, no key — always attempted."""
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
