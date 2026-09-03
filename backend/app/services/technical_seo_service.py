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


# Baseline Google recommends for effectively any site's homepage — Organization
# for the Knowledge Panel, WebSite for the sitelinks search box, BreadcrumbList
# for breadcrumb rich results on internal pages. Not exhaustive (Product/Article/
# FAQPage etc. only apply to specific page types), just the generic floor.
_RECOMMENDED_SCHEMA_TYPES = ["Organization", "WebSite", "BreadcrumbList"]


# Required/recommended top-level properties per Google's rich-result docs
# (https://developers.google.com/search/docs/appearance/structured-data) for
# the types that show up on ordinary business/marketing sites. Not every
# type Google supports — just enough to flag the common gaps without a
# Search Console connection. Field presence only (not value-shape validation,
# e.g. offers needing its own price/availability) — good enough to catch
# "you have a Product block but no image/offers", not full schema linting.
_SCHEMA_FIELD_RULES: dict[str, dict[str, list[str]]] = {
    "Article": {"required": ["headline", "image", "datePublished"], "recommended": ["author", "dateModified", "publisher"]},
    "NewsArticle": {"required": ["headline", "image", "datePublished"], "recommended": ["author", "dateModified", "publisher"]},
    "BlogPosting": {"required": ["headline", "image", "datePublished"], "recommended": ["author", "dateModified", "publisher"]},
    "Product": {"required": ["name"], "recommended": ["image", "description", "offers", "review", "aggregateRating", "brand"]},
    "Organization": {"required": ["name", "url"], "recommended": ["logo", "sameAs"]},
    "LocalBusiness": {"required": ["name", "address"], "recommended": ["image", "telephone", "priceRange", "openingHoursSpecification"]},
    "BreadcrumbList": {"required": ["itemListElement"], "recommended": []},
    "FAQPage": {"required": ["mainEntity"], "recommended": []},
    "Review": {"required": ["reviewRating", "author"], "recommended": ["itemReviewed"]},
    "Event": {"required": ["name", "startDate", "location"], "recommended": ["image", "description", "offers"]},
    "Recipe": {"required": ["name", "image", "author"], "recommended": ["recipeIngredient", "recipeInstructions", "aggregateRating"]},
    "VideoObject": {"required": ["name", "description", "thumbnailUrl", "uploadDate"], "recommended": []},
    "JobPosting": {"required": ["title", "description", "datePosted", "hiringOrganization", "jobLocation"], "recommended": []},
    "HowTo": {"required": ["name", "step"], "recommended": ["image", "totalTime"]},
    "WebSite": {"required": ["name", "url"], "recommended": ["potentialAction"]},
}


def _extract_schema_entities(html_source: str) -> list[dict]:
    """Every JSON-LD entity on the page (flattened out of @graph and array
    forms), each still carrying its own fields so field-level rules can run
    against it."""
    entities: list[dict] = []
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html_source, re.IGNORECASE | re.DOTALL
    ):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue

        top_level = data if isinstance(data, list) else [data]
        for entity in top_level:
            if isinstance(entity, dict) and "@graph" in entity:
                entities.extend(e for e in entity["@graph"] if isinstance(e, dict))
            elif isinstance(entity, dict):
                entities.append(entity)
    return entities


def _schema_types_from_entities(entities: list[dict]) -> list[str]:
    types: list[str] = []
    for entity in entities:
        entity_type = entity.get("@type")
        if isinstance(entity_type, list):
            types.extend(str(t) for t in entity_type)
        elif entity_type:
            types.append(str(entity_type))

    seen = set()
    unique_types = []
    for t in types:
        if t not in seen:
            seen.add(t)
            unique_types.append(t)
    return unique_types


def _schema_field_issues(entities: list[dict]) -> list[str]:
    """'<Type> schema missing required/recommended field: <field>' for every
    rule-covered type detected on the page — the same property gaps
    Google's Rich Results Test would flag, checked locally without a Search
    Console call. Recommended fields (e.g. Organization's "sameAs" — the
    entity-linking property that ties a site to its Wikipedia/Wikidata/
    social profiles for GEO/AI-citation purposes) were defined in
    _SCHEMA_FIELD_RULES but never actually checked here — confirmed against
    a real manual audit (EJTOY) that flagged exactly this as a finding our
    tool didn't surface anywhere."""
    issues: list[str] = []
    for entity in entities:
        entity_types = entity.get("@type")
        entity_types = entity_types if isinstance(entity_types, list) else [entity_types]
        for t in entity_types:
            rules = _SCHEMA_FIELD_RULES.get(str(t))
            if not rules:
                continue
            for field in rules["required"]:
                if field not in entity:
                    issues.append(f"{t} schema missing required field: {field}")
            for field in rules.get("recommended", []):
                if field not in entity:
                    issues.append(f"{t} schema missing recommended field: {field}")
    return issues


_SCHEMA_FIELD_ISSUE_RE = re.compile(r"^(.+?) schema missing (required|recommended) field: (.+)$")


def aggregate_schema_validation(pages: list[dict]) -> dict:
    """Whole-site schema.org coverage + missing-required-property counts,
    built from a multi-page crawl's already-computed per-page meta (see
    _extract_meta) — no extra crawling, just tallying what run_multi_page_audit*
    already collected. Powers both the live UI panel and the PPTX slide."""
    from collections import Counter

    total_pages = 0
    pages_with_schema = 0
    type_counts: Counter = Counter()
    missing_field_counts: Counter = Counter()

    for page in pages:
        meta = page.get("meta")
        if not meta:
            continue
        total_pages += 1
        types_found = meta.get("schema_types_found") or []
        if types_found:
            pages_with_schema += 1
        for t in types_found:
            type_counts[t] += 1
        for issue in meta.get("schema_field_issues") or []:
            match = _SCHEMA_FIELD_ISSUE_RE.match(issue)
            if match:
                missing_field_counts[(match.group(1), match.group(2), match.group(3))] += 1

    type_coverage = [
        {"type": t, "pages_with_it": c, "coverage_pct": round(100 * c / total_pages) if total_pages else 0}
        for t, c in type_counts.most_common()
    ]
    # Required gaps first (Google's Rich Results eligibility depends on
    # these), recommended gaps after — both still ranked by how many pages
    # they affect within their own group.
    missing_properties = sorted(
        (
            {"type": t, "severity": severity, "field": f, "pages_missing": c}
            for (t, severity, f), c in missing_field_counts.items()
        ),
        key=lambda m: (m["severity"] != "required", -m["pages_missing"]),
    )

    return {
        "total_pages": total_pages,
        "pages_with_schema": pages_with_schema,
        "type_coverage": type_coverage,
        "missing_properties": missing_properties,
    }


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
    schema_entities = _extract_schema_entities(html_source) if structured_data_present else []
    schema_types = _schema_types_from_entities(schema_entities)
    schema_types_missing = [t for t in _RECOMMENDED_SCHEMA_TYPES if t not in schema_types]
    schema_field_issues = _schema_field_issues(schema_entities)
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
        "schema_types_found": schema_types,
        "schema_types_missing": schema_types_missing,
        "schema_field_issues": schema_field_issues,
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
    if not meta["structured_data_present"]:
        issues.append("Missing structured data (JSON-LD)")
    else:
        if meta["schema_types_missing"]:
            issues.append(f"Missing recommended schema types: {', '.join(meta['schema_types_missing'])}")
        issues.extend(meta["schema_field_issues"])
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
