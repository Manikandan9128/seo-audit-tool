"""UI-Level Fixes analysis pipeline, Part A1+A2 (2026-09-25 spec): captures
the homepage at two real viewports and measures it from its own DOM/CSS,
instead of asking a vision model to guess at things a screenshot alone
can't confirm (whether an element is actually `position: fixed`, its real
pixel height, whether two elements' bounding boxes truly intersect).

Everything here is best-effort and defensive by design — a JS heuristic
against arbitrary real-world markup will occasionally miss or misclassify
something, and a slow/blocking/bot-protected site can fail capture
entirely. Either failure mode degrades gracefully (fewer/emptier
page_facts, or capture_ui_audit returning None) rather than crashing
report generation; the caller treats a missing capture the same way
screenshot_client.capture_homepage_screenshots' callers already do.

Distinct from screenshot_client.py (used for competitor-narrative visual
grounding and the older single-screenshot UI-fixes vision pass) — this
module owns a heavier, two-viewport, DOM-aware capture specifically for
the UI-Level Fixes rebuild."""

import logging

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_HIDE_WEBDRIVER_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"

_VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 375, "height": 812},
}

_NAV_TIMEOUT_MS = 15000
_NETWORKIDLE_TIMEOUT_MS = 8000
_POPUP_SETTLE_MS = 5000
_MIN_TAP_TARGET_PX = 48

