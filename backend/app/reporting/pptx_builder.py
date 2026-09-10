"""Builds a client-facing PPTX audit report styled after the Cyces deck format:
dark top bar, blue section-title band, light-gray body, white content cards."""

from __future__ import annotations

import re
import threading
from collections import Counter
from io import BytesIO
from urllib.parse import urlparse

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt, Emu

from app.services.keyword_relevance_service import (
    _KEYWORD_PAGE_CATEGORIES,
    _brand_token,
    _classify_keyword_page_category,
    _is_branded_keyword,
    is_branded_or_near_brand,
)

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)

DARK = RGBColor(0x14, 0x17, 0x1C)
# Cyces' own brand red (confirmed from cyces.co's compiled CSS, #FF0000) —
# the report's default accent whenever a client's site has no extractable
# brand color of its own. A client's real brand color (via brand_color_hex)
# always overrides this.
DEFAULT_ACCENT = RGBColor(0xFF, 0x00, 0x00)
ORANGE = RGBColor(0xF2, 0x8C, 0x28)
BODY_BG = RGBColor(0xF4, 0xF5, 0xF7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TEXT_DARK = RGBColor(0x14, 0x17, 0x1C)
TEXT_MUTED = RGBColor(0x6B, 0x72, 0x80)
GOOD = RGBColor(0x1F, 0x9D, 0x66)
WARN = RGBColor(0xE0, 0x8E, 0x1D)
BAD = RGBColor(0xD1, 0x3B, 0x3B)
ROW_ALT = RGBColor(0xF8, 0xF9, 0xFA)
CARD_BORDER = RGBColor(0xE6, 0xE8, 0xEB)
HEADER_ROW_BG = RGBColor(0xF1, 0xF3, 0xF5)
HEADER_ROW_TEXT = RGBColor(0x4A, 0x4A, 0x4A)
CHIP_BG = RGBColor(0xEA, 0xF4, 0xF8)

# Mutable so build_report can theme a single generation run with the client's
# own brand color (scraped from their site) without threading a param through
# every slide-drawing function. Thread-local (not a plain dict) because
# report requests run concurrently in Starlette's thread pool — a shared
# global here let one in-flight build's accent color/footer bleed into
# another's slides mid-build, producing a deck with mismatched colors
# per slide whenever two reports were generated around the same time.
class _ThemeStore(threading.local):
    def __init__(self):
        self.accent = DEFAULT_ACCENT
        self.footer = ""


class _ThemeProxy:
    def __getitem__(self, key):
        return getattr(_theme_store, key)

    def __setitem__(self, key, value):
        setattr(_theme_store, key, value)


_theme_store = _ThemeStore()
_theme = _ThemeProxy()


def _accent() -> RGBColor:
    return _theme["accent"]


def _blank_slide(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _fill(shape, color):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()


def _textbox(slide, left, top, width, height, text, size=14, bold=False, color=TEXT_DARK, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def _body_bg(slide):
    rect = slide.shapes.add_shape(1, 0, 0, SLIDE_W, SLIDE_H)
    _fill(rect, BODY_BG)
    rect.shadow.inherit = False
    return rect


def _content_header(slide, title, eyebrow=None):
    """Thin dark top strip + optional eyebrow tag + bold section title +
    full-width hairline accent rule, used on every content slide. Compact by
    design so slides carry more content below the fold. Also draws the footer."""
    _body_bg(slide)
    bar = slide.shapes.add_shape(1, 0, 0, SLIDE_W, Inches(0.1))
    _fill(bar, DARK)
    bar.shadow.inherit = False

    title_y = Inches(0.22)
    if eyebrow:
        _textbox(slide, Inches(0.4), Inches(0.18), Inches(8), Inches(0.28), eyebrow.upper(), size=10.5, bold=True, color=_accent())
        title_y = Inches(0.44)
    _textbox(slide, Inches(0.4), title_y, Inches(10.5), Inches(0.5), title, size=23, bold=True, color=TEXT_DARK)

    rule = slide.shapes.add_shape(1, Inches(0.4), Inches(0.92), SLIDE_W - Inches(0.8), Pt(1))
    _fill(rule, CARD_BORDER)
    rule.shadow.inherit = False
    accent_tick = slide.shapes.add_shape(1, Inches(0.4), Inches(0.9), Inches(0.55), Pt(3))
    _fill(accent_tick, _accent())
    accent_tick.shadow.inherit = False
    _footer(slide)


def _footer(slide):
    if not _theme["footer"]:
        return
    _textbox(
        slide, Inches(0.4), SLIDE_H - Inches(0.4), Inches(8), Inches(0.3),
        _theme["footer"], size=9, color=TEXT_MUTED,
    )


def _card(slide, left, top, width, height):
    """White rounded panel with a hairline border and a soft drop shadow,
    matching the Semrush/PSI-style panels in the sample deck."""
    card = slide.shapes.add_shape(5, left, top, width, height)  # rounded rectangle
    try:
        card.adjustments[0] = 0.045
    except (IndexError, AttributeError):
        pass
    card.fill.solid()
    card.fill.fore_color.rgb = WHITE
    card.line.color.rgb = CARD_BORDER
    card.line.width = Pt(0.75)
    card.shadow.inherit = False
    _soft_shadow(card)
    return card


def _soft_shadow(shape):
    """Subtle drop shadow via the raw XML shadow effect — python-pptx has no
    high-level API for outer shadows.

    `shape.shadow.inherit = False` (set by callers before this) already
    inserts its own empty <a:effectLst/> to suppress the theme's inherited
    shadow. Appending a second one on top of it is invalid per the OOXML
    schema (effectLst may appear at most once) — PowerPoint silently
    "repaired" every file built this way by stripping the invalid element,
    taking that shape's fill/shadow styling down with it. Removing any
    existing effectLst first keeps this to the one populated element the
    schema allows."""
    from pptx.oxml.ns import qn

    sp_pr = shape._element.spPr
    for existing in sp_pr.findall(qn("a:effectLst")):
        sp_pr.remove(existing)
    effect_lst = sp_pr.makeelement(qn("a:effectLst"), {})
    outer_shdw = sp_pr.makeelement(
        qn("a:outerShdw"),
        {"blurRad": "90000", "dist": "20000", "dir": "5400000", "rotWithShape": "0"},
    )
    clr = sp_pr.makeelement(qn("a:srgbClr"), {"val": "1A1A1A"})
    alpha = sp_pr.makeelement(qn("a:alpha"), {"val": "12000"})
    clr.append(alpha)
    outer_shdw.append(clr)
    effect_lst.append(outer_shdw)
    sp_pr.append(effect_lst)


def _chip_row(slide, left, top, items, max_width, size=11, bg=None, fg=None):
    """Compact wrapping row of pill-shaped tags — denser and more modern than
    a comma-joined text line, and self-wraps onto multiple rows if needed.
    Returns the bottom y (Emu) after the last row drawn."""
    bg = bg or CHIP_BG
    fg = fg or _accent()
    x = left
    y = top
    row_h = Inches(0.3)
    pad_x = Inches(0.14)
    char_w = Emu(int(Inches(1) * (size / 11) * 0.075))
    for item in items:
        w = Emu(int(char_w) * len(item)) + pad_x * 2
        if x + w > left + max_width and x != left:
            x = left
            y += row_h + Inches(0.08)
        chip = slide.shapes.add_shape(5, x, y, w, row_h)
        try:
            chip.adjustments[0] = 0.5
        except (IndexError, AttributeError):
            pass
        chip.fill.solid()
        chip.fill.fore_color.rgb = bg
        chip.line.fill.background()
        chip.shadow.inherit = False
        tf = chip.text_frame
        tf.word_wrap = False
        tf.margin_left = pad_x
        tf.margin_right = pad_x
        tf.margin_top = Emu(0)
        tf.margin_bottom = Emu(0)
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = item
        run.font.size = Pt(size)
        run.font.bold = True
        run.font.color.rgb = fg
        x += w + Inches(0.1)
    return y + row_h


def _icon_dot(slide, cx, cy, diameter, color):
    dot = slide.shapes.add_shape(9, cx, cy, diameter, diameter)
    _fill(dot, color)
    dot.shadow.inherit = False
    return dot


def _format_date_range(date_range: dict) -> str:
    """GA4 and Search Console are pulled with different windows (GSC lags
    ~3 days, see _gather_report_data) — when both are present and differ,
    state each explicitly rather than picking one and implying it covers
    both, since a reader comparing this slide's numbers against their own
    Search Console pull would otherwise get a mismatch with no explanation."""
    from datetime import date as _date

    def _fmt(iso: str) -> str:
        return _date.fromisoformat(iso).strftime("%b %d, %Y")

    ga4_start, ga4_end = date_range.get("ga4_start"), date_range.get("ga4_end")
    gsc_start, gsc_end = date_range.get("gsc_start"), date_range.get("gsc_end")
    parts = []
    if ga4_start and ga4_end:
        parts.append(f"Analytics: {_fmt(ga4_start)} – {_fmt(ga4_end)}")
    if gsc_start and gsc_end and (gsc_start, gsc_end) != (ga4_start, ga4_end):
        parts.append(f"Search Console: {_fmt(gsc_start)} – {_fmt(gsc_end)}")
    return "  ·  ".join(parts)


def _ga4_date_span(date_range: dict | None) -> str:
    """'Aug 01, 2026 – Aug 27, 2026' for the GA4 window in analytics["date_range"],
    or '' if unavailable — used so each Analytics slide states which dates its
    own numbers cover, not just the title slide."""
    if not date_range or not (date_range.get("ga4_start") and date_range.get("ga4_end")):
        return ""
    from datetime import date as _date

    fmt = lambda iso: _date.fromisoformat(iso).strftime("%b %d, %Y")
    return f"{fmt(date_range['ga4_start'])} – {fmt(date_range['ga4_end'])}"


def _gsc_date_span(date_range: dict | None) -> str:
    """Same as _ga4_date_span but for the Search Console window."""
    if not date_range or not (date_range.get("gsc_start") and date_range.get("gsc_end")):
        return ""
    from datetime import date as _date

    fmt = lambda iso: _date.fromisoformat(iso).strftime("%b %d, %Y")
    return f"{fmt(date_range['gsc_start'])} – {fmt(date_range['gsc_end'])}"


def add_title_slide(
    prs: Presentation, client_name: str, website_url: str = "", subtitle: str = "Web and SEO Audit",
    logo_bytes: bytes | None = None, analytics: dict | None = None,
):
    slide = _blank_slide(prs)
    _body_bg(slide)  # not used visually but keeps consistency; overwritten below
    top_bar = slide.shapes.add_shape(1, 0, 0, SLIDE_W, Inches(0.15))
    _fill(top_bar, DARK)
    top_bar.shadow.inherit = False

    white_area = slide.shapes.add_shape(1, 0, Inches(0.15), SLIDE_W, Inches(3.6))
    _fill(white_area, WHITE)
    white_area.shadow.inherit = False

    ring_box = (Inches(11.0), Inches(0.55), Inches(2.0), Inches(2.0))
    if logo_bytes:
        try:
            # Fit within the ring's box without distorting aspect ratio —
            # measure first, then scale by whichever dimension is larger.
            from PIL import Image

            img = Image.open(BytesIO(logo_bytes))
            w, h = img.size
            box_w, box_h = ring_box[2], ring_box[3]
            if w >= h:
                pic_w, pic_h = box_w, int(box_w * h / w)
            else:
                pic_h, pic_w = box_h, int(box_h * w / h)
            left = ring_box[0] + (box_w - pic_w) // 2
            top = ring_box[1] + (box_h - pic_h) // 2
            slide.shapes.add_picture(BytesIO(logo_bytes), left, top, width=pic_w, height=pic_h)
        except Exception:
            logo_bytes = None  # corrupt/unreadable image — fall through to the decorative ring
    if not logo_bytes:
        # decorative accent motif, echoes the score-ring circles used later in the deck
        ring = slide.shapes.add_shape(9, *ring_box)
        ring.fill.background()
        ring.line.color.rgb = _accent()
        ring.line.width = Pt(3)
        ring.shadow.inherit = False

    _textbox(
        slide, Inches(1.5), Inches(1.5), Inches(10), Inches(1.2),
        client_name, size=44, bold=True, color=_accent(), align=PP_ALIGN.LEFT,
    )
    domain = website_url.replace("https://", "").replace("http://", "").rstrip("/")
    if domain:
        _textbox(slide, Inches(1.5), Inches(2.35), Inches(10), Inches(0.5), domain, size=16, color=TEXT_MUTED)

    blue_area = slide.shapes.add_shape(1, 0, Inches(3.75), SLIDE_W, Inches(3.75))
    _fill(blue_area, _accent())
    blue_area.shadow.inherit = False

    _textbox(slide, Inches(1.5), Inches(4.6), Inches(10), Inches(0.6), subtitle, size=24, bold=True, color=WHITE)

    from datetime import date

    date_range_text = _format_date_range(analytics["date_range"]) if analytics and analytics.get("date_range") else ""
    footer_text = f"{date.today().strftime('%B %Y')}   |   Data window — {date_range_text}" if date_range_text else date.today().strftime("%B %Y")
    _textbox(
        slide, Inches(1.5), Inches(5.15), Inches(10), Inches(0.4),
        footer_text, size=13, color=RGBColor(0xE0, 0xE0, 0xE0),
    )
    return slide


def add_section_slide(prs: Presentation, client_name: str, section_title: str):
    slide = _blank_slide(prs)
    top_bar = slide.shapes.add_shape(1, 0, 0, SLIDE_W, Inches(0.15))
    _fill(top_bar, DARK)
    top_bar.shadow.inherit = False

    white_area = slide.shapes.add_shape(1, 0, Inches(0.15), SLIDE_W, Inches(3.6))
    _fill(white_area, WHITE)
    white_area.shadow.inherit = False

    _textbox(slide, Inches(1.5), Inches(1.7), Inches(10), Inches(1.0), client_name, size=36, bold=True, color=_accent())

    blue_area = slide.shapes.add_shape(1, 0, Inches(3.75), SLIDE_W, Inches(3.75))
    _fill(blue_area, _accent())
    blue_area.shadow.inherit = False
    _textbox(slide, Inches(1.5), Inches(4.6), Inches(10), Inches(0.6), section_title, size=24, bold=True, color=WHITE)
    return slide


def _score_color(score: int | None):
    if score is None:
        return TEXT_MUTED
    if score >= 90:
        return GOOD
    if score >= 50:
        return WARN
    return BAD


def _score_ring(slide, cx, cy, diameter, score: int | None, label: str):
    color = _score_color(score)
    # faint full backing ring so the colored arc reads as a gauge, not a plain circle
    backing = slide.shapes.add_shape(9, cx, cy, diameter, diameter)
    backing.fill.background()
    backing.line.color.rgb = RGBColor(0xE5, 0xE5, 0xE5)
    backing.line.width = Pt(7)
    backing.shadow.inherit = False

    ring = slide.shapes.add_shape(9, cx, cy, diameter, diameter)
    ring.fill.solid()
    ring.fill.fore_color.rgb = WHITE
    ring.line.color.rgb = color
    ring.line.width = Pt(6)
    ring.shadow.inherit = False
    tf = ring.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = str(score) if score is not None else "—"
    run.font.size = Pt(28)
    run.font.bold = True
    run.font.color.rgb = color
    _textbox(
        slide, cx - Inches(0.3), cy + diameter + Inches(0.05), diameter + Inches(0.6), Inches(0.35),
        label, size=12, color=TEXT_DARK, align=PP_ALIGN.CENTER,
    )


def _score_legend(slide, left, top):
    """Small colored-square + range legend, like the 0-49 / 50-89 / 90-100 chips in the sample."""
    items = [(BAD, "0–49"), (WARN, "50–89"), (GOOD, "90–100")]
    x = left
    for color, label in items:
        sq = slide.shapes.add_shape(1, x, top, Inches(0.14), Inches(0.14))
        _fill(sq, color)
        sq.shadow.inherit = False
        _textbox(slide, x + Inches(0.2), top - Inches(0.04), Inches(0.9), Inches(0.25), label, size=10, color=TEXT_MUTED)
        x += Inches(1.05)


def add_pagespeed_slide(prs: Presentation, mobile: dict | None, desktop: dict | None):
    slide = _blank_slide(prs)
    _content_header(slide, "Website Performance")
    _textbox(slide, Inches(9.5), Inches(0.3), Inches(3.3), Inches(0.4), "Source: Google PageSpeed Insights", size=11, color=TEXT_MUTED)

    for i, (label, result) in enumerate([("Mobile", mobile), ("Desktop", desktop)]):
        top = Inches(1.1) + Emu(i * Inches(2.9))
        card = _card(slide, Inches(0.4), top, Inches(9.0), Inches(2.5))
        _textbox(slide, Inches(0.6), top + Inches(0.1), Inches(3), Inches(0.4), label, size=16, bold=True)
        if not result:
            _textbox(slide, Inches(0.6), top + Inches(0.9), Inches(6), Inches(0.5), "Not run", size=13, color=TEXT_MUTED)
            continue
        scores = result.get("scores", {})
        labels = [("performance", "Performance"), ("seo", "SEO"), ("accessibility", "Accessibility"), ("best_practices", "Best Practices")]
        for j, (key, disp) in enumerate(labels):
            cx = Inches(0.9) + Emu(j * Inches(2.1))
            _score_ring(slide, cx, top + Inches(0.6), Inches(1.1), scores.get(key), disp)

    _score_legend(slide, Inches(9.8), Inches(1.6))
    _textbox(
        slide, Inches(9.8), Inches(2.0), Inches(3.1), Inches(1.3),
        "Best Practice: A score of 90+ is excellent. 50–89 is good but needs "
        "improvement. Below 50 is poor.",
        size=12, color=TEXT_DARK,
    )

    insights = []
    if mobile and desktop:
        m_scores, d_scores = mobile.get("scores", {}), desktop.get("scores", {})
        for key, disp in [("performance", "Performance"), ("seo", "SEO"), ("accessibility", "Accessibility"), ("best_practices", "Best Practices")]:
            m, d = m_scores.get(key), d_scores.get(key)
            if m is not None and m < 50:
                insights.append(f"Mobile {disp} is {m} — below the poor threshold, hurting mobile rankings directly.")
        if m_scores.get("performance") is not None and d_scores.get("performance") is not None:
            gap = d_scores["performance"] - m_scores["performance"]
            if gap > 15:
                insights.append(f"Mobile Performance trails desktop by {gap} points — most traffic is mobile, so this is the higher-impact fix.")
        worst_key, worst_val = None, 101
        for key, disp in [("performance", "Performance"), ("seo", "SEO"), ("accessibility", "Accessibility"), ("best_practices", "Best Practices")]:
            for scores in (m_scores, d_scores):
                v = scores.get(key)
                if v is not None and v < worst_val:
                    worst_val, worst_key = v, disp
        if worst_key and worst_val < 90:
            insights.append(f"Weakest area overall: {worst_key} at {worst_val}.")
    if insights:
        _insights_strip(slide, Inches(9.8), Inches(4.6), Inches(3.1), insights)
    return slide


def _fmt_metric_value(metric_id: str, value: float) -> str:
    if metric_id == "cumulative-layout-shift":
        return f"{value:.2f}"
    return f"{value / 1000:.1f}s" if value >= 1000 else f"{round(value)}ms"


def add_pagespeed_score_breakdown_slide(prs: Presentation, mobile: dict | None, desktop: dict | None):
    """The Lighthouse Scoring Calculator, reimplemented against our own PSI
    data: which metric is actually costing the most Performance points, and
    what the score would become if each (or the top few) were fixed to
    Google's 'good' threshold. Mobile is the primary table — it's usually
    the worse and higher-traffic surface — desktop only supplies the
    mobile-vs-desktop callout already used on the score-ring slide."""
    primary_label, primary = ("Mobile", mobile) if mobile and mobile.get("quick_wins") else ("Desktop", desktop)
    if not primary or not primary.get("quick_wins"):
        return None

    headers = ["Metric", "Current", "Score", "Good Threshold", "If Fixed", "Score Impact"]
    rows = []
    for w in primary["quick_wins"]:
        current = w.get("display_value") or _fmt_metric_value(w["id"], w["value"])
        threshold = _fmt_metric_value(w["id"], w["p10"])
        delta = w["score_delta"]
        rows.append((
            w["label"], current, str(w["score"]), threshold, str(w["score_if_fixed"]),
            f"+{delta}" if delta > 0 else str(delta),
        ))

    current_score = primary["current_score"]
    insights = [f"{primary_label} Performance score is {current_score} — breakdown ranked by which metric costs the most points."]
    projection = primary.get("combined_projection")
    if projection and projection["metrics"] and projection["score_after"] > projection["score_before"]:
        metrics_str = " + ".join(projection["metrics"])
        insights.append(
            f"Fixing {metrics_str} alone would move Performance from {projection['score_before']} to {projection['score_after']}."
        )
    if mobile and desktop and mobile.get("current_score") is not None and desktop.get("current_score") is not None:
        gap = desktop["current_score"] - mobile["current_score"]
        if gap > 15:
            insights.append(f"Desktop Performance ({desktop['current_score']}) outpaces Mobile ({mobile['current_score']}) by {gap} points.")

    return _table_slide(
        prs, "Website Performance — Score Breakdown", headers, rows,
        col_widths=[2.4, 1.7, 1.3, 2.2, 1.5, 1.8], source="Google PageSpeed Insights (Lighthouse scoring model)",
        insights=insights,
    )


def _fmt_kb(num_bytes: int) -> str:
    return f"{num_bytes / 1024:.0f} KB"


def _short_resource_name(url: str, maxlen: int = 55) -> str:
    from urllib.parse import unquote, urlparse
    path = unquote(urlparse(url).path)
    name = path.rsplit("/", 1)[-1] or url
    return name if len(name) <= maxlen else name[: maxlen - 1] + "…"


def add_pagespeed_script_weight_slide(prs: Presentation, mobile: dict | None, desktop: dict | None):
    """PSI's Treemap view, translated to report form: which JS files are
    heaviest and how much of each is actually wasted (dead/unused code) —
    named resources, not a generic 'reduce unused JavaScript' line. Mobile
    is primary for the same reason as the score-breakdown slide."""
    primary_label, primary = ("Mobile", mobile) if mobile and mobile.get("script_weight") else ("Desktop", desktop)
    sw = (primary or {}).get("script_weight")
    if not sw or not sw.get("top_waste"):
        return None

    headers = ["Script", "Size", "Wasted", "Domain"]
    rows = []
    for w in sw["top_waste"]:
        rows.append((
            _short_resource_name(w["url"]),
            _fmt_kb(w["total_bytes"]),
            f"{w['wasted_percent']:.0f}% ({_fmt_kb(w['wasted_bytes'])})",
            "External" if w["is_third_party"] else "Same domain",
        ))

    # "External" only means the script loads from a different domain than the
    # site itself — it may be a genuine third-party vendor (ads, chat, tag
    # manager) or the client's own CDN subdomain. Deliberately not framed as
    # vendor blame here; that call needs a human look at which domain it is.
    insights = [
        f"Total JS payload ({primary_label}): {_fmt_kb(sw['total_js_bytes'])}, {sw['third_party_pct']}% loads from an external domain "
        "(own CDN or a genuine third-party vendor — worth checking which)."
    ]
    worst = sw["top_waste"][0]
    insights.append(
        f"\"{_short_resource_name(worst['url'])}\" wastes {worst['wasted_percent']:.0f}% of its {_fmt_kb(worst['total_bytes'])} "
        "— highest single opportunity to trim dead code."
    )

    return _table_slide(
        prs, "Website Performance — Script Weight Breakdown", headers, rows,
        col_widths=[5.5, 1.5, 3.0, 2.0], source="Google PageSpeed Insights (Treemap + Unused JavaScript audit)",
        insights=insights,
    )


def _squarify(nodes: list[dict], x: float, y: float, w: float, h: float) -> list[dict]:
    """Same squarified-treemap layout as the frontend's ScriptTreemap
    component (googlechrome.github.io/lighthouse/treemap uses the same
    algorithm) — kept independent since the slide is built server-side
    with no access to the browser layout."""
    total = sum(n["resource_bytes"] for n in nodes) or 1
    area = w * h
    out = []
    remaining = list(nodes)
    rx, ry, rw, rh = x, y, w, h

    while remaining:
        vertical = rw >= rh
        side = rh if vertical else rw
        row, row_sum, best_worst = [], 0.0, float("inf")
        for n in remaining:
            candidate = row + [n]
            s = row_sum + n["resource_bytes"]
            row_area = (s / total) * area
            row_len = row_area / side if side else 0
            worst = max(
                (max(row_len / ((c["resource_bytes"] / total) * area / row_len), ((c["resource_bytes"] / total) * area / row_len) / row_len)
                 if row_len else 0)
                for c in candidate
            )
            if worst <= best_worst or not row:
                row, row_sum, best_worst = candidate, s, worst
            else:
                break
        remaining = remaining[len(row):]
        row_area = (row_sum / total) * area
        row_len = row_area / side if side else 0
        offset = 0.0
        for n in row:
            a = (n["resource_bytes"] / total) * area
            length = a / row_len if row_len else 0
            if vertical:
                out.append({**n, "x": rx, "y": ry + offset, "w": row_len, "h": length})
            else:
                out.append({**n, "x": rx + offset, "y": ry, "w": length, "h": row_len})
            offset += length
        if vertical:
            rx += row_len
            rw -= row_len
        else:
            ry += row_len
            rh -= row_len
    return out


def _format_bytes(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _treemap_tile_color(node: dict) -> RGBColor:
    pct = (node["unused_bytes"] / node["resource_bytes"]) if node["resource_bytes"] else 0
    if pct >= 0.5:
        return BAD
    if pct >= 0.2:
        return WARN
    return RGBColor(0x2F, 0x6F, 0xED)


# Well-known 3rd-party script name fragments — matched against the
# script's filename since some vendor scripts load same-origin through a
# proxy/CDN passthrough and wouldn't otherwise look external by host alone.
# Used to give a specific, nameable vendor in the actionable insight
# ("ask <vendor> whether this needs to block page load") instead of a
# generic "third-party script."
_KNOWN_THIRD_PARTY_SCRIPT_PATTERNS = [
    ("fbevents", "Meta/Facebook Pixel"), ("gtag", "Google Analytics/Ads"), ("gtm.js", "Google Tag Manager"),
    ("posthog", "PostHog analytics"), ("hotjar", "Hotjar"), ("fullstory", "FullStory session replay"),
    ("logrocket", "LogRocket session replay"), ("clarity", "Microsoft Clarity"), ("intercom", "Intercom chat widget"),
    ("hubspot", "HubSpot"), ("memberstack", "Memberstack"), ("segment", "Segment"),
    ("tracing.replay", "a session-replay tool"), ("survey", "a survey widget"), ("platform.js", "a social platform SDK"),
    # Site-builder/CMS platform runtime bundles — not the client's own
    # code even when served same-origin, so must NOT be classified "own"
    # (telling a client to "code-split this, ask your dev team" on a file
    # the platform generates and controls is wrong, actionable advice).
    ("webflow.schunk", "the Webflow platform's own runtime"),
    ("webflow.js", "the Webflow platform's own runtime"),
    ("wp-content", "a WordPress plugin"),
    ("shopify", "the Shopify platform"),
]


def _classify_script_party(name: str, site_domain: str) -> tuple[str, str | None]:
    """('third_party' | 'own', vendor_label). vendor_label names the likely
    vendor when recognizable from the filename; still third_party with
    vendor_label=None otherwise (host differs from the site's own domain
    but isn't in the known-pattern list)."""
    lower = (name or "").lower()
    for pattern, vendor in _KNOWN_THIRD_PARTY_SCRIPT_PATTERNS:
        if pattern in lower:
            return "third_party", vendor
    host = urlparse(name or "").netloc.lower()
    site_host = (site_domain or "").lower().replace("www.", "")
    if host and site_host and site_host not in host:
        return "third_party", None
    return "own", None


def _script_treemap_actions(nodes: list[dict], site_domain: str) -> list[str]:
    """Actionable findings only, per user request 2026-09-09: no bare
    "Total JS analyzed: X" description — every bullet names a concrete
    file and says what to actually do about it. Split 1st-party (client's
    own code — a dev-team code-splitting/dead-code task) vs. 3rd-party
    (vendor scripts — a defer/lazy-load or "do we still need this" call,
    zero engineering risk since it's not the client's own code) since
    those are two entirely different fixes with different owners."""
    classified = [{**n, "_party": p, "_vendor": v} for n in nodes for p, v in [_classify_script_party(n.get("name", ""), site_domain)]]
    third_party = [n for n in classified if n["_party"] == "third_party"]
    own = [n for n in classified if n["_party"] == "own"]

    actions: list[str] = []

    if third_party:
        tp_bytes = sum(n["resource_bytes"] for n in third_party)
        top_tp = max(third_party, key=lambda n: n["resource_bytes"])
        top_tp_name = top_tp["name"].rsplit("/", 1)[-1]
        vendor_note = f" ({top_tp['_vendor']})" if top_tp["_vendor"] else ""
        actions.append(
            f"{len(third_party)} third-party script(s) totaling {_format_bytes(tp_bytes)} can be deferred or "
            f"loaded after the page becomes interactive — none of them need to block first render. Biggest: "
            f"{top_tp_name}{vendor_note} at {_format_bytes(top_tp['resource_bytes'])}."
        )

    own_with_waste = [n for n in own if n.get("unused_bytes", 0) > 0]
    if own_with_waste and len(actions) < 3:
        top_own = max(own_with_waste, key=lambda n: n["unused_bytes"])
        waste_pct = 100 * top_own["unused_bytes"] / top_own["resource_bytes"] if top_own["resource_bytes"] else 0
        top_own_name = top_own["name"].rsplit("/", 1)[-1]
        actions.append(
            f"\"{top_own_name}\" is your own code and wastes {_format_bytes(top_own['unused_bytes'])} "
            f"({waste_pct:.0f}% of {_format_bytes(top_own['resource_bytes'])}) — code-split or remove the unused "
            f"portion; this one needs your dev team, not a vendor."
        )

    named_vendor = next((n for n in third_party if n["_vendor"]), None)
    if named_vendor and len(actions) < 3:
        actions.append(
            f"\"{named_vendor['name'].rsplit('/', 1)[-1]}\" is {named_vendor['_vendor']} "
            f"({_format_bytes(named_vendor['resource_bytes'])}) — confirm it's actually needed on every page load, "
            f"or delay it until after the page is interactive."
        )

    return actions[:3]


def add_script_treemap_slide(prs: Presentation, mobile: dict | None, desktop: dict | None, website_url: str | None = None):
    """Renders Lighthouse's script-treemap-data audit (the same data behind
    googlechrome.github.io/lighthouse/treemap) as native PPTX rectangles —
    which JS bundles are biggest and how much of each goes unused — with
    actionable insights only (2026-09-09 user request: no bare stats,
    every bullet is a concrete thing to do)."""
    result = mobile or desktop
    nodes = (result or {}).get("script_treemap") or []
    if not nodes:
        return None
    label = "Mobile" if result is mobile else "Desktop"

    slide = _blank_slide(prs)
    _content_header(slide, "JavaScript Bundle Breakdown")
    _textbox(slide, Inches(9.5), Inches(0.3), Inches(3.3), Inches(0.4), "Source: Google PageSpeed Insights", size=11, color=TEXT_MUTED)
    _textbox(slide, Inches(0.4), Inches(1.0), Inches(6), Inches(0.4), f"{label} — script treemap (size vs. unused bytes)", size=13, color=TEXT_MUTED)

    origin_x, origin_y = Inches(0.4), Inches(1.5)
    map_w, map_h = Inches(9.0), Inches(5.2)
    laid_out = _squarify(nodes[:40], 0.0, 0.0, float(map_w), float(map_h))

    for n in laid_out:
        left = origin_x + Emu(int(n["x"]))
        top = origin_y + Emu(int(n["y"]))
        w = max(Emu(int(n["w"])), Emu(1))
        h = max(Emu(int(n["h"])), Emu(1))
        tile = slide.shapes.add_shape(1, left, top, w, h)
        _fill(tile, _treemap_tile_color(n))
        tile.line.color.rgb = WHITE
        tile.line.width = Pt(0.75)
        tile.shadow.inherit = False
        if n["w"] > Inches(0.9) and n["h"] > Inches(0.35):
            tf = tile.text_frame
            tf.word_wrap = True
            tf.margin_left = tf.margin_right = Pt(3)
            tf.margin_top = tf.margin_bottom = Pt(2)
            name = n["name"].rsplit("/", 1)[-1] or n["name"]
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = name
            run.font.size = Pt(9)
            run.font.bold = True
            run.font.color.rgb = WHITE
            p2 = tf.add_paragraph()
            run2 = p2.add_run()
            run2.text = _format_bytes(n["resource_bytes"])
            run2.font.size = Pt(8)
            run2.font.color.rgb = WHITE

    legend_x = Inches(9.8)
    for i, (color, text) in enumerate([
        (RGBColor(0x2F, 0x6F, 0xED), "<20% unused"),
        (WARN, "20–50% unused"),
        (BAD, ">50% unused"),
    ]):
        sq_top = Inches(1.6) + Emu(i * Inches(0.4))
        sq = slide.shapes.add_shape(1, legend_x, sq_top, Inches(0.16), Inches(0.16))
        _fill(sq, color)
        sq.shadow.inherit = False
        _textbox(slide, legend_x + Inches(0.28), sq_top - Inches(0.03), Inches(2.6), Inches(0.3), text, size=12)

    insights = _script_treemap_actions(nodes, website_url or "")
    _insights_strip(slide, Inches(9.8), Inches(3.2), Inches(3.1), insights)
    return slide


def _issue_row(slide, left, top, width, text, severity="warn"):
    color = BAD if severity == "error" else WARN
    dot = slide.shapes.add_shape(9, left, top + Inches(0.06), Inches(0.12), Inches(0.12))
    _fill(dot, color)
    dot.shadow.inherit = False
    _textbox(slide, left + Inches(0.25), top, width - Inches(0.25), Inches(0.35), text, size=13)


_CRAWLED_PAGE_CATEGORIES = ["Blocked", "Redirect", "Have issues", "Broken", "Healthy"]


def _canonical_page_totals(site_audit_pages_rows: list[dict] | None, site_audit_overview: dict | None) -> dict | None:
    """One 'how many pages' definition, computed once and reused by every
    slide that shows a page-count figure. Previously each slide picked its
    own number (Crawled Pages export row count vs. Site Health overview's
    own rollup vs. a manual per-slide tally) — the exact 3-conflicting-
    totals confusion the manual analyst process flagged ("check the issues
    tab and count the unique URLs... because this is the confusing one").
    Prefers the per-URL Crawled Pages export (real row-per-page data, so it
    can also yield a true unique-affected-URL count); falls back to the
    Site Health overview's own rolled-up total when only that's uploaded."""
    if site_audit_pages_rows:
        total_urls, issue_urls = set(), set()
        for r in site_audit_pages_rows:
            url = r.get("page_url")
            if not url:
                continue
            total_urls.add(url)
            try:
                has_issues = float(r.get("issues") or 0) > 0
            except (TypeError, ValueError):
                has_issues = False
            if has_issues:
                issue_urls.add(url)
        return {
            "total": len(total_urls),
            "with_issues": len(issue_urls),
            "source": "Semrush Site Audit — Crawled Pages export",
        }
    if site_audit_overview and site_audit_overview.get("pages_total"):
        return {
            "total": site_audit_overview["pages_total"],
            "with_issues": None,
            "source": "Semrush Site Audit — Site Health overview",
        }
    return None


def add_site_health_slide(
    prs: Presentation,
    audit: dict,
    site_audit_overview: dict | None = None,
    site_audit_pages_rows: list[dict] | None = None,
):
    slide = _blank_slide(prs)
    _content_header(slide, "Understanding Current Scenario")
    if site_audit_overview and site_audit_overview.get("export_date"):
        _textbox(
            slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4),
            f"Source: Semrush Site Audit ({site_audit_overview['export_date']})", size=11, color=TEXT_MUTED,
        )

    card1 = _card(slide, Inches(0.6), Inches(1.2), Inches(3.6), Inches(2.6))
    _textbox(slide, Inches(0.8), Inches(1.35), Inches(3), Inches(0.4), "Crawled Pages", size=15, bold=True)

    page_totals = _canonical_page_totals(site_audit_pages_rows, site_audit_overview)
    if site_audit_overview:
        health_pct = site_audit_overview.get("site_health_pct")
        crawled_display = f"{page_totals['total']:,}" if page_totals else "—"
        _textbox(
            slide, Inches(0.8), Inches(1.68), Inches(2.5), Inches(0.5),
            crawled_display, size=26, bold=True, color=_accent(),
        )
        category_colors = {
            "blocked": TEXT_MUTED,
            "redirect": RGBColor(0x5B, 0x5F, 0xE0),
            "have_issues": WARN,
            "broken": BAD,
            "healthy": GOOD,
        }
        y = Inches(2.18)
        for category in _CRAWLED_PAGE_CATEGORIES:
            key = category.lower().replace(" ", "_")
            count = site_audit_overview.get(f"{key}_count")
            pct = site_audit_overview.get(f"{key}_pct")
            if count is None:
                continue
            _icon_dot(slide, Inches(0.85), y + Inches(0.06), Inches(0.11), category_colors[key])
            _textbox(slide, Inches(1.05), y, Inches(2.9), Inches(0.28), f"{category}: {count} ({pct}%)", size=11.5, color=TEXT_DARK)
            y += Inches(0.29)
    else:
        # Crawled Pages / Site Health are Semrush-only now — no own-crawl
        # fallback. A client with no Site Audit Overview PDF uploaded yet
        # gets an explicit "no data" note instead of a silently-substituted
        # (and less accurate) own-crawl approximation.
        health_pct = None
        _textbox(
            slide, Inches(0.8), Inches(1.75), Inches(3.0), Inches(0.9),
            "No Semrush Site Audit data uploaded yet.", size=12.5, color=TEXT_MUTED,
        )

    card2 = _card(slide, Inches(4.5), Inches(1.2), Inches(3.6), Inches(2.6))
    _textbox(slide, Inches(4.7), Inches(1.35), Inches(3), Inches(0.4), "Site Health", size=15, bold=True)
    _score_ring(slide, Inches(5.6), Inches(1.85), Inches(1.4), health_pct, "full site crawl")
    if site_audit_overview and site_audit_overview.get("ai_search_health_pct") is not None:
        _textbox(
            slide, Inches(4.7), Inches(3.45), Inches(3.2), Inches(0.3),
            f"AI Search Health: {site_audit_overview['ai_search_health_pct']}%", size=11.5, color=TEXT_MUTED, align=PP_ALIGN.CENTER,
        )

    card3 = _card(slide, Inches(8.4), Inches(1.2), Inches(4.3), Inches(2.6))
    _textbox(slide, Inches(8.6), Inches(1.35), Inches(4), Inches(0.4), "Foundations", size=15, bold=True)
    rows = [
        ("HTTPS", audit.get("https")),
        ("robots.txt present", audit.get("robots_txt", {}).get("present")),
        ("sitemap.xml present", audit.get("sitemap", {}).get("present")),
        ("Homepage reachable", audit.get("reachable")),
    ]
    for i, (label, ok) in enumerate(rows):
        y = Inches(1.85) + Emu(i * Inches(0.4))
        _icon_dot(slide, Inches(8.6), y + Inches(0.08), Inches(0.14), GOOD if ok else BAD)
        _textbox(slide, Inches(8.85), y, Inches(2.1), Inches(0.35), label, size=13)
        mark = "OK" if ok else "Missing"
        _textbox(slide, Inches(11.2), y, Inches(1.3), Inches(0.35), mark, size=13, bold=True, color=(GOOD if ok else BAD))

    _textbox(slide, Inches(0.6), Inches(4.1), Inches(9), Inches(0.35), f"Domain: {audit.get('url', '')}", size=13, color=TEXT_MUTED)
    return slide


def add_site_structure_slide(prs: Presentation, site_audit_pages_rows: list[dict] | None):
    """Flat directory-level table — Directory | URLs, one row per top-level
    directory, NO sub-directory nesting — matching Semrush's own "Site
    Structure" widget as it appears in the client's own reference manual
    report (BEST audit deck, page 6): a plain table, not a hierarchy tree
    or indented list. Reverted back to this 2026-09-09 after two earlier
    hierarchy-shaped attempts (a node/connector diagram, then an indented
    list) — the diagram version's sub-directory rows also only ever showed
    the top 2 children per parent, which real per-URL counts don't sum
    anywhere close to the parent total (confirmed live: /ca showed 439
    URLs but its 2 shown "children" summed to ~123) — a flat single-level
    table has no such parent/child sum to mislead with. URLs-only, no
    Issues column, per explicit user request. Derived entirely from
    Semrush Site Audit's per-page export (page_url) already parsed for the
    SEO Issues / Tech Fixes slides — no new
    Semrush upload needed, just a grouping."""
    if not site_audit_pages_rows:
        return None

    # WordPress auto-generates a permalink for every post under one of these
    # base prefixes (post/page slugs, author archives, tag/category archives).
    # Each is an individual item or archive page, not a real content section
    # a client would recognize as a site "directory" — bucketing them
    # standalone produces phantom top-level directories (e.g. "/post" with
    # dozens of URLs) that don't correspond to anything in the client's nav.
    # Fold them into "/blog" alongside the section they actually belong to.
    _WP_ARCHIVE_PREFIXES = {"post", "author", "tag", "category", "page"}

    # A crawl can include a handful of pages from a subdomain (e.g. a
    # helpdesk/portal subdomain) alongside the main site — picking "any"
    # domain from a set is non-deterministic and previously surfaced a
    # rarely-crawled subdomain as the header instead of the actual site.
    # The domain with the most crawled pages is the real site, and every
    # other subdomain's paths are excluded below so they can't be
    # misattributed into this domain's directory structure.
    domain_counts = Counter(urlparse(r.get("page_url") or "").netloc for r in site_audit_pages_rows)
    domain_counts.pop("", None)
    if not domain_counts:
        return None
    domain = domain_counts.most_common(1)[0][0]

    top_counts: dict[str, int] = {}
    for r in site_audit_pages_rows:
        parsed = urlparse(r.get("page_url") or "")
        if parsed.netloc != domain:
            continue
        segments = [s for s in parsed.path.split("/") if s]
        if not segments:
            # A bare "/" page — already covered by the domain row itself, so
            # it doesn't need its own "/ (root)" child row.
            continue
        if segments[0].lower() in _WP_ARCHIVE_PREFIXES and len(segments) > 1:
            directory = "/blog"
        else:
            directory = f"/{segments[0]}"
        top_counts[directory] = top_counts.get(directory, 0) + 1

    # A first path segment with exactly 1 page under it isn't a real
    # section/folder a client would recognize — it's just that one page's
    # own slug (e.g. a flat-URL blog post like /1099-filing-mistakes,
    # not nested under /blog/). Confirmed live: on a site with mostly
    # flat post URLs, dozens of these one-off slugs tied at count=1 and,
    # sorted stably, interleaved ahead of real recurring sections like
    # /wiki or /product — crowding out the actual structure the client
    # asked to see, the way Semrush's own widget (real folders only)
    # doesn't have this problem since it isn't drowning in single pages.
    ranked = sorted(((d, c) for d, c in top_counts.items() if c >= 2), key=lambda kv: -kv[1])

    slide = _blank_slide(prs)
    _content_header(slide, "Website Structure")
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), "Source: Semrush Site Audit", size=11, color=TEXT_MUTED, align=PP_ALIGN.RIGHT)

    # Same canonical total as the Site Health slide's "Crawled Pages" card
    # (deduped unique URLs, not just the sum of directory buckets below —
    # those exclude bare "/"-root and single-page/WP-archive URLs folded
    # elsewhere) so this slide's domain-row figure never quietly disagrees
    # with the earlier one for the same crawl.
    canonical = _canonical_page_totals(site_audit_pages_rows, None)
    site_total = canonical["total"] if canonical else sum(top_counts.values())

    ROW_CAP = 12
    shown = ranked[:ROW_CAP - 1]  # -1 to leave room for the domain row
    rows = [(domain, f"{site_total:,}")] + [(directory, f"{count:,}") for directory, count in shown]

    insights = [f"{site_total:,} total crawled URLs across {len(ranked)} top-level director{'y' if len(ranked) == 1 else 'ies'}."]
    if len(ranked) > len(shown):
        insights.append(f"Showing the top {len(shown)} directories by URL count — {len(ranked) - len(shown)} more not shown here.")

    # Inner "Site Structure" label above the table, matching the reference
    # deck's card exactly (BEST_p6.png) — Semrush's own widget carries this
    # same small title inside the card, distinct from the slide's own big
    # "Website Structure" header above it.
    _textbox(slide, Inches(0.6), Inches(1.2), Inches(4), Inches(0.24), "Site Structure", size=12.5, bold=True, color=_accent())
    _draw_table(
        slide, ["Directory", "URLs"], rows, Inches(1.55),
        col_widths=[9.1, 3.0], row_cap=ROW_CAP, insights=insights,
    )
    return slide


def add_company_overview_slide(prs: Presentation, client_name: str, summary: str):
    slide = _blank_slide(prs)
    _content_header(slide, "Company Overview")
    _card(slide, Inches(0.6), Inches(1.2), Inches(12.1), Inches(3.4))
    _textbox(slide, Inches(0.9), Inches(1.45), Inches(11.5), Inches(0.4), client_name, size=17, bold=True)
    _textbox(slide, Inches(0.9), Inches(2.0), Inches(11.5), Inches(2.4), summary, size=14)
    return slide


def _icp_block(slide, x, top, width, label, items, size=11):
    """One labeled chip-row block in the Ideal Customer Profile column.
    Returns the bottom y (Emu) after the block."""
    if not items:
        return top
    _textbox(slide, x, top, width, Inches(0.24), label.upper(), size=9.5, bold=True, color=TEXT_MUTED)
    bottom = _chip_row(slide, x, top + Inches(0.26), items, width, size=size)
    return bottom + Inches(0.14)


def add_company_overview_extracted_slide(prs: Presentation, client_name: str, overview: dict):
    """Renders the Gemini-extracted About/Products/KPIs/ICP overview as two
    dense columns — narrative + KPIs on the left, Ideal Customer Profile as
    wrapping chip rows on the right, contact/registration pinned to the
    footer. Chips pack more into less vertical space than a bullet list."""
    slide = _blank_slide(prs)
    _content_header(slide, overview.get("company_name") or client_name, eyebrow="Company Overview")
    _textbox(slide, Inches(9.6), Inches(0.4), Inches(3.3), Inches(0.3), "Source: site crawl + Gemini", size=10, color=TEXT_MUTED, align=PP_ALIGN.RIGHT)

    top = Inches(1.15)
    height = Inches(5.55)
    left_width = Inches(5.55)
    gap = Inches(0.35)
    right_x = Inches(0.6) + left_width + gap
    right_width = Inches(12.1) - left_width - gap

    _card(slide, Inches(0.6), top, left_width, height)
    _card(slide, right_x, top, right_width, height)

    pad = Inches(0.25)
    y = top + pad

    description = overview.get("description")
    if description:
        _textbox(slide, Inches(0.6) + pad, y, left_width - pad * 2, Inches(1.6), description, size=12.5)
        chars_per_line = 50
        lines = max(1, -(-len(description) // chars_per_line))
        y += Emu(lines * Inches(0.22)) + Inches(0.22)

    kpis = overview.get("kpis") or []
    if kpis:
        _textbox(slide, Inches(0.6) + pad, y, left_width - pad * 2, Inches(0.26), "KEY PERFORMANCE INDICATORS", size=9.5, bold=True, color=_accent())
        y += Inches(0.3)
        kpi_width = left_width - pad * 2 - Inches(0.18)
        line_h = Inches(11.5 * 0.02)
        for k in kpis[:6]:
            lines = _wrap_lines(k, kpi_width, size_pt=11.5)
            _icon_dot(slide, Inches(0.6) + pad, y + Inches(0.07), Inches(0.08), _accent())
            _textbox(slide, Inches(0.6) + pad + Inches(0.18), y, kpi_width, line_h * lines, k, size=11.5)
            y += line_h * lines + Inches(0.06)
        y += Inches(0.1)

    industries = overview.get("industries") or []
    if industries:
        _textbox(slide, Inches(0.6) + pad, y, left_width - pad * 2, Inches(0.24), "INDUSTRIES SERVED", size=9.5, bold=True, color=_accent())
        y = _chip_row(slide, Inches(0.6) + pad, y + Inches(0.26), industries[:8], left_width - pad * 2, size=10.5, bg=ROW_ALT, fg=TEXT_DARK) + Inches(0.1)

    ry = top + pad
    rx = right_x + pad
    rw = right_width - pad * 2
    _textbox(slide, rx, ry, rw, Inches(0.28), "IDEAL CUSTOMER PROFILE", size=10.5, bold=True, color=_accent())
    ry += Inches(0.36)

    target_country = overview.get("target_country")
    target_market = overview.get("target_market")
    if target_country or target_market:
        half = rw / 2 - Inches(0.1)
        if target_country:
            _textbox(slide, rx, ry, half, Inches(0.24), "TARGET COUNTRY", size=9, bold=True, color=TEXT_MUTED)
            _textbox(slide, rx, ry + Inches(0.22), half, Inches(0.3), target_country, size=12.5, bold=True)
        if target_market:
            _textbox(slide, rx + half + Inches(0.2), ry, half, Inches(0.24), "TARGET", size=9, bold=True, color=TEXT_MUTED)
            _textbox(slide, rx + half + Inches(0.2), ry + Inches(0.22), half, Inches(0.3), target_market, size=12.5, bold=True)
        ry += Inches(0.62)

    ry = _icp_block(slide, rx, ry, rw, "Primary buyers", (overview.get("primary_buyers") or [])[:6])
    ry = _icp_block(slide, rx, ry, rw, "Daily users", (overview.get("daily_users") or [])[:6])
    ry = _icp_block(slide, rx, ry, rw, "Beneficiaries", (overview.get("beneficiaries") or [])[:6])

    reg = overview.get("registration_info")
    contact = overview.get("contact")
    footer_bits = [b for b in [reg, contact] if b]
    if footer_bits:
        _textbox(slide, Inches(0.6), top + height + Inches(0.12), Inches(12.1), Inches(0.3), "  |  ".join(footer_bits), size=10.5, color=TEXT_MUTED)
    return slide


def add_solutions_products_slide(prs: Presentation, overview: dict):
    """Renders Solutions / Products-by-category / Industries — matches the
    agency sample deck's 'Solutions, Products & Industries' slide."""
    solutions = overview.get("solutions") or []
    products_by_category = overview.get("products_by_category") or {}
    industries = overview.get("industries") or []
    products_flat = overview.get("products") or []
    if not products_by_category and products_flat:
        products_by_category = {"Products & Services": products_flat}
    if not solutions and not products_by_category and not industries:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Products & Services" if not solutions and not industries else "Solutions, Products & Industries")
    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.9))
    y = Inches(1.35)

    if solutions:
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.35), "Solutions", size=15, bold=True, color=_accent())
        y += Inches(0.4)
        for s in solutions[:8]:
            if y > Inches(6.6):
                break
            text = f"•  {s}"
            lines = _wrap_lines(text, Inches(11.1), size_pt=12)
            line_h = Inches(12 * 0.02)
            box = slide.shapes.add_textbox(Inches(1.1), y, Inches(11.1), line_h * lines)
            tf = box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = text
            run.font.size = Pt(12)
            run.font.color.rgb = TEXT_DARK
            y += line_h * lines + Inches(0.06)
        y += Inches(0.15)

    if products_by_category:
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.35), "Products (by category)", size=15, bold=True, color=_accent())
        y += Inches(0.4)
        for category, items in list(products_by_category.items())[:6]:
            if y > Inches(6.6):
                break
            _textbox(slide, Inches(0.9), y, Inches(11.1), Inches(0.3), category, size=13, bold=True)
            y += Inches(0.32)
            joined = ", ".join(items)
            lines = _wrap_lines(joined, Inches(11.3), size_pt=11)
            line_h = Inches(11 * 0.02)
            box = slide.shapes.add_textbox(Inches(1.0), y, Inches(11.3), line_h * lines)
            tf = box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = joined
            run.font.size = Pt(11)
            run.font.color.rgb = TEXT_MUTED
            y += line_h * lines + Inches(0.2)

    return slide


