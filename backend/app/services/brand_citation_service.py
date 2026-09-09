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

import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 8.0

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/api.php"


def _own_domain(url: str) -> str:
    return urlparse(url if url.startswith("http") else f"https://{url}").netloc.lower().removeprefix("www.")


def _domain_token(client_domain: str) -> str:
    """Domain root, e.g. "lumberfi" from "www.lumberfi.com" — the actual
    coined/distinctive brand identifier, as opposed to client.name which
    can be a short, generic-word company display name (e.g. "Lumber")."""
    return _own_domain(client_domain).split(".")[0]


# A single-word brand name that also happens to be an ordinary dictionary
# word (a client literally named "Lumber", "Ramp", "Brex", "Notion", etc.)
# collides with unrelated news/Wikipedia results about the word itself —
# teammate QA on the last report flagged exactly this: a citation search
# "misunderstanding the brand name". Below this length isn't checked
# against the dictionary list (too many real short brand names would
# false-positive as generic); this is deliberately a short, conservative
# list of genuinely common nouns rather than a broad NLP dictionary check,
# since a false "needs disambiguation" on a real distinctive brand name
# would just as wrongly suppress a genuine citation.
_COMMON_WORD_BRANDS = {
    "lumber", "ramp", "brex", "square", "block", "stripe", "wise", "chime", "current",
    "apple", "amazon", "target", "shell", "arm", "oracle", "gap", "docker", "slack",
}


def _needs_disambiguation(brand_name: str) -> bool:
    """True when brand_name alone is too generic a search term to trust a
    plain-text match against — single word, and either a known common-word
    brand or short enough (<=5 chars) to plausibly collide with everyday
    usage."""
    words = brand_name.strip().split()
    if len(words) != 1:
        return False  # a multi-word name is already self-disambiguating
    word = words[0].lower()
    return word in _COMMON_WORD_BRANDS or len(word) <= 5


def search_brand_mentions(
    brand_name: str, client_domain: str, industry_terms: list[str] | None = None, max_results: int = 6,
) -> list[dict] | None:
    """Returns [{"title", "url", "description"}] for real news results
    mentioning the brand, excluding the client's own site (that's not a
    citation, it's the site itself) — or None if the request failed
    outright. Never raises; a failed lookup here should never take down
    report generation.

    When brand_name alone is too generic to trust (_needs_disambiguation),
    a result must ALSO mention either the domain's own root token (e.g.
    "lumberfi") or one of the client's real industry terms (e.g.
    "payroll", "construction" — from the extracted Company Overview) in
    its title or publisher before counting as a real citation. Domain-
    token alone was tried first and rejected: real press coverage almost
    always uses the display brand name ("Lumber"), never the raw domain
    string, so requiring the domain token literally in the text filtered
    out genuine mentions along with the generic-word noise. Industry-term
    corroboration is the signal that actually distinguishes "Lumber the
    payroll company" coverage from "lumber the building material" without
    silently discarding real citations — a plain "Lumber" news search
    otherwise returns constant false positives, exactly the complaint on
    the last report."""
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

    require_corroboration = _needs_disambiguation(brand_name)
    corroboration_terms = []
    if require_corroboration:
        domain_token = _domain_token(client_domain)
        if domain_token:
            corroboration_terms.append(domain_token)
        corroboration_terms += [t.strip().lower() for t in (industry_terms or []) if t and t.strip()]

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
        if require_corroboration and corroboration_terms:
            haystack = f"{title} {publisher}".lower()
            matched = sum(1 for term in corroboration_terms if re.search(rf"\b{re.escape(term)}\b", haystack))
            # A single incidental term match isn't enough when several are
            # available — confirmed live: a lumber-the-material article
            # about "residential construction" matched the single term
            # "construction" alone. Real coverage of the actual company
            # plausibly mentions its business in more than one of these
            # terms at once (e.g. "payroll" AND "construction" together for
            # a construction-payroll company); a generic-word collision
            # essentially never does.
            required = 2 if len(corroboration_terms) >= 2 else 1
            if matched < required:
                continue  # generic-word collision, e.g. lumber-the-material news — not a real brand citation
        mentions.append({"title": title, "url": link, "description": publisher})
        if len(mentions) >= max_results:
            break
    return mentions


def check_wikipedia_presence(
    brand_name: str, client_domain: str | None = None, industry_terms: list[str] | None = None,
) -> dict | None:
    """Returns {"title", "url"} for the best-matching Wikipedia page if one
    exists for this brand name, else None (no page found, or the request
    failed — treated the same way, since either means "nothing to cite").
    Free, unlimited, no key — always attempted.

    For a generic-word brand name (_needs_disambiguation), the exact-title-
    substring check alone isn't enough — a client named "Lumber" would
    trivially match Wikipedia's actual "Lumber" (the building material)
    article, which has "Lumber" in the title by definition but is not the
    company. The result's own search snippet is also checked for the
    domain token or a real industry term before it counts as a citation."""
    try:
        resp = httpx.get(
            WIKIPEDIA_SEARCH_URL,
            params={
                "action": "query", "list": "search", "srsearch": brand_name,
                "format": "json", "srlimit": 1, "srprop": "snippet",
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
    hit = hits[0]
    title = hit.get("title")
    if not title:
        return None
    # A search "hit" is fuzzy-matched by MediaWiki and can be a same-word
    # but unrelated page (e.g. a common surname) — only treat it as a real
    # citation when the brand name actually appears in the matched title,
    # not just somewhere in that page's body text.
    if brand_name.strip().lower() not in title.lower():
        return None

    if _needs_disambiguation(brand_name):
        corroboration_terms = []
        if client_domain:
            domain_token = _domain_token(client_domain)
            if domain_token:
                corroboration_terms.append(domain_token)
        corroboration_terms += [t.strip().lower() for t in (industry_terms or []) if t and t.strip()]
        if corroboration_terms:
            snippet = re.sub(r"<[^>]+>", "", hit.get("snippet") or "").lower()
            haystack = f"{title.lower()} {snippet}"
            matched = sum(1 for term in corroboration_terms if re.search(rf"\b{re.escape(term)}\b", haystack))
            required = 2 if len(corroboration_terms) >= 2 else 1
            if matched < required:
                return None  # e.g. Wikipedia's actual "Lumber" article — real page, wrong entity

    return {"title": title, "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"}