# The single JS heuristic run once per viewport via page.evaluate() — see
# module docstring for why this exists instead of asking the vision model
# to guess at DOM-only facts (position: fixed, real overlap, real pixel
# sizes). Written as one big object-returning IIFE so python-pptx/Playwright
# only ever crosses the JS<->Python boundary once per viewport, not once
# per fact.
_PAGE_FACTS_JS = r"""
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || parseFloat(style.opacity || "1") === 0) return false;
    return true;
  }

  function rect(el) {
    const r = el.getBoundingClientRect();
    return { top: r.top, left: r.left, width: r.width, height: r.height, bottom: r.bottom, right: r.right };
  }

  // --- H1 / hero headline ---------------------------------------------
  const h1s = Array.from(document.querySelectorAll("h1")).filter(isVisible);
  const h1_texts = h1s.map((h) => clean(h.textContent)).filter(Boolean);
  let hero_headline = h1_texts[0] || "";
  if (!hero_headline) {
    const heading = Array.from(document.querySelectorAll("h2, h3"))
      .filter((el) => isVisible(el) && rect(el).top < vh)
      .sort((a, b) => rect(b).width * rect(b).height - rect(a).width * rect(a).height)[0];
    if (heading) hero_headline = clean(heading.textContent);
  }

  // --- CTAs --------------------------------------------------------------
  const CTA_WORDS = [
    "contact", "talk to us", "get started", "get a quote", "request a quote", "buy", "shop", "book",
    "sign up", "signup", "demo", "request demo", "quote", "call us", "call now", "subscribe", "download",
    "learn more", "get in touch", "request", "enquire", "enquiry", "inquire", "apply", "join", "order now",
    "add to cart", "checkout", "start free trial", "try free", "schedule",
  ];
  const clickable = Array.from(document.querySelectorAll(
    "a, button, input[type=submit], input[type=button], [role=button], [onclick]"
  ));
  const seenCta = new Set();
  const ctas = [];
  for (const el of clickable) {
    const text = clean(el.textContent || el.value || el.getAttribute("aria-label") || "");
    if (!text || text.length > 60) continue;
    const lower = text.toLowerCase();
    if (!CTA_WORDS.some((w) => lower.includes(w))) continue;
    const key = lower + "|" + Math.round(rect(el).top) + "|" + Math.round(rect(el).left);
    if (seenCta.has(key)) continue;
    seenCta.add(key);
    const r = rect(el);
    ctas.push({ text, visible: isVisible(el), top: r.top, left: r.left, width: r.width, height: r.height });
    if (ctas.length >= 25) break;
  }

  // --- Carousel / slider ---------------------------------------------
  const carouselEls = Array.from(document.querySelectorAll(
    '[class*="swiper"], [class*="slick"], [class*="carousel"], [class*="slider"], [data-carousel], [data-slider]'
  )).filter((el) => isVisible(el));
  let carousel = null;
  if (carouselEls.length) {
    // Prefer the largest matching container as the real carousel root —
    // avoids counting a nested "next/prev arrow" wrapper as its own carousel.
    const root = carouselEls.sort((a, b) => rect(b).width * rect(b).height - rect(a).width * rect(a).height)[0];
    const slideCandidates = root.querySelectorAll(
      '[class*="slide"], [class*="item"], > *'
    );
    const slideCount = Math.max(1, new Set(Array.from(slideCandidates).map((s) => s.className)).size <= 1
      ? slideCandidates.length
      : slideCandidates.length);
    const html = root.outerHTML.slice(0, 2000).toLowerCase();
    const autoplayAttr = root.getAttribute("data-autoplay") || root.getAttribute("data-swiper-autoplay");
    const autoplay = autoplayAttr != null ? autoplayAttr !== "false" : (html.includes("autoplay") ? true : null);
    carousel = { present: true, slide_count: slideCount, autoplay };
  }

  // --- Fixed / overlay elements on the first screen -----------------
  const allEls = Array.from(document.querySelectorAll("body *"));
  const overlays = [];
  for (const el of allEls) {
    const style = window.getComputedStyle(el);
    if (style.position !== "fixed" && style.position !== "sticky") continue;
    if (!isVisible(el)) continue;
    const r = rect(el);
    if (r.top > vh || r.bottom < 0) continue; // not actually on the first screen
    if (r.width < 20 || r.height < 10) continue; // tracker pixel / off-screen helper, not a real overlay
    const idClass = ((el.id || "") + " " + (el.className || "")).toLowerCase();
    const text = clean(el.textContent).slice(0, 300);
    let kind = "other";
    if (/cookie|consent|gdpr|privacy/.test(idClass) || /cookie|we use cookies|accept all/i.test(text)) {
      kind = "cookie_banner";
    } else if (/intercom|drift|crisp|tawk|zendesk|whatsapp|chat-widget|livechat/.test(idClass)) {
      kind = "chat_widget";
    } else if (r.top < 60 && r.width > vw * 0.8 && r.height < 80) {
      kind = "announcement_bar";
    } else if (r.width > vw * 0.5 && r.height < 140) {
      kind = "partner_banner";
    }
    const bgColor = style.backgroundColor || "";
    const alphaMatch = /rgba?\([^)]+,\s*([\d.]+)\)/.exec(bgColor);
    const buttons = kind === "cookie_banner"
      ? Array.from(el.querySelectorAll("button, a")).map((b) => clean(b.textContent)).filter(Boolean).slice(0, 6)
      : [];
    overlays.push({
      kind, id: el.id || null, class_name: (el.className || "").toString().slice(0, 120),
      text: text || null, top: r.top, left: r.left, width: r.width, height: r.height,
      background_alpha: alphaMatch ? parseFloat(alphaMatch[1]) : (bgColor && bgColor !== "rgba(0, 0, 0, 0)" ? 1.0 : null),
      padding_left: parseFloat(style.paddingLeft) || 0, padding_right: parseFloat(style.paddingRight) || 0,
      buttons,
    });
    if (overlays.length >= 15) break;
  }

  // --- Overlaps between overlays and CTAs -----------------------------
  function intersects(a, b) {
    return a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top;
  }
  const overlaps = [];
  for (const ov of overlays) {
    for (const cta of ctas) {
      if (!cta.visible) continue;
      if (intersects(ov, cta)) overlaps.push({ overlay_kind: ov.kind, overlay_text: (ov.text || "").slice(0, 80), cta_text: cta.text });
      if (overlaps.length >= 10) break;
    }
  }

  // --- Trust elements ---------------------------------------------------
  const TRUST_PATTERNS = /trusted by|our clients|as seen in|testimonial|case stud|client logo|certified|iso\s?9001|trustpilot|\bbbb\b|\bg2\b|capterra|rated\s*\d/i;
  const KPI_PATTERN = /\b\d[\d,]*\s?[%+]|\b\d[\d,]*\s?(clients|customers|projects|years|countries)\b/i;
  const trust = [];
  const seenTrust = new Set();
  for (const el of document.querySelectorAll("img, section, div, span, p, li")) {
    if (trust.length >= 12) break;
    const text = clean(el.textContent || el.alt || "");
    if (!text || text.length > 200) continue;
    let kind = null;
    if (TRUST_PATTERNS.test(text)) kind = "trust_signal";
    else if (KPI_PATTERN.test(text) && text.length < 60) kind = "kpi_number";
    if (!kind || !isVisible(el)) continue;
    const key = kind + "|" + text.slice(0, 40);
    if (seenTrust.has(key)) continue;
    seenTrust.add(key);
    const r = rect(el);
    trust.push({ kind, text: text.slice(0, 120), page_y: r.top + window.scrollY });
  }

  // --- Tap targets under the minimum touch-target size ------------------
  const small_tap_targets = [];
  for (const el of clickable) {
    if (!isVisible(el)) continue;
    const r = rect(el);
    if (r.height >= 48 || r.height <= 0) continue;
    const text = clean(el.textContent || el.value || el.getAttribute("aria-label") || "");
    if (!text) continue;
    small_tap_targets.push({ text: text.slice(0, 60), width: Math.round(r.width), height: Math.round(r.height) });
    if (small_tap_targets.length >= 15) break;
  }

  return {
    h1_count: h1_texts.length, h1_texts: h1_texts.slice(0, 5), hero_headline,
    ctas, carousel, overlays, overlaps, trust_elements: trust, small_tap_targets,
    viewport: { width: vw, height: vh },
  };
}
"""