def add_tech_stack_slide(prs: Presentation, tech_stack: dict):
    """Detected CMS/framework/hosting/CDN/analytics — from response headers,
    DNS/PTR, and HTML markers. No credentials involved."""
    slide = _blank_slide(prs)
    _content_header(slide, "Tech Stack & Hosting")
    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))
    y = Inches(1.35)

    detected = tech_stack.get("detected") or []
    # CMS pulled out of the grouped "Detected technologies" list and shown
    # as its own top-line fact — same tier as Hostname/IP/HTTPS — since
    # "what CMS is this on" is the single most-asked question about a
    # site's tech stack and was previously easy to miss buried alphabetically
    # among framework/analytics/hosting categories below. Explicit "Not
    # detected" fallback (not silently absent) when no cms-category marker
    # matched, e.g. a custom-built site with none of tech_stack_service's
    # known CMS signatures — so the slide always answers the question
    # instead of just omitting the row.
    cms_names = [item["name"] for item in detected if item.get("category") == "cms"]
    cms_value = ", ".join(dict.fromkeys(cms_names)) if cms_names else "Not detected — likely a custom-built site"
    remaining_detected = [item for item in detected if item.get("category") != "cms"]

    facts = [
        ("CMS", cms_value),
        ("Hostname", tech_stack.get("hostname")),
        ("IP address", tech_stack.get("ip")),
        ("Reverse DNS", tech_stack.get("reverse_dns")),
        ("HTTPS", "Yes" if tech_stack.get("https") else "No"),
    ]
    for label, value in facts:
        if not value:
            continue
        _textbox(slide, Inches(0.9), y, Inches(2.2), Inches(0.3), label, size=11, color=TEXT_MUTED)
        _textbox(slide, Inches(3.2), y, Inches(9.0), Inches(0.3), str(value), size=12, bold=True)
        y += Inches(0.34)

    y += Inches(0.2)
    if remaining_detected:
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.35), "Detected technologies", size=14, bold=True, color=_accent())
        y += Inches(0.42)
        by_category: dict[str, list[str]] = {}
        for item in remaining_detected:
            by_category.setdefault(item["category"], []).append(item["name"])
        for category, names in by_category.items():
            if y > Inches(6.5):
                break
            _textbox(slide, Inches(0.9), y, Inches(2.4), Inches(0.3), category.upper() if len(category) <= 4 else category.title(), size=12, bold=True)
            _textbox(slide, Inches(3.2), y, Inches(9.0), Inches(0.3), ", ".join(names), size=12, color=TEXT_DARK)
            y += Inches(0.36)
    return slide


def add_seo_issues_slide(
    prs: Presentation,
    audit: dict,
    page_audit: dict | None,
    site_audit_issues: list[dict] | None = None,
    site_audit_pages_rows: list[dict] | None = None,
):
    slide = _blank_slide(prs)
    _content_header(slide, "SEO Issues")

    # Consolidated unique-URL count — replaces the manual "check the issues
    # tab and count the unique URLs... because this is the confusing one"
    # step: one row per URL in the Crawled Pages export already carries its
    # own issue count, so a real dedup'd affected-URL total is a straight
    # read, not a per-issue-type sum (which double-counts a page hit by
    # more than one issue type).
    page_totals = _canonical_page_totals(site_audit_pages_rows, None)
    if page_totals and page_totals["with_issues"] is not None:
        _textbox(
            slide, Inches(8.0), Inches(0.3), Inches(4.7), Inches(0.4),
            f"{page_totals['with_issues']:,} of {page_totals['total']:,} crawled pages have at least one issue",
            size=11, color=TEXT_MUTED, align=PP_ALIGN.RIGHT,
        )

    if site_audit_issues:
        # Semrush Site Audit's own issue-type rollup — a real full-site crawl
        # result (hundreds of pages, ~95 issue categories), strictly richer
        # than our own homepage + 20-page checks below. Prefer it when
        # uploaded. Rows with 0 failed checks are noise (the issue TYPE was
        # checked for but never triggered) — drop them so real problems
        # aren't crowded out by "X (0 pages)" lines.
        nonzero = [r for r in site_audit_issues if (r.get("failed_checks") or 0) > 0]
        ranked = sorted(nonzero, key=lambda r: r.get("failed_checks") or 0, reverse=True)
        errors, warnings = [], []
        for row in ranked:
            label = f"{row.get('issue', 'Issue')} ({row.get('failed_checks', 0)} pages)"
            # Semrush's own taxonomy is 3-way (Error/Warning/Notice), not
            # binary — this used to bucket anything non-"ERROR" (including
            # Notices) into Warnings, silently inflating the Warnings count
            # past what Semrush itself reports. Notices are dropped from
            # this slide entirely per user request (2026-09-10) rather than
            # merged in under either column.
            issue_type = str(row.get("issue_type", "")).strip().upper()
            if issue_type == "ERROR":
                errors.append(label)
            elif issue_type == "WARNING":
                warnings.append(label)
    else:
        issues = list(audit.get("issues", []))
        if page_audit:
            for page in page_audit.get("pages", []):
                for issue in page.get("issues", []):
                    issues.append(f"{issue} — {page['url']}")
        errors = [i for i in issues if any(k in i.lower() for k in ["not reachable", "https", "robots", "sitemap"])]
        warnings = [i for i in issues if i not in errors]

    if not errors and not warnings:
        card = _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))
        _textbox(slide, Inches(0.9), Inches(1.3), Inches(10), Inches(0.4), "No issues found on the checked pages.", size=14, color=GOOD)
        return slide

    # Errors and Warnings each get their own full-height container, side by
    # side, rather than stacking in one shared card — stacked lists with 10
    # items per group could run past the bottom of the slide.
    col_top, col_height = Inches(1.1), Inches(5.6)
    col_width = Inches(5.85)
    columns = [("Errors", errors, BAD, Inches(0.6)), ("Warnings", warnings, WARN, Inches(6.85))]
    row_h = Inches(0.32)
    max_rows = 10

    for group_label, group_issues, color, col_left in columns:
        if not group_issues:
            continue
        shown = group_issues[:max_rows]
        _card(slide, col_left, col_top, col_width, col_height)
        y = col_top + Inches(0.2)
        _textbox(slide, col_left + Inches(0.3), y, col_width - Inches(0.6), Inches(0.35), f"{group_label} ({len(shown)})", size=14, bold=True, color=color)
        rule = slide.shapes.add_shape(1, col_left + Inches(0.3), y + Inches(0.36), col_width - Inches(0.6), Pt(1.5))
        _fill(rule, color)
        rule.shadow.inherit = False
        y += Inches(0.55)
        for issue in shown:
            _issue_row(slide, col_left + Inches(0.3), y, col_width - Inches(0.6), issue, severity=("error" if color == BAD else "warn"))
            y += row_h
    return slide


