import asyncio
import html
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


def _fetch(url: str) -> httpx.Response | None:
    try:
        return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
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
) -> dict:
    """Async, concurrent version of the crawl for large sites (thousands of
    URLs) — runs page fetches CONCURRENCY at a time instead of one-by-one, and
    reports progress via on_progress(pages_checked, pages_total, pages_with_issues)
    after each page so a caller can persist progress for polling."""
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

    return {
        "base_url": base_url,
        "sitemap_url": sitemap_url if sitemap_urls else None,
        "pages_checked": len(pages),
        "pages_with_issues": with_issues,
        "pages": pages,
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