def _capture_viewport(browser, url: str, viewport: dict, capture_tap_targets: bool) -> dict | None:
    page = browser.new_page(viewport=viewport, user_agent=_REALISTIC_UA)
    page.add_init_script(_HIDE_WEBDRIVER_SCRIPT)
    try:
        try:
            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="load")
        except Exception as e:
            try:
                page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception:
                logger.warning("UI audit capture: navigation failed for %s: %s", url, e)
                return None
        try:
            page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)
        except Exception:
            pass  # best-effort — many real homepages never truly go idle (trackers, pings)
        page.wait_for_timeout(_POPUP_SETTLE_MS)  # lets a delayed cookie banner/chat widget appear

        first_screen_png = page.screenshot(type="png")
        full_page_png = page.screenshot(type="png", full_page=True)
        try:
            page_facts = page.evaluate(_PAGE_FACTS_JS)
        except Exception:
            logger.exception("UI audit capture: page_facts extraction failed for %s", url)
            page_facts = {}
        if not capture_tap_targets:
            page_facts.pop("small_tap_targets", None)
        return {"first_screen_png": first_screen_png, "full_page_png": full_page_png, "page_facts": page_facts}
    finally:
        page.close()


def capture_ui_audit(website_url: str) -> dict | None:
    """Captures the homepage at desktop (1440x900) and mobile (375x812)
    viewports — first-screen + full-page screenshots, plus a DOM-measured
    page_facts dict, at each. Returns {"desktop": {...}, "mobile": {...}}
    or None if the site couldn't be reached at all (bot-blocked, timed
    out, DNS/unreachable) — same best-effort contract as screenshot_
    client.capture_homepage_screenshots."""
    url = website_url if website_url.startswith(("http://", "https://")) else f"https://{website_url}"
    result: dict = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"], timeout=15000)
            try:
                for name, viewport in _VIEWPORTS.items():
                    captured = _capture_viewport(browser, url, viewport, capture_tap_targets=(name == "mobile"))
                    if captured:
                        result[name] = captured
            finally:
                browser.close()
    except Exception:
        logger.exception("UI audit capture: Playwright/Chromium launch failed for %s", website_url)
        return None
    return result or None
