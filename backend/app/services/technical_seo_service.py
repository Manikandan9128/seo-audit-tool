import asyncio
import html
import json
import re
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

from app.integrations.gemini_client import summarize_company
from app.services.brand_color import extract_brand_color
from app.services.logo_service import find_logo_url

USER_AGENT = "SEOAuditTool/1.0 (+https://seoaudittool.local/bot-info)"
TIMEOUT = 8.0

# JSON-LD @type -> the same curated rich-result category fields the Semrush
# Structured Data export uses (STRUCTURED_DATA_COLUMN_ALIASES in
# semrush_parser.py) — lets crawl-sourced rows feed the exact same
# add_structured_data_slide unchanged when no Semrush export is uploaded.
_SCHEMA_TYPE_FIELDS = {
    "Article": "article_items", "BlogPosting": "article_items", "NewsArticle": "article_items",
    "FAQPage": "faq_items",
    "Product": "product_items",
    "Review": "review_items", "AggregateRating": "review_items",
    "LocalBusiness": "local_business_items",
    "HowTo": "howto_items",
    "BreadcrumbList": "breadcrumb_items",
    "JobPosting": "job_posting_items",
    "Event": "event_items",
}

# Best-effort text patterns for trust signals the manual reference decks call
# out by hand (Trustpilot rating, certifications, security badges, a visible
# phone number) — presence-only, not a quality judgment.
_TRUST_SIGNAL_PATTERNS = {
    "trustpilot": r"trustpilot",
    "certifications": r"\b(BBB|Better Business Bureau|ISO\s?9001|Norton Secured|McAfee Secure)\b",
    "security_badge": r"\b(SSL Secure|Secure Checkout|256-bit encryption)\b",
    "phone_number": r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
}

# Known third-party embed/chat-widget src patterns — a page referencing one
# of these whose src no longer resolves is a broken embed (matches manual
# deck findings like "Home page Instagram feed broken").
_EMBED_PATTERNS = [
    ("Instagram feed", re.compile(r'src=["\']([^"\']*instagram\.com/embed[^"\']*)["\']', re.IGNORECASE)),
    ("Facebook plugin", re.compile(r'src=["\']([^"\']*facebook\.com/plugins[^"\']*)["\']', re.IGNORECASE)),
    ("Intercom chat", re.compile(r'src=["\']([^"\']*widget\.intercom\.io[^"\']*)["\']', re.IGNORECASE)),
    ("Drift chat", re.compile(r'src=["\']([^"\']*js\.driftt\.com[^"\']*)["\']', re.IGNORECASE)),
    ("Tawk.to chat", re.compile(r'src=["\']([^"\']*embed\.tawk\.to[^"\']*)["\']', re.IGNORECASE)),
    ("Zendesk chat", re.compile(r'src=["\']([^"\']*static\.zdassets\.com[^"\']*)["\']', re.IGNORECASE)),
]

THIN_CONTENT_WORD_THRESHOLD = 300


def _fetch(url: str, retries: int = 1) -> httpx.Response | None:
    """One retry on any transport-level failure (timeout, connection reset,
    etc.) — a single momentary blip on this call used to cascade into
    robots.txt/sitemap checks, homepage-reachable, and the Company Overview/
    Solutions slides all failing together, since they all depend on this
    same fetch succeeding."""
    try:
        return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        if retries > 0:
            return _fetch(url, retries=retries - 1)
        return None


def _check_robots_txt(base_url: str) -> dict:
    robots_url = urljoin(base_url, "/robots.txt")
    resp = _fetch(robots_url)
    if resp is None or resp.status_code != 200:
        return {"present": False, "sitemap_urls": []}
    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    sitemap_urls = re.findall(r"(?i)sitemap:\s*(\S+)", resp.text)
    return {"present": True, "sitemap_urls": sitemap_urls}


def _check_sitemap(sitemap_url: str) -> dict:
    resp = _fetch(sitemap_url)
    if resp is None or resp.status_code != 200:
        return {"present": False, "url_count": 0}
    try:
        root = ElementTree.fromstring(resp.content)
        count = len(root)
        return {"present": True, "url_count": count}
    except ElementTree.ParseError:
        return {"present": False, "url_count": 0}