def _traffic_by_path(analytics: dict | None) -> tuple[dict[str, int], dict[str, int]]:
    """One GA4-pageviews + GSC-clicks-by-path join, shared by every slide
    that scores a page by real traffic impact (Priority Issues, Tech
    Fixes) — was independently rebuilt in each before this (Gaps.pdf gap
    4: "the flow doc itself admits this pipeline is being built manually,
    four separate times"). Path is the join key since GSC's pagePath
    dimension carries no host component."""
    analytics = analytics or {}
    pageviews_by_path = {
        (p.get("path") or "").rstrip("/"): int(float(p.get("page_views", 0) or 0))
        for p in (analytics.get("top_pages") or {}).get("rows", [])
    }
    clicks_by_path = {
        urlparse(r.get("page") or "").path.rstrip("/"): int(r.get("clicks", 0) or 0)
        for r in (analytics.get("page_clicks") or {}).get("rows", [])
    }
    return pageviews_by_path, clicks_by_path


def _page_value_score(pageviews: int, clicks: int) -> int:
    """Shared traffic-impact formula (Issue x URL x GA4 traffic x GSC
    clicks -> one page-value number): a lost search click is lost demand,
    weighted heavier than a pageview that could arrive via another
    channel regardless of search ranking. Same weighting now used by both
    Priority Issues and Tech Fixes, which previously only weighed GA4
    pageviews and silently ignored GSC clicks entirely."""
    return pageviews + clicks * 3


# Semrush Site Audit's per-page Structured Data export has ~29 schema.org
# rich-result-type columns — this is the curated, business-relevant subset
# actually surfaced on the slide (nothing is lost on import, see
# STRUCTURED_DATA_COLUMN_ALIASES in semrush_parser.py; this is a display
# choice, not a parsing limit). Matches the style of real manual-report
# findings, e.g. "No Product schema (JSON-LD) on the 5 product pages"
# (confirmed from an EJTOY audit).
_STRUCTURED_DATA_TYPES = [
    ("Article", "article_items", "improves how blog/news content can appear in search"),
    ("FAQ", "faq_items", "adds AI Overview / LLM-citation value"),
    ("Product", "product_items", "enables price/availability rich results on product pages"),
    ("Review", "review_items", "enables star-rating rich results"),
    ("Local Business", "local_business_items", "enables map/business-info rich results"),
    ("How-to", "howto_items", "enables step-by-step rich results"),
    ("Breadcrumb", "breadcrumb_items", "shows the page's site hierarchy in search results"),
    ("Job Posting", "job_posting_items", "enables Google's dedicated job-search rich results"),
    ("Event", "event_items", "enables event date/venue rich results"),
]

# Google Search Central rich-result eligibility, checked manually against
# Google's current documentation — refresh this whenever Google adds or
# retires a result type, don't trust it to stay accurate indefinitely. FAQ
# is marked ineligible because Google fully retired the FAQ rich-result SERP
# dropdown in May 2026; the manual audit process still had "suggest FAQ
# schema" hardcoded with no check against Google's current capabilities, so
# the report kept promising a SERP visual Google no longer grants (Gaps.pdf,
# "Structured Data" gap #3). Types not listed default to eligible.
_SCHEMA_RICH_RESULT_RETIRED_NOTE = {
    "faq_items": "Google retired the classic FAQ rich-result SERP dropdown in May 2026 — this is now content/AI-citation value only, not a SERP visual.",
}


# Every schema.org item-type field Semrush's Structured Data export tracks
# (STRUCTURED_DATA_COLUMN_ALIASES in semrush_parser.py) — a superset of the
# 9 curated rich-result types in _STRUCTURED_DATA_TYPES. Checked so a type
# no one asked to curate (Logo, Organization sitelinks, etc.) still shows up
# on the slide if it actually has real coverage, instead of silently having
# no row at all.
_ALL_SCHEMA_ITEM_FIELDS = [
    ("Article", "article_items"), ("Book", "book_items"), ("Breadcrumb", "breadcrumb_items"),
    ("Carousel", "carousel_items"), ("Course", "course_items"), ("Dataset", "dataset_items"),
    ("Employer Rating", "employer_rating_items"), ("Estimated Salary", "estimated_salary_items"),
    ("Event", "event_items"), ("Fact Check", "fact_check_items"), ("FAQ", "faq_items"),
    ("Guided Recipe", "guided_recipe_items"), ("How-to", "howto_items"), ("Job Posting", "job_posting_items"),
    ("Local Business", "local_business_items"), ("Logo", "logo_items"), ("Merchant Listing", "merchant_listing_items"),
    ("Movie", "movie_items"), ("Product", "product_items"), ("Product Group", "product_group_items"),
    ("Q&A", "qa_items"), ("Recipe", "recipe_items"), ("Review", "review_items"),
    ("Sitelinks Search Box", "sitelinks_searchbox_items"), ("Site Names", "site_names_items"),
    ("Software App", "software_app_items"), ("Vehicle Listing", "vehicle_listing_items"), ("Video", "video_items"),
]

# Contextual schema relevance: a page has to actually BE the shape a schema
# type is for before its absence counts as a gap — a SaaS site with zero
# careers pages doesn't need Job Posting schema, and flagging it as missing
# "on all 1,336 pages" is noise, not a finding. Product/blog/jobs/local/
# event are detected by URL pattern (cheap, no extra crawl); FAQ/how-to
# genuinely can't be — a FAQ section can live on any URL, including a
# homepage or product page — so those fall back to whatever text is
# available (URL slug + page title, when Site Audit Pages data is joined
# in) as a best-effort proxy. True content-scanning (question-shaped
# headings, numbered steps) would need our own crawler to fetch full page
# HTML — that infra existed once (see crawl_extras_2026-09-01) and was
# reverted for being too slow; this stays URL/title-only until that's
# safely rebuilt.
_PRODUCT_SHAPE_RE = re.compile(r"/(?:products?|shop|store|items?)/", re.IGNORECASE)
_BLOG_SHAPE_RE = re.compile(r"/(?:blog|news|articles?|posts?)/", re.IGNORECASE)
_JOBS_SHAPE_RE = re.compile(r"/(?:careers?|jobs?)/", re.IGNORECASE)
_LOCAL_SHAPE_RE = re.compile(r"/(?:locations?|store-locator|near-me|branch(?:es)?)/", re.IGNORECASE)
_EVENT_SHAPE_RE = re.compile(r"/events?/", re.IGNORECASE)
_FAQ_SHAPE_RE = re.compile(r"faq|frequently[\s-]asked[\s-]questions", re.IGNORECASE)
_HOWTO_SHAPE_RE = re.compile(r"how[\s-]to|step[\s-]by[\s-]step|\btutorial\b|\bguide\b", re.IGNORECASE)


def _classify_page_shapes(page_url: str, page_title: str = "") -> set[str]:
    path = urlparse(page_url or "").path
    shapes = set()
    if _PRODUCT_SHAPE_RE.search(path):
        shapes.add("product")
    if _BLOG_SHAPE_RE.search(path):
        shapes.add("blog")
    if _JOBS_SHAPE_RE.search(path):
        shapes.add("jobs")
    if _LOCAL_SHAPE_RE.search(path):
        shapes.add("local")
    if _EVENT_SHAPE_RE.search(path):
        shapes.add("event")
    text = f"{path} {page_title or ''}"
    if _FAQ_SHAPE_RE.search(text):
        shapes.add("faq")
    if _HOWTO_SHAPE_RE.search(text):
        shapes.add("howto")
    return shapes


# Which page shape(s) make a curated schema type's absence a real finding.
# A type with no entry here (Breadcrumb) applies site-wide — every page can
# carry one, so its denominator stays the full crawl, same as before.
_SCHEMA_TYPE_REQUIRED_SHAPES = {
    "article_items": {"blog"},
    "faq_items": {"faq"},
    "product_items": {"product"},
    "review_items": {"product"},  # reviews live on/near the product they review
    "local_business_items": {"local"},
    "howto_items": {"howto"},
    "job_posting_items": {"jobs"},
    "event_items": {"event"},
}
_SCHEMA_TYPE_SHAPE_NOUN = {
    "article_items": "blog/news",
    "faq_items": "FAQ-worthy",
    "product_items": "product",
    "review_items": "product",
    "local_business_items": "location",
    "howto_items": "how-to/guide",
    "job_posting_items": "job listing",
    "event_items": "event",
}


def add_structured_data_slide(
    prs: Presentation, structured_data_rows: list[dict], site_audit_pages_rows: list[dict] | None = None
):
    """Total URLs crawled vs. how many have any schema markup implemented,
    plus a per-type breakdown, worst-coverage first. Shows the 9 curated
    rich-result types PLUS any other tracked type (of all ~27 Semrush
    exports) that actually has real coverage — so if the pages missing a
    curated type (e.g. Product) carry some other schema instead, that shows
    up as its own row here rather than needing a separate slide to explain
    where the remaining % went. total_pages is the real crawl total
    (semrush_parser.py no longer caps structured_data rows at 500 — a real
    client site can have 1200+ crawled pages, and capping silently
    understated both this total and every coverage %).

    The coverage TABLE stays site-wide (X of ALL crawled pages) deliberately
    — narrowing its denominator to "relevant" pages would also silently
    exclude a genuinely valuable finding like a blog post carrying Product
    schema instead of Article (confirmed on a real Lumber crawl). Only the
    "No X schema found" INSIGHT bullets get contextual: a type mapped to a
    page shape (see _SCHEMA_TYPE_REQUIRED_SHAPES) is only flagged as missing
    when at least one page of that shape actually exists on the site — a
    SaaS site with zero careers pages doesn't need a "no Job Posting schema"
    bullet. site_audit_pages_rows (optional) supplies page_title for the
    FAQ/how-to shape check, which can't rely on URL alone."""
    total_pages = len(structured_data_rows)
    if not total_pages:
        return None

    title_by_url: dict[str, str] = {}
    if site_audit_pages_rows:
        for r in site_audit_pages_rows:
            url = r.get("page_url")
            if url:
                title_by_url[url] = r.get("page_title") or ""
    row_shapes = [
        _classify_page_shapes(r.get("page_url") or "", title_by_url.get(r.get("page_url") or "", ""))
        for r in structured_data_rows
    ]
    relevant_counts = {
        field: sum(1 for shapes in row_shapes if shapes & required)
        for field, required in _SCHEMA_TYPE_REQUIRED_SHAPES.items()
    }

    pages_with_any_schema = sum(
        1 for r in structured_data_rows
        if _num(r.get("schema_jsonld")) > 0 or _num(r.get("schema_microdata")) > 0
    )
    any_schema_pct = 100 * pages_with_any_schema / total_pages

    curated_fields = {field for _label, field, _benefit in _STRUCTURED_DATA_TYPES}
    coverage = [
        (label, field, benefit, sum(1 for r in structured_data_rows if _num(r.get(field)) > 0))
        for label, field, benefit in _STRUCTURED_DATA_TYPES
    ]
    # Any non-curated type that actually has coverage earns its own row too
    # — otherwise a client whose gap pages use e.g. Logo schema instead of
    # Product would show 85% Product and nothing explaining the other 15%.
    extra_coverage = [
        (label, field, None, sum(1 for r in structured_data_rows if _num(r.get(field)) > 0))
        for label, field in _ALL_SCHEMA_ITEM_FIELDS
        if field not in curated_fields
    ]
    coverage += [c for c in extra_coverage if c[3] > 0]
    # Highest-coverage first — with an extra non-curated row possibly added
    # above, the table's row cap (9 when insights are shown) must never
    # silently truncate the row(s) that actually have real coverage in
    # favor of interchangeable 0% rows; the "missing" story is already
    # covered by the insight bullets below regardless of which 0% rows
    # make the cut.
    coverage.sort(key=lambda c: c[3], reverse=True)

    headers = ["Schema Type", "Pages With It", "Coverage"]
    col_widths = [4.0, 2.5, 2.5]
    rows = [
        (label, f"{pages_with:,} / {total_pages:,}", f"{100 * pages_with / total_pages:.0f}%")
        for label, _field, _benefit, pages_with in coverage
    ]

    # The per-type coverage rows above are NOT mutually exclusive (a page can
    # carry Product AND Review AND nothing-else-tracked at once), so they
    # never sum to 100% and were never meant to — confirmed this reads as
    # "where's the rest of the percentage?" to a non-technical reviewer.
    # The fix: put the complementary "and here's the rest" stat (pages with
    # NO tracked schema at all) as the SECOND insight, right after the
    # headline stat — guaranteed to render even when the per-type "missing
    # X" bullets after it get cut for space — so the two numbers that
    # actually do sum to 100% of crawled pages are always shown together.
    insights = [
        f"{pages_with_any_schema:,} of {total_pages:,} crawled URLs ({any_schema_pct:.0f}%) have structured data "
        "(schema.org JSON-LD or Microdata) implemented.",
    ]
    best = max(coverage, key=lambda c: c[3])
    gap = total_pages - pages_with_any_schema
    if best[3] > 0 and gap > 0:
        insights.append(
            f"The remaining {gap:,} of {total_pages:,} pages ({100 * gap / total_pages:.0f}%) have NO structured "
            f"data of any tracked type at all — not even {best[0]}, the site's best-covered type. Together with the "
            f"{any_schema_pct:.0f}% above, that accounts for all {total_pages:,} crawled pages."
        )
    elif best[3] > 0 and len(insights) == 1:
        insights.append(f"{best[0]} schema is your best-covered type — present on {best[3]:,} of {total_pages:,} pages.")

    missing = [c for c in coverage if c[3] == 0 and c[2] is not None]  # curated-only, has benefit text
    missing_insights = []
    for label, field, benefit, _pages_with in missing:
        # A retired-rich-result type still gets its own note instead of the
        # "adding it {benefit}" phrasing — see _SCHEMA_RICH_RESULT_RETIRED_NOTE.
        retired_note = _SCHEMA_RICH_RESULT_RETIRED_NOTE.get(field)
        required_shapes = _SCHEMA_TYPE_REQUIRED_SHAPES.get(field)
        if required_shapes is not None:
            relevant = relevant_counts.get(field, 0)
            if relevant == 0:
                # No page on the site is even shaped for this type (e.g. no
                # careers pages at all) — not a gap, just not applicable.
                continue
            noun = _SCHEMA_TYPE_SHAPE_NOUN.get(field, label.lower())
            if retired_note:
                missing_insights.append(
                    f"No {label} schema found on any of the {relevant} {noun} page(s) identified — worth adding for "
                    f"{benefit}, but not a SERP-visual win: {retired_note}"
                )
            else:
                missing_insights.append(
                    f"No {label} schema found on any of the {relevant} {noun} page(s) identified — adding it {benefit}."
                )
        else:
            # Site-wide type (Breadcrumb) — no shape to narrow to, every page qualifies.
            missing_insights.append(f"No {label} schema found on any of your {total_pages} pages — adding it {benefit}.")
        if len(missing_insights) == 2:
            break
    insights += missing_insights

    # The generic _table_slide row_cap (9, once insights are shown) silently
    # dropped whichever curated type sorted last whenever a non-curated
    # extra (e.g. Fact Check) also had real coverage, pushing the curated
    # count over 9 — confirmed live: a real Lumber report showed 9 rows
    # with "Event" (0% coverage) missing entirely, nothing on the slide
    # explaining where the rest of the picture went. The 9 curated types
    # are fixed; a couple of extra real-coverage rows is the realistic
    # case, so this leaves headroom for both while still capping well
    # short of the table pushing insights off the slide.
    return _table_slide(
        prs, "Structured Data", headers, rows, col_widths=col_widths, source="Semrush Site Audit", insights=insights,
        row_cap=min(len(coverage), 11),
    )


# Google Search Central rich-result eligibility for the crawl-detected
# @type names aggregate_schema_validation reports — a broader vocabulary
# than _STRUCTURED_DATA_TYPES' Semrush field names above, but the same
# "refresh when Google changes this" caveat as _SCHEMA_RICH_RESULT_RETIRED_
# NOTE applies. A type with no entry here is unverified, not assumed
# eligible (2026-09-10 spec, Step 4): never claim a rich result, snippet,
# or answer-box outcome for a schema type without checking this first.
_SCHEMA_GOOGLE_ELIGIBILITY: dict[str, str | None] = {
    "Article": None, "BlogPosting": None, "NewsArticle": None, "Product": None,
    "Review": None, "LocalBusiness": None, "Event": None, "BreadcrumbList": None,
    "JobPosting": None, "Recipe": None, "VideoObject": None,
    "Organization": None, "WebSite": None,
    "HowTo": "Google restricted HowTo rich results to a small set of approved sites in 2023 — no longer generally available.",
    "FAQPage": "Google retired the classic FAQ rich-result SERP dropdown in May 2026 — this is content/AI-citation value only, not a SERP visual.",
}


def _schema_eligibility_flag(schema_type: str) -> str | None:
    """None = eligible, rich-result framing permitted. A string = the type
    is ineligible (state plainly, cite it) or unverified (ELIGIBILITY_CHECK_
    STALE — don't claim a rich-result outcome either way)."""
    if schema_type not in _SCHEMA_GOOGLE_ELIGIBILITY:
        return f"ELIGIBILITY_CHECK_STALE — {schema_type} rich-result eligibility hasn't been verified against Google's current docs; treat any SERP-feature claim for it as unconfirmed."
    return _SCHEMA_GOOGLE_ELIGIBILITY[schema_type]


def _schema_validator_insights(schema_validation: dict) -> list[str]:
    """KEY INSIGHTS for Structured Data & Schema Validator (2026-09-10
    spec): a traffic-weighted overall coverage headline instead of a flat
    page-count %, the single highest-pageviews-at-risk gap named
    specifically, any type where presence and required-field validity
    diverge, and a plain eligibility note for any type Google no longer
    grants (or has never had verified) a rich result for. Built entirely
    from by_page_type (already segmented by real page type and joined to
    real GA4 pageviews by aggregate_schema_validation) — never a schema
    type or number not literally present in that data.

    "Other Pages" is excluded from the weighted headline: that bucket's
    schema is whatever baseline/universal markup (Organization, WebSite)
    happens to be sitewide, and Step 2 of the spec requires baseline
    coverage to be reported separately from content-specific schema
    coverage rather than blended into one number that could read as
    "100% have structured data" when only the universal types are
    present."""
    by_page_type = schema_validation.get("by_page_type") or []
    content_rows = [r for r in by_page_type if r["page_type"] != "Other Pages" and r["pages"]]
    if not content_rows:
        return []

    has_traffic = any(r["pageviews"] for r in content_rows)
    if has_traffic:
        weighted_num = sum(r["coverage_pct"] / 100 * r["pages"] * r["pageviews"] for r in content_rows)
        weighted_den = sum(r["pages"] * r["pageviews"] for r in content_rows)
    else:
        weighted_num = sum(r["coverage_pct"] / 100 * r["pages"] for r in content_rows)
        weighted_den = sum(r["pages"] for r in content_rows)
    overall = 100 * weighted_num / weighted_den if weighted_den else 0
    total_content_pages = sum(r["pages"] for r in content_rows)

    insights = [
        f"{'Traffic-weighted' if has_traffic else 'Page-weighted'} content-specific schema coverage across "
        f"{total_content_pages:,} pages (Article/Product/etc.): {overall:.0f}% — Organization/WebSite baseline "
        f"schema is tracked separately below, not blended into this number."
    ]

    # content_rows is already sorted by -pageviews (aggregate_schema_
    # validation's own SEO-priority order), so the first row short of 100%
    # coverage is the single biggest pageviews-at-risk gap.
    top_gap = next((r for r in content_rows if r["coverage_pct"] < 100), None)
    if top_gap:
        at_risk = round(top_gap["pageviews"] * (1 - top_gap["coverage_pct"] / 100))
        insights.append(
            f"Biggest gap: {top_gap['page_type']} ({top_gap['applicable_schema']}) is only {top_gap['coverage_pct']}% "
            f"covered across {top_gap['pages']:,} pages"
            + (f" — {at_risk:,} pageviews behind schema-less pages." if at_risk else ".")
        )

    # Presence vs. validity divergence (Step 3: never call presence alone
    # "valid") — worst gap between coverage_pct and valid_pct.
    diverging = [r for r in content_rows if r["coverage_pct"] and r["coverage_pct"] - r["valid_pct"] >= 20]
    if diverging:
        d = max(diverging, key=lambda r: r["coverage_pct"] - r["valid_pct"])
        insights.append(
            f"{d['page_type']} schema is present on {d['coverage_pct']}% of its pages but only {d['valid_pct']}% "
            f"pass required-field validation — presence isn't validity here."
        )

    # Eligibility framing (Step 4) for every type actually referenced above.
    seen_types = sorted({t for r in content_rows for t in r["applicable_schema"].split("/") if t and t != "—"})
    for t in seen_types:
        flag = _schema_eligibility_flag(t)
        if flag:
            insights.append(flag if flag.startswith("ELIGIBILITY_CHECK_STALE") else f"{t} schema: {flag}")

    return insights[:6]


def add_schema_combined_slide(prs: Presentation, schema_validation: dict):
    """Structured Data coverage (type presence %, ground truth from this
    tool's own crawl — see the prior standalone version's now-removed
    docstring for why crawl beats Semrush's export here) and Schema
    Validator (missing REQUIRED properties + Search Console's real
    rich-result verdicts) on ONE slide, stacked full-width (Structured
    Data on top, Schema Validator below) rather than as two separate
    slides — per client request. Explicitly NOT two half-width side-by-
    side panels: that version cut both tables' row_cap and column widths
    to fit side by side, which lost real rows/truncated real column
    content compared to the original separate full-width slides — full
    width top-to-bottom keeps every column at its original width. Both
    tables are sourced from the same schema_validation dict
    (aggregate_schema_validation), so whenever one would have content the
    other's data is already available too; the Semrush-export-only
    fallback (add_structured_data_slide) stays a separate full-width
    slide for the rare case no crawl-based schema data exists at all."""
    total_pages = schema_validation.get("total_pages") or 0
    if not total_pages:
        return None

    type_coverage = schema_validation.get("type_coverage") or []
    pages_with_schema = schema_validation.get("pages_with_schema") or 0
    missing_properties = schema_validation.get("missing_properties") or []
    gsc_rich_results = schema_validation.get("gsc_rich_results") or []
    missing_types = schema_validation.get("missing_types") or []
    by_page_type = schema_validation.get("by_page_type") or []
    if not type_coverage and not missing_properties and not gsc_rich_results and not missing_types:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Structured Data & Schema Validator")
    source = "Site Audit crawl + Search Console URL Inspection" if gsc_rich_results else "Site Audit crawl (JSON-LD)"
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)

    left, width = Inches(0.6), Inches(12.1)
    y = Inches(1.05)
    insights = []
    ROW_CAP, ROW_H = 6, 0.3

    if by_page_type:
        # Presence, validity, and business value split by PAGE TYPE (not
        # one blended site-wide number) — teammate QA on the last report
        # asked for exactly this: Page Type -> Applicable Schema -> Schema
        # Count -> Validation -> SEO Priority. Rows are already sorted by
        # real GA4 pageviews (the traffic a gap here would actually cost),
        # page count as tiebreaker — the highest-priority gap is always
        # row one, not something the reader has to work out themselves.
        _textbox(slide, left, y, width, Inches(0.24), "Structured Data Coverage by Page Type", size=12.5, bold=True, color=_accent())
        y = y + Inches(0.28)
        coverage_rows = [
            (
                r["page_type"], r["applicable_schema"], f"{r['pages']:,}",
                f"{r['coverage_pct']}%", f"{r['valid_pct']}%",
                f"{r['pageviews']:,}" if r["pageviews"] else "—",
            )
            for r in by_page_type
        ]
        y = _draw_table(
            slide, ["Page Type", "Applicable Schema", "Pages", "Coverage", "Valid", "Pageviews"], coverage_rows, y,
            col_widths=[2.2, 2.6, 1.2, 1.7, 1.6, 2.8], left=left, width=width, row_cap=ROW_CAP, row_height=ROW_H,
        ) + Inches(0.2)
        # 2026-09-10 spec: traffic-weighted coverage headline + the single
        # highest-pageviews-at-risk gap + presence/validity divergence +
        # eligibility framing, all derived from this same table — replaces
        # the flat "X% have structured data" + a second "Highest-priority
        # gap" bullet that used to restate a row from the Findings table
        # below verbatim (the exact duplicate this slide's SNIP reference
        # showed: a page-type's 0%-coverage row appearing a second time as
        # its own "finding" card).
        insights.extend(_schema_validator_insights(schema_validation))
    if type_coverage:
        # "Schema Count" — the flow's own step name for this table — used to
        # be dropped whenever by_page_type also had data (elif, mutually
        # exclusive), even though it's a distinct step from Page Type/
        # Coverage above. Renders in both cases now; tighter row cap only
        # when stacked below the by_page_type table so the 3-section slide
        # (Page Type, Schema Count, Findings) still fits.
        stacked = bool(by_page_type)
        row_cap = 4 if stacked else ROW_CAP
        row_h = 0.26 if stacked else ROW_H
        _textbox(slide, left, y, width, Inches(0.24), "Schema Count by Type", size=12.5, bold=True, color=_accent())
        y = y + Inches(0.28)
        coverage_rows = [(c["type"], f"{c['pages_with_it']:,} / {total_pages:,}", f"{c['coverage_pct']}%") for c in type_coverage]
        y = _draw_table(
            slide, ["Schema Type", "Pages With It", "Coverage"], coverage_rows, y,
            col_widths=[4.0, 4.05, 4.05], left=left, width=width, row_cap=row_cap, row_height=row_h,
        ) + Inches(0.2)
        if not stacked:
            any_schema_pct = 100 * pages_with_schema / total_pages
            insights.append(f"{pages_with_schema:,} of {total_pages:,} pages ({any_schema_pct:.0f}%) have structured data implemented.")

    gsc_rows = []
    gsc_pass_count = gsc_fail_count = 0
    for r in gsc_rich_results:
        verdict = r.get("verdict")
        if verdict == "PASS":
            gsc_pass_count += 1
        elif verdict == "FAIL":
            gsc_fail_count += 1
        for item in r.get("detected_items") or []:
            for sub in item.get("items") or []:
                for issue in sub.get("issues") or []:
                    gsc_rows.append((
                        f"{item.get('type')} (Google-verified)",
                        issue.get("message") or "Flagged by Google's Rich Results check",
                        _truncate_cell(r.get("url") or "", 4.0),
                    ))
    type_rows = [(m["type"], "(entire type missing)", m["reason"]) for m in missing_types]
    rule_rows = [
        (
            m["type"] if m["severity"] == "required" else f"{m['type']} (recommended)",
            f"Missing {m['field']}",
            f"{m['pages_missing']:,} of {total_pages:,}",
        )
        for m in missing_properties
    ]
    finding_rows = gsc_rows + type_rows + rule_rows
    if finding_rows:
        # All 3 sections stacking (Page Type + Schema Count + Findings)
        # needs a tighter cap here too, same reasoning as Schema Count above
        # — otherwise the combined height runs past the slide and crowds
        # out the insights strip below.
        findings_stacked = bool(by_page_type) and bool(type_coverage)
        findings_row_cap = 4 if findings_stacked else ROW_CAP
        _textbox(slide, left, y, width, Inches(0.24), "Schema Validator Findings", size=12.5, bold=True, color=_accent())
        y = y + Inches(0.28)
        y = _draw_table(
            slide, ["Schema Type", "Finding", "Pages Affected"], finding_rows, y,
            col_widths=[4.0, 4.05, 4.05], left=left, width=width, row_cap=findings_row_cap, row_height=ROW_H,
        ) + Inches(0.2)
        if gsc_rich_results:
            insights.append(f"Search Console's own rich-result check: {gsc_pass_count:,} pass, {gsc_fail_count:,} fail.")
        if missing_types:
            insights.append(f"{missing_types[0]['type']} schema entirely missing — {missing_types[0]['reason']}.")
        elif missing_properties:
            worst = missing_properties[0]
            worst_label = "missing (required)" if worst["severity"] == "required" else "missing (recommended)"
            insights.append(f"{worst['type']} schema {worst_label} '{worst['field']}' on {worst['pages_missing']:,} page(s).")

    if insights:
        _insights_strip(slide, left, y, width, insights)
    return slide


