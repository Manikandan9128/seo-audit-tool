"""Best-effort client logo detection and download, for the report's title
slide. python-pptx can only embed raster images, so SVG logos (common for
real header logos) are skipped in favor of a link/meta-tag icon instead —
usually still on-brand and recognizable, just square."""

import re
from urllib.parse import urljoin

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 10.0
MAX_LOGO_BYTES = 2_000_000

_LOGO_IMG_RE = re.compile(
    r'<img[^>]+(?:class|alt|id)=["\'][^"\']*logo[^"\']*["\'][^>]*src=["\']([^"\']+)["\']', re.IGNORECASE
)
_ICON_LINK_RE = re.compile(
    r'<link[^>]+rel=["\'](?:apple-touch-icon(?:-precomposed)?|icon|shortcut icon)["\'][^>]*href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
def find_logo_url(base_url: str, html_source: str) -> str | None:
    """An <img> tagged 'logo' first (the actual visible header logo, highest
    confidence), then a favicon/apple-touch-icon link tag. Deliberately does
    NOT fall back to og:image — that's usually a hero/banner photo, and a
    wrong logo looks worse than no logo. Falls back to the universal
    /favicon.ico convention if neither is found in the static HTML (many
    sites render their header logo client-side via JS, invisible to a
    plain-HTTP crawl, but still serve a real favicon.ico)."""
    for pattern in (_LOGO_IMG_RE, _ICON_LINK_RE):
        match = pattern.search(html_source)
        if match:
            return urljoin(base_url, match.group(1))
    return urljoin(base_url, "/favicon.ico")


def fetch_logo_bytes(logo_url: str) -> bytes | None:
    """Downloads the logo if it's a raster format python-pptx can embed
    directly. Returns None on any failure — the title slide falls back to
    text-only, same as before this feature existed."""
    if logo_url.lower().split("?")[0].endswith(".svg"):
        return None
    try:
        resp = httpx.get(logo_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    content_type = resp.headers.get("content-type", "")
    if "svg" in content_type:
        return None
    if not content_type.startswith("image/"):
        return None
    if len(resp.content) > MAX_LOGO_BYTES:
        return None
    return resp.content