def _extract_meta(html_source: str) -> dict:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_source, re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1).strip()) if title_match else None

    description = None
    for meta_tag in re.findall(r"<meta\b[^>]*>", html_source, re.IGNORECASE):
        if not re.search(r'name=["\']description["\']', meta_tag, re.IGNORECASE):
            continue
        content_match = re.search(r'content=["\'](.*?)["\']', meta_tag, re.IGNORECASE | re.DOTALL)
        if content_match:
            description = html.unescape(content_match.group(1).strip())
        break

    viewport_present = bool(re.search(r'<meta[^>]+name=["\']viewport["\']', html_source, re.IGNORECASE))
    canonical_present = bool(re.search(r'<link[^>]+rel=["\']canonical["\']', html_source, re.IGNORECASE))
    h1_count = len(re.findall(r"<h1[^>]*>", html_source, re.IGNORECASE))
    structured_data_present = bool(re.search(r'application/ld\+json', html_source, re.IGNORECASE))
    og_tags_present = bool(re.search(r'<meta[^>]+property=["\']og:', html_source, re.IGNORECASE))

    return {
        "title": title,
        "title_length": len(title) if title else 0,
        "meta_description": description,
        "meta_description_length": len(description) if description else 0,
        "viewport_present": viewport_present,
        "canonical_present": canonical_present,
        "h1_count": h1_count,
        "structured_data_present": structured_data_present,
        "og_tags_present": og_tags_present,
    }