# Canned fix per page-level issue string from technical_seo_service.py's
# crawler (see _meta_issues() and run_multi_page_audit) — (fix text,
# severity). Matches the real manual-report "Tech Fixes" slide format
# (ISSUE | WHERE | FIX), confirmed from an EJTOY audit — more actionable
# than the SEO Issues slide's issue-type rollup, which says how many pages
# but not which ones or what to do about it.
# category: "technical" = crawlability/infrastructure (page reachability,
# mobile rendering, duplicate-content prevention) vs "seo" = on-page
# content/metadata (title, description, headings) — client asked for Tech
# Fixes split into these two groups instead of one mixed list.
_PAGE_ISSUE_FIXES = {
    "Page not reachable": ("Fix the broken link/redirect, or add a 301 redirect to a working page.", "error", "technical"),
    "Missing <title> tag": ("Add a unique, keyword-relevant <title> tag (50-60 characters).", "error", "seo"),
    "Missing meta description": ("Write a unique meta description (150-160 characters) summarizing the page.", "warn", "seo"),
    "No <h1> tag found": ("Add a single <h1> heading stating the page's main topic.", "warn", "seo"),
    "Multiple <h1> tags found": ("Keep only one <h1> per page — demote extra ones to <h2>/<h3>.", "warn", "seo"),
    "Missing mobile viewport meta tag": ("Add a viewport meta tag so the page renders correctly on mobile.", "warn", "technical"),
    "Missing canonical tag": ("Add a self-referencing canonical tag to prevent duplicate-content issues.", "info", "technical"),
    "Title tag longer than 60 characters": ("Shorten the title tag so it isn't truncated in search results.", "info", "seo"),
    # The crawler (technical_seo_service.py's _meta_issues) emits this
    # 8th real issue string, but it had no entry here at all — silently
    # dropped from Tech Fixes entirely, on top of "technical" rarely
    # having any of its other 3 issue types trip on a modern site. This
    # is also the single most common real finding across most crawls,
    # so leaving it out was the main reason Technical Issues so often
    # came up empty next to a populated SEO Issues slide.
    "Missing structured data (JSON-LD)": ("Add JSON-LD structured data matching the page's content type (Article, Product, FAQ, etc).", "warn", "technical"),
}
_ISSUE_SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}


def _tech_fixes_scored_rows(
    page_audit: dict, analytics: dict | None, site_audit_pages_rows: list[dict] | None = None
) -> list[tuple]:
    # Same GA4+GSC join and page-value formula as Priority Issues (see
    # _traffic_by_path/_page_value_score) — previously this only weighed
    # GA4 pageviews and silently ignored GSC clicks, so a fix on a page
    # with real search clicks but few GA4 pageviews under-ranked here even
    # though Priority Issues would score it higher.
    pageviews_by_path, clicks_by_path = _traffic_by_path(analytics)

    scored_rows = []
    covered_paths: set[str] = set()
    for page in page_audit.get("pages", []):
        path = urlparse(page.get("url", "")).path or "/"
        covered_paths.add(path.rstrip("/") or "/")
        page_views = pageviews_by_path.get(path.rstrip("/"), 0)
        clicks = clicks_by_path.get(path.rstrip("/"), 0)
        score = _page_value_score(page_views, clicks)
        for issue in page.get("issues", []):
            fix = _PAGE_ISSUE_FIXES.get(issue)
            if not fix:
                continue
            fix_text, severity, category = fix
            scored_rows.append((_ISSUE_SEVERITY_RANK[severity], -score, issue, path, fix_text, page_views, category))

    # Semrush's Crawled Pages export (site_audit_pages_rows) covers the
    # site's real full crawl (e.g. 1,340 pages) vs. this tool's own ~20-page
    # sample above — before this, Tech Fixes only ever scored that ~20-page
    # sample, silently ignoring every other page even though Site
    # Structure/SEO Issues/Site Health all use the full export (Gaps.pdf,
    # "Understanding Current Scenario": Tech Fixes should match the same
    # crawled-pages scope). Semrush's per-page export only gives an ISSUE
    # COUNT, not issue names, so these rows can't get a real named Fix —
    # they're added honestly as "N issue(s) reported by Semrush" instead of
    # fabricating a specific fix, ranked below real named findings (info
    # severity) and bucketed into their own "other" category rather than
    # guessing technical vs. seo.
    if site_audit_pages_rows:
        domain_counts = Counter(urlparse(r.get("page_url") or "").netloc for r in site_audit_pages_rows)
        domain_counts.pop("", None)
        own_domain = domain_counts.most_common(1)[0][0] if domain_counts else None
        seen_semrush_paths: set[str] = set()
        for r in site_audit_pages_rows:
            page_url = r.get("page_url")
            issues = r.get("issues")
            if not (page_url and issues):
                continue
            if own_domain and urlparse(page_url).netloc not in ("", own_domain):
                continue
            path = urlparse(page_url).path or "/"
            key = path.rstrip("/") or "/"
            if key in covered_paths or key in seen_semrush_paths:
                continue
            try:
                issue_count = int(float(issues))
            except (TypeError, ValueError):
                continue
            if issue_count <= 0:
                continue
            seen_semrush_paths.add(key)
            page_views = pageviews_by_path.get(key, 0)
            clicks = clicks_by_path.get(key, 0)
            score = _page_value_score(page_views, clicks)
            # 2026-09-10 user request: don't repeat "Semrush's Site Audit" in
            # every row's Fix cell — the slide already names that source
            # once, in the header ("Source: Semrush Site Audit").
            fix_text = (
                "Full per-issue breakdown isn't available for this page in this export — "
                "see SEO Issues for the site-wide breakdown by type."
            )
            scored_rows.append((
                _ISSUE_SEVERITY_RANK["info"], -score, f"{issue_count} issue(s) (Semrush)", path, fix_text, page_views, "other",
            ))

    scored_rows.sort(key=lambda r: (r[0], r[1]))
    return scored_rows


def _tech_fixes_category_slide(prs: Presentation, title: str, scored_rows: list[tuple], source: str = "Site crawl"):
    if not scored_rows:
        return None
    shown = scored_rows[:9]
    col_widths = [2.7, 2.3, 7.1]
    rows = [(_truncate_cell(issue, col_widths[0]), _truncate_cell(path, col_widths[1]), fix_text) for _, _, issue, path, fix_text, _, _ in shown]
    unreachable_count = sum(1 for _, _, issue, _, _, _, _ in scored_rows if issue == "Page not reachable")
    insights = [f"{len(scored_rows)} {title.split(' — ')[-1].lower()} issue(s) found across the crawled pages" + (f", {unreachable_count} unreachable." if unreachable_count else ".")]
    if len(scored_rows) > len(shown):
        insights.append(f"Showing the {len(shown)} highest-priority — see SEO Issues for the full breakdown by type.")
    traffic_matched = [r for r in scored_rows if r[5] > 0]
    if traffic_matched:
        top_traffic = max(traffic_matched, key=lambda r: r[5])
        insights.append(
            f"\"{top_traffic[3]}\" gets real traffic ({top_traffic[5]:,} pageviews in the reporting window) "
            f"and has a \"{top_traffic[2]}\" issue — fixing this one affects real visitors, not just crawl health."
        )
    return _table_slide(
        prs, title, ["Issue", "Where", "Fix"], rows,
        col_widths=col_widths, source=source, insights=insights,
    )


def add_tech_fixes_slide(
    prs: Presentation,
    page_audit: dict | None,
    analytics: dict | None = None,
    site_audit_pages_rows: list[dict] | None = None,
) -> list:
    """Flattens page_audit's per-page issues (up to 20 crawled pages) into
    one Issue/Where/Fix row per (page, issue) pair, worst-severity first
    (severity stays the primary sort — an error is still an error regardless
    of traffic). When real GA4 pageview data is available, ties within the
    same severity are broken by traffic — a fix on a page real visitors
    hit sorts above the same-severity fix on a page nobody visits — and the
    single highest-traffic affected page gets called out as an insight.
    "Tech Fixes — SEO Issues" removed 2026-09-09 per user request —
    redundant with the main SEO Issues slide's Errors/Warnings, which
    already covers SEO-category issues site-wide. Technical Issues stays
    its own slide (see _PAGE_ISSUE_FIXES' category field). A third
    "Additional Pages" slide covers the rest of Semrush's full crawl
    (site_audit_pages_rows) beyond our own ~20-page sample — see
    _tech_fixes_scored_rows for why those rows can't get a named Fix.
    Named Issue/Fix rows still come from our own crawl — Semrush's per-page
    x per-issue-type matrix export (mega_export.csv) isn't parsed at all
    currently (parked deliberately), would give richer named findings for
    the full site later if ever built. The "Additional Pages" slide covers
    the scope gap in the meantime with an honest count-only row instead."""
    if not page_audit:
        return []

    scored_rows = _tech_fixes_scored_rows(page_audit, analytics, site_audit_pages_rows)
    if not scored_rows:
        return []

    technical_rows = [r for r in scored_rows if r[6] == "technical"]
    other_rows = [r for r in scored_rows if r[6] == "other"]
    slides = [
        _tech_fixes_category_slide(prs, "Tech Fixes — Technical Issues", technical_rows),
        _tech_fixes_category_slide(prs, "Priority Issues - Page Wise", other_rows, source="Semrush Site Audit"),
    ]
    return [s for s in slides if s]


