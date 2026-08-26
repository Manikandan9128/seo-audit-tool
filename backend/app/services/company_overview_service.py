"""Crawls a client's About/Products/legal pages and uses Gemini to extract a
structured company overview (name, description, products, KPIs, registration
info) for the report's "About the client" slide."""

import json
import re
from urllib.parse import urljoin, urlparse

import time

import httpx

from app.config import settings
from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text
from app.services.product_catalogue_service import crawl_product_catalogue

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 15.0

PAGE_HINTS = [
    "about", "about-us", "company", "products", "product", "services", "service",
    "our-products", "solutions", "solution", "collections", "collection", "shop",
    "terms", "legal", "privacy", "faq", "how-it-works",
]

EXTRACTION_PROMPT = """You are extracting factual company information from raw website text for an SEO audit report. \
Only use facts explicitly present in the text below — never invent or infer numbers, registration IDs, or claims that \
aren't stated. If a field isn't present, use null.

The five ICP fields (target_country, primary_buyers, daily_users, beneficiaries, target_market) are the one exception: \
these are rarely spelled out verbatim, so infer them from context — pricing tier language, job titles mentioned, \
currency/region cues, "built for X teams" phrasing, etc. Keep every inference grounded in something actually implied \
by the text; use null rather than guessing if there's truly nothing to go on.

Return ONLY valid JSON matching this shape, no markdown fences, no commentary:
{
  "company_name": string or null,
  "description": string or null,   // 2-4 sentence summary of what the company does, in your own words but grounded only in the text
  "products": [string],            // flat list of product/service names mentioned
  "products_by_category": {string: [string]},  // same products grouped under short category headings, e.g. {"Contact Center Software": ["IVR", "Live Chat"]}. Empty object if no clear categories.
  "solutions": [string],           // core solution/service areas as short phrases, e.g. "Omnichannel Experience, Voice, Chat, Email, SMS in a single unified view"
  "industries": [string],          // industries/verticals the company serves, if stated
  "kpis": [string],                 // e.g. "95% claim approval rate", "1.2 million policies issued" — verbatim or close paraphrase, only if stated
  "registration_info": string or null,  // company number, regulatory registration, licensing body, etc. if present
  "contact": string or null,        // phone/email if present
  "target_country": string or null,      // e.g. "Global", "Primary USA" — inferred from region/currency/language cues
  "primary_buyers": [string],            // decision-maker job titles who'd purchase this, e.g. "CHRO", "Head of People", "VP of HR", "Director of HR"
  "daily_users": [string],               // who actually uses the product day-to-day, e.g. "HR teams", "People Ops"
  "beneficiaries": [string],             // who benefits from it without being a direct user, e.g. "Managers", "Team Leads", "Individual Contributors", "Executives"
  "target_market": string or null        // company size/segment sold to, e.g. "Mid Market and Enterprise"
}

WEBSITE TEXT:
---
{content}
---
"""


def _fetch(url: str) -> httpx.Response | None:
    try:
        return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        return None


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sitemap_hint_urls(base_url: str, base_netloc: str) -> list[str]:
    from xml.etree import ElementTree

    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        resp = _fetch(urljoin(base_url, path))
        if resp is None or resp.status_code >= 400:
            continue
        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            continue
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text for el in root.findall(".//sm:url/sm:loc", ns) if el.text]
        hits = []
        for loc in locs:
            if urlparse(loc).netloc != base_netloc:
                continue
            if any(hint in urlparse(loc).path.lower() for hint in PAGE_HINTS):
                hits.append(loc)
        if hits:
            return hits
    return []


def _discover_candidate_urls(website_url: str, limit: int = 18) -> list[str]:
    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    home_resp = _fetch(website_url)
    if home_resp is None or home_resp.status_code >= 400:
        return [website_url]

    links = re.findall(r'href=["\'](.*?)["\']', home_resp.text, re.IGNORECASE)
    candidates = []
    seen = set()
    for href in links:
        full = urljoin(base_url, href)
        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue
        path = urlparse(full).path.lower()
        if any(hint in path for hint in PAGE_HINTS) and full not in seen:
            seen.add(full)
            candidates.append(full)
        if len(candidates) >= limit:
            break

    if len(candidates) < limit:
        for url in _sitemap_hint_urls(base_url, parsed.netloc):
            if url not in seen:
                seen.add(url)
                candidates.append(url)
            if len(candidates) >= limit:
                break

    return [website_url] + candidates[:limit]


def gather_site_text(website_url: str, max_chars: int = 45000) -> str:
    urls = _discover_candidate_urls(website_url)

    catalogue = crawl_product_catalogue(website_url, limit=15)
    for p in catalogue.get("products", []):
        if p["url"] not in urls:
            urls.append(p["url"])

    chunks = []
    total = 0
    for url in urls:
        resp = _fetch(url)
        if resp is None or resp.status_code >= 400:
            continue
        text = _strip_html(resp.text)[:4000]
        if not text:
            continue
        chunk = f"[Page: {url}]\n{text}\n"
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n".join(chunks)[:max_chars]


def extract_company_overview(website_url: str) -> dict:
    """Returns a structured overview dict, or a dict with an 'error' key if
    extraction isn't available (no API key, no crawlable content, bad response).
    Tries Gemini first, falls back to Claude — either key alone is enough."""
    if not settings.gemini_api_key and not settings.claude_api_key:
        return {"error": "No Gemini or Claude API key configured — add one in Settings"}

    site_text = gather_site_text(website_url)
    if not site_text.strip():
        return {"error": "Could not crawl any readable content from this site"}

    prompt = EXTRACTION_PROMPT.replace("{content}", site_text)

    raw = None
    last_error = None
    for attempt in range(3):
        try:
            raw, _provider = generate_text(prompt)
            break
        except NoAIProviderConfigured as e:
            last_error = e
            if "UNAVAILABLE" in str(e) or "503" in str(e):
                time.sleep(2 * (attempt + 1))
                continue
            return {"error": str(e)}
    if raw is None:
        return {"error": str(last_error) if last_error else "AI request failed after retries"}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Gemini did not return valid JSON", "raw": raw[:500]}

    return data