def extract_visible_text(html_source: str) -> str:
    """Strips scripts/styles/tags to leave readable homepage copy, for
    feeding to a summarization model."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_source)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_json_ld_types(html_source: str) -> dict[str, int]:
    """{field_name: count} for each curated schema category found in this
    page's JSON-LD <script> blocks. Handles @graph arrays and top-level
    lists — a single page can bundle several schema objects in one block."""
    counts: dict[str, int] = {}
    for block in re.findall(
        r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_source
    ):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        expanded = []
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                expanded.extend(item["@graph"])
            else:
                expanded.append(item)
        for item in expanded:
            if not isinstance(item, dict):
                continue
            raw_type = item.get("@type")
            for t in raw_type if isinstance(raw_type, list) else [raw_type]:
                field = _SCHEMA_TYPE_FIELDS.get(t)
                if field:
                    counts[field] = counts.get(field, 0) + 1
    return counts


def _extract_images(html_source: str) -> tuple[int, int]:
    """(total <img> tags, tags with a missing/empty alt attribute)."""
    imgs = re.findall(r"<img\b[^>]*>", html_source, re.IGNORECASE)
    missing = 0
    for tag in imgs:
        m = re.search(r'alt=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if not m or not m.group(1).strip():
            missing += 1
    return len(imgs), missing


def _extract_internal_links(html_source: str, base_domain: str) -> set[str]:
    """Same-domain link targets (normalized, no fragment/trailing slash) —
    the raw material for orphan-page detection (built after the whole
    crawl, once every page's outbound links are known)."""
    root_domain = base_domain.removeprefix("www.")
    links = set()
    for href in re.findall(r'<a\b[^>]+href=["\']([^"\'#][^"\']*)["\']', html_source, re.IGNORECASE):
        absolute = urljoin(f"https://{base_domain}", href)
        parsed = urlparse(absolute)
        if parsed.netloc.removeprefix("www.") == root_domain:
            links.add(absolute.split("#")[0].rstrip("/"))
    return links


def _extract_trust_signals(text: str) -> dict[str, bool]:
    return {name: bool(re.search(pattern, text, re.IGNORECASE)) for name, pattern in _TRUST_SIGNAL_PATTERNS.items()}


def _extract_broken_embeds(html_source: str) -> list[dict]:
    """Known third-party embed/chat-widget srcs that no longer resolve on
    this page. A live HEAD check, so only runs for the (rare) pages that
    actually reference one of these — not on every page."""
    found = []
    for label, pattern in _EMBED_PATTERNS:
        for src in pattern.findall(html_source):
            try:
                resp = httpx.head(src, timeout=5.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
                ok = resp.status_code < 400
            except httpx.HTTPError:
                ok = False
            if not ok:
                found.append({"type": label, "src": src})
    return found


_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _get_sitemap_urls(sitemap_url: str, limit: int) -> list[str]:
    resp = _fetch(sitemap_url)
    if resp is None or resp.status_code != 200:
        return []
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError:
        return []

    # Sitemap index: a list of child sitemaps rather than pages directly.
    sitemap_refs = [loc.text.strip() for loc in root.findall("sm:sitemap/sm:loc", _SITEMAP_NS) if loc.text]
    if sitemap_refs:
        urls: list[str] = []
        for ref in sitemap_refs:
            if len(urls) >= limit:
                break
            nested = _fetch(ref)
            if nested is None or nested.status_code != 200:
                continue
            try:
                nested_root = ElementTree.fromstring(nested.content)
            except ElementTree.ParseError:
                continue
            for loc in nested_root.findall("sm:url/sm:loc", _SITEMAP_NS):
                if loc.text:
                    urls.append(loc.text.strip())
                if len(urls) >= limit:
                    break
        return urls[:limit]

    locs = root.findall("sm:url/sm:loc", _SITEMAP_NS)
    return [loc.text.strip() for loc in locs[:limit] if loc.text]


def _meta_issues(meta: dict) -> list[str]:
    issues = []
    if not meta["title"]:
        issues.append("Missing <title> tag")
    elif meta["title_length"] > 60:
        issues.append("Title tag longer than 60 characters")
    if not meta["meta_description"]:
        issues.append("Missing meta description")
    if meta["h1_count"] == 0:
        issues.append("No <h1> tag found")
    elif meta["h1_count"] > 1:
        issues.append("Multiple <h1> tags found")
    if not meta["viewport_present"]:
        issues.append("Missing mobile viewport meta tag")
    if not meta["canonical_present"]:
        issues.append("Missing canonical tag")
    return issues


CONCURRENCY = 15


async def run_multi_page_audit_async(
    website_url: str,
    page_limit: int = 20,
    on_progress: Callable[[int, int, int], None] | None = None,
    deep: bool = False,
) -> dict:
    """Async, concurrent version of the crawl for large sites (thousands of
    URLs) — runs page fetches CONCURRENCY at a time instead of one-by-one, and
    reports progress via on_progress(pages_checked, pages_total, pages_with_issues)
    after each page so a caller can persist progress for polling.

    deep=True additionally extracts, per page: JSON-LD structured-data
    categories (feeds the Structured Data slide when no Semrush export is
    uploaded), image alt-text coverage, outbound internal links (for
    orphan-page detection), word count (thin-content flagging), trust-signal
    presence, and broken third-party embeds — then rolls all of that into a
    "crawl_extras" summary once every page is in. Off by default since it's
    extra per-page work (incl. live HEAD checks for embeds) that the fast
    inline 20-page report-generation fallback shouldn't pay for."""
    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    base_domain = parsed.netloc

    robots_url = urljoin(base_url, "/robots.txt")
    robots_resp = _fetch(robots_url)
    parser = RobotFileParser()
    if robots_resp is not None and robots_resp.status_code == 200:
        parser.parse(robots_resp.text.splitlines())
        sitemap_hint = re.findall(r"(?i)sitemap:\s*(\S+)", robots_resp.text)
    else:
        sitemap_hint = []

    sitemap_url = sitemap_hint[0] if sitemap_hint else urljoin(base_url, "/sitemap.xml")
    sitemap_urls = _get_sitemap_urls(sitemap_url, limit=page_limit)

    urls_to_check = sitemap_urls if sitemap_urls else [website_url]
    urls_to_check = urls_to_check[:page_limit]
    if robots_resp is not None:
        urls_to_check = [u for u in urls_to_check if parser.can_fetch(USER_AGENT, u)]

    total = len(urls_to_check)
    pages: list[dict] = []
    checked = 0
    with_issues = 0
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def check_one(client: httpx.AsyncClient, url: str):
        nonlocal checked, with_issues
        async with semaphore:
            try:
                resp = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
                reachable = resp.status_code < 400
                status_code = resp.status_code
                text = resp.text if reachable else None
            except httpx.HTTPError:
                reachable = False
                status_code = None
                text = None

            page_result = {"url": url, "reachable": reachable, "status_code": status_code}
            if reachable and text is not None:
                meta = _extract_meta(text)
                page_result["meta"] = meta
                page_result["issues"] = _meta_issues(meta)
                if deep:
                    images_total, images_missing_alt = _extract_images(text)
                    page_result["extras"] = {
                        "images_total": images_total,
                        "images_missing_alt": images_missing_alt,
                        "internal_links_out": list(_extract_internal_links(text, base_domain)),
                        "word_count": len(extract_visible_text(text).split()),
                        "trust_signals": _extract_trust_signals(text),
                        "broken_embeds": _extract_broken_embeds(text),
                        **_extract_json_ld_types(text),
                        "schema_jsonld": 1 if "application/ld+json" in text.lower() else 0,
                        "schema_microdata": 1 if "itemscope" in text.lower() else 0,
                    }
            else:
                page_result["meta"] = None
                page_result["issues"] = ["Page not reachable"]

        async with lock:
            pages.append(page_result)
            checked += 1
            if page_result["issues"]:
                with_issues += 1
            if on_progress:
                on_progress(checked, total, with_issues)

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(check_one(client, url) for url in urls_to_check))

    result = {
        "base_url": base_url,
        "sitemap_url": sitemap_url if sitemap_urls else None,
        "pages_checked": len(pages),
        "pages_with_issues": with_issues,
        "pages": pages,
    }
    if deep:
        result["crawl_extras"] = _summarize_crawl_extras(pages)
    return result


def _summarize_crawl_extras(pages: list[dict]) -> dict:
    """Rolls up per-page `extras` (see run_multi_page_audit_async deep=True)
    into the aggregates the report slides actually need: orphan-page
    detection needs every page's outbound links known first, so this can
    only run once the whole crawl is in, not per-page."""
    reachable_pages = [p for p in pages if p.get("extras")]
    urls = {p["url"].rstrip("/") for p in reachable_pages}

    inbound_counts = {u: 0 for u in urls}
    for p in reachable_pages:
        for link in p["extras"].get("internal_links_out", []):
            key = link.rstrip("/")
            if key in inbound_counts and key != p["url"].rstrip("/"):
                inbound_counts[key] += 1
    orphan_pages = sorted(u for u, count in inbound_counts.items() if count == 0)
    avg_internal_links = (
        sum(len(p["extras"].get("internal_links_out", [])) for p in reachable_pages) / len(reachable_pages)
        if reachable_pages else 0
    )

    structured_data_rows = [
        {
            "page_url": p["url"],
            "schema_jsonld": p["extras"].get("schema_jsonld", 0),
            "schema_microdata": p["extras"].get("schema_microdata", 0),
            **{field: p["extras"].get(field, 0) for field in set(_SCHEMA_TYPE_FIELDS.values())},
        }
        for p in reachable_pages
    ]

    thin_content_pages = sorted(
        (
            {"page_url": p["url"], "word_count": p["extras"]["word_count"]}
            for p in reachable_pages if p["extras"]["word_count"] < THIN_CONTENT_WORD_THRESHOLD
        ),
        key=lambda r: r["word_count"],
    )

    total_images = sum(p["extras"]["images_total"] for p in reachable_pages)
    total_missing_alt = sum(p["extras"]["images_missing_alt"] for p in reachable_pages)

    trust_signal_summary = {
        name: any(p["extras"]["trust_signals"].get(name) for p in reachable_pages)
        for name in _TRUST_SIGNAL_PATTERNS
    }

    broken_embeds = [
        {**embed, "page_url": p["url"]} for p in reachable_pages for embed in p["extras"].get("broken_embeds", [])
    ]

    return {
        "structured_data_rows": structured_data_rows,
        "alt_text_summary": {"images_total": total_images, "images_missing_alt": total_missing_alt},
        "internal_linking_summary": {
            "pages_crawled": len(reachable_pages), "orphan_pages": orphan_pages, "avg_internal_links": avg_internal_links,
        },
        "thin_content_pages": thin_content_pages,
        "trust_signal_summary": trust_signal_summary,
        "broken_embeds": broken_embeds,
    }


def run_multi_page_audit(website_url: str, page_limit: int = 20) -> dict:
    """Crawls up to page_limit URLs from the sitemap (falls back to just the
    homepage if no sitemap is found) and runs the same on-page checks on each.
    Respects robots.txt disallow rules and paces requests to be a polite crawler.
    """
    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    robots_url = urljoin(base_url, "/robots.txt")
    robots_resp = _fetch(robots_url)
    parser = RobotFileParser()
    if robots_resp is not None and robots_resp.status_code == 200:
        parser.parse(robots_resp.text.splitlines())
        sitemap_hint = re.findall(r"(?i)sitemap:\s*(\S+)", robots_resp.text)
    else:
        sitemap_hint = []

    sitemap_url = sitemap_hint[0] if sitemap_hint else urljoin(base_url, "/sitemap.xml")
    sitemap_urls = _get_sitemap_urls(sitemap_url, limit=page_limit)

    urls_to_check = sitemap_urls if sitemap_urls else [website_url]
    urls_to_check = urls_to_check[:page_limit]

    pages = []
    for url in urls_to_check:
        if robots_resp is not None and not parser.can_fetch(USER_AGENT, url):
            continue
        resp = _fetch(url)
        reachable = resp is not None and resp.status_code < 400
        page_result = {
            "url": url,
            "reachable": reachable,
            "status_code": resp.status_code if resp else None,
        }
        if reachable:
            meta = _extract_meta(resp.text)
            page_result["meta"] = meta
            page_result["issues"] = _meta_issues(meta)
        else:
            page_result["meta"] = None
            page_result["issues"] = ["Page not reachable"]
        pages.append(page_result)
        time.sleep(0.3)

    return {
        "base_url": base_url,
        "sitemap_url": sitemap_url if sitemap_urls else None,
        "pages_checked": len(pages),
        "pages_with_issues": sum(1 for p in pages if p["issues"]),
        "pages": pages,
    }


def run_site_audit(website_url: str) -> dict:
    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    https = parsed.scheme == "https"

    started = time.monotonic()
    home_resp = _fetch(website_url)
    load_time_ms = int((time.monotonic() - started) * 1000)

    result = {
        "url": website_url,
        "https": https,
        "reachable": home_resp is not None and home_resp.status_code < 400,
        "status_code": home_resp.status_code if home_resp else None,
        "load_time_ms": load_time_ms,
        "page_size_bytes": len(home_resp.content) if home_resp else None,
    }

    robots = _check_robots_txt(base_url)
    result["robots_txt"] = robots

    sitemap_url = robots["sitemap_urls"][0] if robots["sitemap_urls"] else urljoin(base_url, "/sitemap.xml")
    result["sitemap"] = _check_sitemap(sitemap_url)

    if home_resp is not None and home_resp.status_code < 400:
        result["meta"] = _extract_meta(home_resp.text)
        result["company_summary"] = summarize_company(website_url, extract_visible_text(home_resp.text))
        result["brand_color"] = extract_brand_color(home_resp.text)
        result["logo_url"] = find_logo_url(base_url, home_resp.text)
    else:
        result["meta"] = None
        result["company_summary"] = None
        result["brand_color"] = None
        result["logo_url"] = None

    result["issues"] = _derive_issues(result)
    return result


def _derive_issues(result: dict) -> list[str]:
    issues = []
    if not result["https"]:
        issues.append("Site is not served over HTTPS")
    if not result["reachable"]:
        issues.append("Homepage was not reachable")
    if not result["robots_txt"]["present"]:
        issues.append("robots.txt not found")
    if not result["sitemap"]["present"]:
        issues.append("sitemap.xml not found or unreadable")
    meta = result.get("meta")
    if meta:
        issues.extend(_meta_issues(meta))
    return issues