def _truncate_cell(text: str, width_in: float, size_pt: float = 11, max_lines: int = 1) -> str:
    """Truncates a table cell's text (with an ellipsis) so it fits within
    max_lines at this column width/font size. Table rows in this file get a
    fixed height (_table_slide's row_cap * Inches(0.4)), unlike every other
    text renderer here — there's no y-cursor to check against a max_y and
    break early. A real page URL or joined issue list can run 60-130+
    chars; wrapped into a narrow column that's 2-3 lines PowerPoint then
    grows the row to fit, which pushes the table's actual rendered height
    past what this file computed for it — overlapping the insights/footer
    positioned below at that computed (not actual) height. Confirmed live
    on the Priority Issues and Tech Fixes slides (long blog-post URLs and
    page paths). Same 154-chars-per-inch-at-11pt calibration as _wrap_lines."""
    chars_per_line = max(10, int(width_in * (154 / size_pt)))
    limit = chars_per_line * max_lines
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def _wrap_lines(text: str, width_emu, size_pt: float = 12) -> int:
    """Estimate how many lines `text` wraps to in a box of this width/font
    size — advancing y by this (instead of a fixed single-line guess) is
    what keeps a wrapped second line from rendering on top of the next
    item. 154 chars-per-inch-at-size-11 matches the calibration already
    proven in _insights_strip below."""
    width_in = max(width_emu / 914400, 0.3)
    chars_per_line = max(10, int(width_in * (154 / size_pt)))
    return max(1, -(-len(text) // chars_per_line))


def add_core_problem_slide(prs: Presentation, core_problem: dict):
    """The report's single diagnostic thesis — everything else gathered
    synthesized into one root-cause statement plus a category breakdown
    (On-page SEO / Off-page SEO / Content & Keyword Strategy), matching
    the real manual-report "Core Problem" slide format confirmed from a
    SPOTONIX audit. AI-generated (core_problem_service.py) from
    already-gathered findings, not invented — only called when a real
    thesis was returned; a category with no points is dropped entirely
    rather than padded with generic advice."""
    thesis = core_problem.get("thesis")
    if not thesis:
        return None
    categories = [c for c in (core_problem.get("categories") or []) if c.get("points")]

    slide = _blank_slide(prs)
    _content_header(slide, "Core Problem")

    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(1.3))
    _textbox(
        slide, Inches(0.9), Inches(1.3), Inches(11.5), Inches(0.9), thesis,
        size=16, bold=True, color=_accent(),
    )

    if not categories:
        return slide

    col_top, col_height = Inches(2.65), Inches(4.0)
    gap = Inches(0.2)
    total_width = Inches(12.1)
    col_width = Emu(int((total_width - gap * (len(categories) - 1)) / len(categories)))
    left = Inches(0.6)
    for cat in categories:
        _card(slide, left, col_top, col_width, col_height)
        y = col_top + Inches(0.2)
        _textbox(slide, left + Inches(0.25), y, col_width - Inches(0.5), Inches(0.35), cat.get("name", ""), size=14, bold=True, color=_accent())
        rule = slide.shapes.add_shape(1, left + Inches(0.25), y + Inches(0.36), col_width - Inches(0.5), Pt(1.5))
        _fill(rule, _accent())
        rule.shadow.inherit = False
        y += Inches(0.55)
        for point in cat["points"][:5]:
            text = f"•  {point}"
            lines = _wrap_lines(text, col_width - Inches(0.5), size_pt=11.5)
            line_h = Inches(0.24)
            box = slide.shapes.add_textbox(left + Inches(0.25), y, col_width - Inches(0.5), line_h * lines)
            tf = box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = text
            run.font.size = Pt(11.5)
            run.font.color.rgb = TEXT_DARK
            y += line_h * lines + Inches(0.08)
        left += col_width + gap
    return slide


def _insights_strip(slide, left, top, width, insights, title="Key Insights", max_y=None):
    """2-5 bullet takeaways mechanically derived from the slide's own data —
    no free-text generation, every line traces back to a number on the same
    slide. Returns the bottom y (Emu) after the strip.

    max_y (defaults to leaving room for the footer) hard-caps how far this
    strip may draw — a long real insight sentence at a table with many rows
    (row_cap pushes `top` down) could otherwise run past the slide bottom or
    print over the footer text with no visible break, since this was the
    one text-list renderer in the file with no truncate-rather-than-overflow
    guard; every AI-bullet slide already stops before its card boundary the
    same way this now does."""
    if not insights:
        return top
    if max_y is None:
        max_y = SLIDE_H - Inches(0.5)
    _textbox(slide, left, top, width, Inches(0.24), title.upper(), size=9.5, bold=True, color=_accent())
    y = top + Inches(0.26)
    # Width-aware wrap estimate (~14 chars/inch at size 11) — a fixed
    # chars-per-line regardless of column width caused text in narrow
    # columns (e.g. the PageSpeed sidebar) to under-reserve height and
    # overlap the next bullet.
    text_width_in = max(width - Inches(0.18), Inches(0.5)) / 914400
    chars_per_line = max(20, int(text_width_in * 14))
    for item in insights[:5]:
        lines = max(1, -(-len(item) // chars_per_line))
        line_h = Inches(0.22)
        item_h = line_h * lines + Inches(0.05)
        if y + item_h > max_y:
            break
        _icon_dot(slide, left, y + Inches(0.07), Inches(0.08), _accent())
        _textbox(slide, left + Inches(0.18), y, width - Inches(0.18), line_h * lines, item, size=11)
        y += item_h
    return y


def _draw_table(slide, headers, rows, top, col_widths=None, row_cap=None, left=None, width=None, insights=None, row_height=0.4, wrap_cols=None):
    """Shared table-drawing body behind _table_slide, factored out so a
    slide needing extra content above the table (e.g. a stat card) can draw
    its own header/card and still reuse this instead of duplicating the
    table + insights-strip logic. Returns the slide's own bottom y (Emu)
    after the table (and insights strip, if any).

    row_height/wrap_cols let a caller whose cell content needs more than
    one line (e.g. Priority Issues' full issue list per URL, previously
    truncated to fit a single line) request taller rows with word-wrap
    enabled on specific columns, instead of every table being forced to
    the same fixed single-line row height."""
    if left is None:
        left = Inches(0.6)
    if width is None:
        width = Inches(12.1)
    if row_cap is None:
        row_cap = 9 if insights else 14
    n_cols = len(headers)
    n_rows = min(len(rows), row_cap) + 1
    height = Inches(row_height) * n_rows
    gframe = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = gframe.table
    table.first_row = False  # suppress the built-in banded-header theme so our colors apply cleanly

    if col_widths:
        for i, w in enumerate(col_widths):
            table.columns[i].width = Inches(w)

    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = HEADER_ROW_BG
        para = cell.text_frame.paragraphs[0]
        para.font.size = Pt(11)
        para.font.bold = True
        para.font.color.rgb = HEADER_ROW_TEXT

    for i, row in enumerate(rows[:row_cap], start=1):
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.text = str(val)
            cell.fill.solid()
            cell.fill.fore_color.rgb = ROW_ALT if i % 2 == 0 else WHITE
            cell.text_frame.word_wrap = wrap_cols is not None and j in wrap_cols
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(11)
            para.font.color.rgb = TEXT_DARK

    bottom = top + height
    if insights:
        bottom = _insights_strip(slide, left, bottom + Inches(0.15), width, insights)
    return bottom


def _table_slide(prs, title, headers, rows, col_widths=None, source=None, insights=None, row_cap=None, row_height=0.4, wrap_cols=None):
    slide = _blank_slide(prs)
    _content_header(slide, title)
    if source:
        _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)
    _draw_table(slide, headers, rows, Inches(1.2), col_widths=col_widths, row_cap=row_cap, insights=insights, row_height=row_height, wrap_cols=wrap_cols)
    return slide


# GA4's own default channel-group names, defined here for a couple that
# read as opaque jargon to a non-technical report reader — expanded inline
# wherever an insight names one of these, since the table itself (row
# labels only) has no room for an explanation. Not our classification —
# these are Google Analytics's own standard channel groups, straight off
# the sessionDefaultChannelGroup dimension.
_CHANNEL_DEFINITIONS = {
    "cross-network": "spans multiple ad networks/inventory types at once — typically a Google Ads Performance Max campaign",
    "unassigned": "GA4 couldn't determine a channel for this traffic, usually missing or broken UTM tags",
}


def _channel_note(channel: str) -> str:
    definition = _CHANNEL_DEFINITIONS.get((channel or "").strip().lower())
    return f" ({definition})" if definition else ""


def _traffic_sources_insights(
    shown: list[dict], total_sessions: float, prior_rows: list[dict] | None = None
) -> list[str]:
    """Traffic Sources insight rules (2026-09-09 spec):

    Step 1 — period mode is "comparison" only when real prior-period
    channel rows are actually supplied, never assumed; otherwise "single".
    Step 2 — every channel name/figure referenced below comes only from
    `shown` (the exact rows the table renders, already sorted+capped by
    the caller — same "Cross-network" bug this guards against as the
    return-rate max below) or `prior_rows`; never introduced, rounded, or
    recalled from elsewhere.
    Step 3 — return rate: trust an already-given return_rate_pct field;
    only fall back to computing returning-users / sessions when that field
    is genuinely absent, and always verify the actual max before naming a
    channel as "strongest."
    Step 4/5 — up to 3 bullets: largest session share paired with a
    quality signal (never size alone), the verified strongest return
    rate, a size/quality mismatch flag, and — comparison mode only — the
    single biggest period-over-period swing plus new/missing channels.
    No trend/MoM language is used in single mode."""
    if not shown:
        return []

    prior_by_channel = {p["channel"]: p for p in prior_rows} if prior_rows else {}
    comparison_mode = bool(prior_by_channel)

    verified = []
    for s in shown:
        sessions = float(s.get("sessions", 0) or 0)
        pct_share = (sessions / total_sessions * 100) if total_sessions else 0.0
        new_users = float(s.get("new_users", 0) or 0)
        returning_users = float(s.get("returning_users", 0) or 0)
        return_rate = s.get("return_rate_pct")
        if return_rate is None and sessions:
            return_rate = round(100 * returning_users / sessions, 1)
        verified.append({
            "channel": s["channel"], "sessions": sessions, "pct_share": pct_share,
            "new_users": new_users, "returning_users": returning_users, "return_rate": return_rate,
        })

    insights: list[str] = []
    used: set[str] = set()

    # Largest share, paired with a quality signal — never size alone.
    top = max(verified, key=lambda r: r["sessions"])
    if top["return_rate"] is not None:
        quality = f"a {top['return_rate']:.0f}% return rate"
    elif top["new_users"] or top["returning_users"]:
        quality = f"{top['new_users']:,.0f} new vs {top['returning_users']:,.0f} returning users"
    else:
        quality = "no new-vs-returning data available for this channel"
    insights.append(
        f"{top['channel']}{_channel_note(top['channel'])} drives the largest share of sessions "
        f"({_pct_text(top['pct_share'])} of {int(total_sessions):,} total) — {quality}."
    )
    used.add(top["channel"])

    # Verified strongest return rate (Step 3: confirmed max, not assumed).
    rate_ranked = sorted((r for r in verified if r["return_rate"] is not None), key=lambda r: r["return_rate"], reverse=True)
    if rate_ranked and rate_ranked[0]["channel"] not in used and len(insights) < 3:
        best = rate_ranked[0]
        insights.append(
            f"{best['channel']}{_channel_note(best['channel'])} has the strongest return rate at "
            f"{best['return_rate']:.0f}% ({_pct_text(best['pct_share'])} of sessions)."
        )
        used.add(best["channel"])

    # Mismatch: large share, return rate well below the group's own
    # average (cross-segment check, not a fixed threshold).
    if len(insights) < 3:
        rated = [r for r in verified if r["return_rate"] is not None]
        if len(rated) >= 2:
            avg_rate = sum(r["return_rate"] for r in rated) / len(rated)
            mismatched = sorted(
                (r for r in rated if r["channel"] not in used and r["pct_share"] >= 15 and r["return_rate"] < avg_rate - 10),
                key=lambda r: r["pct_share"], reverse=True,
            )
            if mismatched:
                m = mismatched[0]
                insights.append(
                    f"{m['channel']}{_channel_note(m['channel'])} carries {_pct_text(m['pct_share'])} of sessions but only a "
                    f"{m['return_rate']:.0f}% return rate, well below the {avg_rate:.0f}% average across channels — a "
                    f"size/quality mismatch worth a closer look."
                )
                used.add(m["channel"])

    # Comparison mode only: the single biggest verified period-over-period
    # swing (share delta), stated with real numbers from both periods —
    # then a new/missing channel flag if room remains.
    if comparison_mode and len(insights) < 3:
        prior_total = sum(float(p.get("sessions", 0) or 0) for p in prior_rows) if prior_rows else 0.0
        deltas = []
        for r in verified:
            if r["channel"] in used:
                continue
            prior = prior_by_channel.get(r["channel"])
            if prior is None:
                continue
            prior_sessions = float(prior.get("sessions", 0) or 0)
            prior_share = (prior_sessions / prior_total * 100) if prior_total else 0.0
            deltas.append((abs(r["pct_share"] - prior_share), r, prior_share, prior_sessions))
        if deltas:
            deltas.sort(key=lambda d: d[0], reverse=True)
            _, r, prior_share, prior_sessions = deltas[0]
            direction = "up" if r["pct_share"] >= prior_share else "down"
            insights.append(
                f"{r['channel']}{_channel_note(r['channel'])} moved {direction} from {_pct_text(prior_share)} of sessions "
                f"({int(prior_sessions):,}) in the prior period to {_pct_text(r['pct_share'])} ({int(r['sessions']):,}) this period."
            )
            used.add(r["channel"])

        if len(insights) < 3:
            current_names = {v["channel"] for v in verified}
            new_channel = next((r["channel"] for r in verified if r["channel"] not in prior_by_channel and r["channel"] not in used), None)
            if new_channel:
                insights.append(f"{new_channel}{_channel_note(new_channel)} is new this period — no data for it in the prior period.")
            else:
                missing = next((c for c in prior_by_channel if c not in current_names), None)
                if missing:
                    insights.append(f"{missing}{_channel_note(missing)} appeared in the prior period but has no sessions this period.")

    return insights[:3]


# Recruitment-pattern terms — a branded "careers"/"jobs" query is real
# search demand but not commercial branded-search value (nobody searching
# "lumberfi careers" is evaluating the product), so it has to be tagged and
# excluded from the headline metric rather than silently inflating it.
_RECRUITMENT_QUERY_TERMS = [
    "career", "careers", "job", "jobs", "hiring", "employment", "vacancy", "vacancies",
    "working at", "salary", "glassdoor", "indeed",
]


def _classify_branded_query(query: str, brand_tokens: set[str], relevance_terms: list[str]) -> str:
    """One of BRAND_CORE / RECRUITMENT / PRODUCT_RELEVANT / AMBIGUOUS
    (2026-09-09 spec) — rule-based, no AI call, since every category here
    is a mechanical pattern check. AMBIGUOUS is the honest fallback rather
    than forcing a guess when the query text alone isn't enough."""
    text = (query or "").lower().strip()
    if not text:
        return "AMBIGUOUS"
    if any(term in text for term in _RECRUITMENT_QUERY_TERMS):
        return "RECRUITMENT"
    if any(term and term.lower() in text for term in relevance_terms):
        return "PRODUCT_RELEVANT"
    # What's left after stripping the brand token(s) out — if nothing (or
    # only a stray short word, e.g. a near-brand misspelling) remains, this
    # is the brand name itself with no other intent signal.
    remainder = text
    for token in brand_tokens:
        if token:
            remainder = re.sub(rf"\b{re.escape(token)}\b", " ", remainder)
    remainder = remainder.strip()
    if not remainder or len(remainder.split()) <= 1:
        return "BRAND_CORE"
    return "AMBIGUOUS"


def _branded_query_insights(
    query_table: list[dict], brand_tokens: set[str], client_relevance_profile: dict
) -> list[str]:
    """Search Queries — Branded insights (2026-09-09 spec): classify every
    branded query first, then report raw-vs-RECRUITMENT-excluded metrics
    side by side — never a single blended average as the finding. Only
    products/categories literally in client_relevance_profile are used for
    PRODUCT_RELEVANT matching (never inferred); only queries/figures
    literally in query_table are ever cited."""
    if not query_table:
        return []

    relevance_terms = list(client_relevance_profile.get("products") or []) + list(
        client_relevance_profile.get("categories") or []
    )
    classified = [
        {**q, "_tag": _classify_branded_query(q.get("query", ""), brand_tokens, relevance_terms)}
        for q in query_table
    ]

    def _totals(rows: list[dict]) -> tuple[int, int, float]:
        clicks = sum(int(r.get("clicks", 0) or 0) for r in rows)
        impressions = sum(int(r.get("impressions", 0) or 0) for r in rows)
        ctr = (clicks / impressions * 100) if impressions else 0.0
        return clicks, impressions, ctr

    raw_clicks, raw_impressions, raw_ctr = _totals(classified)
    filtered = [q for q in classified if q["_tag"] != "RECRUITMENT"]
    filt_clicks, filt_impressions, filt_ctr = _totals(filtered)

    insights = [
        f"Raw: {raw_clicks:,} clicks / {raw_impressions:,} impressions ({raw_ctr:.1f}% CTR) across all branded queries. "
        f"Excluding recruitment queries: {filt_clicks:,} clicks / {filt_impressions:,} impressions ({filt_ctr:.1f}% CTR)."
    ]

    recruitment = [q for q in classified if q["_tag"] == "RECRUITMENT"]
    if recruitment:
        top_r = max(recruitment, key=lambda q: q.get("impressions", 0))
        r_ctr = float(top_r.get("ctr", 0) or 0) * 100
        insights.append(
            f"\"{top_r['query']}\" ({top_r.get('clicks', 0):,} clicks / {top_r.get('impressions', 0):,} impressions, "
            f"{r_ctr:.1f}% CTR) is a recruitment query — job-seeker intent, not commercial branded-search value."
        )

    core_or_relevant = [q for q in classified if q["_tag"] in ("BRAND_CORE", "PRODUCT_RELEVANT")]
    if core_or_relevant and len(insights) < 3:
        top_c = max(core_or_relevant, key=lambda q: q.get("impressions", 0))
        c_ctr = float(top_c.get("ctr", 0) or 0) * 100
        weak_note = ""
        if filt_impressions and c_ctr < filt_ctr * 0.6:
            weak_note = f" — well below the {filt_ctr:.1f}% filtered average despite the volume"
        insights.append(
            f"\"{top_c['query']}\" ({top_c['_tag'].replace('_', ' ').title()}, {top_c.get('clicks', 0):,} clicks / "
            f"{top_c.get('impressions', 0):,} impressions, {c_ctr:.1f}% CTR at position {float(top_c.get('position', 0) or 0):.1f}){weak_note}."
        )

    return insights[:3]


def _standout_query_insight(subset: list[dict]) -> str | None:
    """Finds ONE standout row instead of summarizing with a blended
    average (2026-09-09 spec): either a single query dominating the
    table's impressions/clicks, or a row that inverts expectation (the
    best-positioned query converting far worse than the table's own
    average, or a low-impression query outperforming everything else).
    Real numbers only, no invented reasoning, no qualitative label without
    a comparison basis actually computed from this same table."""
    if not subset:
        return None
    total_impressions = sum(int(q.get("impressions", 0) or 0) for q in subset)
    if not total_impressions:
        return None
    total_clicks = sum(int(q.get("clicks", 0) or 0) for q in subset)
    avg_ctr = (total_clicks / total_impressions * 100) if total_impressions else 0.0

    by_impressions = max(subset, key=lambda q: q.get("impressions", 0) or 0)
    imp_share = (by_impressions.get("impressions", 0) or 0) / total_impressions * 100
    if imp_share > 50:
        ctr = float(by_impressions.get("ctr", 0) or 0) * 100
        return f"One query — \"{by_impressions['query']}\" — makes up {imp_share:.0f}% of all impressions, but only converts at {ctr:.1f}% CTR."

    if total_clicks:
        by_clicks = max(subset, key=lambda q: q.get("clicks", 0) or 0)
        click_share = (by_clicks.get("clicks", 0) or 0) / total_clicks * 100
        if click_share > 50:
            ctr = float(by_clicks.get("ctr", 0) or 0) * 100
            return f"One query — \"{by_clicks['query']}\" — makes up {click_share:.0f}% of all clicks, at a {ctr:.1f}% CTR."

    positioned = [q for q in subset if (q.get("clicks", 0) or 0) > 0 and q.get("position") is not None]
    if positioned:
        best = min(positioned, key=lambda q: q["position"])
        best_ctr = float(best.get("ctr", 0) or 0) * 100
        if best_ctr < avg_ctr * 0.5:
            return (
                f"\"{best['query']}\" ranks best in this table (position {best['position']:.1f}) but converts at only "
                f"{best_ctr:.1f}% CTR, well below the {avg_ctr:.1f}% table average."
            )

    if len(subset) >= 3:
        sorted_by_imp = sorted(subset, key=lambda q: q.get("impressions", 0) or 0)
        low_pool = sorted_by_imp[: max(1, len(sorted_by_imp) // 3)]
        best_low = max((q for q in low_pool if q.get("impressions", 0)), key=lambda q: q.get("ctr", 0) or 0, default=None)
        if best_low:
            low_ctr = float(best_low.get("ctr", 0) or 0) * 100
            if low_ctr > avg_ctr * 1.5:
                return (
                    f"\"{best_low['query']}\" looks unimportant ({int(best_low['impressions']):,} impressions) but is "
                    f"outperforming everything else at {low_ctr:.1f}% CTR."
                )
    return None


def _validate_slide_insights(insights: list[str], shown_rows: list[dict], name_field: str) -> tuple[list[str], list[str]]:
    """QA gate (2026-09-10 spec) run right before an insights block is
    published: every quoted name an insight cites must be one of the rows
    literally drawn in this slide's own table (`shown_rows`), not some
    broader dataset the insight was computed from — catches the class of
    bug where a generator scans the full dataset (e.g. every branded query,
    for accurate raw-vs-filtered totals) while the table only shows a
    capped/sorted subset of it, so a named example can fall outside what
    the reader can actually see on the slide. Confirmed live: an insight
    named a query ("lumberfy") that ranked outside the shown top-14-by-
    clicks rows. Never softens or substitutes a bad line — drops it
    outright. Returns (validated_insights, removed_notes); removed_notes
    is QA-only, never rendered on the slide."""
    shown_names = {str(r.get(name_field, "")).strip().lower() for r in shown_rows}
    validated: list[str] = []
    removed: list[str] = []
    for line in insights:
        quoted = re.findall(r'"([^"]+)"', line)
        bad = [q for q in quoted if q.strip().lower() not in shown_names]
        if bad:
            removed.append(f"dropped (cites {bad!r}, not in this slide's table): {line}")
            continue
        validated.append(line)
    return validated, removed


def add_traffic_overview_slide(prs: Presentation, analytics: dict):
    slide = _blank_slide(prs)
    _content_header(slide, "Traffic Overview")
    span = _ga4_date_span(analytics.get("date_range"))
    source_text = f"Source: Google Analytics ({span})" if span else "Source: Google Analytics"
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), source_text, size=11, color=TEXT_MUTED)

    traffic = (analytics.get("traffic_overview") or {}).get("rows", [])
    if not traffic:
        _textbox(slide, Inches(0.6), Inches(1.4), Inches(8), Inches(0.5), "No GA4 data available for this period.", size=14, color=TEXT_MUTED)
        return slide

    def total(key):
        return sum(float(r.get(key, 0) or 0) for r in traffic)

    sessions = total("sessions")
    users = total("total_users")
    pageviews = total("page_views")
    engagement_seconds = total("engagement_duration")
    avg_engagement = (sum(float(r.get("engagement_rate", 0) or 0) for r in traffic) / len(traffic)) * 100
    avg_bounce = (sum(float(r.get("bounce_rate", 0) or 0) for r in traffic) / len(traffic)) * 100

    def _fmt_duration(total_seconds: float) -> str:
        m, s = divmod(round(total_seconds), 60)
        return f"{m}m {s:02d}s"

    avg_session_duration = engagement_seconds / sessions if sessions else 0
    avg_time_on_page = engagement_seconds / pageviews if pageviews else 0

    metrics = [
        ("Sessions", f"{sessions:,.0f}"),
        ("Users", f"{users:,.0f}"),
        ("Page views", f"{pageviews:,.0f}"),
        ("Avg. engagement", f"{avg_engagement:.1f}%"),
        ("Bounce rate", f"{avg_bounce:.1f}%"),
        ("Avg. session", _fmt_duration(avg_session_duration)),
        ("Avg. time on page", _fmt_duration(avg_time_on_page)),
    ]
    # 4 cards per row instead of one fixed-width row of 5 — 7 metrics
    # (Avg. session and Avg. time on page added) no longer fit one row
    # without shrinking the value text past legibility.
    cols_per_row = 4
    gap = Inches(0.15)
    total_width = Inches(12.1)
    card_width = Emu(int((total_width - gap * (cols_per_row - 1)) / cols_per_row))
    card_height = Inches(1.35)
    row_gap = Inches(0.15)
    for i, (label, value) in enumerate(metrics):
        row, col = divmod(i, cols_per_row)
        left = Inches(0.6) + Emu(col * (card_width + gap))
        top = Inches(1.3) + Emu(row * (card_height + row_gap))
        _card(slide, left, top, card_width, card_height)
        _textbox(slide, left + Inches(0.15), top + Inches(0.15), card_width - Inches(0.3), Inches(0.4), label, size=12, color=TEXT_MUTED)
        _textbox(slide, left + Inches(0.15), top + Inches(0.55), card_width - Inches(0.3), Inches(0.7), value, size=20, bold=True, color=_accent())

    # Key-insights strip removed per teammate QA on the last report — this
    # is a pure overview slide (raw KPI cards), the per-metric commentary
    # duplicated what the numbers already showed and belongs on the slides
    # that actually break the data down (Traffic Sources, Traffic Spike,
    # Traffic Breakdown), not restated again here.
    return slide


def _traffic_spike_hypothesis(spike: dict) -> list[str]:
    """Channel -> landing page -> engagement -> key event evidence chain,
    ending in one testable causal hypothesis — teammate QA on the last
    report flagged this slide as a plain date/country/channel dump with no
    reasoning. Every clause here traces to a real number ga4_service.
    get_traffic_spike_breakdown returned; when engagement/key-event data
    isn't available (property has no Google Signals / key events
    configured) the hypothesis narrows to what evidence actually exists
    rather than guessing at the missing half."""
    lines: list[str] = []
    top_channel = (spike.get("by_channel") or [None])[0]
    top_landing = (spike.get("by_landing_page") or [None])[0]
    if top_channel and top_landing:
        lines.append(
            f"{top_channel['label']} drove {top_channel['pct']:.0f}% of the spike, landing mostly on "
            f"\"{top_landing['label']}\" ({top_landing['pct']:.0f}% of that day's sessions)."
        )
    elif top_channel:
        lines.append(f"{top_channel['label']} drove {top_channel['pct']:.0f}% of the spike.")

    avg_eng, spike_eng = spike.get("avg_engagement_rate"), spike.get("spike_engagement_rate")
    avg_ke, spike_ke = spike.get("avg_key_events"), spike.get("spike_key_events")
    have_engagement = avg_eng is not None and spike_eng is not None
    have_key_events = avg_ke is not None and spike_ke is not None

    if have_engagement:
        eng_pct, avg_eng_pct = spike_eng * 100, avg_eng * 100
        eng_delta = eng_pct - avg_eng_pct
        eng_verdict = "held up" if eng_delta >= -5 else "dropped noticeably"
        lines.append(f"Engagement rate that day was {eng_pct:.0f}% vs a {avg_eng_pct:.0f}% period average — {eng_verdict}.")
    if have_key_events:
        ke_verdict = "rose with it" if spike_ke >= avg_ke * 1.1 else ("stayed flat" if spike_ke >= avg_ke * 0.9 else "did not follow")
        lines.append(f"Key events that day: {spike_ke:.0f} vs a {avg_ke:.0f}/day average — {ke_verdict}.")

    # Bounce rate — the number that answers "was this good traffic or
    # noise" directly, added as one line alongside the engagement/key-event
    # evidence above without touching that existing logic. GA4's bounceRate
    # metric is a 0-1 fraction, same convention as engagementRate above, so
    # *100 for both display and the "~5 points" comparison threshold.
    # Classification + reasoning stays one sentence, per spec, so it can't
    # push an existing line out of _insights_strip's 5-line cap.
    avg_bounce, spike_bounce = spike.get("avg_bounce_rate"), spike.get("spike_bounce_rate")
    if avg_bounce is not None and spike_bounce is not None:
        avg_bounce_pct, spike_bounce_pct = avg_bounce * 100, spike_bounce * 100
        duration_note = ""
        avg_dur, spike_dur = spike.get("avg_session_duration_sec"), spike.get("spike_session_duration_sec")
        if avg_dur is not None and spike_dur is not None:
            duration_note = f", avg. session duration {spike_dur:.0f}s vs {avg_dur:.0f}s average"

        diff = spike_bounce_pct - avg_bounce_pct
        if abs(diff) <= 5:
            label, reason = "SAME PATTERN", "likely a real volume event, not a quality issue"
        elif diff < 0:
            label, reason = "MORE ENGAGED THAN USUAL", "worth identifying and repeating the driver"
        else:
            label, reason = "LESS ENGAGED THAN USUAL", "likely low-quality/bot traffic, treat with caution"
        lines.append(
            f"Bounce rate that day was {spike_bounce_pct:.0f}% vs a {avg_bounce_pct:.0f}% period average{duration_note} "
            f"— {label}: {reason}."
        )

    # The hypothesis itself: only stated when there's enough evidence to
    # actually distinguish "real demand" from "low-quality traffic" —
    # engagement AND key events both present and pointing the same
    # direction. Anything thinner than that stays as the raw evidence
    # lines above without a claimed verdict, rather than guessing.
    if have_engagement and have_key_events:
        engagement_held = spike_eng >= avg_eng * 0.9
        key_events_held = spike_ke >= avg_ke * 0.9
        if engagement_held and key_events_held:
            lines.append("Hypothesis: this looks like genuine demand, not bot/referral noise — engagement and key events moved with sessions, not against them.")
        elif not engagement_held and not key_events_held:
            lines.append("Hypothesis: this spike is likely low-intent or referral/bot traffic — sessions rose but engagement and key events didn't follow, worth checking the top landing page's referrer detail.")
    return lines


def add_traffic_spike_slide(prs: Presentation, spike: dict):
    """One slide: the single biggest single-day traffic spike in the period,
    and which age/gender/country/channel segments drove it — mechanically
    detected in ga4_service.get_traffic_spike_breakdown, not a canned
    template section. Only called when a real spike was found (see that
    function's threshold)."""
    slide = _blank_slide(prs)
    _content_header(slide, "Traffic Spike Analysis — Single Day")

    from datetime import date as _date

    fmt_date = _date.fromisoformat(spike["date"]).strftime("%b %d, %Y")

    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(1.15))
    _textbox(
        slide, Inches(0.9), Inches(1.25), Inches(11.5), Inches(0.4),
        f"{fmt_date} ({spike['day_of_week']}) — {spike['sessions']:,} sessions", size=17, bold=True, color=_accent(),
    )
    _textbox(
        slide, Inches(0.9), Inches(1.65), Inches(11.5), Inches(0.4),
        f"{spike['pct_above_avg']:.0f}% above the period average of {spike['avg_sessions']:,} sessions/day.",
        size=12.5, color=TEXT_MUTED,
    )

    columns = [
        (label, rows)
        for label, rows in [
            ("Age", spike.get("by_age") or []),
            ("Gender", spike.get("by_gender") or []),
            ("Top Countries", spike.get("by_country") or []),
            ("Channel", spike.get("by_channel") or []),
        ]
        if rows
    ]
    if not columns:
        return slide

    # Width is divided evenly across however many columns actually have
    # data (2-4 in practice) rather than a fixed per-column width, so a
    # 4th column (Channel) fits without redesigning the layout, and a
    # client with only 2 populated columns still fills the row width.
    col_top, col_height = Inches(2.55), Inches(3.7)
    gap = Inches(0.2)
    total_width = Inches(12.1)
    col_width = Emu(int((total_width - gap * (len(columns) - 1)) / len(columns)))
    left = Inches(0.6)
    for label, rows in columns:
        _card(slide, left, col_top, col_width, col_height)
        y = col_top + Inches(0.2)
        _textbox(slide, left + Inches(0.25), y, col_width - Inches(0.5), Inches(0.35), label, size=14, bold=True, color=_accent())
        rule = slide.shapes.add_shape(1, left + Inches(0.25), y + Inches(0.36), col_width - Inches(0.5), Pt(1.5))
        _fill(rule, _accent())
        rule.shadow.inherit = False
        y += Inches(0.55)
        for row in rows:
            _textbox(slide, left + Inches(0.25), y, col_width - Inches(1.1), Inches(0.3), row["label"], size=12, color=TEXT_DARK)
            _textbox(
                slide, left + col_width - Inches(1.05), y, Inches(0.8), Inches(0.3),
                f"{row['pct']:.0f}%", size=12, bold=True, color=TEXT_MUTED, align=PP_ALIGN.RIGHT,
            )
            y += Inches(0.36)
        left += col_width + gap

    hypothesis = _traffic_spike_hypothesis(spike)
    if hypothesis:
        _insights_strip(slide, Inches(0.6), col_top + col_height + Inches(0.15), Inches(11.9), hypothesis, title="Causal Hypothesis")
    return slide


# Common countries abbreviated per client spec (not strict ISO — client
# asked for "US, IND, SG" specifically, a mix of 2- and 3-letter forms).
# Anything not in this table falls back to its first 3 letters uppercased,
# which is still short enough to keep the Country Split column readable.
_COUNTRY_ABBREVIATIONS = {
    "united states": "US", "united kingdom": "UK", "india": "IND", "singapore": "SG",
    "canada": "CA", "australia": "AU", "germany": "DE", "france": "FR", "spain": "ES",
    "italy": "IT", "netherlands": "NL", "ireland": "IE", "new zealand": "NZ",
    "united arab emirates": "UAE", "south africa": "ZA", "brazil": "BR", "mexico": "MX",
    "japan": "JP", "china": "CN", "south korea": "KR", "philippines": "PH",
    "indonesia": "ID", "malaysia": "MY", "vietnam": "VN", "thailand": "TH",
    "pakistan": "PK", "bangladesh": "BD", "nigeria": "NG", "kenya": "KE",
    "saudi arabia": "SA", "sweden": "SE", "norway": "NO", "denmark": "DK",
    "poland": "PL", "switzerland": "CH", "belgium": "BE", "portugal": "PT",
}


def _abbreviate_country(name: str) -> str:
    return _COUNTRY_ABBREVIATIONS.get(name.strip().lower(), name[:3].upper())


def _pct_text(pct: float) -> str:
    """Never round a small share to zero — show the real decimal for
    anything under 1%, since that's exactly where a new/emerging channel's
    real number matters most even though it's small (2026-09-09 spec)."""
    return f"{pct:.1f}%" if pct < 1 else f"{pct:.0f}%"


def add_traffic_channel_breakdown_slide(prs: Presentation, breakdown: dict, source: str | None = None):
    """Channel is the primary key (one row per channel, per report spec) —
    country and device are folded into that same row as each channel's own
    top-3 split, rather than getting separate sections, since channel is
    the priority lens here and country/device are secondary detail."""
    rows_data = breakdown.get("rows") or []
    if not rows_data:
        return None

    def _split_text(entries):
        return ", ".join(f"{e['label']} {e['pct']:.0f}%" for e in entries) if entries else "—"

    used_abbreviations: dict[str, str] = {}
    rows = []
    for r in rows_data:
        countries = r.get("top_countries") or []
        for c in countries:
            used_abbreviations[_abbreviate_country(c["label"])] = c["label"]
        rows.append((
            r["channel"],
            f"{r['avg_sessions_month']:,}",
            f"{r['pct_share']:.0f}%",
            _split_text([{"label": _abbreviate_country(c["label"]), "pct": c["pct"]} for c in countries]),
            # A device that rounds to 0.0% (e.g. 1 session out of 5,000) is
            # still real but not worth a slot in an already-tight cell —
            # drop it instead of showing "Tablet 0%".
            _split_text([{"label": d["label"].title(), "pct": d["pct"]} for d in (r.get("top_devices") or []) if round(d["pct"]) > 0]),
        ))
    months = breakdown.get("months")

    # Insight rules (2026-09-09 user spec): never state the biggest number
    # as the whole finding — "X is the leading channel" is a description,
    # not a finding. Every insight pairs a number with a quality signal
    # (bounce rate) or cross-checks one segment against the others, since a
    # single ~30-day window has no "change over time" to lean on for
    # what's interesting. Never round a small share to zero. Cap 2-3
    # bullets, ranked by usefulness.
    insights: list[str] = []
    used_channels: set[str] = set()
    have_quality = bool(rows_data) and all(r.get("bounce_rate_pct") is not None for r in rows_data)

    if have_quality:
        avg_bounce = sum(r["bounce_rate_pct"] for r in rows_data) / len(rows_data)
        top = rows_data[0]
        top_delta = top["bounce_rate_pct"] - avg_bounce
        if abs(top_delta) <= 5:
            quality_note = f"bounce rate ({top['bounce_rate_pct']:.0f}%) is in line with the {avg_bounce:.0f}% average across all channels"
        elif top_delta > 0:
            quality_note = f"but its bounce rate ({top['bounce_rate_pct']:.0f}%) runs meaningfully above the {avg_bounce:.0f}% cross-channel average — size alone doesn't mean quality here"
        else:
            quality_note = f"and its bounce rate ({top['bounce_rate_pct']:.0f}%) also beats the {avg_bounce:.0f}% cross-channel average"
        insights.append(
            f"{top['channel']} carries {_pct_text(top['pct_share'])} of sessions ({top['avg_sessions_month']:,}/month), {quality_note}."
        )
        used_channels.add(top["channel"])

        # Cross-check: the channel whose bounce rate deviates most from the
        # group average (excluding whichever channel bullet 1 already
        # covered) — this is the "different from the other segments in
        # this table" finding a size-only ranking can't surface, standing
        # in for trend comparison on a single-period dataset.
        deviations = sorted(
            (r for r in rows_data if r["channel"] not in used_channels),
            key=lambda r: abs(r["bounce_rate_pct"] - avg_bounce), reverse=True,
        )
        if deviations and abs(deviations[0]["bounce_rate_pct"] - avg_bounce) > 5:
            outlier = deviations[0]
            delta = outlier["bounce_rate_pct"] - avg_bounce
            direction = "far above" if delta > 0 else "far below"
            if delta > 0:
                verdict = "worth investigating for traffic quality despite its size"
            elif outlier["pct_share"] < 10:
                verdict = "the most efficient channel in this table, disproportionate to its small size"
            else:
                verdict = "a genuine quality strength worth understanding and repeating"
            insights.append(
                f"{outlier['channel']} ({_pct_text(outlier['pct_share'])} of sessions) has a bounce rate {direction} "
                f"the {avg_bounce:.0f}% cross-channel average ({outlier['bounce_rate_pct']:.0f}%) — {verdict}."
            )
            used_channels.add(outlier["channel"])
    elif rows_data:
        # No bounce-rate data (older cached breakdown result) — still frame
        # as a comparison between segments, never a bare size statement.
        top = rows_data[0]
        used_channels.add(top["channel"])
        second = next((r for r in rows_data if r["channel"] not in used_channels), None)
        if second:
            insights.append(
                f"{top['channel']} ({_pct_text(top['pct_share'])} of sessions) leads {second['channel']} "
                f"({_pct_text(second['pct_share'])}) by {top['pct_share'] - second['pct_share']:.0f} points."
            )
            used_channels.add(second["channel"])

    # A genuinely small but real channel (e.g. an emerging/new initiative)
    # — actual decimal, never rounded away to "0%" — only when there's
    # still room in the cap and it isn't already covered above.
    if len(insights) < 3:
        small = [r for r in rows_data if r["channel"] not in used_channels and 0 < r["pct_share"] < 1]
        if small:
            s = max(small, key=lambda r: r["pct_share"])
            insights.append(
                f"{s['channel']} is a small but real channel at {s['pct_share']:.1f}% of sessions "
                f"({s['avg_sessions_month']:,}/month) — worth tracking, not yet material."
            )

    if months:
        insights.append(f"Figures are monthly averages across the last {months:.1f} month(s) of tracked data.")
    if used_abbreviations:
        legend = ", ".join(f"{abbr} - {full}" for abbr, full in sorted(used_abbreviations.items()))
        insights.append(f"* {legend}")
    return _table_slide(
        prs, "Traffic Breakdown — Monthly Average",
        ["Channel", "Avg Sessions/mo", "% Share", "Top Countries", "Top Devices"],
        rows, col_widths=[2.0, 1.7, 1.1, 3.5, 3.8], source=source, insights=insights,
    )


def _fmt_num(v):
    """Domain Overview numeric fields come back as floats (2400.0, 616.0)
    even for whole-number counts — strip the trailing '.0' for display."""
    if v in (None, ""):
        return ""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(n)) if n == int(n) else f"{n:.1f}"


def add_competitor_table_slide(prs: Presentation, competitor_rows: list[dict]):
    """Matches the reference deck's Competitor Analysis table. Renders the
    full DR/Backlinks/Top Countries/Branded-split columns when the rows come
    from Domain Overview uploads; falls back to a slimmer traffic/keywords/
    common-keywords table when only an Organic Competitors export is available
    (that file type has no DR, backlinks, or branded-split data at all)."""
    has_rich_data = any(r.get("authority_score") or r.get("backlinks_total") for r in competitor_rows)
    # Domain Overview PDF's Organic Traffic/Keywords are always a single
    # country's database (confirmed: every export headed "US | Domain |
    # ..."), never Worldwide. When a domain's separate "Overview Trend" CSV
    # was uploaded with Database=Worldwide (see site_audit.py's
    # worldwide_by_domain merge), add the extra Worldwide columns — only
    # when at least one row actually has the data, so a client where nobody
    # uploaded that file yet doesn't get two permanently-blank columns.
    has_worldwide_data = any(r.get("organic_traffic_worldwide") is not None for r in competitor_rows)

    if has_rich_data and has_worldwide_data:
        # Paid Traffic column dropped 2026-09-10 per user request — Semrush's
        # paid-traffic estimate for a competitor has no actionable follow-up
        # for an SEO/organic-focused report, just an idle number.
        headers = [
            "Domain", "Organic Traffic", "Organic Traffic (Global)", "Organic Keywords", "Organic Keywords (Global)",
            "DR", "Backlinks", "Top Countries", "Branded", "Non-Branded",
        ]
        col_widths = [2.15, 1.0, 1.15, 1.0, 1.2, 0.7, 1.0, 1.1, 0.9, 1.0]
        rows = [
            (
                r.get("domain", ""),
                _fmt_num(r.get("organic_traffic")),
                _fmt_num(r.get("organic_traffic_worldwide")),
                _fmt_num(r.get("organic_keywords")),
                _fmt_num(r.get("organic_keywords_worldwide")),
                _fmt_num(r.get("authority_score")),
                _fmt_num(r.get("backlinks_total")),
                r.get("top_countries", ""),
                r.get("branded_pct", ""),
                r.get("nonbranded_pct", ""),
            )
            for r in competitor_rows[:14]
        ]
    elif has_rich_data:
        headers = ["Domain", "Organic Traffic", "Organic Keywords", "DR", "Backlinks", "Top Countries", "Branded", "Non-Branded"]
        col_widths = [3.8, 1.3, 1.3, 0.9, 1.2, 1.3, 1.1, 1.2]
        rows = [
            (
                r.get("domain", ""),
                _fmt_num(r.get("organic_traffic")),
                _fmt_num(r.get("organic_keywords")),
                _fmt_num(r.get("authority_score")),
                _fmt_num(r.get("backlinks_total")),
                r.get("top_countries", ""),
                r.get("branded_pct", ""),
                r.get("nonbranded_pct", ""),
            )
            for r in competitor_rows[:14]
        ]
    else:
        headers = ["Domain", "Organic Traffic", "Organic Keywords", "Common Keywords"]
        col_widths = [5.5, 2.6, 2.6, 2.6]
        rows = [
            (
                r.get("domain", ""),
                _fmt_num(r.get("organic_traffic")),
                _fmt_num(r.get("organic_keywords")),
                _fmt_num(r.get("common_keywords")),
            )
            for r in competitor_rows[:14]
        ]

    # Own-site row is guaranteed first only for the Domain Overview path (see
    # site_audit.py's sort) — an Organic Competitors export has no "own" row
    # mixed in at all, so skip these own-vs-competitor insights there.
    own_row = competitor_rows[0] if (has_rich_data and competitor_rows) else None
    competitors = competitor_rows[1:] if own_row and len(competitor_rows) > 1 else []
    insights = []
    if own_row and competitors:
        traffic_leader = max(competitors, key=lambda r: _num(r.get("organic_traffic")))
        own_traffic = _num(own_row.get("organic_traffic"))
        leader_traffic = _num(traffic_leader.get("organic_traffic"))
        if leader_traffic > own_traffic:
            multiple = f"{leader_traffic / own_traffic:.0f}x" if own_traffic else "significantly"
            insights.append(f"{traffic_leader.get('domain', 'Top competitor')} gets {multiple} more organic traffic ({leader_traffic:,.0f} vs {own_traffic:,.0f}).")
        if has_rich_data:
            dr_leader = max(competitors, key=lambda r: _num(r.get("authority_score")))
            own_dr = _num(own_row.get("authority_score"))
            if _num(dr_leader.get("authority_score")) > own_dr:
                insights.append(f"Domain authority gap: {dr_leader.get('domain')} sits at DR {int(_num(dr_leader.get('authority_score')))} vs your {int(own_dr)}.")
            bl_leader = max(competitors, key=lambda r: _num(r.get("backlinks_total")))
            own_bl = _num(own_row.get("backlinks_total"))
            if _num(bl_leader.get("backlinks_total")) > own_bl:
                insights.append(f"{bl_leader.get('domain')} has {_num(bl_leader.get('backlinks_total')):,.0f} backlinks vs your {own_bl:,.0f}.")
        kw_leader = max(competitors, key=lambda r: _num(r.get("organic_keywords")))
        own_kw = _num(own_row.get("organic_keywords"))
        if _num(kw_leader.get("organic_keywords")) > own_kw:
            insights.append(f"{kw_leader.get('domain')} ranks for {_num(kw_leader.get('organic_keywords')) - own_kw:,.0f} more organic keywords than you.")

    own_export_date = own_row.get("export_date") if own_row else None
    own_worldwide_date_raw = own_row.get("worldwide_as_of_date") if own_row else None
    own_worldwide_date = None
    if own_worldwide_date_raw:
        from datetime import date as _date

        try:
            own_worldwide_date = _date.fromisoformat(own_worldwide_date_raw).strftime("%b %d, %Y")
        except ValueError:
            own_worldwide_date = own_worldwide_date_raw
    date_bits = [d for d in [own_export_date] if d]
    if own_worldwide_date and own_worldwide_date != own_export_date:
        date_bits.append(f"Worldwide as of {own_worldwide_date}")
    source = f"Semrush export ({'; '.join(date_bits)})" if date_bits else "Semrush export"
    return _table_slide(prs, "Competitor Analysis", headers, rows, col_widths=col_widths, source=source, insights=insights)


