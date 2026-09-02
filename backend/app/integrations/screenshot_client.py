"""Headless-browser homepage screenshots for competitor narrative slides —
visual grounding matching the manual reference deck. No paid API, just a
local Chromium instance via Playwright.

Best-effort only: many sites block headless browsers (bot detection), are
slow, or unreachable. Any failure here just means no screenshot for that
one competitor — never a report-generation crash, and the caller (site_
audit.py) treats a missing entry as "skip this competitor's image", not
an error. One browser instance is reused sequentially across every domain
in a batch (cheap page navigations, not N separate browser launches) —
also avoids Playwright's sync API having to run across multiple threads,
which it isn't reliably safe to do.
"""

from playwright.sync_api import sync_playwright


def capture_homepage_screenshots(domains: list[str], timeout_ms: int = 10000) -> dict[str, bytes]:
    """Returns {domain: png_bytes} — only for domains that actually
    succeeded. A domain missing from the result means capture failed
    (blocked, timed out, DNS error, etc); skip it silently, don't retry."""
    screenshots: dict[str, bytes] = {}
    if not domains:
        return screenshots
    try:
        with sync_playwright() as p:
            # Explicit launch timeout — was relying on Playwright's implicit
            # 30s default, undocumented anywhere in this code. If Chromium
            # can't start cleanly in the deploy environment (missing
            # dependency, resource limit, sandbox issue), this is a
            # best-effort feature — fail fast rather than silently holding
            # up the entire report for 30s on every single generation.
            browser = p.chromium.launch(args=["--no-sandbox"], timeout=15000)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                for domain in domains:
                    try:
                        page.goto(f"https://{domain}", timeout=timeout_ms, wait_until="load")
                        screenshots[domain] = page.screenshot(type="png")
                    except Exception:
                        continue
            finally:
                browser.close()
    except Exception:
        pass  # Playwright/Chromium unavailable in this environment — no screenshots this run
    return screenshots
