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

import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


def capture_homepage_screenshots(domains: list[str], timeout_ms: int = 10000) -> dict[str, bytes]:
    """Returns {domain: png_bytes} — only for domains that actually
    succeeded. A domain missing from the result means capture failed
    (blocked, timed out, DNS error, etc); skip it silently, don't retry.

    Every failure is logged (2026-09-10: this used to swallow everything,
    including a Chromium-launch failure, with zero trace anywhere — a
    launch failure means EVERY domain in every call fails, every single
    report, with nothing in the logs to say why. Logged now so a missing
    `playwright install --with-deps chromium` on a deploy target — the
    most likely cause of an every-time, not intermittent, failure — shows
    up immediately instead of just being an empty dict downstream."""
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
                    except Exception as e:
                        logger.warning("Homepage screenshot failed for %s: %s", domain, e)
                        continue
            finally:
                browser.close()
    except Exception:
        logger.exception("Playwright/Chromium launch failed — no screenshots this run for %s", domains)
    return screenshots