def add_competitor_positions_slides(prs: Presentation, competitor_positions: dict[str, list[dict]]):
    """One table slide per competitor domain from a Semrush Organic Research
    > Positions export — what that domain ranks for, and at what position.
    Matches the "Competitor Keywords: {domain}" slide format. Branded
    keywords (containing the competitor's own brand name) are excluded —
    the slide should only surface non-branded keyword opportunities."""
    slides = []
    for domain, rows in competitor_positions.items():
        brand = _brand_token(domain)
        rows = [r for r in rows if not _is_branded_keyword(r.get("keyword", ""), brand)]
        if not rows:
            continue
        sorted_rows = sorted(rows, key=lambda r: _num(r.get("search_volume")), reverse=True)
        table_rows = [
            (
                r.get("keyword", ""),
                f"{int(_num(r.get('search_volume'))):,}",
                r.get("keyword_difficulty", ""),
                r.get("position", ""),
                r.get("previous_position", ""),
            )
            for r in sorted_rows[:14]
        ]

        top1_10 = sum(1 for r in rows if 0 < _num(r.get("position")) <= 10)
        top_kw = sorted_rows[0]
        rising = [r for r in rows if 0 < _num(r.get("position")) < _num(r.get("previous_position") or r.get("position"))]
        insights = [
            f"Ranks in the top 10 for {top1_10} of {len(rows)} tracked keywords — {top1_10 / len(rows) * 100:.0f}% of their visible footprint.",
            f"Highest-volume keyword: \"{top_kw.get('keyword')}\" at position {top_kw.get('position')}, {int(_num(top_kw.get('search_volume'))):,} monthly searches.",
        ]
        if rising:
            insights.append(f"{len(rising)} keyword(s) climbing in rank — worth watching where they're pulling traffic from.")

        slide = _table_slide(
            prs, f"Competitor Keywords: {domain}",
            ["Keyword", "Search Volume", "KD", "Position", "Previous Position"], table_rows,
            col_widths=[5.0, 2.0, 1.5, 1.9, 1.9], source="Semrush — Organic Research: Positions",
            insights=insights,
        )
        slides.append(slide)
    return slides


def _cross_competitor_keyword_insights(competitor_positions: dict[str, list[dict]]) -> list[str]:
    """Deterministic (no AI — same reliability bar as the rest of this
    file's insight bullets) findings computed across ALL competitors' FULL
    keyword sets at once, not just each one's individual top-14 table.
    Branded keywords already excluded per-domain by the caller before this
    runs, matching the non-branded-opportunities-only convention used
    elsewhere in this section."""
    non_empty = {d: rows for d, rows in competitor_positions.items() if rows}
    if len(non_empty) < 2:
        return []

    insights = []
    footprint = {
        d: sum(1 for r in rows if 0 < _num(r.get("position")) <= 10)
        for d, rows in non_empty.items()
    }
    leader = max(footprint, key=footprint.get)
    insights.append(
        f"{leader} has the largest page-1 footprint across all tracked competitors — "
        f"{footprint[leader]} keywords in the top 10 (out of {len(non_empty[leader])} tracked)."
    )

    keyword_sets = {d: {r.get("keyword", "").lower() for r in rows if r.get("keyword")} for d, rows in non_empty.items()}
    shared_all = set.intersection(*keyword_sets.values())
    if shared_all:
        insights.append(
            f"{len(shared_all)} keyword(s) are contested by every tracked competitor — "
            "the core battleground terms for this category."
        )

    keyword_counts: dict[str, int] = {}
    for kws in keyword_sets.values():
        for kw in kws:
            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
    majority = len(non_empty) // 2 + 1
    contested_by_most = sum(1 for count in keyword_counts.values() if count >= majority)
    if contested_by_most:
        insights.append(
            f"{contested_by_most} keyword(s) rank for {majority}+ of the {len(non_empty)} tracked competitors — "
            "strong signal these are worth targeting directly."
        )

    total_tracked = sum(len(rows) for rows in non_empty.values())
    insights.append(f"{total_tracked:,} competitor keyword rows tracked in total across {len(non_empty)} domains — see the linked sheets for the full lists.")
    return insights


def add_competitor_keyword_sheets_slide(
    prs: Presentation,
    competitor_positions: dict[str, list[dict]],
    sheet_links: dict[str, str],
):
    """Replaces the old one-slide-per-competitor capped-at-14-rows table
    (add_competitor_positions_slides, kept above for the no-Sheets-
    configured fallback) with a single slide: one link per competitor to a
    Google Sheet holding that competitor's FULL keyword list (keyword,
    search volume, KD, position, previous position — everything, not just
    the top rows), plus insights computed across all competitors' complete
    keyword sets rather than just each one's truncated table."""
    slide = _blank_slide(prs)
    _content_header(slide, "Competitor Keywords — Full Data")
    _textbox(
        slide, Inches(0.4), Inches(1.05), Inches(11), Inches(0.4),
        "Complete keyword lists (not just top 10) — click a link to open the full sheet.",
        size=13, color=TEXT_MUTED,
    )

    # Real bug caught live on report 48 (Lumber, 2026-09-09): with 4
    # competitors + 4 insight bullets, this loop's own math (1.6 + 4*0.85
    # for cards, then +0.2+0.4+4*0.45 for insights) runs to y=7.4in — past
    # the footer text at ~7.1in and close to the 7.5in slide edge, so the
    # last insight bullet visually overwrote the footer. This was the only
    # text-block layout in the file with no max-height guard at all (every
    # other insights renderer here — _insights_strip — already has one).
    # Same footer clearance _insights_strip uses (SLIDE_H - Inches(0.5)).
    max_y = SLIDE_H - Inches(0.5)

    y = Inches(1.6)
    for domain, url in sheet_links.items():
        if y + Inches(0.7) > max_y:
            break
        rows = competitor_positions.get(domain) or []
        card = _card(slide, Inches(0.6), y, Inches(11.1), Inches(0.7))
        _textbox(slide, Inches(0.8), y + Inches(0.08), Inches(4), Inches(0.3), domain, size=14, bold=True)
        _textbox(slide, Inches(0.8), y + Inches(0.38), Inches(3), Inches(0.25), f"{len(rows):,} keywords tracked", size=11, color=TEXT_MUTED)
        link_box = slide.shapes.add_textbox(Inches(5.2), y + Inches(0.08), Inches(6.3), Inches(0.55))
        tf = link_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = "Open full keyword list →"
        run.font.size = Pt(13)
        run.font.color.rgb = _accent()
        run.hyperlink.address = url
        y += Inches(0.85)

    insights = _cross_competitor_keyword_insights(competitor_positions)
    if insights and y + Inches(0.6) <= max_y:
        insight_y = y + Inches(0.2)
        _textbox(slide, Inches(0.6), insight_y, Inches(4), Inches(0.3), "Key Insights", size=14, bold=True)
        insight_y += Inches(0.4)
        for text in insights:
            if insight_y + Inches(0.45) > max_y:
                break
            _textbox(slide, Inches(0.6), insight_y, Inches(11.1), Inches(0.5), f"• {text}", size=12)
            insight_y += Inches(0.45)
    return slide


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def add_keyword_gap_slide(prs: Presentation, competitor_analysis: dict):
    """Table version of semrush_analysis_service's keyword-gap detection —
    the "issues" list only surfaces one summary sentence + a single top
    example; this renders the full ranked list of keywords a competitor
    ranks for that the client doesn't (or ranks far ahead on), so it reads
    like the manual report's keyword tables instead of one line of prose.
    Includes both gap types the analysis surfaces: not ranking at all, and
    ranking so far behind a page-1 competitor it's effectively invisible."""
    rows = competitor_analysis.get("keyword_gap_rows") or []
    rows = [r for r in rows if not _is_branded_keyword(r.get("keyword", ""), _brand_token(r.get("competitor_domain", "")))]
    if not rows:
        return None

    table_rows = [
        (
            r["keyword"],
            r["competitor_domain"] or "—",
            f"#{r['competitor_position']}" if r.get("competitor_position") else "—",
            f"#{r['your_position']}" if r.get("your_position") else "Not ranking",
            f"{r['search_volume']:,}",
            r["keyword_difficulty"] if r.get("keyword_difficulty") not in (None, "") else "—",
        )
        for r in rows
    ]
    total_volume = sum(r["search_volume"] for r in rows)
    top = rows[0]
    insights = [f"{len(rows)} keyword gap(s) found, {total_volume:,} combined monthly searches."]
    if top.get("competitor_domain"):
        your_pos_text = f"you're at #{top['your_position']}" if top.get("your_position") else "you don't rank at all"
        insights.append(f"Highest-volume gap: \"{top['keyword']}\" ({top['search_volume']:,} searches) — {top['competitor_domain']} ranks #{top['competitor_position']}, {your_pos_text}.")
    else:
        insights.append(f"Highest-volume gap: \"{top['keyword']}\" ({top['search_volume']:,} searches).")
    cpcs = [r["cpc"] for r in rows if r.get("cpc") not in (None, "")]
    if cpcs:
        avg_cpc = sum(cpcs) / len(cpcs)
        insights.append(f"Avg. CPC across these gaps is ${avg_cpc:.2f} — {'strong commercial intent, worth prioritizing' if avg_cpc > 10 else 'moderate commercial intent'}.")

    return _table_slide(
        prs, "Competitor Keyword Gap Analysis", ["Keyword", "Competitor", "Their Position", "Your Position", "Search Volume", "Difficulty"], table_rows,
        col_widths=[3.7, 2.4, 1.5, 1.5, 1.7, 1.1], source="Semrush Keyword Gap export", insights=insights,
    )


