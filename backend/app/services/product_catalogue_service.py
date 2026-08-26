"""Deterministic product/solutions catalogue crawler.

No LLM involved — pulls candidate product/collection URLs from the nav menu,
sitemap.xml, and common catalogue path patterns, then reads each page's
title as the product name. Straightforward extraction, not summarization.
"""

import re
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

USER_AGENT = "SEOAuditTool/1.0 (+https://seoaudittool.local/bot-info)"
TIMEOUT = 8.0

PRODUCT_PATH_HINTS = [
    "/product/", "/products/", "/collections/", "/collection/",
    "/solutions/", "/solution/", "/services/", "/service/", "/shop/",
]

SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml"]

MAX_PAGES = 40


def _fetch(url: str) -> httpx.Response | None:
    try:
        return httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        return None


def _same_site(url: str, base_netloc: str) -> bool:
    return urlparse(url).netloc == base_netloc


def _is_product_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(hint in path for hint in PRODUCT_PATH_HINTS)


def _nav_links(home_html: str, base_url: str, base_netloc: str) -> list[str]:
    nav_blocks = re.findall(r"(?is)<nav[^>]*>.*?</nav>", home_html)
    links = []
    for block in nav_blocks:
        for href in re.findall(r'href=["\'](.*?)["\']', block, re.IGNORECASE):
            full = urljoin(base_url, href)
            if _same_site(full, base_netloc):
                links.append(full)
    return links


def _sitemap_urls(base_url: str, base_netloc: str) -> list[str]:
    urls: list[str] = []
    for path in SITEMAP_PATHS:
        resp = _fetch(urljoin(base_url, path))
        if resp is None or resp.status_code >= 400:
            continue
        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            continue

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # sitemap index -> fetch each child sitemap
        sub_sitemaps = [el.text for el in root.findall(".//sm:sitemap/sm:loc", ns) if el.text]
        for sub in sub_sitemaps[:10]:
            sub_resp = _fetch(sub)
            if sub_resp is None or sub_resp.status_code >= 400:
                continue
            try:
                sub_root = ElementTree.fromstring(sub_resp.content)
            except ElementTree.ParseError:
                continue
            for el in sub_root.findall(".//sm:url/sm:loc", ns):
                if el.text and _same_site(el.text, base_netloc):
                    urls.append(el.text)

        # plain urlset
        for el in root.findall(".//sm:url/sm:loc", ns):
            if el.text and _same_site(el.text, base_netloc):
                urls.append(el.text)

        if urls:
            break
    return urls


def _page_title(url: str) -> str | None:
    resp = _fetch(url)
    if resp is None or resp.status_code >= 400:
        return None
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", resp.text)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def _slug_to_name(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").strip().title() or url


def crawl_product_catalogue(website_url: str, limit: int = MAX_PAGES) -> dict:
    """Returns {"products": [{"name": str, "url": str}], "sources": [...]} or
    {"error": str} if the site couldn't be crawled."""
    home_resp = _fetch(website_url)
    if home_resp is None or home_resp.status_code >= 400:
        return {"error": "Could not reach site"}

    parsed = urlparse(website_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    base_netloc = parsed.netloc

    sources_used = []
    candidates: list[str] = []
    seen = set()

    def _add(urls: list[str]):
        for u in urls:
            if u not in seen and _is_product_path(u):
                seen.add(u)
                candidates.append(u)

    nav = _nav_links(home_resp.text, base_url, base_netloc)
    if nav:
        sources_used.append("nav_menu")
        _add(nav)

    sitemap = _sitemap_urls(base_url, base_netloc)
    if sitemap:
        sources_used.append("sitemap.xml")
        _add(sitemap)

    if not candidates:
        return {"products": [], "sources": sources_used}

    products = []
    for url in candidates[:limit]:
        title = _page_title(url) or _slug_to_name(url)
        products.append({"name": title, "url": url})

    return {"products": products, "sources": sources_used}
