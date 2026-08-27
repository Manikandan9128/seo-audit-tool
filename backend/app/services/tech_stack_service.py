"""Fingerprints a site's tech stack and hosting — response headers, DNS/PTR
records, and HTML markers (CMS, framework, analytics tags). No credentials
needed; everything here is publicly observable."""

import re
import socket
from urllib.parse import urlparse

import httpx

USER_AGENT = "SEOAuditTool/1.0 (+https://seoaudittool.local/bot-info)"
TIMEOUT = 8.0

HEADER_HOSTING_HINTS = [
    (lambda h: "cf-ray" in h or "cloudflare" in h.get("server", "").lower(), "Cloudflare", "cdn"),
    (lambda h: any(k.startswith("x-vercel") for k in h), "Vercel", "hosting"),
    (lambda h: "x-nf-request-id" in h or "netlify" in h.get("server", "").lower(), "Netlify", "hosting"),
    (lambda h: "x-amz-cf-id" in h or "cloudfront" in h.get("via", "").lower(), "Amazon CloudFront", "cdn"),
    (lambda h: "x-github-request-id" in h, "GitHub Pages", "hosting"),
    (lambda h: "x-shopify-stage" in h or "shopify" in h.get("server", "").lower(), "Shopify", "hosting"),
    (lambda h: "x-served-by" in h and "fastly" in h.get("x-served-by", "").lower(), "Fastly", "cdn"),
    (lambda h: "x-sucuri-id" in h, "Sucuri", "security/cdn"),
]

HTML_TECH_HINTS = [
    (r"wp-content|wp-includes", "WordPress", "cms"),
    (r"cdn\.shopify\.com|Shopify\.theme", "Shopify", "cms"),
    (r"static\.wixstatic\.com|wix\.com", "Wix", "cms"),
    (r"webflow\.js|webflow\.com", "Webflow", "cms"),
    (r"squarespace\.com|static1\.squarespace", "Squarespace", "cms"),
    (r"__NEXT_DATA__|_next/static", "Next.js", "framework"),
    (r"data-reactroot|react-dom", "React", "framework"),
    (r"ng-version=", "Angular", "framework"),
    (r"__NUXT__", "Nuxt.js", "framework"),
    (r"cdn\.shopify\.com/s/files", "Shopify (theme assets)", "cms"),
    (r"gtag\(|googletagmanager\.com/gtag", "Google Analytics (GA4)", "analytics"),
    (r"www\.googletagmanager\.com/gtm\.js", "Google Tag Manager", "analytics"),
    (r"connect\.facebook\.net.*fbevents", "Meta Pixel", "analytics"),
    (r"hotjar\.com", "Hotjar", "analytics"),
    (r"cdn\.segment\.com", "Segment", "analytics"),
    (r"jquery[.-](\d+\.\d+\.\d+)?.*\.js", "jQuery", "library"),
    (r"cdn\.jsdelivr\.net/npm/bootstrap|bootstrap\.min\.css", "Bootstrap", "library"),
    (r"tailwind", "Tailwind CSS", "library"),
]

PTR_HOSTING_HINTS = [
    ("amazonaws.com", "Amazon Web Services"),
    ("cloudfront.net", "Amazon CloudFront"),
    ("googleusercontent.com", "Google Cloud Platform"),
    ("1e100.net", "Google"),
    ("azurewebsites.net", "Microsoft Azure"),
    ("cloudapp.azure.com", "Microsoft Azure"),
    ("digitalocean.com", "DigitalOcean"),
    ("linode.com", "Linode (Akamai)"),
    ("ovh.net", "OVHcloud"),
    ("hetzner.com", "Hetzner"),
    ("fastly.net", "Fastly"),
    ("cloudflare.com", "Cloudflare"),
    ("wpengine.com", "WP Engine"),
    ("bluehost.com", "Bluehost"),
    ("godaddy.com", "GoDaddy"),
    ("siteground.net", "SiteGround"),
]


def _resolve_ip(hostname: str) -> str | None:
    try:
        return socket.gethostbyname(hostname)
    except OSError:
        return None


def _reverse_dns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


def _guess_hosting_from_ptr(ptr: str | None) -> str | None:
    if not ptr:
        return None
    ptr_lower = ptr.lower()
    for needle, label in PTR_HOSTING_HINTS:
        if needle in ptr_lower:
            return label
    return None


def detect_tech_stack(website_url: str) -> dict:
    parsed = urlparse(website_url)
    hostname = parsed.hostname or ""

    # One retry on transport failure — a single transient blip used to drop
    # the whole Tech Stack & Hosting slide with no second chance.
    resp = None
    last_error: httpx.HTTPError | None = None
    for _ in range(2):
        try:
            resp = httpx.get(website_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
            break
        except httpx.HTTPError as e:
            last_error = e
    if resp is None:
        return {"error": f"Could not reach site: {last_error}"}

    headers_lower = {k.lower(): v for k, v in resp.headers.items()}
    html = resp.text or ""

    detected: list[dict] = []
    seen_names = set()

    def _add(name: str, category: str):
        if name not in seen_names:
            seen_names.add(name)
            detected.append({"name": name, "category": category})

    for predicate, name, category in HEADER_HOSTING_HINTS:
        try:
            if predicate(headers_lower):
                _add(name, category)
        except Exception:
            pass

    for pattern, name, category in HTML_TECH_HINTS:
        if re.search(pattern, html, re.IGNORECASE):
            _add(name, category)

    generator_match = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if generator_match:
        _add(generator_match.group(1).strip(), "cms")

    server_header = headers_lower.get("server")
    if server_header:
        _add(server_header, "server")

    powered_by = headers_lower.get("x-powered-by")
    if powered_by:
        _add(powered_by, "server")

    ip = _resolve_ip(hostname) if hostname else None
    ptr = _reverse_dns(ip) if ip else None
    hosting_guess = _guess_hosting_from_ptr(ptr)
    if hosting_guess:
        _add(hosting_guess, "hosting")

    return {
        "hostname": hostname,
        "ip": ip,
        "reverse_dns": ptr,
        "https": resp.url.scheme == "https",
        "detected": detected,
        "raw_headers": {k: v for k, v in resp.headers.items() if k.lower() in (
            "server", "x-powered-by", "via", "cf-ray", "x-vercel-id", "x-nf-request-id", "x-amz-cf-id", "x-served-by"
        )},
    }