def add_competitor_best_at_slide(prs: Presentation, competitor_domain: str, narrative: dict):
    """Matches the reference decks' "What {Competitor} Does Well" slide —
    objective bullets on the competitor's own tactics/strengths, grounded in
    their homepage text and metrics (narrative["best_at"]), rendered ahead
    of the "Areas of Focus" response slide. Distinct from that slide: this
    one states facts about the competitor, not advice for the client.
    Absent (no slide) if the AI narrative didn't produce best_at bullets —
    same silent-skip pattern as the rest of this file's AI-derived content."""
    best_at = narrative.get("best_at") or []
    if not best_at:
        return None
    screenshot = narrative.get("screenshot")

    slide = _blank_slide(prs)
    _content_header(slide, f"What {competitor_domain} Does Well")
    card_top, card_height = Inches(1.1), Inches(5.9)
    card_width = Inches(7.8) if screenshot else Inches(12.1)
    text_width = card_width - Inches(0.75)
    _card(slide, Inches(0.6), card_top, card_width, card_height)
    y = Inches(1.35)

    if screenshot:
        img_left, img_width = Inches(8.7), Inches(3.9)
        try:
            slide.shapes.add_picture(BytesIO(screenshot), img_left, card_top, width=img_width)
        except Exception:
            pass
        else:
            _textbox(slide, img_left, card_top + Inches(2.6), img_width, Inches(0.3), competitor_domain, size=10.5, color=TEXT_MUTED, align=PP_ALIGN.CENTER)

    bullets_max_y = card_top + card_height - Inches(0.15)
    chars_per_line = max(20, int(text_width / 914400 * 14))
    line_h = Inches(0.24)
    for item in best_at[:6]:
        lines = max(1, -(-len(item) // chars_per_line))
        item_h = line_h * lines + Inches(0.08)
        if y + item_h > bullets_max_y:
            break
        _icon_dot(slide, Inches(0.9), y + Inches(0.08), Inches(0.09), DEFAULT_ACCENT)
        _textbox(slide, Inches(1.15), y, text_width - Inches(0.25), line_h * lines, item, size=12.5)
        y += item_h
    return slide


def _opportunity_quadrant(slide, left, top, width, height, label, items, color):
    """One quadrant card of add_competitor_opportunity_slide: a label
    header plus height-budgeted bullets, same truncate-rather-than-overflow
    discipline as the rest of this file's AI-derived content. Deliberately
    conservative chars-per-line estimate (11, vs. ~13-14 used for the wider
    single-card slides elsewhere in this file) — a narrow ~5in quadrant
    column wraps on whole words, so a flat width/avg-char-width estimate
    under-counts wrapped lines more here than it does on a wide card,
    which under-reserved height and let real (longer) wrapped text run
    into the next bullet."""
    _card(slide, left, top, width, height)
    pad = Inches(0.22)
    _textbox(slide, left + pad, top + Inches(0.15), width - pad * 2, Inches(0.3), label, size=13, bold=True, color=color)
    y = top + Inches(0.55)
    max_y = top + height - Inches(0.15)
    text_width = width - pad * 2 - Inches(0.2)
    chars_per_line = max(16, int(text_width / 914400 * 11))
    line_h = Inches(0.22)
    for item in items:
        lines = max(1, -(-len(item) // chars_per_line))
        item_h = line_h * lines + Inches(0.09)
        if y + item_h > max_y:
            break
        _icon_dot(slide, left + pad, y + Inches(0.07), Inches(0.07), color)
        _textbox(slide, left + pad + Inches(0.2), y, text_width, line_h * lines, item, size=10.5)
        y += item_h


def add_competitor_opportunity_slide(prs: Presentation, client_name: str, competitor_domain: str, narrative: dict):
    """Competitor Opportunity Analysis — merged with the former standalone
    "Areas of Focus for {Client} (vs {Competitor})" slide (2026-09-07, per
    SEO team + account manager review: the two slides said the same thing
    twice — opportunity_analysis's "client_should_build" quadrant was just
    a shorter restatement of the areas_of_focus bullets below it, so that
    quadrant is dropped and the two slides folded into one instead of
    trimming content). Top: three quadrants (WHAT COMPETITOR HAS / WHAT
    CLIENT LACKS / WHY IT MATTERS) — the evidence/reasoning. Bottom: the
    areas_of_focus bulleted recommendation list plus a closing "Strategic
    Growth Opportunity" paragraph — the prescriptive advice, in Cyces'
    own brand red (not the client's brand color) since this is
    agency-authored strategic content. Height-budgeted throughout so long
    AI-generated text can't overflow into the footer. Grounded only in
    narrative["opportunity_analysis"] / ["areas_of_focus"] /
    ["growth_opportunity"] (same batched AI call, see
    competitor_narrative_service) — absent (no slide) if the AI produced
    none of it, same silent-skip pattern as every other AI-derived slide
    in this file."""
    opp = narrative.get("opportunity_analysis") or {}
    has = opp.get("competitor_has") or []
    lacks = opp.get("client_lacks") or []
    why = opp.get("why_it_matters") or []
    areas = narrative.get("areas_of_focus") or []
    opportunity = narrative.get("growth_opportunity")
    if not any([has, lacks, why, areas, opportunity]):
        return None
    screenshot = narrative.get("screenshot")

    slide = _blank_slide(prs)
    _content_header(slide, f"Competitor Opportunity Analysis: {competitor_domain}")

    gutter = Inches(0.25)
    left0, quad_top = Inches(0.6), Inches(1.1)
    total_w, quad_h = Inches(12.1), Inches(1.85)
    col_w = int((total_w - gutter * 2) / 3)
    if any([has, lacks, why]):
        _opportunity_quadrant(slide, left0, quad_top, col_w, quad_h, f"What {competitor_domain} Has", has[:3], _accent())
        _opportunity_quadrant(slide, left0 + col_w + gutter, quad_top, col_w, quad_h, f"What {client_name} Lacks", lacks[:3], BAD)
        _opportunity_quadrant(slide, left0 + (col_w + gutter) * 2, quad_top, col_w, quad_h, "Why It Matters", why[:3], WARN)
        card_top = quad_top + quad_h + Inches(0.2)
    else:
        card_top = quad_top
    card_height = Inches(7.15) - card_top
    card_width = Inches(7.8) if screenshot else Inches(12.1)
    text_width = card_width - Inches(0.75)
    _card(slide, Inches(0.6), card_top, card_width, card_height)
    y = card_top + Inches(0.25)

    if screenshot:
        img_left, img_width = Inches(8.7), Inches(3.9)
        try:
            slide.shapes.add_picture(BytesIO(screenshot), img_left, card_top, width=img_width)
        except Exception:
            pass  # corrupt/unreadable capture — skip the image, text side is unaffected
        else:
            _textbox(slide, img_left, card_top + Inches(2.6), img_width, Inches(0.3), competitor_domain, size=10.5, color=TEXT_MUTED, align=PP_ALIGN.CENTER)

    # Reserve room for at least 2 lines of the closing paragraph before
    # bullets are allowed to eat into that space.
    bullets_max_y = card_top + card_height - (Inches(0.7) if opportunity else Inches(0.15))
    chars_per_line = max(20, int(text_width / 914400 * 14))
    line_h = Inches(0.22)
    # Each area_of_focus item is now a structured recommendation object
    # (recommendation/evidence/lumber_applicability/impact/effort/kpi/
    # status — see competitor_narrative_service's prompt schema), not a
    # plain string: impact/effort/KPI/status render as a compact tag line
    # under the recommendation itself so the priority/testability info a
    # reader needs to act on it doesn't require re-deriving it elsewhere.
    # A malformed item (missing the required fields — an off-spec AI
    # response) is skipped rather than guessed at, same silent-skip
    # discipline as every other AI-derived slide in this file.
    STATUS_COLOR = {
        "Already exists": TEXT_MUTED,
        "Quick win": GOOD,
        "Not applicable": TEXT_MUTED,
    }
    for item in areas[:7]:
        if not isinstance(item, dict):
            continue
        rec = item.get("recommendation")
        if not rec:
            continue
        lines = max(1, -(-len(rec) // chars_per_line))
        tag_parts = [p for p in [
            f"Impact: {item['impact']}" if item.get("impact") else None,
            f"Effort: {item['effort']}" if item.get("effort") else None,
            item.get("status"),
            f"KPI: {item['kpi']}" if item.get("kpi") else None,
        ] if p]
        tag_line = "  ·  ".join(tag_parts)
        item_h = line_h * lines + (Inches(0.19) if tag_line else 0) + Inches(0.06)
        if y + item_h > bullets_max_y:
            break
        dot_color = STATUS_COLOR.get(item.get("status"), DEFAULT_ACCENT)
        _icon_dot(slide, Inches(0.9), y + Inches(0.07), Inches(0.08), dot_color)
        _textbox(slide, Inches(1.15), y, text_width - Inches(0.25), line_h * lines, rec, size=11.5)
        y += line_h * lines
        if tag_line:
            _textbox(slide, Inches(1.15), y, text_width - Inches(0.25), Inches(0.19), tag_line, size=8.5, color=TEXT_MUTED)
            y += Inches(0.19)
        y += Inches(0.06)

    if opportunity:
        y += Inches(0.12)
        _textbox(slide, Inches(0.9), y, text_width, Inches(0.26), "Strategic Growth Opportunity:", size=12, bold=True, color=DEFAULT_ACCENT)
        y += Inches(0.3)
        chars_per_line = max(20, int(text_width / 914400 * 15))
        available_h = (card_top + card_height) - y - Inches(0.1)
        max_lines = max(1, int(available_h / Inches(0.22)))
        max_chars = max_lines * chars_per_line
        text = opportunity if len(opportunity) <= max_chars else opportunity[: max(0, max_chars - 1)].rsplit(" ", 1)[0] + "…"
        lines = max(1, -(-len(text) // chars_per_line))
        _textbox(slide, Inches(0.9), y, text_width, Inches(0.22) * lines, text, size=11, color=TEXT_DARK)
    return slide


def add_keyword_research_slide(prs: Presentation, keyword_rows: list[dict], max_clusters: int = 10):
    """One table slide per keyword cluster (Educational Toys, Development
    Skills, etc.), matching the reference deck's "Target Keywords" format —
    instead of one flat 14-row table that drowns thousands of uploaded rows
    into a single slide. Falls back to a flat table when no Cluster column
    was present in the uploaded export."""
    # Role column marks the Primary keyword (highest search volume in the
    # cluster — same one _keyword_insights below calls "top") vs. every
    # other keyword as Secondary, matching the lead's reference flow's
    # FINAL CLUSTER -> Primary Keyword / Secondary Keywords step.
    headers = ["Keyword", "Role", "Search Volume", "Keyword Difficulty"]
    col_widths = [5.6, 1.6, 2.6, 2.3]

    clusters: dict[str, list[dict]] = {}
    for r in keyword_rows:
        label = (r.get("cluster") or "").strip()
        clusters.setdefault(label, []).append(r)

    def _keyword_insights(rows_for_group: list[dict]) -> list[str]:
        volumes = [_num(r.get("search_volume")) for r in rows_for_group]
        kds = [_num(r.get("keyword_difficulty")) for r in rows_for_group if r.get("keyword_difficulty") not in (None, "")]
        total_volume = sum(volumes)
        top = max(rows_for_group, key=lambda r: _num(r.get("search_volume")))
        easy_wins = [r for r in rows_for_group if _num(r.get("keyword_difficulty"), default=100) < 20 and _num(r.get("search_volume")) > 0]
        out = [f"{len(rows_for_group)} keywords, {total_volume:,.0f} combined monthly searches."]
        out.append(f"Top opportunity: \"{top.get('keyword')}\" — {_num(top.get('search_volume')):,.0f} searches/month, KD {top.get('keyword_difficulty', 'n/a')}.")
        page_category = top.get("page_category")
        existing_url = top.get("existing_page_url")
        if page_category and existing_url:
            out.append(f"Recommended format: {page_category} — an existing page already covers this: {existing_url}.")
        elif page_category:
            out.append(f"Recommended format: {page_category} — no existing page covers this yet, new page opportunity.")
        # Cluster validation (lead's reference flow, doc 1 final step): can
        # one page realistically satisfy every keyword in this cluster?
        # Deterministic — reuses page_category already computed per
        # keyword, no extra AI call. A single stray keyword in a different
        # format isn't treated as a real split signal (min_count=2) —
        # only flagged when there's a genuine second sub-group worth its
        # own page.
        categories_present = [r.get("page_category") for r in rows_for_group if r.get("page_category")]
        if categories_present:
            cat_counts = Counter(categories_present).most_common()
            if len(cat_counts) > 1 and cat_counts[1][1] >= 2:
                cats_text = ", ".join(f"{c} ({n})" for c, n in cat_counts)
                out.append(f"Cluster validation: mixed page formats — {cats_text}. One page likely can't satisfy all of these; consider splitting into separate pages.")
            else:
                # 2026-09-10 spec: never publish two different values for the
                # same classified field on one slide — anchor to page_category
                # (what "Recommended format" above is actually built on)
                # instead of independently re-deriving the mode, which could
                # disagree when the top-volume keyword's own format wasn't
                # the cluster's most common one.
                out.append(f"Cluster validation: consistent format ({page_category or cat_counts[0][0]}) — one page can reasonably target every keyword here.")
        if kds:
            out.append(f"Avg. keyword difficulty {sum(kds) / len(kds):.0f} — {'competitive cluster, prioritize content depth over volume' if sum(kds) / len(kds) > 40 else 'low-competition cluster, faster to rank in'}.")
        if easy_wins:
            out.append(f"{len(easy_wins)} low-difficulty (KD<20) keyword(s) with real search volume — quick-win content targets.")
        cpcs = [_num(r.get("cpc")) for r in rows_for_group if r.get("cpc") not in (None, "")]
        commercial = [r for r in rows_for_group if "commercial" in str(r.get("intent", "")).lower() or "transactional" in str(r.get("intent", "")).lower()]
        if cpcs and commercial:
            avg_cpc = sum(cpcs) / len(cpcs)
            out.append(f"Avg. CPC ${avg_cpc:.2f}, {len(commercial)} of {len(rows_for_group)} keyword(s) show commercial intent — prioritize these for conversion-focused pages.")
        elif cpcs:
            avg_cpc = sum(cpcs) / len(cpcs)
            out.append(f"Avg. CPC ${avg_cpc:.2f} — {'high commercial value, worth ranking organically for' if avg_cpc > 10 else 'moderate commercial value'}.")
        elif commercial:
            out.append(f"{len(commercial)} of {len(rows_for_group)} keyword(s) show commercial/transactional intent — prioritize these for conversion-focused pages.")
        return out

    if not clusters or set(clusters.keys()) == {""}:
        sorted_rows = sorted(keyword_rows, key=lambda r: _num(r.get("search_volume")), reverse=True)
        seen = set()
        deduped = []
        for r in sorted_rows:
            kw = r.get("keyword", "")
            if kw in seen:
                continue
            seen.add(kw)
            deduped.append(r)
        rows = [
            (r.get("keyword", ""), "Primary" if i == 0 else "Secondary", r.get("search_volume", ""), r.get("keyword_difficulty", ""))
            for i, r in enumerate(deduped)
        ]
        insights = _keyword_insights(deduped) if deduped else []
        return [_table_slide(prs, "Target Keywords", headers, rows, col_widths=col_widths, source="Semrush export", insights=insights)]

    # Rank clusters by combined search volume, keep the strongest ones —
    # matches the reference deck's ~8-10 cluster slides rather than dumping
    # every long-tail cluster into its own slide.
    ranked = sorted(
        clusters.items(),
        key=lambda kv: sum(_num(r.get("search_volume")) for r in kv[1]),
        reverse=True,
    )
    slides = []
    for label, rows_for_cluster in ranked[:max_clusters]:
        sorted_rows = sorted(rows_for_cluster, key=lambda r: _num(r.get("search_volume")), reverse=True)
        seen = set()
        deduped = []
        for r in sorted_rows:
            kw = r.get("keyword", "")
            if kw in seen:
                continue
            seen.add(kw)
            deduped.append(r)
        rows = [
            (r.get("keyword", ""), "Primary" if i == 0 else "Secondary", r.get("search_volume", ""), r.get("keyword_difficulty", ""))
            for i, r in enumerate(deduped)
        ]
        title = f"Target Keywords: {label}" if label else "Target Keywords"
        insights = _keyword_insights(deduped) if deduped else []
        slides.append(_table_slide(prs, title, headers, rows, col_widths=col_widths, source="Semrush export", insights=insights))
    return slides


# Industry-average organic CTR by SERP position — NOT this client's measured
# data (Semrush/GSC don't give per-keyword CTR for keywords not yet
# ranking, which is exactly the case this exists to estimate). Values from
# the widely-cited Backlinko 2023 organic CTR study — standard practice for
# this kind of projection in real SEO reports; clearly labeled as an
# estimate everywhere it's shown, never presented as measured fact.
_CTR_BY_POSITION = {
    1: 0.317, 2: 0.247, 3: 0.187, 4: 0.136, 5: 0.095,
    6: 0.062, 7: 0.042, 8: 0.031, 9: 0.025, 10: 0.022,
}
_CTR_BEYOND_PAGE_ONE = 0.01


def _ctr_for_position(position) -> float:
    pos = _num(position)
    if pos <= 0:
        return 0.0
    return _CTR_BY_POSITION.get(round(pos), _CTR_BEYOND_PAGE_ONE)


def add_keyword_opportunity_slide(prs: Presentation, keyword_rows: list[dict], max_rows: int = 14):
    """Doc 2 of the lead's reference flow, run end to end: for each
    cluster's Primary keyword (same clustering add_keyword_research_slide
    uses) — Current Ranking -> Opportunity Score -> Priority tier ->
    Recommendation -> Expected KPI. Real data (current_position/current_url
    from a Keyword Gap export's own-domain column, when uploaded) feeds
    everything except the CTR benchmark used for the KPI projection, which
    is an industry-average estimate by design — no per-keyword CTR exists
    for a keyword this client doesn't yet rank for. Returns None if no
    cluster has enough data (current_position OR page_category) to score."""
    clusters: dict[str, list[dict]] = {}
    for r in keyword_rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)
    if not clusters:
        return None

    candidates = []
    for label, rows_for_cluster in clusters.items():
        primary = max(rows_for_cluster, key=lambda r: _num(r.get("search_volume")))
        volume = _num(primary.get("search_volume"))
        if volume <= 0:
            continue
        position = primary.get("current_position")
        kd = primary.get("keyword_difficulty")

        # Gap Factor — how much headroom is left to capture. Not ranking at
        # all leaves the most room; already top-3 leaves the least.
        pos_num = _num(position)
        if pos_num <= 0:
            gap_factor = 1.0
        elif pos_num > 20:
            gap_factor = 0.9
        elif pos_num > 10:
            gap_factor = 0.7
        elif pos_num > 3:
            gap_factor = 0.4
        else:
            gap_factor = 0.15

        # Feasibility — lower keyword difficulty means the volume is more
        # realistically capturable; unknown KD is treated as neutral rather
        # than penalized (we simply don't know, not evidence of hard).
        kd_num = _num(kd) if kd not in (None, "") else None
        feasibility = (100 - kd_num) / 100 if kd_num is not None else 0.5

        score = volume * gap_factor * feasibility

        existing_url = primary.get("current_url") or primary.get("existing_page_url")
        if pos_num <= 3 and pos_num > 0:
            recommendation, target_position = "Monitor", max(1, int(pos_num) - 1)
        elif 3 < pos_num <= 10:
            recommendation, target_position = "Expand Content", 3
        elif pos_num > 10:
            recommendation, target_position = "Optimize Existing Page", 10
        elif existing_url:
            # A page already covers this topic but isn't ranking at all —
            # likely targeting/intent mismatch rather than a content gap.
            recommendation, target_position = "Re-Align Page", 10
        else:
            recommendation, target_position = "Create New Page", 10

        current_clicks = volume * _ctr_for_position(pos_num)
        expected_clicks = volume * _ctr_for_position(target_position)
        clicks_gain = expected_clicks - current_clicks
        growth_pct = (clicks_gain / current_clicks * 100) if current_clicks > 0 else None

        candidates.append({
            "cluster": label, "keyword": primary.get("keyword", ""), "volume": volume,
            "position": pos_num if pos_num > 0 else None, "score": score,
            "recommendation": recommendation, "target_position": target_position,
            "expected_clicks": expected_clicks, "clicks_gain": clicks_gain, "growth_pct": growth_pct,
        })

    if not candidates:
        return None

    candidates.sort(key=lambda c: c["score"], reverse=True)
    # Priority tiers are relative to THIS client's own opportunity set (top
    # third / middle third / bottom third of scores) rather than fixed
    # thresholds — self-normalizing across wildly different site sizes and
    # industries instead of assuming what "high volume" means universally.
    n = len(candidates)
    high_cut = max(1, n // 3)
    medium_cut = max(high_cut + 1, (2 * n) // 3)
    for i, c in enumerate(candidates):
        c["priority"] = "High" if i < high_cut else ("Medium" if i < medium_cut else "Low")

    shown = candidates[:max_rows]
    rows = [
        (
            c["keyword"], c["position"] if c["position"] else "Not ranking", c["priority"],
            c["recommendation"], f"#{c['target_position']}",
            f"{c['expected_clicks']:,.0f}" if c["expected_clicks"] else "—",
            f"+{c['growth_pct']:.0f}%" if c["growth_pct"] is not None else "New",
        )
        for c in shown
    ]
    high_count = sum(1 for c in candidates if c["priority"] == "High")
    top = candidates[0]
    top_position_text = f"currently position {top['position']:.0f}" if top["position"] else "currently not ranking"
    insights = [
        f"{high_count} High-priority opportunity cluster(s) out of {n} scored.",
        f"Top opportunity: \"{top['keyword']}\" — {top_position_text}, targeting #{top['target_position']}, est. {top['expected_clicks']:,.0f} monthly clicks.",
        "Expected clicks use industry-average CTR by position (Backlinko study), not this client's measured data — a projection, not a guarantee.",
    ]
    # col_widths previously summed to 13.1in — on a 12.1in-wide table area
    # (left margin 0.6in on a 13.33in-wide slide) that ran the table's right
    # edge to 13.7in, off the slide's right edge. Rescaled to sum to 12.1in,
    # matching every other table in this file.
    return _table_slide(
        prs, "Keyword Opportunity Analysis",
        ["Keyword", "Current Position", "Priority", "Recommendation", "Target", "Est. Monthly Clicks", "Growth"],
        rows, col_widths=[3.1, 1.6, 1.2, 2.0, 1.0, 1.8, 1.4],
        source="Semrush Keyword Gap + industry-benchmark CTR", insights=insights,
    )


def add_backlink_profile_slide(
    prs: Presentation, backlink_rows: list[dict], row_count: int, backlink_summary: dict | None = None,
    own_domain_rating: int | None = None,
):
    """Ahrefs/Semrush-widget-style summary — Backlinks, Referring Domains,
    Domain Rating, % dofollow, plus a Link Attributes breakdown when a
    Semrush Backlink List PDF summary was uploaded (backlink_summary). That
    export's aggregate stats are a real site-wide count, more authoritative
    than what's computed from a possibly-partial backlinks CSV, so they're
    preferred wherever both are available.

    Domain Rating itself is NOT sourced from Semrush at all (2026-08-28
    decision) — the manual report uses Ahrefs DR, a different metric on a
    different scale than Semrush's Authority Score, and Ahrefs has no free
    bulk/API access, so own_domain_rating comes from the user's manually
    entered DomainRating table, same as the Competitor Analysis DR column."""
    slide = _blank_slide(prs)
    _content_header(slide, "Backlink Profile")
    if backlink_summary and backlink_summary.get("export_date"):
        _textbox(
            slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4),
            f"Source: Semrush Backlink List ({backlink_summary['export_date']})", size=11, color=TEXT_MUTED,
        )

    dofollow = sum(1 for r in backlink_rows if str(r.get("nofollow", "")).strip().lower() not in ("1", "true", "yes"))
    csv_pct_dofollow = round(100 * dofollow / len(backlink_rows)) if backlink_rows else None

    ref_domains = {urlparse(r["source_url"]).netloc for r in backlink_rows if r.get("source_url")}

    total_backlinks = backlink_summary["backlinks_total"] if backlink_summary else (row_count or None)
    total_referring_domains = backlink_summary["referring_domains"] if backlink_summary else (len(ref_domains) or None)
    pct_dofollow = backlink_summary.get("follow_pct") if backlink_summary and backlink_summary.get("follow_pct") is not None else csv_pct_dofollow
    authority_score = own_domain_rating
    authority_label = "Domain Rating"

    # Distinct from Domain Rating above (that's the site's own manually-
    # entered Ahrefs DR) — this averages each individual referring domain's
    # own authority/page score (domain_score, parsed per backlink row from
    # the Semrush CSV) to give a read on the QUALITY of sites linking in,
    # not the client's own authority.
    domain_scores = [float(r["domain_score"]) for r in backlink_rows if r.get("domain_score") not in (None, "")]
    avg_authority_score = round(sum(domain_scores) / len(domain_scores), 1) if domain_scores else None

    _card(slide, Inches(0.6), Inches(1.2), Inches(12.1), Inches(2.2))
    stats = [
        ("Backlinks", f"{int(total_backlinks):,}" if total_backlinks is not None else "—", f"{pct_dofollow:.0f}% dofollow" if pct_dofollow is not None else None),
        ("Referring domains", f"{int(total_referring_domains):,}" if total_referring_domains is not None else "—", None),
        (authority_label, str(int(authority_score)) if authority_score is not None else "—", None),
        ("Avg. authority score", f"{avg_authority_score:g}" if avg_authority_score is not None else "—", "of referring domains" if avg_authority_score is not None else None),
    ]
    card_w = Inches(2.75)
    gap = Inches(0.15)
    for i, (label, value, sub) in enumerate(stats):
        left = Inches(0.9) + Emu(i * (card_w + gap))
        _textbox(slide, left, Inches(1.4), card_w, Inches(0.35), label, size=13, color=TEXT_MUTED)
        _textbox(slide, left, Inches(1.8), card_w, Inches(0.8), value, size=30, bold=True, color=_accent())
        if sub:
            _textbox(slide, left, Inches(2.65), card_w, Inches(0.3), sub, size=12, color=TEXT_MUTED)

    y = Inches(3.65)
    attr_labels = ["Follow", "Nofollow", "Sponsored", "UGC"]
    has_attrs = backlink_summary and any(backlink_summary.get(f"{label.lower()}_count") is not None for label in attr_labels)
    if has_attrs:
        _card(slide, Inches(0.6), y, Inches(12.1), Inches(1.1))
        _textbox(slide, Inches(0.9), y + Inches(0.15), Inches(4), Inches(0.3), "Link Attributes", size=13, bold=True, color=_accent())
        attr_colors = {"follow": GOOD, "nofollow": WARN, "sponsored": TEXT_MUTED, "ugc": TEXT_MUTED}
        for i, label in enumerate(attr_labels):
            key = label.lower()
            count = backlink_summary.get(f"{key}_count")
            pct = backlink_summary.get(f"{key}_pct")
            if count is None:
                continue
            left = Inches(0.9) + Emu(i * Inches(2.9))
            _icon_dot(slide, left, y + Inches(0.62), Inches(0.11), attr_colors.get(key, TEXT_MUTED))
            _textbox(slide, left + Inches(0.22), y + Inches(0.5), Inches(2.6), Inches(0.3), f"{label}: {int(count):,} ({pct}%)", size=12, color=TEXT_DARK)
        y += Inches(1.35)
    else:
        y += Inches(0.2)

    insights = []
    if total_backlinks and total_referring_domains:
        links_per_domain = total_backlinks / total_referring_domains
        if links_per_domain > 5:
            insights.append(f"{links_per_domain:.1f} backlinks per referring domain — link profile is concentrated in a few sources, worth diversifying.")
        else:
            insights.append(f"{int(total_referring_domains)} distinct referring domains behind {int(total_backlinks)} backlinks — reasonably diverse source spread.")
    if pct_dofollow is not None:
        if pct_dofollow < 50:
            insights.append(f"Only {pct_dofollow:.0f}% of backlinks are dofollow — most links here aren't passing ranking authority.")
        else:
            insights.append(f"{pct_dofollow:.0f}% of backlinks are dofollow — the majority are passing ranking authority.")
    if authority_score is not None:
        insights.append(f"Domain Rating is {int(authority_score)} — {'a strong, established domain' if authority_score >= 40 else 'still building authority, prioritize link acquisition'}.")
    if avg_authority_score is not None:
        insights.append(f"Referring domains average an authority score of {avg_authority_score:g} — {'links are coming from generally reputable sites' if avg_authority_score >= 30 else 'link quality is on the lower end, prioritize higher-authority placements'}.")
    _insights_strip(slide, Inches(0.6), y, Inches(11.9), insights)
    return slide


# A generic, high-authority set of listing/review directories worth a
# submission for most B2B sites (matches the manual reference decks'
# "Current Brand Mentions" recommendations section — SPOTONIX itself
# recommended this same style of generic directory list, not a bespoke
# per-client set). Deliberately NOT a claim about whether the client is
# already listed anywhere — that would need a live search/citation-lookup
# API this app doesn't have, and this file's "never invent data" discipline
# rules out guessing. This is the submission-recommendation half only.
_BRAND_DIRECTORY_RECOMMENDATIONS = [
    ("G2", "Buyer-intent software marketplace — strong for B2B SaaS comparison shoppers."),
    ("Capterra", "Gartner-owned software directory — high-intent traffic, category-specific listings."),
    ("TrustRadius", "In-depth review platform enterprise buyers check before a demo call."),
    ("SoftwareSuggest", "Regional/vertical software directory — useful for reaching underserved markets."),
    ("Crunchbase", "Company profile indexed by AI/LLM training and citation sources — strengthens entity recognition."),
    ("Trustpilot", "General-purpose review platform — builds the trust signals AI Overviews and shoppers both check."),
]


def add_brand_mentions_slide(
    prs: Presentation, client_name: str, citations: list[dict] | None = None, wikipedia: dict | None = None,
):
    """Brand citation/directory-listing opportunities — the manual
    reference decks' "Current Brand Mentions" slide. When citations/
    wikipedia are supplied (see brand_citation_service — free, keyless
    DuckDuckGo/Google News + Wikipedia lookups), a "Where You're
    Already Cited" section leads the slide with grounded, real results.
    Silently falls back to directory-recommendations-only (the original
    version of this slide) when neither is available — never invents a
    citation that wasn't actually found, same discipline as the rest of
    this file."""
    citations = citations or []
    slide = _blank_slide(prs)
    _content_header(slide, "Brand Citation Opportunities")
    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.9))
    max_y = Inches(6.9)
    y = Inches(1.3)

    if citations or wikipedia:
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.3), "Where You're Already Cited", size=13, bold=True, color=_accent())
        y += Inches(0.36)
        if wikipedia:
            _icon_dot(slide, Inches(0.9), y + Inches(0.07), Inches(0.08), GOOD)
            _textbox(slide, Inches(1.15), y, Inches(11), Inches(0.3), f"Wikipedia: \"{wikipedia['title']}\"", size=11.5)
            y += Inches(0.3)
        for c in citations[:4]:
            if y > max_y - Inches(0.5):
                break
            _icon_dot(slide, Inches(0.9), y + Inches(0.07), Inches(0.08), GOOD)
            domain = urlparse(c["url"]).netloc
            _textbox(slide, Inches(1.15), y, Inches(11), Inches(0.3), f"{c['title']} — {domain}", size=11.5)
            y += Inches(0.3)
        y += Inches(0.25)

    _textbox(
        slide, Inches(0.9), y, Inches(11.5), Inches(0.5),
        "Getting listed on high-authority directories builds the trust signals both human buyers and AI answer "
        "engines check before recommending a brand.", size=12.5, color=TEXT_MUTED,
    )
    y += Inches(0.6)
    for name, blurb in _BRAND_DIRECTORY_RECOMMENDATIONS:
        if y > max_y - Inches(0.2):
            break
        _icon_dot(slide, Inches(0.9), y + Inches(0.08), Inches(0.09), _accent())
        _textbox(slide, Inches(1.15), y, Inches(2.4), Inches(0.3), name, size=13, bold=True)
        _textbox(slide, Inches(3.7), y, Inches(8.3), Inches(0.5), blurb, size=12, color=TEXT_DARK)
        y += Inches(0.62)
    _textbox(
        slide, Inches(0.9), min(y + Inches(0.1), max_y), Inches(11.3), Inches(0.4),
        f"Submit {client_name} to the directories most relevant to its category first — a targeted, complete "
        "profile beats a partial listing on every site at once.", size=11, color=TEXT_MUTED,
    )
    return slide


def _derive_next_steps(site_audit: dict | None, page_audit: dict | None) -> list[str]:
    steps = []
    if site_audit:
        for issue in site_audit.get("issues", []):
            if "https" in issue.lower():
                steps.append("Move the site fully to HTTPS — browsers flag non-HTTPS pages as insecure.")
            elif "robots" in issue.lower():
                steps.append("Add a robots.txt file so search engines can crawl the site predictably.")
            elif "sitemap" in issue.lower():
                steps.append("Publish a valid sitemap.xml and submit it in Google Search Console.")
            elif "title" in issue.lower():
                steps.append("Fix missing/oversized <title> tags — keep titles under 60 characters and unique per page.")
            elif "meta description" in issue.lower():
                steps.append("Add unique meta descriptions to every page to improve click-through from search results.")
            elif "h1" in issue.lower():
                steps.append("Ensure every page has exactly one <h1> describing its main topic.")
            elif "viewport" in issue.lower():
                steps.append("Add a mobile viewport meta tag — required for mobile usability and rankings.")
            elif "canonical" in issue.lower():
                steps.append("Add canonical tags to prevent duplicate-content issues.")
    if page_audit and page_audit.get("pages_with_issues"):
        steps.append(
            f"{page_audit['pages_with_issues']} of {page_audit['pages_checked']} crawled pages have on-page "
            "issues (missing titles/descriptions) — work through the page list and fix each."
        )
    # de-dupe while preserving order
    seen = set()
    unique = []
    for s in steps:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def add_domain_strategy_slide(prs: Presentation, domain_strategy: dict):
    """Domain Strategy finding — generic-TLD vs. single-target-country
    mismatch. Framed as a tradeoff explainer plus an open question, since
    whether to migrate depends on the client's expansion plans, which a
    crawl has no way to know."""
    slide = _blank_slide(prs)
    _content_header(slide, "Domain Strategy")
    card = _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))

    _textbox(slide, Inches(0.9), Inches(1.35), Inches(11.4), Inches(0.3), "Finding", size=13, bold=True, color=_accent())
    _textbox(slide, Inches(0.9), Inches(1.7), Inches(11.4), Inches(2.0), domain_strategy["finding"], size=12.5)

    _textbox(slide, Inches(0.9), Inches(4.0), Inches(11.4), Inches(0.3), "Open Question for the Client", size=13, bold=True, color=_accent())
    _textbox(slide, Inches(0.9), Inches(4.35), Inches(11.4), Inches(2.0), domain_strategy["open_question"], size=12.5)
    return slide


def add_ux_findings_slides(prs: Presentation, ux_findings: dict) -> list:
    """UI-Level Fixes (Issue/Where/Fix/Severity) — real ui_fixes render
    whenever present, regardless of whether a manual UX pass was ever done
    (2026-09-09: no longer gated on no_ux_pass_done — that flag sat this
    slide on a permanent fallback message since no reviewer had ever
    actually typed manual notes in for a real client; ui_fixes now comes
    from a vision pass over a real homepage screenshot instead, see
    ux_findings_service.generate_ui_fixes_from_screenshot). The "no pass
    done" message only shows when there's genuinely nothing — no ui_fixes
    from either source (report spec Rule 8: state the gap explicitly
    rather than silently skip the dimension).

    Conversion Opportunities (from the same ux_findings dict) render on
    their own "Next Steps: Conversion SEO" slide instead — see
    add_conversion_seo_next_steps_slide. That field is still manual-notes-
    only, unaffected by the ui_fixes change above.

    Onboarding Breakdown (below) is its own separate vision pass over the
    same screenshot."""
    slides = []

    fixes = ux_findings.get("ui_fixes") or []
    if fixes:
        rows = [(f.get("issue", ""), f.get("where", ""), f.get("fix", ""), f.get("severity", "")) for f in fixes]
        critical = sum(1 for f in fixes if (f.get("severity") or "").lower() == "critical")
        source = "Homepage screenshot analysis" if ux_findings.get("ui_fixes_source") == "vision" else "Manual UX walkthrough"
        insights = [f"{len(fixes)} UI issue(s) found."]
        if critical:
            insights.append(f"{critical} flagged Critical — these block a purchase or signup and should be fixed first.")
        slides.append(_table_slide(
            prs, "UI-Level Fixes", ["Issue", "Where", "Fix", "Severity"], rows,
            col_widths=[3.4, 2.8, 4.4, 1.5], source=source, insights=insights,
        ))
    elif ux_findings.get("note"):
        slide = _blank_slide(prs)
        _content_header(slide, "UI-Level Fixes")
        _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(2.0))
        _textbox(slide, Inches(0.9), Inches(1.4), Inches(11.4), Inches(1.4), ux_findings["note"], size=13)
        slides.append(slide)

    # Onboarding-bias breakdown of the landing page — separate slide from
    # UI-Level Fixes (that one is broken/missing things; this one is "the
    # page works but is fighting the visitor's psychology"), per the
    # team-lead prompt: cover onboarding biases, top 5, directional
    # suggestions. Sourced from a real homepage screenshot when the manual
    # notes above didn't already supply one — see the module docstring.
    breakdown = ux_findings.get("onboarding_breakdown") or []
    if breakdown:
        rows = [(b.get("bias", ""), b.get("where", ""), b.get("suggestion", "")) for b in breakdown[:5]]
        slides.append(_table_slide(
            prs, "Onboarding Breakdown — Landing Page", ["Bias", "Where It Shows Up", "Directional Suggestion"], rows,
            col_widths=[2.6, 3.6, 5.9], source="Homepage screenshot analysis", row_height=0.6, wrap_cols={0, 1, 2},
            insights=[f"Top {len(rows)} onboarding-psychology gap(s) on the landing page, ranked by likely impact on sign-up/purchase completion."],
        ))

    return slides


def _next_steps_category_slide(prs: Presentation, title: str, intro: str | None, items: list[str]):
    """Shared renderer for the 4 Next Steps category slides (Local/Technical/
    Content/Conversion SEO) and the split AEO/GEO slides — one full-width
    numbered list, roomier per item than the old combined roadmap slide
    since each slide now covers only one category."""
    if not items:
        return None
    slide = _blank_slide(prs)
    _content_header(slide, title)
    y = Inches(1.05)
    if intro:
        _textbox(slide, Inches(0.6), y, Inches(12.1), Inches(0.4), intro, size=12, color=TEXT_MUTED)
        y += Inches(0.45)
    card_top = y
    card_bottom = Inches(6.7)
    _card(slide, Inches(0.6), card_top, Inches(12.1), card_bottom - card_top)
    y = card_top + Inches(0.25)
    for i, item in enumerate(items, start=1):
        lines = _wrap_lines(item, Inches(11.0), size_pt=13)
        line_h = Inches(13 * 0.02) * lines
        if y + line_h > card_bottom - Inches(0.15):
            break
        num = slide.shapes.add_textbox(Inches(0.85), y, Inches(0.4), Inches(0.3))
        p = num.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = f"{i}."
        r.font.bold = True
        r.font.size = Pt(13)
        r.font.color.rgb = _accent()
        _textbox(slide, Inches(1.25), y, Inches(11.0), line_h, item, size=13)
        y += line_h + Inches(0.22)
    return slide


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "page"


# Eligibility floor for calling something "Programmatic SEO" rather than
# just "add a couple more pages" — teammate QA on the last report flagged
# this slide as recommending page generation with no eligibility rules at
# all (every 2+-keyword cluster got a hub+sub-page recommendation
# regardless of real scale or intent uniqueness). Real programmatic SEO
# implies a genuine template pattern across enough distinct pages to be
# worth building infrastructure for — below this, it's just normal
# content work, already covered by the Content SEO Next Steps slide.
_PROGRAMMATIC_MIN_SUBPAGES = 3
_PROGRAMMATIC_MIN_CLUSTER_VOLUME = 300
# Two sub-keywords whose token sets overlap this much are the same search
# intent wearing different phrasing (e.g. "certified payroll software" vs
# "certified payroll software tool") — templating them as two separate
# pages is exactly the thin/duplicate-content risk (SERP non-uniqueness)
# the eligibility flow asks to rule out before recommending page
# generation, not a second real sub-page.
_PROGRAMMATIC_DEDUP_OVERLAP = 0.6


def _keyword_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def add_programmatic_seo_slide(prs: Presentation, keyword_rows: list[dict] | None):
    """Hub + sub-page content-architecture recommendations — matches the
    manual reference deck's "Programmatic SEO Opportunities" slide (one main
    hub page per topic, sub-pages beneath it targeting specific keywords).
    Built from the keyword clusters already identified for the Target
    Keywords slides, gated by real eligibility rules first: enough
    genuinely distinct sub-keywords to justify a template (not just 2 near-
    duplicate phrasings), and real demand behind the cluster as a whole."""
    if not keyword_rows:
        return None
    clusters: dict[str, list[dict]] = {}
    for r in keyword_rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)
    if not clusters:
        return None

    ranked = sorted(clusters.items(), key=lambda kv: sum(_num(r.get("search_volume")) for r in kv[1]), reverse=True)
    items = []
    for label, rows_for_cluster in ranked:
        cluster_volume = sum(_num(r.get("search_volume")) for r in rows_for_cluster)
        if cluster_volume < _PROGRAMMATIC_MIN_CLUSTER_VOLUME:
            continue  # demand eligibility: not enough real search volume behind this topic to template

        hub_slug = _slugify(label)
        hub_tokens = _keyword_tokens(label)
        top_keywords = sorted(rows_for_cluster, key=lambda r: _num(r.get("search_volume")), reverse=True)
        sub_slugs: list[str] = []
        sub_token_sets: list[set[str]] = []
        for r in top_keywords:
            keyword = r.get("keyword", "")
            slug = _slugify(keyword)
            if not slug or slug == hub_slug or slug in sub_slugs:
                continue
            # Compare only the DISTINCTIVE tokens (cluster-label words like
            # "certified payroll" stripped out first) — every keyword in a
            # cluster shares those by definition, so comparing full token
            # sets flagged nearly every pair in the cluster as "overlapping"
            # regardless of real intent difference (confirmed: collapsed a
            # 5-distinct-intent cluster down to 1). Comparing what's left
            # after the shared topic is stripped is the actual SERP-
            # uniqueness signal — same distinctive word(s) means same
            # intent, different distinctive words means a different page.
            tokens = _keyword_tokens(keyword) - hub_tokens
            if not tokens:
                continue  # nothing left but the cluster topic itself — same intent as the hub page
            is_near_duplicate = any(
                len(tokens & seen) / max(1, min(len(tokens), len(seen))) >= _PROGRAMMATIC_DEDUP_OVERLAP
                for seen in sub_token_sets
            )
            if is_near_duplicate:
                continue
            sub_slugs.append(slug)
            sub_token_sets.append(tokens)
            if len(sub_slugs) == 4:
                break
        if len(sub_slugs) < _PROGRAMMATIC_MIN_SUBPAGES:
            continue  # not enough genuinely distinct sub-pages to call this a template pattern

        subpages = ", ".join(f"/{hub_slug}/{s}" for s in sub_slugs)
        items.append(
            f"{label} ({int(cluster_volume):,} combined monthly searches, {len(sub_slugs)} distinct sub-intents "
            f"eligible): main hub page /{hub_slug}, with sub-pages {subpages}. Each sub-page needs genuinely "
            "unique content per intent — canonical/noindex any page that ends up too similar to another rather "
            "than publishing near-duplicates."
        )
    if not items:
        return None

    intro = (
        "Only clusters that clear real eligibility for a template pattern — enough distinct search intent and "
        "demand to justify hub+sub-page infrastructure, not a couple of near-duplicate phrasings."
    )
    return _next_steps_category_slide(prs, "Programmatic SEO Opportunities", intro, items)


def add_goals_slide(
    prs: Presentation,
    own_domain_rating: int | None,
    competitor_rows: list[dict] | None,
    keyword_rows: list[dict] | None,
):
    """Early Stage / Advanced Stage target slide, matching the manual
    reference deck's closing "Goal" page. Early Stage targets are derived
    from data already gathered elsewhere in the report (low-difficulty
    keywords on the table, current Domain Rating vs. the strongest tracked
    competitor's) — Advanced Stage stays qualitative/process-oriented, same
    as the reference deck's own advanced-stage bullets (SERP features, AI
    answer visibility, brand-authority signals), since those aren't
    something a crawl or export can size numerically."""
    early = []
    if keyword_rows:
        low_kd = [
            r for r in keyword_rows
            if r.get("keyword_difficulty") not in (None, "") and _num(r.get("keyword_difficulty"), default=100) < 30
        ]
        if low_kd:
            volume = sum(_num(r.get("search_volume")) for r in low_kd)
            early.append(
                f"Rank on page 1 (top 10) for the {len(low_kd)} target keyword(s) already identified under "
                f"KD 30 — {volume:,.0f} combined monthly searches on the table today."
            )
    if own_domain_rating is not None:
        competitor_drs = [
            _num(r.get("authority_score")) for r in (competitor_rows or [])
            if r.get("authority_score") not in (None, "") and r.get("domain")
        ]
        leader_dr = max(competitor_drs) if competitor_drs else None
        if leader_dr and leader_dr > own_domain_rating:
            target = min(int(leader_dr), own_domain_rating + 20)
            early.append(
                f"Increase Domain Rating from {own_domain_rating} toward {target} — the strongest tracked "
                f"competitor sits at DR {int(leader_dr)}."
            )
        else:
            early.append(f"Increase Domain Rating from {own_domain_rating} to {own_domain_rating + 15}+ through consistent, relevant backlink acquisition.")
    if not early:
        return None
    early.append("Drive consistent month-over-month organic traffic growth from the keyword and content work above.")

    advanced = [
        "Rank for high-difficulty (KD 50+) category keywords once the page-1 foundation from Early Stage is established.",
        "Diversify traffic beyond traditional search — earn visibility in AI answers (ChatGPT, Gemini, Claude) alongside classic search results.",
        "Win SERP features: featured snippets, AI Overviews, and image search placements.",
        "Build brand-authority signals — directory/citation listings, LinkedIn referral traffic, and steady backlink growth toward the category-leading Domain Rating.",
    ]
    items = [f"Early Stage: {b}" for b in early] + [f"Advanced Stage: {b}" for b in advanced]
    intro = "Near-term targets build the foundation; advanced-stage targets compound on them once page-1 rankings and a stronger Domain Rating are in place."
    return _next_steps_category_slide(prs, "SEO Goals & Targets", intro, items)


def add_technical_seo_next_steps_slide(
    prs: Presentation,
    site_audit: dict | None,
    page_audit: dict | None,
    tech_stack: dict | None,
    domain_strategy: dict | None,
):
    items = list(_derive_next_steps(site_audit, page_audit))
    if tech_stack and tech_stack.get("https") is False and not any("HTTPS" in i for i in items):
        items.append("Move the site fully to HTTPS before any further SEO work — it's a baseline ranking and trust signal.")
    if domain_strategy:
        items.insert(0, f"Decide the domain strategy first — {domain_strategy['open_question']}")
    intro = "Foundational and on-page fixes that unblock every other SEO effort — tackle these first."
    return _next_steps_category_slide(prs, "Next Steps: Technical SEO", intro, items)


def add_content_seo_next_steps_slide(prs: Presentation, keyword_rows: list[dict] | None):
    items = []
    if keyword_rows:
        # WHAT KIND of page each keyword calls for (format), separate from
        # WHAT TOPIC it belongs to (the cluster bullets below) — a client
        # can need both a landing page AND a guide for the same topic.
        category_counts: dict[str, int] = {}
        category_volume: dict[str, float] = {}
        category_examples: dict[str, list[str]] = {}
        for r in keyword_rows:
            keyword = r.get("keyword")
            if not keyword:
                continue
            category = _classify_keyword_page_category(keyword, r.get("intent"))
            if not category:
                continue
            category_counts[category] = category_counts.get(category, 0) + 1
            category_volume[category] = category_volume.get(category, 0) + _num(r.get("search_volume"))
            examples = category_examples.setdefault(category, [])
            if len(examples) < 2:
                examples.append(keyword)

        category_action = {label: action for label, _signals, action in _KEYWORD_PAGE_CATEGORIES}
        for label, count in sorted(category_counts.items(), key=lambda kv: -category_volume.get(kv[0], 0)):
            vol = category_volume.get(label, 0)
            vol_text = f", {int(vol):,} combined monthly searches" if vol else ""
            example_text = ", ".join(f"\"{e}\"" for e in category_examples.get(label, []))
            items.append(
                f"{count} target keyword(s) are {label}-shaped{vol_text} (e.g. {example_text}) — "
                f"{category_action[label]}."
            )

        cluster_counts: dict[str, int] = {}
        cluster_volume: dict[str, float] = {}
        cluster_rows: dict[str, list[dict]] = {}
        for r in keyword_rows:
            label = (r.get("cluster") or "").strip()
            if not label:
                continue
            cluster_counts[label] = cluster_counts.get(label, 0) + 1
            cluster_volume[label] = cluster_volume.get(label, 0) + _num(r.get("search_volume"))
            cluster_rows.setdefault(label, []).append(r)

        # Exact URL decision per cluster — teammate QA on the last report
        # flagged this slide as repeating cluster volumes without ever
        # deciding a real URL: update vs. create. existing_page_url is
        # already populated per-keyword upstream (site_audit.py's
        # match_existing_page pass, same word-overlap match the Target
        # Keywords slide uses) — reused here rather than re-matching.
        # "Update existing" only when that one URL actually covers HALF or
        # more of the cluster's own keywords — a single incidental match
        # among many unmatched keywords isn't a real update target.
        url_to_clusters: dict[str, set[str]] = {}
        for label, count in sorted(cluster_counts.items(), key=lambda kv: -cluster_volume.get(kv[0], 0)):
            vol = cluster_volume.get(label, 0)
            vol_text = f", {int(vol):,} combined monthly searches" if vol else ""
            url_counts = Counter(r["existing_page_url"] for r in cluster_rows[label] if r.get("existing_page_url"))
            if url_counts:
                target_url, matched = url_counts.most_common(1)[0]
                if matched >= max(1, count // 2):
                    action = f"update the existing page ({target_url}) rather than starting a new one"
                    url_to_clusters.setdefault(target_url, set()).add(label)
                else:
                    action = f"create a new page — the closest existing match ({target_url}) only covers {matched} of this cluster's {count} keyword(s)"
            else:
                action = "create a new page — no existing page covers this cluster's keywords"
            items.append(f"Build out content for the \"{label}\" keyword cluster — {count} keyword(s) tracked{vol_text}; {action}.")

        # Cannibalization: two or more DIFFERENT clusters both resolving to
        # the same existing URL as their update target means that one page
        # would otherwise be asked to rank for two distinct search intents
        # at once — flagged explicitly rather than silently telling the
        # client to "update" the same URL twice under different bullets.
        for url, clusters in url_to_clusters.items():
            if len(clusters) > 1:
                cluster_list = ", ".join(f"\"{c}\"" for c in sorted(clusters))
                items.append(
                    f"Cannibalization risk: {url} is the best existing match for {len(clusters)} different keyword "
                    f"clusters ({cluster_list}) — consolidate them onto that one page with clear on-page sections "
                    "per intent, or split it into separate pages, rather than letting them compete for the same query."
                )
    items.append("Audit existing content for thin or outdated pages and refresh or consolidate them to strengthen topical authority.")
    items.append("Keep a content calendar built around the highest-volume clusters above so publishing stays consistent rather than one-off.")
    intro = "Where to focus content production, based on the keyword research and clustering above."
    return _next_steps_category_slide(prs, "Next Steps: Content SEO", intro, items)


def add_local_seo_next_steps_slide(prs: Presentation, structured_data_rows: list[dict] | None):
    items = []
    if structured_data_rows:
        total = len(structured_data_rows)
        with_local = sum(1 for r in structured_data_rows if _num(r.get("local_business_items")) > 0)
        if with_local == 0:
            items.append(f"No Local Business schema found on any of the {total} crawled pages — add it to enable map/business-info rich results.")
        else:
            items.append(f"Local Business schema present on {with_local} of {total} crawled pages — extend it to any location page still missing it.")
    items += [
        "Claim and fully complete the Google Business Profile — hours, categories, services, and photos all factor into local ranking.",
        "Keep Name/Address/Phone (NAP) identical across the website, Google Business Profile, and every directory listing — inconsistencies hurt local trust signals.",
        "Build a dedicated landing page per physical location or service area, each with unique local content rather than a duplicated template.",
        "Actively request and respond to Google reviews — review volume and recency are a direct local-ranking factor.",
        "Pursue local citations and backlinks from location-relevant directories, chambers of commerce, and local press.",
    ]
    intro = "Local visibility signals — mostly checklist items a crawl can't verify directly, so the schema finding above is the one grounded data point."
    return _next_steps_category_slide(prs, "Next Steps: Local SEO", intro, items)


def add_conversion_seo_next_steps_slide(
    prs: Presentation,
    ux_findings: dict | None,
    backlink_row_count: int,
):
    items = []
    if ux_findings and not ux_findings.get("error") and not ux_findings.get("no_ux_pass_done"):
        items += list(ux_findings.get("conversion_opportunities") or [])
    if backlink_row_count:
        items.append(
            f"Turn authority growth into conversions — pair the {backlink_row_count:,} tracked backlinks with clear, "
            "tested calls-to-action on the pages that authority actually lands on."
        )
    if not items:
        return None
    intro = "Turning existing traffic into leads and sales — from the manual UX walkthrough where available."
    return _next_steps_category_slide(prs, "Next Steps: Conversion SEO", intro, items)


def add_aeo_slide(prs: Presentation, site_audit: dict | None, page_audit: dict | None):
    """Answer Engine Optimization — schema/structured-data-eligibility
    recommendations for appearing in AI Overviews and answer boxes. Split
    out from the old combined AEO & GEO slide into its own slide."""
    schema_present = None
    if site_audit and site_audit.get("meta"):
        schema_present = bool(site_audit["meta"].get("structured_data_present"))
    missing_schema_pages = None
    if page_audit and page_audit.get("pages_with_issues"):
        missing_schema_pages = page_audit.get("pages_with_issues")

    items = ["Add structured FAQ sections to every key page, answering the questions customers actually ask before buying."]
    if schema_present is False:
        items.append("Homepage has no schema.org (JSON-LD) markup — add Organization, Product, and FAQ schema so AI Overviews and rich results can parse the page.")
    elif missing_schema_pages:
        items.append(f"{missing_schema_pages} crawled page(s) are missing schema markup that other pages already have — bring them in line.")
    else:
        items.append("Implement Organization, Product/Service, FAQ, and Breadcrumb schema site-wide for AI Overview eligibility.")
    items += [
        "Write concise, extractable answer blocks (2-3 sentences) near the top of key pages — this is what LLMs quote directly.",
        "Build a dedicated FAQ hub covering the full buyer journey: eligibility, pricing, process, and comparisons.",
    ]
    return _next_steps_category_slide(prs, "Answer Engine Optimization (AEO)", None, items)


def add_geo_slide(prs: Presentation):
    """Generative Engine Optimization — standard practice for AI-citation
    entity-building, since that isn't something a crawl can measure
    directly. Split out from the old combined AEO & GEO slide."""
    items = [
        "Publish authoritative content that positions the brand as a specialist in its core category — the framing AI engines reuse when answering category questions.",
        "Check whether the brand appears on the sources LLMs actually cite for this category (industry directories, comparison sites, press) — competitors already do.",
        "Create comparison-friendly content (\"X vs Y\", \"how to choose\") that AI systems can reference directly when answering evaluation queries.",
        "Interlink product pages, guides, and FAQs into topic clusters — stronger contextual relevance improves AI-driven discovery.",
    ]
    return _next_steps_category_slide(prs, "Generative Engine Optimization (GEO)", None, items)


def build_report(
    client_name: str,
    website_url: str,
    site_audit: dict | None = None,
    page_audit: dict | None = None,
    psi_mobile: dict | None = None,
    psi_desktop: dict | None = None,
    analytics: dict | None = None,
    competitor_rows: list[dict] | None = None,
    keyword_rows: list[dict] | None = None,
    backlink_rows: list[dict] | None = None,
    backlink_row_count: int = 0,
    competitor_positions: dict[str, list[dict]] | None = None,
    competitor_narratives: dict[str, dict] | None = None,
    brand_color_hex: str | None = None,
    logo_bytes: bytes | None = None,
    company_overview: dict | None = None,
    tech_stack: dict | None = None,
    competitor_analysis: dict | None = None,
    domain_strategy: dict | None = None,
    ux_findings: dict | None = None,
    site_audit_issues: list[dict] | None = None,
    structured_data_rows: list[dict] | None = None,
    site_audit_overview: dict | None = None,
    backlink_summary: dict | None = None,
    own_domain_rating: int | None = None,
    core_problem: dict | None = None,
    site_audit_pages_rows: list[dict] | None = None,
    next_steps_ai: dict | None = None,
    schema_validation: dict | None = None,
    brand_citations: list[dict] | None = None,
    brand_wikipedia: dict | None = None,
    geopulse_analysis: dict | None = None,
    competitor_keyword_sheet_links: dict[str, str] | None = None,
    competitor_positions_full: dict[str, list[dict]] | None = None,
) -> bytes:
    if brand_color_hex:
        try:
            _theme["accent"] = RGBColor.from_string(brand_color_hex.lstrip("#"))
        except ValueError:
            _theme["accent"] = DEFAULT_ACCENT
    else:
        _theme["accent"] = DEFAULT_ACCENT

    domain = website_url.replace("https://", "").replace("http://", "").rstrip("/")
    _theme["footer"] = f"{client_name}  ·  {domain}"

    try:
        return _build_report(
            client_name, website_url, site_audit, page_audit, psi_mobile, psi_desktop,
            analytics, competitor_rows, keyword_rows, backlink_rows, backlink_row_count,
            company_overview, tech_stack, competitor_analysis, competitor_positions, logo_bytes,
            competitor_narratives, domain_strategy, ux_findings, site_audit_issues, site_audit_overview,
            backlink_summary, structured_data_rows, own_domain_rating, core_problem,
            site_audit_pages_rows, next_steps_ai, schema_validation,
            brand_citations, brand_wikipedia, geopulse_analysis, competitor_keyword_sheet_links,
            competitor_positions_full,
        )
    finally:
        _theme["footer"] = ""
        _theme["accent"] = DEFAULT_ACCENT


def _build_report(
    client_name: str,
    website_url: str,
    site_audit: dict | None,
    page_audit: dict | None,
    psi_mobile: dict | None,
    psi_desktop: dict | None,
    analytics: dict | None,
    competitor_rows: list[dict] | None,
    keyword_rows: list[dict] | None,
    backlink_rows: list[dict] | None,
    backlink_row_count: int,
    company_overview: dict | None = None,
    tech_stack: dict | None = None,
    competitor_analysis: dict | None = None,
    competitor_positions: dict[str, list[dict]] | None = None,
    logo_bytes: bytes | None = None,
    competitor_narratives: dict[str, dict] | None = None,
    domain_strategy: dict | None = None,
    ux_findings: dict | None = None,
    site_audit_issues: list[dict] | None = None,
    site_audit_overview: dict | None = None,
    backlink_summary: dict | None = None,
    structured_data_rows: list[dict] | None = None,
    own_domain_rating: int | None = None,
    core_problem: dict | None = None,
    site_audit_pages_rows: list[dict] | None = None,
    next_steps_ai: dict | None = None,
    schema_validation: dict | None = None,
    brand_citations: list[dict] | None = None,
    brand_wikipedia: dict | None = None,
    geopulse_analysis: dict | None = None,
    competitor_keyword_sheet_links: dict[str, str] | None = None,
    competitor_positions_full: dict[str, list[dict]] | None = None,
) -> bytes:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    add_title_slide(prs, client_name, website_url, logo_bytes=logo_bytes, analytics=analytics)

    if company_overview:
        add_company_overview_extracted_slide(prs, client_name, company_overview)
        add_solutions_products_slide(prs, company_overview)
    elif site_audit and site_audit.get("company_summary"):
        add_company_overview_slide(prs, client_name, site_audit["company_summary"])

    # Domain Strategy slide cut per user request 2026-09-08 — function kept
    # below for fast re-enable if ever needed; domain_strategy is still
    # threaded into the Technical SEO Next Steps slide separately.
    # if domain_strategy:
    #     add_domain_strategy_slide(prs, domain_strategy)

    # Understanding Current Scenario section — template order: Website
    # Performance (PageSpeed) first, then the rest of the crawl-based
    # findings. Tech Stack & Hosting now renders AFTER this whole section
    # (was previously rendered before it started) — moved per the client
    # template's specified order.
    if site_audit or page_audit or psi_mobile or psi_desktop:
        add_section_slide(prs, client_name, "Understanding Current Scenario")
        if psi_mobile or psi_desktop:
            add_pagespeed_slide(prs, psi_mobile, psi_desktop)
            add_pagespeed_score_breakdown_slide(prs, psi_mobile, psi_desktop)
            add_script_treemap_slide(prs, psi_mobile, psi_desktop, website_url)
            add_pagespeed_script_weight_slide(prs, psi_mobile, psi_desktop)
        if site_audit:
            add_site_health_slide(prs, site_audit, site_audit_overview, site_audit_pages_rows)
            if site_audit_pages_rows:
                add_site_structure_slide(prs, site_audit_pages_rows)
            add_seo_issues_slide(prs, site_audit, page_audit, site_audit_issues, site_audit_pages_rows)
            add_tech_fixes_slide(prs, page_audit, analytics, site_audit_pages_rows)
            if schema_validation and schema_validation.get("total_pages"):
                add_schema_combined_slide(prs, schema_validation)
            elif structured_data_rows:
                add_structured_data_slide(prs, structured_data_rows, site_audit_pages_rows)

    if tech_stack:
        add_tech_stack_slide(prs, tech_stack)

    if ux_findings:
        add_ux_findings_slides(prs, ux_findings)

    # Backlink Profile slide temporarily pulled from the report per user
    # request 2026-09-08 — re-enable this call (function untouched below)
    # once the backlink-total inconsistency (see pending_data_inconsistencies
    # memory) is resolved.
    # if backlink_rows or backlink_summary or own_domain_rating is not None:
    #     add_backlink_profile_slide(prs, backlink_rows or [], backlink_row_count, backlink_summary, own_domain_rating)

    # Brand Citation Opportunities slide cut again 2026-09-09 per user
    # request ("remove as of now, will suggest if needed") — disambiguation
    # bug fix from earlier today (brand_citation_service, same date) is
    # untouched and function kept below for fast re-enable.
    # add_brand_mentions_slide(prs, client_name, brand_citations, brand_wikipedia)

    if analytics:
        add_section_slide(prs, client_name, "Traffic & Search Performance")
        ga4_span = _ga4_date_span(analytics.get("date_range"))
        gsc_span = _gsc_date_span(analytics.get("date_range"))
        ga4_source = f"Google Analytics ({ga4_span})" if ga4_span else "Google Analytics"
        gsc_source = f"Google Search Console ({gsc_span})" if gsc_span else "Google Search Console"
        if analytics.get("traffic_overview"):
            add_traffic_overview_slide(prs, analytics)
        if analytics.get("traffic_spike"):
            add_traffic_spike_slide(prs, analytics["traffic_spike"])
        if analytics.get("traffic_channel_breakdown"):
            add_traffic_channel_breakdown_slide(prs, analytics["traffic_channel_breakdown"], source=ga4_source)
        # Top Pages — Branded vs Non-Branded slide removed 2026-09-09 per
        # user request — redundant with GSC's own query-level Branded/
        # Non-Branded split below (search_queries), which classifies real
        # search intent directly instead of inferring it from a page-path
        # signal list.
        sources = (analytics.get("traffic_sources") or {}).get("rows", [])
        if sources:
            total_sessions = sum(int(float(s.get("sessions", 0) or 0)) for s in sources)
            # Sorted by sessions and capped BEFORE anything below reads from
            # it — every insight below names a channel by its exact string,
            # so it must only ever pick from the same rows the table
            # actually renders. ROW_CAP must match _table_slide's own
            # row_cap for THIS call, not just be "a" cap — confirmed live
            # 2026-09-09: this was capped at 14 while _draw_table's default
            # row_cap silently drops to 9 whenever insights are passed
            # (`row_cap = 9 if insights else 14`), so an insight could
            # still name a channel (e.g. "Cross-network") ranked #10-14
            # that never actually made it onto the visible 9-row table.
            # Passed explicitly to _table_slide below too, so this can't
            # drift out of sync with _draw_table's default again.
            ROW_CAP = 9
            shown = sorted(sources, key=lambda s: float(s.get("sessions", 0) or 0), reverse=True)[:ROW_CAP]
            rows = [
                (
                    s["channel"],
                    f"{int(float(s['sessions'])):,}",
                    f"{(float(s['sessions']) / total_sessions * 100 if total_sessions else 0):.1f}%",
                    f"{int(float(s.get('new_users', 0) or 0)):,}",
                    f"{int(float(s.get('returning_users', 0) or 0)):,}",
                    f"{s['return_rate_pct']:.0f}%" if s.get("return_rate_pct") is not None else "—",
                )
                for s in shown
            ]
            # No prior-period channel data is fetched anywhere in this
            # pipeline (every other slide in this report is single-
            # snapshot, same convention) — _traffic_sources_insights
            # always runs in "single" mode today, but is written to
            # activate real comparison-mode output the moment prior-period
            # rows are ever passed in, per the 2026-09-09 spec.
            insights = _traffic_sources_insights(shown, total_sessions)
            _table_slide(
                prs, "Traffic Sources", ["Channel", "Sessions", "% of Sessions", "New Users", "Returning Users", "Return Rate"], rows,
                col_widths=[3.4, 1.8, 1.8, 1.8, 1.9, 1.4], source=ga4_source, insights=insights, row_cap=ROW_CAP,
            )
        queries = (analytics.get("search_queries") or {}).get("rows", [])
        if queries:
            # The domain-derived token alone ("lumberfi" from lumberfi.com)
            # missed real branded queries built around the actual company
            # name ("lumber careers", "lumber payroll") since "lumberfi"
            # never appears in them as a whole word — confirmed on a real
            # Lumber regen, every one of those fell into Non-Branded. Match
            # on EITHER the company-name token OR the domain token so both
            # "lumber ..." and "lumberfi ..." style queries count as branded.
            brand_tokens = {t for t in (_brand_token(client_name), _brand_token(website_url)) if t}

            def _is_branded(query: str) -> bool:
                # is_branded_or_near_brand (not the plain whole-word
                # _is_branded_keyword) also catches a real near-brand query
                # variant/misspelling, e.g. "lumberfy" — confirmed live,
                # that landed in Non-Branded since it's not an exact match
                # for either "lumber" or "lumberfi".
                return is_branded_or_near_brand(query, brand_tokens)

            def _query_table_slide(title: str, subset: list[dict]):
                if not subset:
                    return
                top_q = sorted(subset, key=lambda q: q.get("clicks", 0), reverse=True)[:14]
                rows = [(q["query"], q["clicks"], q["impressions"], f"{q['ctr']*100:.1f}%", f"{q['position']:.1f}") for q in top_q]
                total_clicks = sum(q.get("clicks", 0) for q in subset)
                total_impressions = sum(q.get("impressions", 0) for q in subset)
                avg_ctr = total_clicks / total_impressions * 100 if total_impressions else 0
                # Impression-weighted, not a plain per-row average — matches
                # how GSC itself reports the period average position.
                avg_position = (
                    sum(q.get("position", 0) * q.get("impressions", 0) for q in subset) / total_impressions
                    if total_impressions else 0
                )
                # 2026-09-09 spec: never a single blended average as the
                # finding. Branded gets the classify-first pipeline
                # (recruitment vs. core-brand vs. product-relevant, raw-vs-
                # filtered metrics); Non-Branded gets the standout-row rule
                # (one dominant or inverted-expectation row, not an average
                # CTR against a made-up "~3%" benchmark like this used to
                # state with no benchmark actually provided).
                if title == "Search Queries — Branded":
                    insights = _branded_query_insights(subset, brand_tokens, client_relevance_profile)
                else:
                    standout = _standout_query_insight(top_q)
                    insights = [standout] if standout else []
                # 2026-09-10 QA gate: _branded_query_insights scans the full
                # subset (needed for accurate raw-vs-filtered totals), so its
                # named examples can fall outside top_q, the rows actually
                # drawn below — drop any that do rather than publish a query
                # the reader can't find in the table.
                insights, _ = _validate_slide_insights(insights, top_q, "query")
                if not insights:
                    # From top_q (the rows actually drawn on the slide), not
                    # the full subset — confirmed live: the insight named a
                    # query ("lumberfy") that ranked outside the shown
                    # top-14-by-clicks rows, never appeared in the table.
                    best_positioned = min(
                        (q for q in top_q if q.get("clicks", 0) > 0), key=lambda q: q.get("position", 999), default=None
                    )
                    if best_positioned:
                        insights = [f"Best-ranking clicked query: \"{best_positioned['query']}\" at position {best_positioned['position']:.1f}."]

                # Overall summary card strip (Total Clicks/Impressions/CTR/
                # Avg. position) across the FULL branded/non-branded subset,
                # not just the top-14 shown in the table below — added per
                # user request 2026-09-08, same pattern as the KPI cards on
                # the Traffic Overview slide.
                slide = _blank_slide(prs)
                _content_header(slide, title)
                _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {gsc_source}", size=11, color=TEXT_MUTED)
                metrics = [
                    ("Total Clicks", f"{total_clicks:,}"),
                    ("Total Impressions", f"{total_impressions:,}"),
                    ("Avg. CTR", f"{avg_ctr:.1f}%"),
                    ("Avg. Position", f"{avg_position:.1f}"),
                ]
                gap = Inches(0.15)
                total_width = Inches(12.1)
                card_width = Emu(int((total_width - gap * (len(metrics) - 1)) / len(metrics)))
                card_height = Inches(0.95)
                card_top = Inches(1.1)
                for i, (label, value) in enumerate(metrics):
                    left = Inches(0.6) + Emu(i * (card_width + gap))
                    _card(slide, left, card_top, card_width, card_height)
                    _textbox(slide, left + Inches(0.15), card_top + Inches(0.12), card_width - Inches(0.3), Inches(0.35), label, size=11, color=TEXT_MUTED)
                    _textbox(slide, left + Inches(0.15), card_top + Inches(0.42), card_width - Inches(0.3), Inches(0.45), value, size=18, bold=True, color=_accent())

                # No explicit row_cap here — _draw_table's own default (9
                # rows when insights are passed, matching this call before
                # the KPI cards were added) is what keeps the table's actual
                # bottom edge on the slide. This slide's table starts ~1.05in
                # lower than the plain _table_slide default (to make room for
                # the card row above), so forcing all 14 built rows through
                # (confirmed live: rows ran to 8.25in on a 7.5in-tall slide,
                # off the bottom edge) would blow well past the slide bottom.
                _draw_table(
                    slide, ["Query", "Clicks", "Impressions", "CTR", "Avg. position"], rows,
                    card_top + card_height + Inches(0.2), col_widths=[5.5, 1.5, 1.9, 1.5, 1.7],
                    insights=insights,
                )

            # Only products/categories literally present in the extracted
            # Company Overview — never inferred — per the classify pipeline's
            # own hard rule ("never infer what the client sells").
            client_relevance_profile = {
                "products": (company_overview or {}).get("products") or [],
                "categories": list((company_overview or {}).get("industries") or [])
                + list((company_overview or {}).get("solutions") or []),
            }
            branded_queries = [q for q in queries if _is_branded(q.get("query", ""))]
            nonbranded_queries = [q for q in queries if not _is_branded(q.get("query", ""))]
            _query_table_slide("Search Queries — Branded", branded_queries)
            _query_table_slide("Search Queries — Non-Branded", nonbranded_queries)

    if competitor_rows or keyword_rows or backlink_rows or backlink_summary or competitor_positions or competitor_narratives:
        add_section_slide(prs, client_name, "Competitor & Keyword Research")
        if keyword_rows:
            add_keyword_research_slide(prs, keyword_rows)
            add_keyword_opportunity_slide(prs, keyword_rows)
        if competitor_rows:
            add_competitor_table_slide(prs, competitor_rows)
        if competitor_positions:
            # Google Sheet links (full, uncapped keyword lists) take
            # priority over the old per-competitor capped-table slides —
            # falls back automatically when no service account is
            # configured or sheet creation failed for every domain, see
            # site_audit.py._build_pptx_for_client.
            if competitor_keyword_sheet_links:
                add_competitor_keyword_sheets_slide(
                    prs, competitor_positions_full or competitor_positions, competitor_keyword_sheet_links
                )
            else:
                add_competitor_positions_slides(prs, competitor_positions)
        if competitor_narratives:
            for domain, narrative in competitor_narratives.items():
                if "error" not in narrative:
                    add_competitor_best_at_slide(prs, domain, narrative)
                    add_competitor_opportunity_slide(prs, client_name, domain, narrative)
        if competitor_analysis and competitor_analysis.get("keyword_gap_rows"):
            add_keyword_gap_slide(prs, competitor_analysis)

    if core_problem:
        add_core_problem_slide(prs, core_problem)

    add_section_slide(prs, client_name, "Next Steps")

    # AI-generated categories (next_steps_ai) take priority when present —
    # bespoke, business-aware advice grounded in this client's actual
    # products/competitors/numbers, matching how real manual-report decks
    # handle this section (confirmed against 3 references: a category that
    # doesn't fit the business, e.g. Local SEO for a national B2B SaaS with
    # no physical locations, is dropped entirely rather than padded with
    # generic checklist filler). Falls back to the static template slide
    # per-category whenever the AI call failed, or that one category came
    # back empty/malformed — never lets one bad category blank the section.
    ai_categories = (next_steps_ai or {}).get("categories") or {}
    # Kept in sync with next_steps_service.CATEGORY_TITLES by hand (a small,
    # stable map) rather than importing that module here — this file
    # otherwise has zero app.* imports, staying a pure rendering layer with
    # no network-client dependencies pulled in transitively. content_seo has
    # no entry here — that slide always renders through the deterministic
    # classifier below, never through this AI-category routing.
    _ai_category_titles = {
        "local_seo": "Next Steps: Local SEO",
        "technical_seo": "Next Steps: Technical SEO",
        "conversion_seo": "Next Steps: Conversion SEO",
        "aeo": "Answer Engine Optimization (AEO)",
        "geo": "Generative Engine Optimization (GEO)",
        "goals": "SEO Goals & Targets",
    }

    def _next_steps_slide(key: str, fallback_fn, *fallback_args):
        category = ai_categories.get(key)
        if not category:
            return fallback_fn(*fallback_args)
        if category.get("applicable") is False:
            # The AI explicitly judged this category irrelevant to this
            # business — that's a real finding, not a gap to paper over
            # with the generic fallback checklist.
            return None
        items = category.get("items")
        if not items:
            return fallback_fn(*fallback_args)
        title = _ai_category_titles.get(key, key)
        return _next_steps_category_slide(prs, title, category.get("intro") or None, items)

    _next_steps_slide("local_seo", add_local_seo_next_steps_slide, prs, structured_data_rows)
    _next_steps_slide(
        "technical_seo", add_technical_seo_next_steps_slide, prs, site_audit, page_audit, tech_stack, domain_strategy
    )
    # Always the deterministic keyword-page-category classifier below, never
    # the AI path — this needs an EXACT, guaranteed-consistent rule applied
    # every time (comparison-shaped vs. blog-shaped vs. landing-page-shaped
    # keywords), not an AI's variable phrasing of the same idea.
    add_content_seo_next_steps_slide(prs, keyword_rows)
    add_programmatic_seo_slide(prs, keyword_rows)
    _next_steps_slide("conversion_seo", add_conversion_seo_next_steps_slide, prs, ux_findings, backlink_row_count)
    # GeoPulse-grounded content (the client's own AI-visibility tool export)
    # outranks both the generic next_steps_ai category AND the static
    # schema-only fallback below — it's the only source of these two slides
    # actually backed by real AI-search-visibility data rather than an LLM
    # guessing from site/competitor data alone.
    if geopulse_analysis and geopulse_analysis.get("aeo_items"):
        _next_steps_category_slide(prs, "Answer Engine Optimization (AEO)", None, geopulse_analysis["aeo_items"])
    else:
        _next_steps_slide("aeo", add_aeo_slide, prs, site_audit, page_audit)
    if geopulse_analysis and geopulse_analysis.get("geo_items"):
        _next_steps_category_slide(prs, "Generative Engine Optimization (GEO)", None, geopulse_analysis["geo_items"])
    else:
        _next_steps_slide("geo", add_geo_slide, prs)
    _next_steps_slide("goals", add_goals_slide, prs, own_domain_rating, competitor_rows, keyword_rows)

    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()
