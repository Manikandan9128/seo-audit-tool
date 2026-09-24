"""Builds a client-facing PPTX audit report styled after the Cyces deck format:
dark top bar, blue section-title band, light-gray body, white content cards."""

from __future__ import annotations

import difflib
import logging
import re
import threading
from collections import Counter
from io import BytesIO
from urllib.parse import urlparse

import pycountry
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt, Emu

logger = logging.getLogger(__name__)

from app.services.keyword_relevance_service import (
    _KEYWORD_PAGE_CATEGORIES,
    _brand_token,
    _classify_keyword_page_category,
    _is_branded_keyword,
    filter_other_brand_keywords,
    is_branded_or_near_brand,
    classify_page_type,
    normalize_source_url,
)
from app.services.keyword_cluster_pipeline import (
    _CAREER_ROUTE_CLUSTER_LABEL,
    _COMPETITOR_ROUTE_CLUSTER_LABEL,
    _GEO_ROUTE_CLUSTER_LABEL,
    _NEEDS_REVIEW_CLUSTER_LABEL,
    _UNJUDGED_REASON,
)
from app.services.content_safety import redact_presentation
from app.services.keyword_intelligence_service import _EXCLUDED_RELEVANCE_STATUSES, is_temporal, modifier_types
from app.services.priority_model import compute_priority_score

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


# Every content slide's title lives at L=0.4in, and slide functions that add
# a "Source: ..." label independently put it at L=8.0-8.3in on the SAME row
# (title_y=0.22-0.44in, Source y=0.3in) — the title box used to be Inches(10.5)
# wide regardless, so any title long enough at the default 23pt bold ran
# straight into whatever Source label that slide added (confirmed on a real
# generated deck: "Target Keywords: Tata Trucks & Commercial Vehicles",
# "Branded vs Non-Branded Search Performance", etc. all visibly collided with
# their Source label). _content_header has no way to know whether a given
# slide will add a Source label, so it always reserves the same clearance.
_TITLE_MAX_WIDTH = Inches(7.4)  # right edge 7.8in, clear of the earliest Source L=8.0in


def _fit_title_font_size(text: str, box_width, max_size=23, min_size=14) -> float:
    """Shrinks (never grows) a single-line bold title's font size just
    enough to keep it inside box_width, so long AI/data-driven titles
    (cluster names, competitor domains, etc.) never encroach on the
    Source-label zone. Same char-width heuristic as _estimate_text_extent,
    with a smaller safety pad since this drives an actual layout decision,
    not just an overlap-detection guess."""
    box_in = Emu(int(box_width)).inches
    size = max_size
    while size > min_size and (0.55 * size * len(text) * 1.1) / 72 > box_in:
        size -= 1
    return size


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
    title_size = _fit_title_font_size(title, _TITLE_MAX_WIDTH)
    _textbox(slide, Inches(0.4), title_y, _TITLE_MAX_WIDTH, Inches(0.5), title, size=title_size, bold=True, color=TEXT_DARK)

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


def _channel_breakdown_date_span(date_range: dict | None) -> str:
    """Same as _ga4_date_span but for Traffic Breakdown - Monthly Average's
    own wider window (2026-09-11 fix) — this slide pulls ~4 months of data
    to average over (see channel_breakdown_start in site_audit.py), a
    different range than every other GA4 slide's 30-day window, so it
    needs its own Source caption instead of reusing ga4_start/ga4_end and
    silently mislabeling itself with the wrong dates."""
    if not date_range or not (date_range.get("channel_breakdown_start") and date_range.get("channel_breakdown_end")):
        return ""
    from datetime import date as _date

    fmt = lambda iso: _date.fromisoformat(iso).strftime("%b %d, %Y")
    return f"{fmt(date_range['channel_breakdown_start'])} – {fmt(date_range['channel_breakdown_end'])}"


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


def _drop_divider_if_section_empty(prs: Presentation, count_before_divider: int | None) -> None:
    """Removes a section divider that nothing ended up following — confirmed
    real 2026-09-23 (BharatBenz): `analytics` was a non-empty dict (date
    range only) so "Traffic & Search Performance" rendered, but every GA4/
    GSC slide under it had no rows, leaving a bare divider in the deck."""
    if count_before_divider is None or len(prs.slides) != count_before_divider + 1:
        return
    sld_id_lst = prs.slides._sldIdLst
    last = sld_id_lst[-1]
    prs.part.drop_rel(last.rId)
    sld_id_lst.remove(last)


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


# 2026-09-21 spec section 5 — a fixed, per-metric explanation of what that
# metric being weak actually indicates, never a guess at root cause beyond
# what the metric itself supports. Only ever shown for a metric whose
# status isn't "Good" (rule 5: "only generate a diagnosis when the
# corresponding metric actually supports it").
_DIAGNOSIS_BY_METRIC = {
    "largest-contentful-paint": "LCP is a performance constraint here — investigate the specific LCP element and its loading/rendering delays.",
    "cumulative-layout-shift": "CLS indicates layout instability — identify the elements responsible for the shifts.",
    "total-blocking-time": "TBT indicates significant main-thread activity — investigate long-running JavaScript tasks.",
    "first-contentful-paint": "FCP indicates delayed visual rendering — investigate the PSI diagnostics responsible for the delay.",
    "speed-index": "Speed Index indicates delayed visual rendering — investigate the PSI diagnostics responsible for the delay.",
}


def _cwv_status_line(field_data: dict | None) -> str:
    """Rule 4/11: Core Web Vitals status only from real CrUX field data —
    never inferred from the Lighthouse lab metrics in the table above, and
    never claimed passed/failed when CrUX has nothing for this site."""
    if not field_data:
        return "Insufficient field data for Core Web Vitals assessment."
    parts = []
    for key, label in (("lcp", "LCP"), ("inp", "INP"), ("cls", "CLS")):
        m = field_data.get(key)
        if m and m.get("category"):
            parts.append(f"{label}: {m['category']}")
        else:
            parts.append(f"{label}: no field data")
    fallback_note = (
        " (origin-level fallback — this exact URL doesn't have enough CrUX traffic on its own)"
        if field_data.get("is_origin_fallback") else ""
    )
    overall = field_data.get("overall_category")
    headline = f"Overall: {overall}. " if overall else ""
    return f"{headline}{'; '.join(parts)}{fallback_note}"


def add_pagespeed_score_breakdown_slide(prs: Presentation, mobile: dict | None, desktop: dict | None) -> list:
    """2026-09-21 spec rebuild: shows what PSI actually measured, explains
    what it means, and surfaces what PSI itself says should be
    investigated — no reimplemented Lighthouse scoring curve, no "if this
    metric were fixed the score would become Y" projection anywhere (that
    entire mechanism no longer exists in pagespeed_client.py). Mobile stays
    primary — usually the worse, higher-traffic surface; desktop only
    supplies the mobile-vs-desktop callout. Returns a list of the slides
    actually added (1 or 2), never a single implicit slide, since PSI
    Opportunities/Diagnostics/LCP Breakdown get their own slide when data
    for them exists."""
    primary_label, primary = ("Mobile", mobile) if mobile and mobile.get("metric_table") else ("Desktop", desktop)
    if not primary or not primary.get("metric_table"):
        return []

    slide = _blank_slide(prs)
    _content_header(slide, "Website Performance — Score Breakdown")
    _textbox(
        slide, Inches(8.0), Inches(0.3), Inches(4.8), Inches(0.4),
        "Source: Google PageSpeed Insights (Lighthouse lab data)", size=11, color=TEXT_MUTED,
    )

    left, width = Inches(0.6), Inches(12.1)
    max_y = SLIDE_H - Inches(0.4)
    y = Inches(1.0)

    current_score = primary.get("current_score")
    score_line = (
        f"{primary_label} Performance score: {current_score}" if current_score is not None
        else f"{primary_label} Performance score: not available"
    )
    _textbox(slide, left, y, width, Inches(0.3), score_line, size=15, bold=True, color=_accent())
    y += Inches(0.42)

    # Section 1 — Metric | Current | Good Threshold | Status (2026-09-21
    # spec section 2). No Score/If Fixed/Score Impact columns: Status comes
    # straight from PSI's own per-audit score (rule 3), Good Threshold from
    # Google's published cutoffs, never a custom benchmark.
    rows = [
        (
            m["label"], m.get("display_value") or _fmt_metric_value(m["id"], m["value"]),
            f"≤{_fmt_metric_value(m['id'], m['good_threshold'])}", m.get("status") or "—",
        )
        for m in primary["metric_table"]
    ]
    y = _draw_table(
        slide, ["Metric", "Current", "Good Threshold", "Status"], rows, y,
        col_widths=[3.4, 2.7, 2.9, 3.1], left=left, width=width, row_height=0.38,
    )
    y += Inches(0.18)

    # Section 2 — Core Web Vitals, field data only, shown only here (never
    # repeated against the lab table above).
    _textbox(slide, left, y, width, Inches(0.22), "CORE WEB VITALS (FIELD DATA)", size=9.5, bold=True, color=_accent())
    y += Inches(0.24)
    _textbox(slide, left, y, width, Inches(0.4), _cwv_status_line(primary.get("field_data")), size=11)
    y += Inches(0.46)

    # Section 3 — Performance Diagnosis: what the data indicates, never a
    # repeat of the table's own numbers (rule 9).
    diagnosis = [
        _DIAGNOSIS_BY_METRIC[m["id"]] for m in primary["metric_table"]
        if m.get("status") in ("Poor", "Needs Improvement") and m["id"] in _DIAGNOSIS_BY_METRIC
    ]
    if diagnosis and y < max_y - Inches(0.3):
        y = _insights_strip(slide, left, y, width, diagnosis, title="Performance Diagnosis", max_y=max_y)

    if (
        mobile and desktop and mobile.get("current_score") is not None and desktop.get("current_score") is not None
        and y < max_y - Inches(0.3)
    ):
        gap = desktop["current_score"] - mobile["current_score"]
        if gap > 15:
            _textbox(
                slide, left, y, width, Inches(0.3),
                f"Desktop Performance ({desktop['current_score']}) outpaces Mobile ({mobile['current_score']}) by {gap} points.",
                size=10.5, color=TEXT_MUTED,
            )

    slides = [slide]
    opp_slide = _add_pagespeed_opportunities_slide(prs, primary_label, primary)
    if opp_slide:
        slides.append(opp_slide)
    return slides


def _add_pagespeed_opportunities_slide(prs: Presentation, primary_label: str, primary: dict) -> object | None:
    """Website Performance — PSI Opportunities & Diagnostics (2026-09-21
    spec sections 6-9): populated ONLY from PSI's own opportunity/
    diagnostic audits for this run — no generic "compress images"/"remove
    unused JS" filler when PSI didn't actually flag one with a real
    number. Absent entirely when PSI returned none of the three."""
    opportunities = primary.get("opportunities") or []
    diagnostics = primary.get("diagnostics") or []
    lcp_breakdown = primary.get("lcp_breakdown") or []
    if not opportunities and not diagnostics and not lcp_breakdown:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Website Performance — PSI Opportunities & Diagnostics")
    _textbox(
        slide, Inches(7.6), Inches(0.3), Inches(5.2), Inches(0.4),
        f"Source: Google PageSpeed Insights ({primary_label} Lighthouse run)", size=11, color=TEXT_MUTED,
    )

    left, width = Inches(0.6), Inches(12.1)
    max_y = SLIDE_H - Inches(0.4)
    y = Inches(1.05)

    if opportunities:
        _textbox(slide, left, y, width, Inches(0.24), "TOP PSI OPPORTUNITIES", size=12.5, bold=True, color=_accent())
        y += Inches(0.28)
        rows = [(o["title"], o["savings"]) for o in opportunities[:8]]
        y = _draw_table(
            slide, ["Opportunity", "Estimated Savings"], rows, y,
            col_widths=[8.6, 3.5], left=left, width=width, row_height=0.36, row_cap=8,
        )
        y += Inches(0.2)

    if lcp_breakdown and y < max_y - Inches(1.2):
        _textbox(slide, left, y, width, Inches(0.24), "LCP BREAKDOWN", size=12.5, bold=True, color=_accent())
        y += Inches(0.28)
        rows = [(r["phase"], _fmt_metric_value("largest-contentful-paint", r["timing_ms"])) for r in lcp_breakdown]
        y = _draw_table(
            slide, ["Phase", "Timing"], rows, y,
            col_widths=[8.6, 3.5], left=left, width=width, row_height=0.35, row_cap=4,
        )
        y += Inches(0.2)

    if diagnostics and y < max_y - Inches(0.3):
        diag_lines = [f"{d['label']}: {d['value']}" for d in diagnostics]
        _insights_strip(slide, left, y, width, diag_lines, title="Diagnostics", max_y=max_y)

    return slide


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

    # total_js_bytes/third_party_bytes are already summed from EVERY node in
    # the script-treemap response (see _extract_script_weight), not just the
    # top_waste rows shown in the table below — so the total reconciles to
    # the full tracked script count, not the excerpt on the slide (2026-09-16
    # user spec, Step 1). "External" only means the script loads from a
    # different domain than the site itself — it may be a genuine third-
    # party vendor (ads, chat, tag manager) or the client's own CDN
    # subdomain; is_third_party is always resolved one way or the other
    # (Step 2), never left an open question.
    total_scripts = sw.get("total_script_count") or len(sw.get("top_scripts") or [])
    shown_count = len(sw["top_waste"])
    insights = [
        f"Total JS payload ({primary_label}): {_fmt_kb(sw['total_js_bytes'])} across all {total_scripts} tracked "
        f"scripts, not just the {shown_count} shown below — {sw['third_party_pct']}% loads from an external domain "
        "(own CDN or a genuine third-party vendor — worth checking which)."
    ]
    third_party_named = [w for w in sw["top_waste"] if w["is_third_party"]]
    if third_party_named:
        names = " + ".join(_short_resource_name(w["url"]) for w in third_party_named[:3])
        named_bytes = sum(w["total_bytes"] for w in third_party_named[:3])
        insights.append(f"{names} account for {_fmt_kb(named_bytes)} of the external total shown below.")
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
    health_pct = site_audit_overview.get("site_health_pct") if site_audit_overview else None
    if page_totals:
        crawled_display = f"{page_totals['total']:,}"
        _textbox(
            slide, Inches(0.8), Inches(1.68), Inches(2.5), Inches(0.5),
            crawled_display, size=26, bold=True, color=_accent(),
        )
        if site_audit_overview:
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
                # Some Site Audit sources carry counts without percentages —
                # derive from the crawl total rather than printing "None%"
                # (confirmed real on a BharatBenz deck, 2026-09-23).
                if pct is None and page_totals.get("total"):
                    pct = round(count / page_totals["total"] * 100, 1)
                _icon_dot(slide, Inches(0.85), y + Inches(0.06), Inches(0.11), category_colors[key])
                _textbox(slide, Inches(1.05), y, Inches(2.9), Inches(0.28), f"{category}: {count:,}" + (f" ({pct}%)" if pct is not None else ""), size=11.5, color=TEXT_DARK)
                y += Inches(0.29)
        elif page_totals.get("with_issues") is not None:
            _textbox(
                slide, Inches(0.8), Inches(2.18), Inches(3.0), Inches(0.6),
                f"{page_totals['with_issues']:,} page(s) have issues.", size=12.5, color=TEXT_MUTED,
            )
    else:
        # Neither the per-URL Crawled Pages export nor the Site Health
        # overview PDF has been uploaded — no data to show at all.
        _textbox(
            slide, Inches(0.8), Inches(1.75), Inches(3.0), Inches(0.9),
            "No Semrush Site Audit data uploaded yet.", size=12.5, color=TEXT_MUTED,
        )

    # Card grown from 2.6in to 3.0in and the AI Search Health line
    # repositioned below _score_ring's own "full site crawl" sub-label
    # (2026-09-17 fix — _audit_slide_geometry caught the two text boxes
    # overlapping: the ring's diameter grew after this fixed y=3.45 was
    # written, but nothing here tracked the ring's real bottom edge).
    ring_cy, ring_diameter = Inches(1.85), Inches(1.4)
    card2 = _card(slide, Inches(4.5), Inches(1.2), Inches(3.6), Inches(3.0))
    _textbox(slide, Inches(4.7), Inches(1.35), Inches(3), Inches(0.4), "Site Health", size=15, bold=True)
    _score_ring(slide, Inches(5.6), ring_cy, ring_diameter, health_pct, "full site crawl")
    if site_audit_overview and site_audit_overview.get("ai_search_health_pct") is not None:
        ring_label_bottom = ring_cy + ring_diameter + Inches(0.05) + Inches(0.35)
        _textbox(
            slide, Inches(4.7), ring_label_bottom + Inches(0.05), Inches(3.2), Inches(0.3),
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


def classify_seo_issues(site_audit_issues: list[dict]) -> tuple[list[dict], list[dict]]:
    """Splits Semrush Site Audit's issue-type rollup into (errors, warnings)
    — Notices dropped, zero-count rows dropped, ranked by pages affected.
    Shared by add_seo_issues_slide's own rendering and the AI insights
    prompt (site_audit.py) so both work off one classification instead of
    two independently-maintained copies that could drift apart. Each entry
    is {"issue": str, "pages": int}, not a pre-formatted label string, so
    the AI prompt gets structured data instead of parsing "(N pages)" back
    out of a display string."""
    nonzero = [r for r in site_audit_issues if (r.get("failed_checks") or 0) > 0]
    ranked = sorted(nonzero, key=lambda r: r.get("failed_checks") or 0, reverse=True)
    errors, warnings = [], []
    for row in ranked:
        issue_type = str(row.get("issue_type", "")).strip().upper()
        entry = {"issue": row.get("issue", "Issue"), "pages": row.get("failed_checks", 0)}
        if issue_type == "ERROR":
            errors.append(entry)
        elif issue_type == "WARNING":
            warnings.append(entry)
    return errors, warnings


def _issue_count_label(count: int, total_crawled: int | None) -> str:
    """Semrush's per-issue-type "Failed Checks" number is a unique-page
    count for most issue types, but a raw occurrence count for link-based
    issues — one page with 40 broken internal links contributes 40, not 1
    — so it can legitimately exceed the site's total crawled-page count
    (confirmed live: Lumber's "Broken internal links" read 2,108 against a
    1,351-page crawl). Labeling every row "N pages" regardless produced a
    literally-impossible-looking number that undermines the report's
    credibility with a client who can just count their own crawled pages.
    Falls back to the honest, unit-agnostic "occurrences" label whenever
    the count exceeds what's actually known to have been crawled."""
    if total_crawled and count > total_crawled:
        return f"{count:,} occurrences"
    return f"{count:,} pages"


def add_critical_issues_slide(
    prs: Presentation, site_audit_issues: list[dict] | None, site_audit_pages_rows: list[dict] | None = None
):
    """Semrush Site Audit ERROR-severity issues alone, one full slide —
    distinct from SEO Issues (which caps Errors at 10 rows split side-by-
    side against Warnings). Matches the real manual reference deck's
    dedicated "Critical Issues" slide (template item 8): errors only, no
    row cap beyond what one table page can hold, so a reader gets the
    complete error list rather than the top-10 half of a two-column
    layout. Warnings are deliberately excluded — that's what SEO Issues is
    for."""
    if not site_audit_issues:
        return None
    errors, _warnings = classify_seo_issues(site_audit_issues)
    if not errors:
        return None
    total_crawled = (_canonical_page_totals(site_audit_pages_rows, None) or {}).get("total")
    rows = [(e["issue"], _issue_count_label(e["pages"], total_crawled)) for e in errors]
    total_pages_affected = sum(e["pages"] for e in errors)
    insights = [f"{len(errors)} error-level issue type(s) found, {total_pages_affected:,} affected instance(s) total."]
    if len(errors) > 14:
        insights.append(f"Showing all {len(errors)} — see SEO Issues for Errors alongside Warnings.")
    return _table_slide(
        prs, "Critical Issues", ["Issue", "Affected"], rows,
        col_widths=[9.5, 2.6], source="Semrush Site Audit", insights=insights, row_cap=14,
    )


def add_seo_issues_slide(
    prs: Presentation,
    audit: dict,
    page_audit: dict | None,
    site_audit_issues: list[dict] | None = None,
    site_audit_pages_rows: list[dict] | None = None,
    insights_ai: dict | None = None,
    analytics: dict | None = None,
):
    slide = _blank_slide(prs)
    _content_header(slide, "SEO Issues")

    scope_note = None
    if site_audit_issues:
        # Semrush Site Audit's own issue-type rollup — a real full-site crawl
        # result (hundreds of pages, ~95 issue categories), strictly richer
        # than our own homepage + 20-page checks below. Prefer it when
        # uploaded.
        error_entries, warning_entries = classify_seo_issues(site_audit_issues)
        total_crawled = (_canonical_page_totals(site_audit_pages_rows, None) or {}).get("total")
        errors = [f"{e['issue']} ({_issue_count_label(e['pages'], total_crawled)})" for e in error_entries]
        warnings = [f"{w['issue']} ({_issue_count_label(w['pages'], total_crawled)})" for w in warning_entries]
    else:
        # No Semrush Site Audit issues rollup uploaded — fall back to our
        # own homepage + up-to-20-page crawl. This used to list one raw
        # "{issue} — {url}" row per occurrence, so the same issue type
        # (e.g. "Title tag longer than 60 characters") showed up as several
        # separate, uncounted rows instead of one row with a page count —
        # the exact "not counting the numbers" gap the site_audit_issues
        # branch above already solved with _issue_count_label. Group by
        # issue text and count pages here too, so this fallback path looks
        # and behaves the same way regardless of which Semrush exports a
        # given client has or hasn't uploaded.
        per_page_issue_counts: dict[str, int] = {}
        if page_audit:
            for page in page_audit.get("pages", []):
                for issue in page.get("issues", []):
                    per_page_issue_counts[issue] = per_page_issue_counts.get(issue, 0) + 1
        total_pages_checked = len(page_audit.get("pages", [])) if page_audit else None

        # audit["issues"] is a SEPARATE homepage-only check that runs the
        # same _meta_issues() detector page_audit's per-page crawl already
        # runs on every page it checks, homepage included — confirmed real
        # on a BharatBenz regen: "Title tag longer than 60 characters" and
        # "Missing structured data (JSON-LD)" each showed up TWICE, once
        # bare from here and once grouped-with-count from per_page_issue_
        # counts (which already covers the homepage as one of its N pages).
        # Only genuinely site-level-only findings (HTTPS/reachable/robots/
        # sitemap — nothing page_audit's per-page loop ever produces) stay
        # bare; anything page_audit could also report is skipped here and
        # left to the grouped count below, which already represents it
        # (including the homepage) without double-counting.
        site_wide_issues = [i for i in audit.get("issues", []) if i not in per_page_issue_counts]

        # No real severity comes back from this fallback's own crawler
        # (_meta_issues in technical_seo_service.py returns a flat list of
        # strings, no severity field) — unlike the site_audit_issues branch
        # above, which gets Semrush's own ERROR/WARNING classification per
        # issue type. Confirmed live on a Geopits regen where NO Semrush
        # Site Audit Issues export was uploaded: this fallback ran, and the
        # old keyword list here (only "not reachable"/https/robots/sitemap)
        # never matches any of _meta_issues's 8 real issue strings, so every
        # single finding landed in Warnings and the Errors column came up
        # completely empty. A page missing an element outright (no title,
        # no meta description, no h1, no canonical, no structured data at
        # all) is a real on-page defect, not a style nitpick — those now
        # classify as Errors, matching how Semrush itself treats the same
        # findings (confirmed against a Semrush-sourced Geopits report the
        # day before). Length/multiplicity/recommended-but-optional issues
        # ("Title tag longer than 60 characters", "Multiple <h1> tags
        # found", "Missing recommended schema types: ...") stay Warnings.
        def _is_error_text(text: str) -> bool:
            lower = text.lower()
            if any(k in lower for k in ["not reachable", "https", "robots", "sitemap"]):
                return True
            return any(
                lower.startswith(k) or lower == k
                for k in [
                    "missing <title> tag",
                    "missing meta description",
                    "no <h1> tag found",
                    "missing canonical tag",
                    "missing mobile viewport meta tag",
                    "missing structured data (json-ld)",
                ]
            )

        errors, warnings = [], []
        for issue in site_wide_issues:
            (errors if _is_error_text(issue) else warnings).append(issue)
        for issue_text, count in sorted(per_page_issue_counts.items(), key=lambda kv: kv[1], reverse=True):
            label = f"{issue_text} ({_issue_count_label(count, total_pages_checked)})"
            (errors if _is_error_text(issue_text) else warnings).append(label)

        # This whole fallback only ever covers the ~20 pages this tool
        # crawled directly — but Priority Issues - Page Wise reads a much
        # bigger, real dataset (Semrush's Crawled Pages export,
        # site_audit_pages_rows, often 1,000+ pages) whenever that export IS
        # uploaded, which it can be even when the separate Site Audit Issues
        # export (the one this slide actually needs) isn't. Confirmed real:
        # a Geopits report where SEO Issues showed 2 tiny rows while Priority
        # Issues, one slide over, listed 1,137 issues across 150 blog pages
        # alone — same client, same crawl, reading two different Semrush
        # exports with wildly different scope, with nothing on either slide
        # explaining why. This note makes that gap visible instead of
        # letting the two slides silently disagree.
        real_totals = _canonical_page_totals(site_audit_pages_rows, None)
        if real_totals and real_totals.get("with_issues") and (
            not total_pages_checked or real_totals["total"] > total_pages_checked
        ):
            total_issue_count = sum(
                int(float(r.get("issues") or 0)) for r in (site_audit_pages_rows or []) if r.get("issues")
            )
            scope_note = (
                f"This breakdown covers only the {total_pages_checked or 0} page(s) this tool crawled directly. "
                f"Semrush's full crawl found {real_totals['with_issues']:,} of {real_totals['total']:,} pages with "
                f"at least one issue ({total_issue_count:,} issues total) — upload Semrush's Site Audit Issues export to see these "
                "broken down by type here."
            )

    if not errors and not warnings:
        card = _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))
        _textbox(slide, Inches(0.9), Inches(1.3), Inches(10), Inches(0.4), "No issues found on the checked pages.", size=14, color=GOOD)
        return slide

    # Errors and Warnings each get their own full-height container, side by
    # side, rather than stacking in one shared card — stacked lists with 10
    # items per group could run past the bottom of the slide. Card height
    # shrinks when the AI insights section below has real content to show —
    # 10 rows only ever need ~4.0in (header + 10*row_h + padding), the extra
    # 1.6in of card height was previously just empty space at the bottom of
    # each card, now reclaimed for the insights section instead.
    priority_shortlist = _seo_issues_priority_shortlist_text(page_audit, analytics)
    has_insights = bool(insights_ai and insights_ai.get("headline")) or bool(priority_shortlist)
    has_scope_note = bool(scope_note)
    scope_note_line_h = Inches(0.19)
    scope_note_lines = _wrap_lines(scope_note, Inches(12.1), size_pt=10) if has_scope_note else 0
    scope_note_h = scope_note_line_h * scope_note_lines
    col_top = Inches(1.1)
    reserved = Inches(0) if not has_scope_note else scope_note_h + Inches(0.15)
    col_height = (Inches(4.0) if has_insights else Inches(5.6)) - reserved
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
        # Long issue strings ("Missing structured data (JSON-LD) — https://
        # www.<domain>/some-long-slug") wrap to 2 lines at this column width,
        # but row_h only advances y by one line's worth — the wrapped second
        # line then rendered on top of the next row below it (confirmed live
        # on Bharatbenz/Geopits: 10 stacked rows reading as one garbled
        # overlapping block). Truncating to what actually fits on one line
        # keeps every row's advance matching row_h.
        row_text_width_in = (col_width - Inches(0.6) - Inches(0.25)) / 914400
        for issue in shown:
            issue = _truncate_cell(issue, row_text_width_in, size_pt=13, max_lines=1)
            _issue_row(slide, col_left + Inches(0.3), y, col_width - Inches(0.6), issue, severity=("error" if color == BAD else "warn"))
            y += row_h

    note_bottom = col_top + col_height
    if has_scope_note:
        note_bottom += Inches(0.15)
        _textbox(
            slide, Inches(0.6), note_bottom, Inches(12.1), scope_note_h,
            scope_note, size=10, color=TEXT_MUTED,
        )
        note_bottom += scope_note_h
    if has_insights:
        _seo_issues_insights_section(slide, note_bottom + Inches(0.15), insights_ai or {}, priority_shortlist)
    return slide


def _seo_issues_insights_section(slide, top, insights_ai: dict, priority_shortlist: str | None = None):
    """Renders the AI-generated headline / supporting-bullets / executive-
    takeaway insights (2026-09-10 spec) below the Errors/Warnings columns —
    distinct visual weight per part so a reader can skim just the headline
    + takeaway without reading the supporting bullets, per the spec's own
    "one sentence a non-technical stakeholder could read" framing for the
    takeaway line.

    Every item advanced y by a flat guess regardless of how many lines it
    actually wrapped to, with no check against the footer's own fixed
    position (SLIDE_H - 0.4in) — confirmed live 2026-09-17: a long headline
    or 4 wrapped supporting bullets pushed the takeaway line down onto the
    "{client} · {domain}" footer text. Now advances by each item's real
    wrapped-line count (same _wrap_lines heuristic used elsewhere in this
    file) and stops adding items once the next one would cross into the
    footer's zone, truncating the takeaway to one line as a last resort
    instead of dropping it — it's the one line meant to survive a skim."""
    left, width = Inches(0.6), Inches(12.1)
    max_y = SLIDE_H - Inches(0.55)  # stay clear of the footer at SLIDE_H - 0.4in
    line_h = Inches(0.2)
    y = top
    if y >= max_y:
        return

    _textbox(slide, left, y, width, Inches(0.22), "INSIGHTS", size=9.5, bold=True, color=_accent())
    y += Inches(0.26)

    headline = insights_ai.get("headline") or ""
    if headline:
        lines = _wrap_lines(headline, width, size_pt=12.5)
        item_h = line_h * lines + Inches(0.14)
        if y + item_h <= max_y:
            _textbox(slide, left, y, width, line_h * lines, headline, size=12.5, bold=True, color=TEXT_DARK)
            y += item_h

    for point in (insights_ai.get("supporting") or [])[:4]:
        lines = _wrap_lines(point, width - Inches(0.18), size_pt=11)
        item_h = line_h * lines + Inches(0.08)
        if y + item_h > max_y:
            break
        _icon_dot(slide, left, y + Inches(0.07), Inches(0.08), _accent())
        _textbox(slide, left + Inches(0.18), y, width - Inches(0.18), line_h * lines, point, size=11)
        y += item_h

    takeaway = insights_ai.get("takeaway")
    if takeaway:
        text = f"Takeaway: {takeaway}"
        lines = _wrap_lines(text, width, size_pt=11)
        item_h = line_h * lines + Inches(0.05)
        if y + item_h > max_y:
            text = _truncate_cell(text, width / 914400, size_pt=11, max_lines=1)
            lines, item_h = 1, line_h + Inches(0.05)
        if y + item_h <= max_y:
            y += Inches(0.05)
            _textbox(slide, left, y, width, line_h * lines, text, size=11, bold=True, color=_accent())
            y += item_h

    # Closes Key Insights with a traffic-ranked shortlist instead of a
    # separate priority slide (2026-09-23 spec) — deliberately last, after
    # the takeaway, since it's the "now go do this" line.
    if priority_shortlist:
        lines = _wrap_lines(priority_shortlist, width - Inches(0.18), size_pt=11)
        item_h = line_h * lines + Inches(0.08)
        if y + item_h > max_y:
            priority_shortlist = _truncate_cell(priority_shortlist, (width - Inches(0.18)) / 914400, size_pt=11, max_lines=1)
            lines, item_h = 1, line_h + Inches(0.08)
        if y + item_h <= max_y:
            _icon_dot(slide, left, y + Inches(0.07), Inches(0.08), _accent())
            _textbox(slide, left + Inches(0.18), y, width - Inches(0.18), line_h * lines, priority_shortlist, size=11)


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
# 2026-09-22 spec: a careers/jobs INDEX/listing/department/recruitment page
# must never be classified as an individual JobPosting page — same
# discipline and exclusion list as technical_seo_service.py's crawl-based
# JobPosting shape regex (kept in sync — the excluded word can appear as
# ANY hyphen-delimited slug token, not just the leading one), since this
# fallback path (used only when there's no real crawl, just a Semrush
# structured_data export) can otherwise flag "Job Posting" as applicable
# off nothing but a bare careers landing page.
_JOBS_SHAPE_RE = re.compile(
    r"/(?:jobs?|careers?)/(?!(?:[a-z0-9]+-)*(?:apply|openings?|open-positions?|open-roles?|index|list|search"
    r"|browse|all|department|departments|team|teams|recruitment|hiring|employment|join-?us|join-?our-?team"
    r"|culture|benefits|life-at|about|faq)(?:-[a-z0-9]+)*/?(?:$|\?))"
    r"[a-z0-9][a-z0-9-]{3,}/?(?:$|\?)",
    re.IGNORECASE,
)
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
    "Recipe": None, "VideoObject": None,
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


_PAGE_TYPE_LABELS = {
    "Article-type": "Blog / Article", "Product": "Product", "JobPosting": "Job Detail Pages",
    "LocalBusiness": "Local / Location", "Event": "Event", "FAQPage": "FAQ-style Pages", "Other Pages": "Other Pages",
}
_PAGE_TYPE_SCHEMA_LABEL = {
    "Article-type": "Article", "Product": "Product", "JobPosting": "JobPosting",
    "LocalBusiness": "LocalBusiness", "Event": "Event", "FAQPage": "FAQPage",
}
_PAGE_TYPE_WHY_APPLIES = {
    "Article-type": "Content is dated, authored editorial — eligible for article rich results.",
    "Product": "Has price/availability data — eligible for product rich results.",
    # Never "a Careers section exists" — this bucket only ever populates
    # when at least one crawled URL matched the individual-job-detail-page
    # shape (2026-09-22 spec rule 2), so its presence here already IS the
    # evidence, never an inference from a careers/jobs index page alone.
    "JobPosting": "Individual job-detail page URLs detected (not a careers index/listing page) — eligible for job-posting rich results.",
    "LocalBusiness": "Has location/contact details — eligible for map/business-info rich results.",
    "Event": "Contains event date/venue details — eligible for event rich results.",
    "FAQPage": "Contains genuine Q&A content — only pages with this content qualify.",
}
# 2026-09-22 spec rule 1's required table order: Blog/Article, Product,
# JobPosting (only when the bucket exists at all), any other applicable
# content type, then Site-wide, then Other Pages always last. Site-wide
# and Other Pages are appended by build_schema_report_parts itself, in
# that order, after this list is used to sort everything else — never
# left to whatever order aggregate_schema_validation's own pageview/
# priority sort (a different, legitimate concern for ITS OWN consumers)
# happened to produce, which could put "Other Pages" first if it has the
# most traffic.
_PAGE_TYPE_DISPLAY_ORDER = ["Article-type", "Product", "JobPosting", "LocalBusiness", "Event", "FAQPage"]


def _page_type_sort_key(page_type: str) -> int:
    try:
        return _PAGE_TYPE_DISPLAY_ORDER.index(page_type)
    except ValueError:
        return len(_PAGE_TYPE_DISPLAY_ORDER)  # an unlisted type still sorts before Other Pages, after the named ones


def build_schema_report_parts(schema_validation: dict) -> dict:
    """Builds Part 1 (Page Type -> Recommended Schema -> Pages -> Why It
    Applies) and Part 2 (Schema Type -> Applicable -> Present -> Valid ->
    Invalid -> Missing -> Coverage %) rows for the Structured Data & Schema
    Validator slide (2026-09-20 user spec), from aggregate_schema_
    validation's already-computed by_page_type/type_coverage/missing_
    properties — no new crawling, no invented numbers.

    Present vs Valid are kept separate (a page can have the schema block
    present but still fail a required-field check), and Missing (not
    detected at all) is kept separate from Invalid (detected but failing
    validation) — zero Invalid never gets read as "therefore Valid" the
    way the old single "Errors" column allowed.

    WebSite/Organization are SITE-LEVEL/ENTITY-LEVEL schema, not a per-page
    requirement: their row reports Applicable="Site-level", Present/Valid
    as Yes/No/Unknown, and Invalid/Missing/Coverage as "—" rather than a
    page-count percentage — the crawled page total must never be used as
    their applicable-page count. BreadcrumbList and content types (Article/
    Product/LocalBusiness/Event/FAQPage) stay page-level, denominator is
    always the pages that type actually applies to (BreadcrumbList: real
    content pages, never blended with utility "Other Pages"; never the
    full site total). JobPosting is page-level too, but only ever appears
    as a bucket when at least one crawled URL actually looks like an
    individual job-detail page (spec section 29) — a careers index/listing
    page alone never creates a JobPosting row.

    Shared by the slide's own rendering and the AI insights prompt
    (site_audit.py) so both work off identical numbers.

    Row order (2026-09-22 spec rule 1) is enforced here, independent of
    whatever order aggregate_schema_validation's own by_page_type arrives
    in (that list is sorted by pageview/priority for ITS OWN other
    consumers, which could otherwise put "Other Pages" first if it happens
    to carry the most traffic): Blog/Article, Product, JobPosting (only
    when that bucket exists), any other applicable content type, then
    Site-wide, then Other Pages always last."""
    total_pages = schema_validation.get("total_pages") or 0
    by_page_type = schema_validation.get("by_page_type") or []
    type_coverage = {c["type"]: c["pages_with_it"] for c in (schema_validation.get("type_coverage") or [])}
    missing_props_by_type: dict[str, int] = {}
    for m in schema_validation.get("missing_properties") or []:
        if m["severity"] == "required":
            missing_props_by_type[m["type"]] = missing_props_by_type.get(m["type"], 0) + m["pages_missing"]

    content_rows = sorted(
        (r for r in by_page_type if r["page_type"] != "Other Pages"),
        key=lambda r: _page_type_sort_key(r["page_type"]),
    )
    other_pages_rows = [r for r in by_page_type if r["page_type"] == "Other Pages"]

    part1, part2 = [], []
    content_pages_total = 0
    for row in content_rows:
        pt = row["page_type"]
        pages = row["pages"]
        label = _PAGE_TYPE_LABELS.get(pt, pt)
        content_pages_total += pages
        schema_label = _PAGE_TYPE_SCHEMA_LABEL.get(pt, pt)
        part1.append({
            "page_type": label, "recommended_schema": f"{schema_label}, BreadcrumbList", "pages": pages,
            "why_it_applies": _PAGE_TYPE_WHY_APPLIES.get(pt, ""),
        })
        present = row.get("present_pages", 0)
        valid = row.get("valid_pages", 0)
        # Traffic-weighted coverage (2026-09-16 spec: never a flat page-
        # count percentage) when this bucket has real GA4 pageview data to
        # weight by — falls back to the page-count basis only when there's
        # genuinely no traffic data to weight with (valid_pct_traffic_
        # weighted is None, not 0, in that case — see aggregate_schema_
        # validation), never silently presented as if it were weighted.
        traffic_weighted = row.get("valid_pct_traffic_weighted")
        part2.append({
            "schema_type": schema_label, "applicable": pages, "present": present, "valid": valid,
            "invalid": max(present - valid, 0), "missing": max(pages - present, 0),
            "coverage_pct": traffic_weighted if traffic_weighted is not None else row["valid_pct"],
            "coverage_is_traffic_weighted": traffic_weighted is not None, "site_level": False,
        })

    if total_pages:
        part1.append({
            "page_type": "Site-wide", "recommended_schema": "WebSite, Organization", "pages": "Site-level",
            "why_it_applies": "Baseline identity schema, applies to every page.",
        })
        for t in ("WebSite", "Organization"):
            present = type_coverage.get(t, 0) > 0
            # Both types have required-field rules (technical_seo_service._
            # SCHEMA_FIELD_RULES), so "present" always yields a definite
            # Yes/No here — Validation Not Available never needed for these
            # two, only reserved as a concept for a type with no field rules.
            valid = missing_props_by_type.get(t, 0) == 0 if present else None
            part2.append({
                "schema_type": t, "applicable": "Site-level", "present": "Yes" if present else "No",
                "valid": "—" if valid is None else ("Yes" if valid else "No"),
                "invalid": "—", "missing": "—", "coverage_pct": None,
                "coverage_is_traffic_weighted": False, "site_level": True,
            })

    if content_pages_total:
        present = min(type_coverage.get("BreadcrumbList", 0), content_pages_total)
        invalid = missing_props_by_type.get("BreadcrumbList", 0)
        valid = max(present - invalid, 0)
        part2.append({
            "schema_type": "BreadcrumbList", "applicable": content_pages_total, "present": present, "valid": valid,
            "invalid": max(present - valid, 0), "missing": max(content_pages_total - present, 0),
            "coverage_pct": round(100 * valid / content_pages_total) if content_pages_total else 0,
            "coverage_is_traffic_weighted": False, "site_level": False,
        })

    # Schema the site already HAS but that no row above covers (e.g.
    # FAQPage on BharatBenz's /trucks and /buses pages): previously only
    # recommended types got a row, so a deck said nothing about schema that
    # actually exists — and Core Problem could then claim "every page lacks
    # JSON-LD" while SEO Goals counted 26 pages carrying it (2026-09-23).
    # Reported as detected-only: Present/Valid are real counts; Applicable/
    # Missing/Coverage are "—" because no page-type rule says where it
    # should be.
    listed = {r["schema_type"] for r in part2}
    for t, n in sorted(type_coverage.items(), key=lambda kv: -kv[1]):
        if not n or t in listed or t in ("WebSite", "Organization", "BreadcrumbList"):
            continue
        invalid = min(missing_props_by_type.get(t, 0), n)
        part2.append({
            "schema_type": f"{t} (detected)", "applicable": "—", "present": n, "valid": n - invalid,
            "invalid": invalid, "missing": "—", "coverage_pct": None,
            "coverage_is_traffic_weighted": False, "site_level": False, "detected_only": True,
        })

    # Other Pages always last (spec rule 1/11) — never a schema gap, never
    # counted toward content_pages_total, no Part 2 row at all (it has no
    # applicable schema to validate).
    for row in other_pages_rows:
        part1.append({
            "page_type": _PAGE_TYPE_LABELS.get(row["page_type"], row["page_type"]), "recommended_schema": "N/A",
            "pages": row["pages"], "why_it_applies": "No content-specific schema type applies to this template.",
        })

    return {"part1": part1, "part2": part2}


def schema_eligibility_notes(part2: list[dict]) -> dict[str, str]:
    """Google rich-result eligibility note per schema type appearing in
    Part 2 — only populated for a type that's NOT fully eligible (retired,
    restricted, or unverified). Fed to the Part 3 AI insights prompt so it
    never claims a rich-result/CTR benefit for a type Google doesn't
    actually grant one for."""
    notes = {}
    for row in part2:
        flag = _schema_eligibility_flag(row["schema_type"])
        if flag:
            notes[row["schema_type"]] = flag
    return notes


def add_schema_combined_slide(prs: Presentation, schema_validation: dict, schema_ai_insights: dict | None = None):
    """Structured Data & Schema Validator, 2026-09-10 user spec: straight
    into two tables (no summary metric cards) — Part 1 maps each page type
    to its recommended schema and why it applies; Part 2 reports Applicable/
    Present/Valid/Invalid/Missing/Coverage % per schema type, denominator
    always the pages that type applies to, baseline (WebSite/Organization)
    reported as site-level Yes/No/Unknown facts and BreadcrumbList kept in
    its own row rather than blended into content-type coverage.
    Key Insights is AI-written from these exact same numbers (see
    build_schema_report_parts, prompt in structured_data_insights_service.py)
    — grouping by shared root cause, calling out confirmed wins and
    correctly-excluded pages, never inventing a schema type. Falls back to
    tables-only (no Key Insights) when AI insights
    aren't available, same discipline as every other AI section in this
    file."""
    total_pages = schema_validation.get("total_pages") or 0
    if not total_pages:
        return None

    parts = build_schema_report_parts(schema_validation)
    part1, part2 = parts["part1"], parts["part2"]
    if not part1 and not part2:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Structured Data & Schema Validator")
    gsc_rich_results = schema_validation.get("gsc_rich_results") or []
    source = "Site Audit crawl + Search Console URL Inspection" if gsc_rich_results else "Site Audit crawl (JSON-LD)"
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)

    left, width = Inches(0.6), Inches(12.1)
    y = Inches(1.05)
    ROW_H = 0.28

    # Overall vs. priority-page coverage (2026-09-16 spec: "report both") —
    # a single compact line above the tables rather than summary metric
    # cards, matching this slide's existing straight-into-tables design.
    # Only rendered when there's real GA4 pageview data to weight by;
    # silent otherwise rather than showing a misleading 0%/0%.
    overall_cov = schema_validation.get("overall_coverage_pct_traffic_weighted")
    priority_cov = schema_validation.get("priority_page_coverage_pct_traffic_weighted")
    if overall_cov is not None or priority_cov is not None:
        bits = []
        if overall_cov is not None:
            bits.append(f"Overall coverage: {overall_cov}%")
        if priority_cov is not None:
            bits.append(f"Priority pages (Product/LocalBusiness/Event): {priority_cov}%")
        _textbox(slide, left, y, width, Inches(0.24), "  ·  ".join(bits) + "  (traffic-weighted)", size=11.5, bold=True, color=_accent())
        y += Inches(0.32)

    if part1:
        _textbox(slide, left, y, width, Inches(0.24), "Part 1 — Applicable Schema by Page Type", size=12.5, bold=True, color=_accent())
        y += Inches(0.28)
        rows1 = [
            (r["page_type"], r["recommended_schema"], r["pages"] if isinstance(r["pages"], str) else f"{r['pages']:,}", r["why_it_applies"])
            for r in part1
        ]
        y = _draw_table(
            slide, ["Page Type", "Recommended Schema", "Pages", "Why It Applies"], rows1, y,
            col_widths=[2.0, 2.3, 1.0, 6.8], left=left, width=width, row_cap=6, row_height=ROW_H, wrap_cols={3},
        ) + Inches(0.15)

    if part2:
        _textbox(slide, left, y, width, Inches(0.24), "Part 2 — Validation Results", size=12.5, bold=True, color=_accent())
        y += Inches(0.28)

        def _cell(v) -> str:
            return v if isinstance(v, str) else f"{v:,}"

        rows2 = [
            (
                r["schema_type"], _cell(r["applicable"]), _cell(r["present"]), _cell(r["valid"]),
                _cell(r["invalid"]), _cell(r["missing"]),
                "—" if r["coverage_pct"] is None else f"{r['coverage_pct']}%",
            )
            for r in part2
        ]
        y = _draw_table(
            slide, ["Schema Type", "Applicable", "Present", "Valid", "Invalid", "Missing", "Coverage %"], rows2, y,
            col_widths=[2.2, 1.6, 1.4, 1.4, 1.4, 1.4, 2.7], left=left, width=width, row_cap=8, row_height=ROW_H,
        ) + Inches(0.15)
        # Transparency note (2026-09-16 spec: never present a page-count %
        # as if it were traffic-weighted) — only shown when at least one row
        # actually IS traffic-weighted, so the reader knows Coverage % means
        # "share of pageviews to valid pages," not "share of pages," where
        # GA4 data made that possible; silently says nothing when no row has
        # traffic data rather than claiming a basis that isn't true anywhere
        # in the table.
        if any(r.get("coverage_is_traffic_weighted") for r in part2) and y + Inches(0.22) <= SLIDE_H - Inches(0.5):
            note = "Coverage % is traffic-weighted (share of pageviews reaching a valid page) where GA4 pageview data was available for that type; page-count share otherwise."
            _textbox(slide, left, y, width, Inches(0.22), note, size=9.5, color=TEXT_MUTED)
            y += Inches(0.26)

    insights = _drop_contradicting_schema_insights(
        list((schema_ai_insights or {}).get("insights") or []), part2,
    )[:5]
    if insights:
        _insights_strip(slide, left, y, width, insights)
    return slide


_SCHEMA_POSITIVE_CLAIM_RE = re.compile(
    r"valid win|present:?\s*yes|valid:?\s*yes|no action needed|already (?:in place|implemented|present)|is (?:present|implemented|valid)|correctly implemented",
    re.IGNORECASE,
)


def _drop_contradicting_schema_insights(insights: list, part2: list[dict]) -> list:
    """The Key Insights are AI-written from the same table, but nothing
    checked them against it — a BharatBenz deck (2026-09-23) showed
    WebSite/Organization as Present "No" in the table and, on the same
    slide, "WebSite/Organization schema – Valid win. Present Yes, Valid
    Yes. No action needed." An insight that names a schema type the table
    shows as absent AND claims it's present/valid is dropped."""
    absent = [
        r["schema_type"].replace(" (detected)", "") for r in part2
        if r.get("present") in ("No", 0)
    ]
    kept = []
    for text in insights:
        t = str(text)
        if any(a.lower() in t.lower() for a in absent) and _SCHEMA_POSITIVE_CLAIM_RE.search(t):
            continue
        kept.append(text)
    return kept


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
    # This dict's own text is never actually shown for this one issue key —
    # _page_level_issue_records overrides it with _structured_data_fix_text's
    # real, page-type-specific schema name (2026-09-22 spec rule 6 bans the
    # generic "(Article, Product, FAQ, etc)" list below). Kept here only so
    # this issue still participates in every OTHER piece of shared plumbing
    # (severity/category lookup) the same way every other issue key does.
    "Missing structured data (JSON-LD)": ("Add JSON-LD structured data matching the page's content type (Article, Product, FAQ, etc).", "warn", "technical"),
}
_ISSUE_SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}
_STRUCTURED_DATA_ISSUE_NAME = "Missing structured data (JSON-LD)"


_NON_CONTENT_URL_EXTENSIONS = (".xml", ".txt", ".json", ".pdf")


def _is_content_page_url(url: str) -> bool:
    """Rule 2 (2026-09-16 spec): sitemap/robots/raw-data files aren't
    "pages" and must never land in a page-level issue table or directory
    rollup — a full-site crawl (Semrush's site_audit_pages export
    especially) legitimately walks these and can attach issue counts to
    them the same as any real page."""
    path = (urlparse(url or "").path or "").lower()
    if path.endswith(_NON_CONTENT_URL_EXTENSIONS):
        return False
    segments = [s for s in path.split("/") if s]
    if segments and (segments[0].startswith("sitemap") or segments[-1] == "robots.txt"):
        return False
    return True


_SOP_PAGE_TYPE_TO_SCHEMA = {
    "product/category": "Product",
    "location": "LocalBusiness",
    "blog/informational": "Article",
}


def _structured_data_fix_text(url: str) -> str:
    """Real schema type from the page's own URL-structure classification
    (_sop_page_type — the same signal Content SEO's Next Steps already
    trusts), never a generic "(Article, Product, FAQ, etc)" list (2026-09-22
    spec rule 6: "Only recommend a schema type when the audit data supports
    that it applies to the page"). BreadcrumbList is always the fallback,
    never a guess at content type — it applies to every real content page
    regardless of type, matching the Structured Data & Schema Validator
    slide's own Part 1 convention (_PAGE_TYPE_SCHEMA_LABEL's
    "{schema}, BreadcrumbList" pairing)."""
    schema = _SOP_PAGE_TYPE_TO_SCHEMA.get(_sop_page_type(url))
    if schema:
        return f"Add {schema} and BreadcrumbList structured data (JSON-LD) matching this page's content type."
    return "Add BreadcrumbList structured data (JSON-LD) — this page's specific content-type schema could not be determined from its URL structure."


def _page_level_issue_records(page_audit: dict | None, analytics: dict | None) -> dict[str, dict]:
    """Groups every CONFIRMED, named SEO issue (i.e. one _PAGE_ISSUE_FIXES
    recognizes) by page path — the single shared building block for both
    the technical/seo rows that feed Next Steps (_tech_fixes_scored_rows)
    and Priority Issues - Page Wise's own priority rows
    (_page_wise_priority_rows). 2026-09-22 spec rule 1 ("must be derived
    from CONFIRMED PAGE-LEVEL SEO ISSUES... not behave as a generic
    top-pages-with-most-issues report") and rule 4 ("never treat a bare
    issue count as sufficient evidence"): only ever reads this tool's own
    crawl sample (page_audit["pages"]), which carries real per-issue names.
    Semrush's full-site site_audit_pages export is deliberately never read
    here — it only ever gives a bare issue COUNT per URL with no issue
    name, which can never satisfy "traceable to a detected issue"; that
    count-only signal belongs only to the separate SEO Issues/site-audit
    rollup, never to a page-specific recommendation."""
    if not page_audit:
        return {}
    pageviews_by_path, clicks_by_path = _traffic_by_path(analytics)
    by_path: dict[str, dict] = {}
    for page in page_audit.get("pages", []):
        url = page.get("url", "")
        if not _is_content_page_url(url):
            continue
        path = urlparse(url).path or "/"
        key = path.rstrip("/") or "/"
        page_views = pageviews_by_path.get(key, 0)
        clicks = clicks_by_path.get(key, 0)
        entry = by_path.setdefault(key, {
            "path": path, "page_views": page_views, "score": _page_value_score(page_views, clicks), "items": [],
        })
        for issue in page.get("issues", []):
            fix = _PAGE_ISSUE_FIXES.get(issue)
            if not fix:
                continue
            fix_text, severity, category = fix
            if issue == _STRUCTURED_DATA_ISSUE_NAME:
                fix_text = _structured_data_fix_text(url)
            entry["items"].append({
                "severity_rank": _ISSUE_SEVERITY_RANK[severity], "issue": issue,
                "fix_text": fix_text, "category": category,
            })
    return by_path


def _tech_fixes_scored_rows(page_audit: dict, analytics: dict | None = None, site_audit_pages_rows=None) -> list[tuple]:
    """Technical/SEO-category per-issue rows feeding Next Steps: Technical
    SEO — unchanged shape/consumer contract (_tech_fixes_next_steps_items).
    site_audit_pages_rows is accepted-but-unused (kept only for call-site
    compatibility) — 2026-09-22: Priority Issues - Page Wise no longer
    shares this function's output at all; see _page_wise_priority_rows,
    which never reads Semrush's count-only export either, per spec rule 4."""
    if not page_audit:
        return []
    by_path = _page_level_issue_records(page_audit, analytics)
    scored_rows = []
    for entry in by_path.values():
        for item in entry["items"]:
            if item["category"] not in ("technical", "seo"):
                continue
            scored_rows.append((
                item["severity_rank"], -entry["score"], item["issue"], entry["path"], item["fix_text"],
                entry["page_views"], item["category"],
            ))
    scored_rows.sort(key=lambda r: (r[0], r[1]))
    return scored_rows


def _page_wise_priority_rows(
    page_audit: dict | None, analytics: dict | None = None, exclude_paths: set[str] | None = None,
) -> list[dict]:
    """Priority Issues - Page Wise's dedicated row builder (2026-09-22
    spec). Every row is a page with at least one CONFIRMED, named SEO
    issue — a bare Semrush issue count can never produce a row here (see
    _page_level_issue_records). Priority order is severity first, then
    page traffic-value (GA4 pageviews + GSC clicks), then confirmed-issue-
    count only as a tertiary tiebreaker — issue count alone never decides
    priority (spec rule 2: "a page with 97 low-value/count-only issues
    must not automatically outrank a page with fewer but more important
    confirmed SEO issues"). Since this only ever draws from the tool's own
    bounded ~20-page crawl sample (never Semrush's full-site export), the
    near-duplicate-URL flooding spec rule 8 warns against is structurally
    ruled out — there's no large enough candidate pool left for it to
    happen; _page_wise_group_insights' blog/product rollups still surface
    the recurring-template pattern as an insight."""
    by_path = _page_level_issue_records(page_audit, analytics)
    exclude_norm = {p.rstrip("/") or "/" for p in (exclude_paths or set())}

    rows = []
    for key, entry in by_path.items():
        if not entry["items"] or key in exclude_norm:
            continue
        items = sorted(entry["items"], key=lambda it: it["severity_rank"])  # worst severity first
        seen_issue_names: list[str] = []
        seen_fix_texts: list[str] = []
        for it in items:
            if it["issue"] not in seen_issue_names:
                seen_issue_names.append(it["issue"])
                seen_fix_texts.append(it["fix_text"])
        rows.append({
            "path": entry["path"], "page_views": entry["page_views"], "score": entry["score"],
            "severity_rank": items[0]["severity_rank"], "issue_names": seen_issue_names,
            "fix_text": _combined_page_fix(seen_fix_texts, len(items)), "confirmed_issue_count": len(items),
        })
    rows.sort(key=lambda r: (r["severity_rank"], -r["score"], -r["confirmed_issue_count"]))
    return rows


def _seo_issues_priority_shortlist_text(
    page_audit: dict | None, analytics: dict | None, top_n: int = 3,
) -> str | None:
    """SEO Issues slide's closing Key Insights bullet (2026-09-23 spec):
    "Start with your highest-traffic affected pages" — the top_n confirmed-
    issue pages by GA4 pageviews, descending. Computed here in code, not by
    the AI insights prompt, same reasoning as _page_wise_priority_rows'
    docstring: confirmed_issues values must match a real issue name
    word-for-word, which code guarantees and an LLM only approximates.
    Reuses _page_level_issue_records so "confirmed issue" means the same
    thing here as it does on Priority Issues - Page Wise (a named,
    recognized issue — never a bare Semrush issue count). No separate
    slide — this folds straight into _seo_issues_insights_section."""
    by_path = _page_level_issue_records(page_audit, analytics)
    candidates = [e for e in by_path.values() if e["items"] and e["page_views"] > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e["page_views"], reverse=True)
    parts = []
    for entry in candidates[:top_n]:
        issue_names = []
        for item in sorted(entry["items"], key=lambda it: it["severity_rank"]):
            if item["issue"] not in issue_names:
                issue_names.append(item["issue"])
        parts.append(f"{entry['path']} ({entry['page_views']:,} views): {', '.join(issue_names)}")
    return "Start with your highest-traffic affected pages: " + "; ".join(parts) + "."


_TECH_IMPACT_BY_SEVERITY_RANK = {
    0: "High — error-level issue, likely blocking indexing/rankings or breaking the user experience.",
    1: "Medium — warning-level issue, weakens on-page SEO signal quality.",
    2: "Low — informational issue.",
}


def build_structured_technical_recommendations(
    page_audit: dict | None, analytics: dict | None = None, site_audit_pages_rows=None,
) -> list[dict]:
    """Universal SEO Audit Engine spec (2026-09-20) section 31: every
    technical recommendation as its own record carrying Issue, Evidence,
    Affected URLs, Impact, Action, and Priority — not just the single
    combined Fix sentence the Priority Issues - Page Wise TABLE renders
    (that table's own compact free-text cell is a presentation choice, per
    section 47's "renderer is presentation-only" — the underlying decision
    data itself must exist as real fields, which is what this returns).
    Reuses _page_wise_priority_rows' exact same evidence and ordering (one
    record per confirmed-issue page, 2026-09-22) — never a second,
    independently-derived judgment of severity or priority, and never a
    Semrush count-only page (site_audit_pages_rows is accepted-but-unused,
    kept only for call-site compatibility — see _page_level_issue_records'
    docstring for why that source can never produce a recommendation
    here)."""
    if not page_audit:
        return []
    rows = _page_wise_priority_rows(page_audit, analytics)
    _tech_severity_score = {0: 1.0, 1: 0.6, 2: 0.3}
    recommendations = []
    for i, row in enumerate(rows):
        page_views = row["page_views"]
        evidence = f"Detected on {row['path']}"
        if page_views:
            evidence += f" ({page_views:,} pageviews in the analytics window)"
        # Unified Priority Model (spec section 40) — additive alongside the
        # existing `priority` rank (which stays this table's own actual sort
        # key, unchanged). business_relevance/evidence_confidence are 1.0
        # since this is always the client's own crawled page with real
        # detected issues; ranking_opportunity/intent_strength/
        # commercial_value have no real per-page signal here so stay
        # neutral rather than guessed.
        priority_model_result = compute_priority_score(
            business_relevance=1.0,
            search_demand=min(page_views / 1000.0, 1.0) if page_views else 0.0,
            current_visibility=1.0 if page_views else 0.3,
            ranking_opportunity=0.5,
            intent_strength=0.3,
            commercial_value=0.3,
            conversion_potential=0.5,
            technical_severity=_tech_severity_score.get(row["severity_rank"], 0.3),
            effort=0.3,
            evidence_confidence=1.0,
        )
        recommendations.append({
            "issue": "; ".join(row["issue_names"]),
            "evidence": evidence,
            "affected_urls": [row["path"]],
            "impact": _TECH_IMPACT_BY_SEVERITY_RANK.get(row["severity_rank"], "Low"),
            "action": row["fix_text"],
            "priority": i + 1,
            "priority_score": priority_model_result["score"],
            "priority_factors": priority_model_result["factors"],
            "category": "other",
            # 2026-09-21 spec section 10 output structure — page_type is a
            # URL-structure label (metadata), never used to invent an
            # issue; organic_visibility_signal is the real GA4/GSC pageview
            # count when known, None (not 0/guessed) when analytics wasn't
            # joined for this path at all.
            "page_type": _sop_page_type(row["path"]),
            "organic_visibility_signal": page_views if page_views else None,
        })
    return recommendations


def _issue_noun(count: int) -> str:
    """Clean 'N issue'/'N issues' — 2026-09-20 spec bans '(s)' formatting
    and appending the source (e.g. '(Semrush)') beside the count; the
    source is already named once in the slide header."""
    return f"{count:,} issue" if count == 1 else f"{count:,} issues"


_COMBINED_PAGE_FIX_MAX_ITEMS = 3


def _combined_page_fix(distinct_fix_texts: list[str], total_issue_count: int) -> str:
    """One page-specific Fix sentence built from the ACTUAL distinct issues
    detected on this URL (2026-09-20 spec sections 32-36) — every fix_text
    here comes straight from _PAGE_ISSUE_FIXES, already in worst-severity-
    first order, never a generic "review the individual failed checks"
    placeholder. Capped to the 3 most severe distinct fixes (same row-
    height discipline as the rest of this table, see _sop_recommended_
    action's own 2026-09-18 fix) with the remainder named by count, not
    silently dropped."""
    shown = distinct_fix_texts[:_COMBINED_PAGE_FIX_MAX_ITEMS]
    text = " ".join(shown)
    remaining = total_issue_count - len(shown)
    if remaining > 0:
        text += f" ({remaining} more issue{'s' if remaining != 1 else ''} detected on this page.)"
    return text


# /blog/* and /product/* + /integrations* are the two URL patterns this
# slide's data has actually shown pages clustering under in real reports —
# matches the user's own spec examples. Broadened to "integration" (not
# just "/integrations") so per-connector pages like /netsuite-integration
# and /sage-intacct-integration are caught too, not just the hub page.
def _is_blog_pattern(path: str) -> bool:
    return "/blog/" in path or path.rstrip("/").endswith("/blog")


def _is_product_integration_pattern(path: str) -> bool:
    lowered = path.lower()
    return "/product/" in lowered or lowered.rstrip("/").endswith("/product") or "integration" in lowered


_PAGE_WISE_SEVERITY_LABEL = {0: "Error", 1: "Warning", 2: "Info"}
_PAGE_WISE_ISSUE_NAMES_MAX = 3


def _page_wise_issue_names_text(issue_names: list[str]) -> str:
    shown = issue_names[:_PAGE_WISE_ISSUE_NAMES_MAX]
    text = "; ".join(shown)
    remaining = len(issue_names) - len(shown)
    if remaining > 0:
        text += f" (+{remaining} more)"
    return text


def _page_wise_evidence_text(row: dict) -> str:
    """"Why Prioritized" cell (2026-09-22 spec rule 10) — severity first,
    then real page-value signal when one exists, confirmed-issue count only
    as a trailing, clearly-secondary fact. Never invents a commercial-
    importance or traffic claim the data doesn't actually support."""
    severity_label = _PAGE_WISE_SEVERITY_LABEL.get(row["severity_rank"], "Info")
    count = row["confirmed_issue_count"]
    parts = [f"{severity_label}-level issue{'s' if count != 1 else ''} confirmed"]
    if row["page_views"]:
        parts.append(f"{row['page_views']:,} pageviews in the analytics window")
    parts.append(f"{count:,} confirmed issue{'s' if count != 1 else ''} on this page")
    return " — ".join(parts)


def _page_wise_group_insights(rows: list[dict]) -> list[str]:
    """2026-09-20 spec: PATTERN -> EVIDENCE -> ACTION, stating only what the
    data actually shows (a page count and a total confirmed-issue count per
    group) — never an inferred cause. The prior wording guessed a root
    cause from the URL pattern alone ("likely thin/duplicate content ...
    missing BlogPosting schema") and claimed unsupported outcomes
    ("suppress rankings", "waste crawl budget") that this data cannot
    support, and "BlogPosting schema" isn't even the central Schema
    Validator's terminology for that type (see pptx_builder's
    _PAGE_TYPE_SCHEMA_LABEL — "Article"). Exact counts (never "over X")
    stay: plain len()/sum() over the same rows is exact by construction.

    2026-09-22 spec rule 11 ("any page called out here must exist in the
    actual Priority Issues table"): the caller passes only the rows
    actually rendered in the visible table (the top-9 slice), never the
    full candidate pool — a page trimmed from the table by the priority
    sort can never be named in an insight here."""
    blog_rows = [r for r in rows if _is_blog_pattern(r["path"])]
    product_rows = [r for r in rows if _is_product_integration_pattern(r["path"])]

    insights = []
    if blog_rows:
        total_issues = sum(r["confirmed_issue_count"] for r in blog_rows)
        insights.append(
            f"{len(blog_rows)} blog page(s) (/blog/*) have {total_issues:,} combined confirmed SEO issues. "
            "Review the recurring issue types and prioritize high-severity issues that can be addressed "
            "through the blog template."
        )
    if product_rows:
        total_issues = sum(r["confirmed_issue_count"] for r in product_rows)
        insights.append(
            f"{len(product_rows)} product/integration page(s) (/product/*, /integrations) have {total_issues:,} "
            "combined confirmed SEO issues. Review the recurring issue types and prioritize high-severity issues "
            "that can be addressed through the shared template."
        )
    if rows:
        top = max(rows, key=lambda r: r["confirmed_issue_count"])
        insights.append(
            f"\"{top['path']}\" has the highest confirmed issue count among the selected pages with "
            f"{top['confirmed_issue_count']:,} detected issues. Prioritize remediation based on the severity "
            "and type of those issues."
        )
    return insights


def add_priority_issues_page_wise_slide(prs: Presentation, rows: list[dict]) -> object | None:
    """Priority Page | Confirmed SEO Issue(s) | Evidence / Why Prioritized |
    Recommended Fix (2026-09-22 spec). Every row comes straight from
    _page_wise_priority_rows — a page with at least one CONFIRMED, named
    SEO issue; a bare Semrush issue count can never reach this table (see
    _page_level_issue_records' docstring). Insights are computed here, not
    by an AI (see _page_wise_group_insights) — the spec requires an exact
    count, which code guarantees and an LLM only approximates.

    Row order is _page_wise_priority_rows' own severity -> traffic-value ->
    confirmed-issue-count sort, untouched here — issue count is only ever
    the tertiary tiebreaker, never the primary ranking signal (spec rule
    2)."""
    if not rows:
        return None
    shown = rows[:9]
    col_widths = [2.1, 3.0, 3.0, 4.0]
    table_rows = [
        (
            _truncate_cell(r["path"], col_widths[0]),
            _page_wise_issue_names_text(r["issue_names"]),
            _page_wise_evidence_text(r),
            r["fix_text"],
        )
        for r in shown
    ]
    # Rule 11: insights are scoped to the visible table (shown), never the
    # full candidate pool, so nothing named here can be absent from it.
    insights = _page_wise_group_insights(shown)[:4]
    remaining = len(rows) - len(shown)
    if remaining > 0:
        page_word = "page" if remaining == 1 else "pages"
        insights.append(
            f"{remaining:,} additional {page_word} with confirmed SEO issues are affected site-wide. The table "
            f"highlights the {len(shown)} highest-priority pages for remediation."
        )
    return _table_slide(
        prs, "Priority Issues - Page Wise",
        ["Priority Page", "Confirmed SEO Issue(s)", "Evidence / Why Prioritized", "Recommended Fix"], table_rows,
        col_widths=col_widths, source="Site Audit crawl (confirmed page-level issues)", insights=insights[:5],
        # 2026-09-22: only the Priority Page column was ever truncated; the
        # other three (up to 198 chars of joined issue names/evidence/fix
        # text) rendered with word_wrap off, so PowerPoint didn't clip them
        # to the cell — it spilled the overflow across the neighboring
        # columns (confirmed live on a Geopits regen: "alignment and
        # overwrite issues" on this exact slide). wrap_cols makes _draw_table
        # both word-wrap these columns AND size each row to its real
        # wrapped-line count, the same fix already applied to every other
        # long-text table in this file.
        wrap_cols={1, 2, 3},
    )


def add_tech_fixes_slide(
    prs: Presentation,
    page_audit: dict | None,
    analytics: dict | None = None,
    site_audit_pages_rows=None,
    page_wise_ai: dict | None = None,
    page_wise_exclude_paths: set[str] | None = None,
) -> list:
    """Priority Issues - Page Wise only — the technical-category rows this
    function used to also render as a standalone "Tech Fixes — Technical
    Issues" slide now merge into "Next Steps: Technical SEO" instead
    (2026-09-16 user request: "merge Tech Fixes into Next Steps: Technical
    SEO, do not create a standalone slide" — see _tech_fixes_next_steps_items
    and its wiring in build_report). "Tech Fixes — SEO Issues" removed
    2026-09-09 per user request — redundant with the main SEO Issues
    slide's Errors/Warnings, which already covers SEO-category issues
    site-wide. site_audit_pages_rows and page_wise_ai are accepted-but-
    unused (kept only for call-site compatibility, per build_report's own
    established pattern — see page_wise_ai's own history in site_audit.py)
    — 2026-09-22: Priority Issues - Page Wise is now built exclusively from
    confirmed page-level issues (_page_wise_priority_rows), never from
    Semrush's count-only export or an AI guess."""
    if not page_audit:
        return []

    rows = _page_wise_priority_rows(page_audit, analytics, page_wise_exclude_paths)
    slide = add_priority_issues_page_wise_slide(prs, rows)
    return [slide] if slide else []


def _tech_fixes_next_steps_items(
    page_audit: dict | None, analytics: dict | None, site_audit_pages_rows: list[dict] | None, max_items: int = 5,
) -> list[str]:
    """The technical-category rows from _tech_fixes_scored_rows, GROUPED and
    reformatted as Next Steps bullets (2026-09-23 spec: consolidate every
    URL sharing the same issue+fix into ONE action instead of one bullet
    per page — a template-level issue on 12 pages must read as one
    recommendation, not 12, per the spec's "Group Related Issues"/"Avoid
    Repetition" rules). Grouping key is (issue, fix_text) rather than issue
    alone: Missing structured data's fix_text varies by each page's own
    classified schema type (_structured_data_fix_text) — pages needing
    different schema types stay separate, evidence-backed actions instead
    of merging into one misleadingly generic bullet (spec's schema rule).
    Ranked by severity first, then aggregate GA4 traffic across the
    group's pages — never by raw page count alone (spec: "do not
    prioritize solely by raw issue count"). Feeds BOTH the AI-generated
    and static-fallback versions of "Next Steps: Technical SEO" (see its
    call site in build_report) so the merge holds regardless of which one
    actually renders for a given report."""
    if not page_audit:
        return []
    scored_rows = _tech_fixes_scored_rows(page_audit, analytics, site_audit_pages_rows)
    technical_rows = [r for r in scored_rows if r[6] == "technical"]

    groups: dict[tuple[str, str], dict] = {}
    for severity_rank, _neg_score, issue, path, fix_text, page_views, _category in technical_rows:
        g = groups.setdefault((issue, fix_text), {"severity_rank": severity_rank, "paths": [], "traffic": 0})
        g["severity_rank"] = min(g["severity_rank"], severity_rank)
        g["traffic"] += page_views
        if path not in g["paths"]:
            g["paths"].append(path)

    ordered = sorted(groups.items(), key=lambda kv: (kv[1]["severity_rank"], -kv[1]["traffic"]))[:max_items]

    action_word = {0: "Fix", 1: "Resolve", 2: "Improve"}
    items = []
    for (issue, fix_text), g in ordered:
        paths = g["paths"]
        verb = action_word.get(g["severity_rank"], "Resolve")
        if len(paths) == 1:
            evidence = f"confirmed on {paths[0]}"
        elif len(paths) <= 3:
            evidence = f"confirmed on {len(paths)} pages ({', '.join(paths)})"
        else:
            # Page count and examples only — a shared template is never
            # claimed without evidence of one (Technical SEO spec
            # 2026-09-23, section 6).
            evidence = f"confirmed on {len(paths)} pages (e.g. {', '.join(paths[:2])})"
        items.append(f"{verb} {issue} — {evidence} — {fix_text}")
    return items


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


def _insights_strip(slide, left, top, width, insights, title="Key Insights", max_y=None, max_items=5):
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
    # Width-aware wrap estimate (~14 chars/inch at size 11) — a fixed
    # chars-per-line regardless of column width caused text in narrow
    # columns (e.g. the PageSpeed sidebar) to under-reserve height and
    # overlap the next bullet.
    text_width_in = max(width - Inches(0.18), Inches(0.5)) / 914400
    chars_per_line = max(20, int(text_width_in * 14))
    line_h = Inches(0.22)
    heading_h = Inches(0.26)

    # Dry-run the fit check BEFORE drawing anything — a table with a tall
    # row_cap (e.g. Website Structure's 12 rows) can push `top` so close to
    # max_y that zero bullets actually fit, which used to still draw the
    # "KEY INSIGHTS" heading with nothing under it (orphan heading, no
    # bullets, on the rendered slide).
    fitted = []
    y = top + heading_h
    for item in insights[:max_items]:
        lines = max(1, -(-len(item) // chars_per_line))
        item_h = line_h * lines + Inches(0.05)
        if y + item_h > max_y:
            break
        fitted.append((item, lines, item_h))
        y += item_h
    if not fitted:
        return top

    _textbox(slide, left, top, width, Inches(0.24), title.upper(), size=9.5, bold=True, color=_accent())
    y = top + heading_h
    for item, lines, item_h in fitted:
        _icon_dot(slide, left, y + Inches(0.07), Inches(0.08), _accent())
        _textbox(slide, left + Inches(0.18), y, width - Inches(0.18), line_h * lines, item, size=11)
        y += item_h
    return y


def _draw_table(slide, headers, rows, top, col_widths=None, row_cap=None, left=None, width=None, insights=None, row_height=0.4, wrap_cols=None, insights_max=5, return_table=False):
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
    shown_rows = rows[:row_cap]
    n_rows = len(shown_rows) + 1

    # Per-row height, computed for real instead of assumed uniform (2026-09-16
    # fix — confirmed live: Key Insights strips overlapping the table above
    # them on Priority Issues, Search Opportunities, and Cross-Competitor
    # Summary, all tables using wrap_cols). PowerPoint auto-grows a row to
    # fit wrapped cell text at DISPLAY time regardless of what height this
    # code declares — python-pptx has no way to ask PowerPoint how tall that
    # will actually render, so the only fix is to stop assuming every row is
    # exactly row_height tall and estimate real wrapped-line count instead,
    # the same _wrap_lines heuristic used elsewhere in this file, so the
    # declared (and returned `bottom`) height tracks what will actually
    # render closely enough that nothing positioned after it lands on top.
    col_widths_emu = [Inches(w) for w in col_widths] if col_widths else [width // n_cols] * n_cols
    # Bounded against the actual page (2026-09-17 fix — confirmed live:
    # Onboarding Breakdown's uncapped wrap let a long "Directional
    # Suggestion" cell balloon a row past 1in tall, and with 5 such rows the
    # table's real bottom ran 0.9in past the slide edge, caught by
    # _audit_slide_geometry). A flat per-row line cap alone isn't enough —
    # 5 rows at even 3 lines each can still overflow depending on `top` and
    # row_height — so this tries progressively tighter uniform caps (3, then
    # 2, then 1 line/row) and stops at the first one whose total height fits
    # what's actually left on the slide below `top`. Cap 1 (single line,
    # ellipsis-truncated — the same fallback _truncate_cell already uses on
    # Priority Issues/Tech Fixes) always fits row_cap rows at row_height
    # each, so this can never leave the table taller than the page.
    available = SLIDE_H - top - Inches(0.3)

    def _row_line_caps(max_lines: int) -> list[int]:
        caps = [1]  # header row never wraps
        for row in shown_rows:
            lines_needed = 1
            if wrap_cols:
                for j in wrap_cols:
                    if j < len(row):
                        lines_needed = max(lines_needed, _wrap_lines(str(row[j]), col_widths_emu[j], size_pt=11))
            caps.append(min(lines_needed, max_lines))
        return caps

    for max_lines in (3, 2, 1):
        row_line_caps = _row_line_caps(max_lines)
        row_heights = [Inches(row_height) * cap for cap in row_line_caps]
        height = sum(row_heights, Emu(0))
        if height <= available or max_lines == 1:
            break
    gframe = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = gframe.table
    table.first_row = False  # suppress the built-in banded-header theme so our colors apply cleanly

    if col_widths:
        for i, w in enumerate(col_widths):
            table.columns[i].width = Inches(w)
    for i, h in enumerate(row_heights):
        table.rows[i].height = h

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
            text = str(val)
            if wrap_cols and j in wrap_cols:
                width_in = col_widths_emu[j] / 914400
                text = _truncate_cell(text, width_in, size_pt=11, max_lines=row_line_caps[i])
            cell.text = text
            cell.fill.solid()
            cell.fill.fore_color.rgb = ROW_ALT if i % 2 == 0 else WHITE
            cell.text_frame.word_wrap = wrap_cols is not None and j in wrap_cols
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(11)
            para.font.color.rgb = TEXT_DARK

    bottom = top + height
    if insights:
        bottom = _insights_strip(slide, left, bottom + Inches(0.15), width, insights, max_items=insights_max)
    if return_table:
        return bottom, table
    return bottom


def _table_slide(prs, title, headers, rows, col_widths=None, source=None, insights=None, row_cap=None, row_height=0.4, wrap_cols=None, insights_max=5):
    slide = _blank_slide(prs)
    _content_header(slide, title)
    if source:
        _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)
    _draw_table(slide, headers, rows, Inches(1.2), col_widths=col_widths, row_cap=row_cap, insights=insights, row_height=row_height, wrap_cols=wrap_cols, insights_max=insights_max)
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


_TRAFFIC_SOURCES_MIN_SAMPLE_SESSIONS = 20
_TRAFFIC_SOURCES_OVER_RELIANCE_PCT = 45
_TRAFFIC_SOURCES_DIRECT_INFLATION_PCT = 30


def _traffic_sources_insights(shown: list[dict], total_sessions: float) -> list[str]:
    """Traffic Sources insight rules (2026-09-10 spec): this is always a
    SINGLE snapshot (no prior-period rows are ever available to this
    report tool) — nothing here may claim growth, decline, or any trend
    ("grew"/"up"/"declining"/"improving" are all fabricated implications
    with no second period to compare against). Every channel name/figure
    referenced comes only from `shown` (the exact rows the table renders,
    already sorted+capped by the caller) — never introduced, rounded, or
    recalled from elsewhere. Up to 4 bullets, prioritized:
    1. Composition — largest share paired with a within-period quality
       signal (never size alone).
    2. Over-reliance — flagged only when one channel's share crosses a
       real concentration threshold, not asserted for a healthy mix.
    3. Data-quality — elevated Direct share framed as "worth
       investigating" (a common symptom of lost/broken UTM tagging), never
       stated as a confirmed fact.
    4. Sample-size caution — any shown channel below a real statistical
       floor is flagged as too small to conclude from, not treated as a
       finding (return-rate "strongest" is also excluded from this pool).
    Return rate / mismatch fill remaining slots as efficiency signals —
    valid within a single period since they're ratios, not trends."""
    if not shown:
        return []

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

    # 1. Composition: largest share, paired with a within-period quality
    # signal — never size alone.
    top = max(verified, key=lambda r: r["sessions"])
    if top["return_rate"] is not None:
        quality = f"a {top['return_rate']:.0f}% return rate this period"
    elif top["new_users"] or top["returning_users"]:
        quality = f"{top['new_users']:,.0f} new vs {top['returning_users']:,.0f} returning users this period"
    else:
        quality = "no new-vs-returning data available for this channel"
    insights.append(
        f"{top['channel']}{_channel_note(top['channel'])} makes up the largest share of sessions "
        f"({_pct_text(top['pct_share'])} of {int(total_sessions):,} total) — {quality}."
    )
    used.add(top["channel"])

    # 2. Over-reliance: only flagged when the mix is genuinely concentrated
    # — a real threshold, not asserted for every report.
    if top["pct_share"] >= _TRAFFIC_SOURCES_OVER_RELIANCE_PCT and len(insights) < 4:
        insights.append(
            f"{_pct_text(top['pct_share'])} of sessions come through {top['channel']} alone — a concentrated "
            "acquisition mix with real exposure if that one channel is disrupted, not a diversified one."
        )

    # 3. Data-quality flag: elevated Direct share is a classic symptom of
    # lost/broken UTM tagging misattributing real campaign traffic as
    # "Direct" — framed as worth investigating, never stated as fact,
    # since a single snapshot can't distinguish a tracking gap from
    # genuine type-in/bookmark traffic.
    if len(insights) < 4:
        direct = next((r for r in verified if (r["channel"] or "").strip().lower() == "direct"), None)
        if direct and direct["pct_share"] >= _TRAFFIC_SOURCES_DIRECT_INFLATION_PCT:
            insights.append(
                f"Direct accounts for {_pct_text(direct['pct_share'])} of sessions — worth investigating whether "
                "UTM tagging or campaign attribution is incomplete, since Direct is where GA4 dumps traffic it "
                "can't otherwise source; this can't be confirmed from a single snapshot alone."
            )

    # 4. Sample-size caution: a shown channel with too few sessions to
    # draw any real conclusion from — flagged, not presented as a finding.
    if len(insights) < 4:
        tiny = [r for r in verified if 0 < r["sessions"] < _TRAFFIC_SOURCES_MIN_SAMPLE_SESSIONS and r["channel"] not in used]
        if tiny:
            smallest = min(tiny, key=lambda r: r["sessions"])
            insights.append(
                f"{smallest['channel']}{_channel_note(smallest['channel'])} has only {int(smallest['sessions'])} session(s) "
                "in this period — too small a sample to draw any real conclusion from, shown for completeness only."
            )
            used.add(smallest["channel"])

    # Efficiency signal (fills a remaining slot): verified strongest return
    # rate, excluding any channel below the sample-size floor so a
    # near-100%-of-7-sessions channel can't be crowned "strongest."
    if len(insights) < 4:
        rate_ranked = sorted(
            (r for r in verified if r["return_rate"] is not None and r["sessions"] >= _TRAFFIC_SOURCES_MIN_SAMPLE_SESSIONS),
            key=lambda r: r["return_rate"], reverse=True,
        )
        if rate_ranked and rate_ranked[0]["channel"] not in used:
            best = rate_ranked[0]
            insights.append(
                f"{best['channel']}{_channel_note(best['channel'])} has the strongest return rate this period at "
                f"{best['return_rate']:.0f}% ({_pct_text(best['pct_share'])} of sessions)."
            )
            used.add(best["channel"])

    # Efficiency signal: size/quality mismatch — large share, return rate
    # well below the group's own average (cross-segment check within this
    # period, not a trend).
    if len(insights) < 4:
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
                    f"{m['return_rate']:.0f}% return rate this period, well below the {avg_rate:.0f}% average across "
                    "channels — a size/quality mismatch worth a closer look."
                )
                used.add(m["channel"])

    return insights[:4]


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


def build_branded_vs_nonbranded_comparison(branded_queries: list[dict], nonbranded_queries: list[dict]) -> dict:
    """Part 1 of the Branded vs Non-Branded slide (2026-09-10 user spec):
    Clicks/Impressions/CTR/Avg. Position per group, plus branded's share of
    total clicks — the headline number. Avg. Position is impression-
    weighted, matching how GSC itself reports a period average. Pure data
    shaping, no AI — every number here is a direct sum/average of the rows
    already fetched, shared by the slide's own rendering and the Part 4 AI
    insights prompt so both work off identical figures."""
    def _group_stats(rows: list[dict]) -> dict:
        clicks = sum(int(r.get("clicks", 0) or 0) for r in rows)
        impressions = sum(int(r.get("impressions", 0) or 0) for r in rows)
        ctr_pct = round(clicks / impressions * 100, 1) if impressions else 0.0
        avg_position = (
            round(sum(r.get("position", 0) * r.get("impressions", 0) for r in rows) / impressions, 1)
            if impressions else 0.0
        )
        return {"clicks": clicks, "impressions": impressions, "ctr_pct": ctr_pct, "avg_position": avg_position}

    branded = _group_stats(branded_queries)
    nonbranded = _group_stats(nonbranded_queries)
    total_clicks = branded["clicks"] + nonbranded["clicks"]
    total_impressions = branded["impressions"] + nonbranded["impressions"]
    branded_share_pct = round(branded["clicks"] / total_clicks * 100, 1) if total_clicks else 0.0
    # Table shows share (%), not raw volume (2026-09-16 user spec) — the
    # headline elsewhere on the slide already states branded_share_pct, so
    # branded's clicks_pct here MUST come from this exact same division
    # (same total_clicks, same rounding) or the slide would show two
    # different percentages for one figure. Non-branded's pct is 100 minus
    # branded's, not its own independent division, so the pair always sums
    # to exactly 100.0 regardless of rounding.
    branded["clicks_pct"] = branded_share_pct
    nonbranded["clicks_pct"] = round(100.0 - branded_share_pct, 1)
    branded["impressions_pct"] = round(branded["impressions"] / total_impressions * 100, 1) if total_impressions else 0.0
    nonbranded["impressions_pct"] = round(100.0 - branded["impressions_pct"], 1) if total_impressions else 0.0
    return {
        "branded": branded, "nonbranded": nonbranded, "branded_share_pct": branded_share_pct,
        "total_clicks": total_clicks, "total_impressions": total_impressions,
    }


_DEMAND_GAP_TARGET_PERIOD_DAYS = 30
_DEMAND_GAP_MIN_IMPRESSIONS = 50


def build_branded_dependency_narrative(
    comparison: dict, nonbranded_queries: list[dict],
    competitor_positions: dict[str, list[dict]] | None = None,
    period_days: int = _DEMAND_GAP_TARGET_PERIOD_DAYS,
) -> dict:
    """Parts 1 (headline), 2 (demand gap) and 3 (cost of inaction + one
    concrete example) of the Branded vs Non-Branded "outcome case" rebuild
    (2026-09-11 user spec) — deterministic, no AI, so the slide still
    carries real analysis with no AI provider configured (Part 4, Key
    Insights, stays the only AI-written piece). The position-appropriate
    CTR benchmark reuses _ctr_decay_pct, the same strict decay model
    add_keyword_gap_slide already uses for click projections, instead of
    inventing a second benchmark system. Every modeled number states its
    own assumption inline rather than standing alone as a bare figure."""
    branded, nonbranded = comparison["branded"], comparison["nonbranded"]
    pct = comparison["branded_share_pct"]
    scale = (_DEMAND_GAP_TARGET_PERIOD_DAYS / period_days) if period_days else 1.0

    if pct >= 70:
        headline = (
            f"{pct:.1f}% of your organic clicks come from people already searching your brand name — "
            f"non-branded discovery is minimal."
        )
    elif pct >= 40:
        headline = (
            f"{pct:.1f}% of your organic clicks are branded search — a significant share of your traffic "
            f"depends on people who already know your brand, not on discovering it through a problem "
            f"they're searching to solve."
        )
    else:
        headline = (
            f"Only {pct:.1f}% of your organic clicks are branded — non-branded discovery already carries "
            f"the majority of your organic traffic."
        )

    demand_gap = None
    benchmark_ctr = _ctr_decay_pct(round(nonbranded["avg_position"])) if nonbranded["impressions"] else 0.0
    if benchmark_ctr > nonbranded["ctr_pct"] and nonbranded["impressions"]:
        extra_clicks_period = nonbranded["impressions"] * (benchmark_ctr - nonbranded["ctr_pct"]) / 100
        extra_clicks_monthly = round(extra_clicks_period * scale)
        period_note = "" if period_days == _DEMAND_GAP_TARGET_PERIOD_DAYS else f", scaled from the {period_days}-day period this data covers"
        demand_gap = {
            "benchmark_ctr_pct": benchmark_ctr, "extra_clicks_monthly": extra_clicks_monthly,
            "text": (
                f"Estimate: closing non-branded click-through rate from {nonbranded['ctr_pct']:.1f}% to the "
                f"{benchmark_ctr:.1f}% typical for position {nonbranded['avg_position']:.1f} would add roughly "
                f"{extra_clicks_monthly:,} clicks per 30 days — assumes impressions hold steady and no ranking "
                f"change{period_note}."
            ),
        }

    concrete_example = None
    candidates = []
    for q in nonbranded_queries:
        impressions = float(q.get("impressions", 0) or 0)
        if impressions < _DEMAND_GAP_MIN_IMPRESSIONS:
            continue
        position = float(q.get("position", 0) or 0)
        ctr_pct = float(q.get("ctr", 0) or 0) * 100
        q_benchmark = _ctr_decay_pct(round(position))
        if q_benchmark > ctr_pct:
            candidates.append((impressions, q.get("query"), position, ctr_pct, q_benchmark))
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        impressions, query, position, ctr_pct, q_benchmark = candidates[0]
        modeled_clicks = round(impressions * (q_benchmark - ctr_pct) / 100 * scale)
        concrete_example = {
            "query": query, "impressions": int(impressions), "ctr_pct": round(ctr_pct, 1), "position": round(position, 1),
            "text": (
                f"\"{query}\" draws {int(impressions):,} impressions at position {position:.1f} but only a "
                f"{ctr_pct:.1f}% click-through rate. Estimate: closing that to the {q_benchmark:.1f}% typical "
                f"for this position is roughly {modeled_clicks:,} extra clicks per 30 days, same assumptions "
                f"as above."
            ),
        }

    competitor_clause = ""
    if competitor_positions:
        nb_terms = {str(q.get("query", "")).strip().lower() for q in nonbranded_queries if q.get("query")}
        overlap = []
        for domain, rows in competitor_positions.items():
            for r in rows:
                kw = str(r.get("keyword", "")).strip().lower()
                if kw and kw in nb_terms and r.get("position") is not None:
                    overlap.append((domain, r.get("keyword"), r.get("position")))
        if overlap:
            overlap.sort(key=lambda o: _num(o[2], default=999))
            domain, kw, pos = overlap[0]
            competitor_clause = (
                f" At least {len(overlap)} of your non-branded search terms are already ranked by a tracked "
                f"competitor — \"{kw}\" is currently held by {domain} at position {pos}, not by you."
            )
        else:
            competitor_clause = (
                f" {len(competitor_positions)} competitor domain(s) are tracked in this account; no direct "
                f"overlap with your top non-branded terms showed up in this dataset, but it's worth watching "
                f"as that content grows."
            )

    if pct >= 40:
        cost_of_inaction = (
            "Relying this heavily on branded search caps growth at how many people already know to look for "
            "you by name — there's little to no new-customer discovery happening through search. If brand "
            "awareness ever plateaus (a paid-spend cut, a slower launch cadence, a louder competitor), organic "
            f"traffic has no non-branded floor to fall back on.{competitor_clause}"
        )
    else:
        cost_of_inaction = (
            f"Even with a healthy non-branded share, the {pct:.1f}% of clicks still tied to brand search is "
            f"demand that stops the moment brand awareness dips — worth protecting deliberately, not just "
            f"treating as the smaller number on the table.{competitor_clause}"
        )

    return {
        "headline": headline, "demand_gap": demand_gap,
        "concrete_example": concrete_example, "cost_of_inaction": cost_of_inaction,
    }


_HIGH_POTENTIAL_MIN_IMPRESSIONS_PAGE = 50

# Country tiering (2026-09-16 user spec) — single split by click volume:
# clicks >= threshold is "material" (gets an individual Fix), clicks below
# it is "low-signal" (grouped summary only, never an individual Fix).
_COUNTRY_MIN_CLICK_THRESHOLD = 15
_COUNTRY_LOW_SIGNAL_MIN_IMPRESSIONS = 100

# 2026-09-22 spec's own required disclosure — country_rows only ever
# carries impressions/clicks/CTR/country (see build_high_potential_
# countries), never page/query-level data, so this sentence is always
# true here and always appended.
_COUNTRY_EVIDENCE_DISCLAIMER = (
    "Country-level data indicates search presence; page/query validation is required before prescribing a "
    "specific optimization."
)


_PAGE_CTR_BENCHMARK_SOURCE = "internal position-based CTR benchmark (not an external published study)"


def _ctr_band_for_position(position: float) -> tuple[float, float] | None:
    """Rough typical-CTR-by-position bands, our own model, deliberately
    coarse (these vary a lot by query intent/vertical) — used only to
    catch a page whose CTR is NOTICEABLY under its band, not to make a
    precise claim. Cited as _PAGE_CTR_BENCHMARK_SOURCE, never as an
    external study, since these numbers aren't sourced from one."""
    if 1 <= position <= 3:
        return (15.0, 30.0)
    if 4 <= position <= 10:
        return (3.0, 10.0)
    if 11 <= position <= 20:
        return (1.0, 3.0)
    return None


def build_high_potential_pages(page_rows: list[dict]) -> list[dict]:
    """Part 2 flagging logic (2026-09-16 user spec): the lever depends on
    whether the page is already on page 1. position <= 10 -> lever is CTR
    (page is visible, the opportunity is closing the click-through gap via
    title/meta — only flagged if CTR is noticeably under the position's
    benchmark band). position > 10 -> lever is RANKING (a title/meta
    rewrite won't help until the page ranks higher; the opportunity is
    internal links/content depth). Each row gets exactly ONE lever, never
    both glued together. Pages below the minimum-impressions floor are
    excluded outright — not enough signal to act on. Sorted by impressions
    (the pages worth the most attention first), each row grounded only in
    its own real position/CTR/impressions, no invented numbers."""
    flagged = []
    for r in page_rows:
        impressions = float(r.get("impressions", 0) or 0)
        if impressions < _HIGH_POTENTIAL_MIN_IMPRESSIONS_PAGE:
            continue
        position = float(r.get("position", 0) or 0)
        ctr_pct = float(r.get("ctr", 0) or 0) * 100

        if position <= 10:
            lever = "CTR"
            band = _ctr_band_for_position(position)
            if not (band and ctr_pct < band[0]):
                continue  # already visible AND already clearing its CTR band — no fix to make
            fix = (
                f"Rewrite title/meta to close the click-through-rate gap — currently {ctr_pct:.1f}%, "
                f"typical for position {position:.0f} is {band[0]:.0f}-{band[1]:.0f}% "
                f"(source: {_PAGE_CTR_BENCHMARK_SOURCE})."
            )
        else:
            lever = "RANKING"
            fix = (
                f"This page isn't ranking on page 1 (position {position:.1f}) — a title/meta rewrite won't "
                f"move clicks until ranking improves. Focus on internal links and content depth to close the "
                f"ranking gap first; revisit CTR once it reaches page 1."
            )
        flagged.append({
            "page": r.get("page"), "impressions": int(impressions), "ctr_pct": round(ctr_pct, 1),
            "position": round(position, 1), "fix": fix, "lever": lever,
            "page_type": r.get("page_type"),
        })
    flagged.sort(key=lambda r: r["impressions"], reverse=True)
    return flagged


# Search Opportunities — Pages, CTR-fix-only re-scope (2026-09-18 user spec).
# Distinct from build_high_potential_pages above (which still feeds the
# Branded vs Non-Branded Key Insights prompt unchanged): this narrows
# selection to the position band GSC's own CTR curve is well-behaved for
# (4-10 — page 1 but not the top 3, where CTR bands are noisy at the extremes)
# and to pages an on-page fix can realistically move, per the explicit ask
# ("pages where improving CTR can generate additional organic clicks without
# requiring a new page") — position >10 pages need ranking work, not a
# title/meta fix, so they're out of scope for this table entirely.
_SOP_MIN_IMPRESSIONS = _HIGH_POTENTIAL_MIN_IMPRESSIONS_PAGE
_SOP_POSITION_LO, _SOP_POSITION_HI = 4.0, 10.0

_SOP_PAGE_TYPE_PATTERNS = [
    ("product/category", re.compile(r"/(products?|shop|store|item|sku|categor(y|ies)|collections?|catalog)(/|$)", re.I)),
    ("service/pricing", re.compile(r"/(services?|solutions?|pricing|plans?)(/|$)", re.I)),
    ("location", re.compile(r"/(locations?|near-me|branches?|stores?)(/|$)", re.I)),
    ("blog/informational", re.compile(r"/(blog|articles?|guides?|resources?|news|faq)(/|$)", re.I)),
]

def _sop_page_type(url: str) -> str | None:
    """Page type inferred from URL structure only (real data, never
    guessed from content) — used to vary the Recommended Action instead
    of writing the same "rewrite title/meta" line for every row."""
    path = urlparse(url or "").path
    for label, pattern in _SOP_PAGE_TYPE_PATTERNS:
        if pattern.search(path):
            return label
    return None


def _sop_normalize_url(url: str) -> str:
    p = urlparse(url or "")
    return f"{p.netloc}{p.path.rstrip('/')}".lower()


def _sop_crawled_meta_index(crawled_pages: list[dict] | None) -> dict:
    """Maps normalized URL -> crawled {title, meta_description, ...} for
    pages the technical crawl actually fetched (page_limit=20, so this
    won't cover every GSC page — a row without a match falls back to
    slug-only reasoning, or an explicit data-insufficient statement)."""
    index = {}
    for p in crawled_pages or []:
        meta = p.get("meta")
        if p.get("url") and meta and meta.get("title"):
            index[_sop_normalize_url(p["url"])] = meta
    return index


_SOP_TOPIC_MAX_CHARS = 40  # keeps the whole sentence table-cell-sized (2026-09-18 fix — see below)
_SOP_QUERY_MIN_IMPRESSIONS = 10  # a per-query floor, smaller than the page-level floor — one query is a slice of a page's total impressions, not the whole page's signal


def _sop_query_type(query: str, brand_tokens) -> str:
    if not brand_tokens:
        return "unknown"
    return "brand" if is_branded_or_near_brand(query, brand_tokens) else "non_brand"


def _sop_page_query_index(page_query_rows: list[dict] | None, brand_tokens) -> dict[str, list[dict]]:
    """normalized page url -> its real GSC (page, query) rows, each tagged
    brand/non_brand/unknown (2026-09-20 spec) — the actual query driving a
    page's visibility, never a keyword derived from the URL slug. Empty
    when page_query_rows wasn't supplied (older caller, or the GSC (page,
    query) pull failed) — callers must treat "no entry for this page" as
    "no query-level data available," not "no queries exist"."""
    index: dict[str, list[dict]] = {}
    for r in page_query_rows or []:
        query = (r.get("query") or "").strip()
        url = r.get("page") or ""
        key = _sop_normalize_url(url)
        if not query or not key:
            continue
        index.setdefault(key, []).append({
            "query": query,
            "impressions": float(r.get("impressions", 0) or 0),
            "clicks": float(r.get("clicks", 0) or 0),
            "ctr": float(r.get("ctr", 0) or 0) * 100,
            "position": float(r.get("position", 0) or 0),
            "query_type": _sop_query_type(query, brand_tokens),
        })
    return index


def _sop_driving_query(queries: list[dict]) -> dict | None:
    """The strongest NON-BRAND opportunity-driving query for a page
    (2026-09-20 spec, "Query Selection for Recommendations" + "Brand vs
    Non-Brand Classification"): position generally 4-10 with meaningful
    impressions. Branded queries are never used to justify an incremental
    SEO recommendation — a branded query's low CTR often reflects
    navigational intent, not a fixable SERP problem, even when it's the
    page's biggest query by volume."""
    candidates = [
        q for q in queries
        if q["query_type"] == "non_brand" and q["impressions"] >= _SOP_QUERY_MIN_IMPRESSIONS
        and _SOP_POSITION_LO <= q["position"] <= _SOP_POSITION_HI
    ]
    return max(candidates, key=lambda q: q["impressions"]) if candidates else None


def _sop_recommended_action(position: float, meta: dict | None, driving_query: dict | None) -> str:
    """Builds a page-specific Recommended Action from only what this row's
    real data supports — impression volume, page-one position, and, when a
    real GSC (page, query) row identifies one, the actual non-brand query
    driving this page's visibility (2026-09-20 spec). Never derives a
    keyword from the URL slug: when no qualifying query is available, says
    so plainly instead of guessing one.

    2026-09-21 spec (no universal CTR benchmark): CTR is an observed,
    displayed metric, never a pass/fail threshold — this function does not
    take a CTR value or a benchmark band as input at all, so it's
    structurally impossible for the reason to read "CTR trails benchmark
    X-Y%." The reason is impressions + position + query relevance only.
    Any action beyond a real title/query mismatch (which the crawled title
    data actually supports) would be asserting a SERP-messaging problem we
    have no SERP data to back up — that case states the evidence gap
    ("Query/SERP validation required...") instead of inventing a fix.

    Kept deliberately to ONE short sentence (2026-09-18 fix — confirmed
    live on the BharatBenz regen: a longer version didn't fit _draw_table's
    row height at 9 rows and collapsed every row to one truncated line)."""
    if not driving_query:
        return "Insufficient query-level GSC evidence."

    query_text = driving_query["query"]
    short_query = query_text if len(query_text) <= _SOP_TOPIC_MAX_CHARS else query_text[:_SOP_TOPIC_MAX_CHARS].rstrip() + "…"
    reason = (
        f"Strong impression volume ({driving_query['impressions']:,.0f}) with page-one visibility "
        f"(position {position:.1f}) for \"{short_query}\" creates an observable search opportunity."
    )
    title = (meta or {}).get("title")
    if title and short_query.lower() not in title.lower():
        action = f"Front-load \"{short_query}\" in the title/H1 — current title doesn't lead with it."
    else:
        action = "Query/SERP validation required before making a CTR-focused recommendation."
    return f"{reason} {action}"


def build_search_opportunity_pages(
    page_rows: list[dict], crawled_pages: list[dict] | None = None,
    page_query_rows: list[dict] | None = None, brand_tokens=None,
) -> list[dict]:
    """Search Opportunities — Pages table (2026-09-21 spec: no universal CTR
    benchmark). Selection is evidence-based, never CTR-vs-threshold: real
    impression volume, page-one visibility (position 4-10 — page 1, but not
    the noisy top-3 extreme), and a real non-brand GSC (page, query) row
    establishing query relevance. CTR is collected and displayed (still a
    table column) but is NOT evaluated against any hard-coded percentage —
    it never gates which pages qualify and never drives the priority score.
    A page whose query breakdown shows its top query is BRANDED is excluded
    (branded demand isn't a clear incremental non-brand SEO opportunity); a
    page with no query breakdown at all still qualifies, just with the
    fixed "Insufficient query-level GSC evidence." action instead of a
    guessed one.

    Priority is impressions-only (a real, observed signal, not a modeled
    CTR-gap x impressions score — that formula was the exact "universal
    benchmark by another name" this spec forbids): the highest-visibility
    pages surface first, tiered into High/Medium/Low thirds."""
    meta_index = _sop_crawled_meta_index(crawled_pages)
    query_index = _sop_page_query_index(page_query_rows, brand_tokens)
    scored = []
    for r in page_rows:
        impressions = float(r.get("impressions", 0) or 0)
        if impressions < _SOP_MIN_IMPRESSIONS:
            continue
        position = float(r.get("position", 0) or 0)
        if not (_SOP_POSITION_LO <= position <= _SOP_POSITION_HI):
            continue
        ctr_pct = float(r.get("ctr", 0) or 0) * 100  # observed/displayed only — never a selection gate

        url = r.get("page") or ""
        key = _sop_normalize_url(url)
        page_queries = query_index.get(key)
        driving_query = _sop_driving_query(page_queries) if page_queries else None
        if page_queries and not driving_query:
            top_query = max(page_queries, key=lambda q: q["impressions"])
            if top_query["query_type"] == "brand":
                continue  # primarily branded-driven — not a clear incremental non-brand SEO opportunity

        page_type = _sop_page_type(url)
        meta = meta_index.get(key)

        scored.append({
            "page": url, "impressions": int(impressions), "ctr_pct": round(ctr_pct, 1),
            "position": round(position, 1), "page_type": page_type,
            "recommended_action": _sop_recommended_action(position, meta, driving_query),
            "driving_query": driving_query["query"] if driving_query else None,
            "evidence_confidence": "high" if driving_query else "low",
            "data_source": "GSC",
        })

    scored.sort(key=lambda r: r["impressions"], reverse=True)
    third = max(1, -(-len(scored) // 3))
    for i, r in enumerate(scored):
        r["priority"] = "High" if i < third else ("Medium" if i < 2 * third else "Low")
    return scored


def _country_label(raw_code: str) -> str:
    """GSC's country dimension returns raw ISO-3166-1 alpha-3 codes (e.g.
    "usa", "grc") — resolved here to the full country name via pycountry
    so a slide never shows an unexplained 3-letter code. Falls back to
    the uppercased raw code only for the rare value pycountry doesn't
    recognize."""
    code = str(raw_code or "").strip()
    if len(code) == 3:
        match = pycountry.countries.get(alpha_3=code.upper())
        if match:
            return match.name
    return code.upper()


def _summarize_low_signal_countries(rows: list[dict]) -> dict | None:
    """One grouped, deliberately muted line for countries with real
    impressions but single-digit clicks (2026-09-11 user spec) — never
    rendered as individual table rows, so a near-zero-click country can't
    read with the same weight as a material opportunity."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: float(r.get("impressions", 0) or 0), reverse=True)
    names = [_country_label(r.get("country", "")) for r in rows]
    clicks = [int(float(r.get("clicks", 0) or 0)) for r in rows]
    impressions = [int(float(r.get("impressions", 0) or 0)) for r in rows]
    if len(set(clicks)) == 1:
        click_part = f"{clicks[0]} click each" if clicks[0] == 1 else f"{clicks[0]} clicks each"
    else:
        click_part = f"{min(clicks)}-{max(clicks)} clicks"
    imp_part = f"{impressions[0]}" if min(impressions) == max(impressions) else f"{min(impressions)}-{max(impressions)}"
    summary = (
        f"{', '.join(names)}: {click_part} despite {imp_part} impressions — "
        f"sample too small to act on, worth re-checking next period"
    )
    return {"countries": names, "summary": summary}


def build_high_potential_countries(
    country_rows: list[dict],
    target_countries: list[str] | None = None,
    minimum_click_threshold: float = _COUNTRY_MIN_CLICK_THRESHOLD,
) -> dict:
    """Part 3 flagging logic (2026-09-16 user spec, recommendation wording
    rewritten 2026-09-22): split by signal strength first — clicks <
    minimum_click_threshold never gets an individual Fix, it's rolled into
    one grouped low-signal summary.

    2026-09-22 spec: country-level GSC data (impressions/clicks/CTR/
    country) shows OBSERVED search presence only, never the cause of it —
    this function no longer benchmarks a country's CTR against anything
    (another country, an aggregate, an external curve) to justify a
    prescriptive fix like "expand budget" or "localize title/meta/
    currency." CTR is an observed metric here, not a universal benchmark,
    and must never independently trigger a recommendation. Every material
    row instead gets the same directional "review pages/queries and assess
    demand" next step (target-market membership is the only thing that
    branches it, never a CTR or click-volume comparison — clicks here are
    already >= minimum_click_threshold by construction, so a zero-click
    branch would be dead code), plus the spec's own required disclosure
    sentence, since page/query-level
    evidence is categorically unavailable here (country_rows never carries
    a page or query dimension). Countries outside target_countries (when
    supplied) are labeled explicitly as out-of-market rather than told to
    expand. Country codes are resolved to full names via _country_label so
    nothing renders as an unexplained code."""
    candidates = [r for r in country_rows if float(r.get("impressions", 0) or 0) >= _COUNTRY_LOW_SIGNAL_MIN_IMPRESSIONS]
    if not candidates:
        return {"material": [], "low_signal": None}

    material_pool = [r for r in candidates if float(r.get("clicks", 0) or 0) >= minimum_click_threshold]
    low_signal_rows = [r for r in candidates if float(r.get("clicks", 0) or 0) < minimum_click_threshold]
    if not material_pool:
        return {"material": [], "low_signal": _summarize_low_signal_countries(low_signal_rows)}

    target_set = {str(t).strip().lower() for t in target_countries} if target_countries else None

    material = []
    for r in material_pool:
        impressions = float(r.get("impressions", 0) or 0)
        clicks = float(r.get("clicks", 0) or 0)
        ctr_pct = float(r.get("ctr", 0) or 0) * 100
        label = _country_label(r.get("country", ""))
        code = str(r.get("country", "")).strip().lower()
        out_of_market = target_set is not None and code not in target_set

        if out_of_market:
            fix = f"{label} is outside the client's stated target markets. {_COUNTRY_EVIDENCE_DISCLAIMER}"
        else:
            fix = (
                f"Review the highest-visibility pages and queries for {label} ({int(clicks):,} clicks, "
                f"{int(impressions):,} impressions observed) and assess whether country-specific content is "
                f"justified by that demand. {_COUNTRY_EVIDENCE_DISCLAIMER}"
            )
        material.append({
            "country": label, "impressions": int(impressions), "clicks": int(clicks),
            "ctr_pct": round(ctr_pct, 1), "fix": fix,
        })
    material.sort(key=lambda r: r["impressions"], reverse=True)

    return {"material": material, "low_signal": _summarize_low_signal_countries(low_signal_rows)}


def add_branded_vs_nonbranded_slide(
    prs: Presentation, comparison: dict, narrative: dict | None, ai_insights: dict | None, source: str
) -> object | None:
    """Branded vs Non-Branded rebuilt as an outcome case, not a two-row
    table with one headline stat (2026-09-11 user spec): headline (business-
    dependency framing) -> compact ground-truth table -> demand-gap
    estimate -> cost of inaction -> one concrete example -> Key Insights.
    Parts 1-3/headline/example are all from build_branded_dependency_
    narrative (deterministic, no AI); Part 4 (Key Insights) is the only
    AI-written piece, same convention as every other AI slide in this
    file. High-potential pages/countries still render on the separate
    Search Opportunities — Pages/Countries slides so text never overlaps."""
    branded, nonbranded = comparison["branded"], comparison["nonbranded"]
    if not branded["clicks"] and not nonbranded["clicks"] and not branded["impressions"] and not nonbranded["impressions"]:
        return None
    narrative = narrative or {}

    slide = _blank_slide(prs)
    _content_header(slide, "Branded vs Non-Branded Search Performance")
    _textbox(slide, Inches(8.0), Inches(0.3), Inches(4.7), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)

    left, width = Inches(0.6), Inches(12.1)
    max_y = SLIDE_H - Inches(0.5)

    def _wrapped_lines(text: str, w=width, size=11) -> int:
        # Same ~14-chars-per-inch-at-size-11 heuristic _insights_strip uses,
        # scaled by font size so a larger headline doesn't under-reserve height.
        chars_per_line = max(20, int(w / 914400 * 14 * (11 / size)))
        return max(1, -(-len(text) // chars_per_line))

    def _paragraph(top, label: str, text: str, label_color=None) -> object:
        _textbox(slide, left, top, width, Inches(0.22), label.upper(), size=9.5, bold=True, color=label_color or _accent())
        top += Inches(0.24)
        lines = _wrapped_lines(text)
        line_h = Inches(0.2)
        _textbox(slide, left, top, width, line_h * lines, text, size=11)
        return top + line_h * lines + Inches(0.16)

    headline = narrative.get("headline") or f"Branded queries carry {comparison['branded_share_pct']:.1f}% of total clicks"
    headline_lines = _wrapped_lines(headline, size=15)
    y = Inches(1.05)
    _textbox(slide, left, y, width, Inches(0.28) * headline_lines, headline, size=15, bold=True, color=_accent())
    y += Inches(0.3) * headline_lines + Inches(0.1)

    rows = [
        ("Branded", f"{branded['clicks_pct']:.1f}%", f"{branded['impressions_pct']:.1f}%", f"{branded['ctr_pct']:.1f}%", f"{branded['avg_position']:.1f}"),
        ("Non-Branded", f"{nonbranded['clicks_pct']:.1f}%", f"{nonbranded['impressions_pct']:.1f}%", f"{nonbranded['ctr_pct']:.1f}%", f"{nonbranded['avg_position']:.1f}"),
    ]
    y = _draw_table(
        slide, ["Query Group", "Clicks (%)", "Impressions (%)", "CTR", "Avg. Position"], rows, y,
        col_widths=[3.0, 2.3, 2.3, 2.3, 2.2], left=left, width=width, row_height=0.35,
    )
    footnote = (
        f"Based on {branded['clicks']:,} branded / {nonbranded['clicks']:,} non-branded clicks across "
        f"{branded['impressions']:,} / {nonbranded['impressions']:,} impressions."
    )
    _textbox(slide, left, y, width, Inches(0.2), footnote, size=9, color=TEXT_MUTED)
    y += Inches(0.28)

    demand_gap = narrative.get("demand_gap")
    if demand_gap:
        y = _paragraph(y, "The Gap in Demand Terms", demand_gap["text"])
    elif nonbranded["impressions"]:
        y = _paragraph(
            y, "The Gap in Demand Terms",
            f"Non-branded click-through rate ({nonbranded['ctr_pct']:.1f}%) already meets or beats the typical "
            f"rate for position {nonbranded['avg_position']:.1f} — the opportunity here is ranking higher, not "
            f"closing a click-through gap.",
        )

    cost_of_inaction = narrative.get("cost_of_inaction")
    if cost_of_inaction and y < max_y - Inches(0.4):
        y = _paragraph(y, "Cost of Inaction", cost_of_inaction, label_color=WARN)

    example = narrative.get("concrete_example")
    if example and y < max_y - Inches(0.4):
        y = _paragraph(y, "One Concrete Example", example["text"])

    insights = list((ai_insights or {}).get("insights") or [])[:5]
    if insights:
        _insights_strip(slide, left, y, width, insights, max_y=max_y)
    return slide


def add_search_opportunities_pages_slide(prs: Presentation, opportunity_pages: list[dict], source: str) -> object | None:
    """Search Opportunities — Pages (2026-09-18 user spec rebuild): existing
    page-1 (position 4-10) pages with a real CTR gap against the internal
    benchmark for that range, sorted by estimated extra monthly clicks if
    that gap closed — so "high impressions + strong ranking + low CTR"
    naturally lands first. Table matches the requested 5-column format
    exactly (Page/Impressions/CTR/Position/Recommended Action); the row
    count comes from build_search_opportunity_pages, which already excludes
    anything without a real CTR gap in this position band — nothing here
    is a generic "improve CTR" placeholder."""
    if not opportunity_pages:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Search Opportunities — Pages")
    _textbox(slide, Inches(8.0), Inches(0.3), Inches(4.7), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)

    left, width = Inches(0.6), Inches(12.1)
    y = Inches(1.05)
    # 2026-09-20 spec: opportunities here may involve CTR, SERP messaging,
    # or query/page alignment — not necessarily a proven CTR problem — so
    # the section title must not narrow to "(Existing Pages, CTR
    # Opportunity)".
    _textbox(slide, left, y, width, Inches(0.24), "High-Potential Pages — CTR & SERP Opportunities", size=12.5, bold=True, color=_accent())
    y += Inches(0.28)
    # 6, not the 9 other Search Opportunities/Traffic Sources tables use
    # (2026-09-18 fix): Recommended Action here runs ~2 lines even at its
    # shortest, and 9 rows of 2-line cells don't fit this slide's available
    # height — _draw_table's auto-shrink then collapsed every row to a
    # single truncated line, cutting the page-specific half of the sentence
    # off entirely. 6 rows of up to 2 lines fits without that collapse.
    row_cap = 6
    shown = opportunity_pages[:row_cap]
    rows = [
        (_truncate_cell(r["page"], 3.2), f"{r['impressions']:,}", f"{r['ctr_pct']:.1f}%", f"{r['position']:.1f}", r["recommended_action"])
        for r in shown
    ]
    y, table = _draw_table(
        slide, ["Page", "Impressions", "CTR", "Position", "Recommended Action"], rows, y,
        col_widths=[3.2, 1.3, 1.1, 1.1, 5.4], left=left, width=width, row_cap=row_cap, row_height=0.4, wrap_cols={4},
        return_table=True,
    )
    y += Inches(0.15)
    # Page URL as a real clickable hyperlink (2026-09-19 user spec) — the
    # cell text can be the truncated display string, the link target is
    # always the full, untruncated URL. python-pptx has no per-cell
    # hyperlink helper anywhere in this file yet, so this sets it directly
    # on the run _draw_table's own `cell.text = ...` already created.
    for i, r in enumerate(shown, start=1):
        if i >= len(table.rows):
            break
        run = table.cell(i, 0).text_frame.paragraphs[0].runs[0]
        run.hyperlink.address = r["page"]
        run.font.color.rgb = _accent()
        run.font.underline = True

    # KEY INSIGHTS: name the single highest-visibility row (impressions —
    # a real observed signal, never a modeled CTR-gap number, 2026-09-21
    # spec: no universal CTR benchmark anywhere in this reasoning),
    # disclose how many candidates exist beyond the table, and flag
    # data-insufficient rows explicitly rather than letting them read like
    # every other row. No individual query is named here — that detail
    # stays in each row's own Recommended Action.
    insights = []
    top = opportunity_pages[0]
    insights.append(
        f"Highest-visibility page: \"{top['page']}\" (position {top['position']:.1f}, {top['impressions']:,} "
        "impressions) — the largest observed search visibility in this set; CTR is shown per-row as an observed "
        "metric, not compared against a fixed benchmark."
    )
    insufficient = sum(1 for r in opportunity_pages if r["recommended_action"] == "Insufficient query-level GSC evidence.")
    if insufficient:
        insights.append(f"{insufficient} of {len(opportunity_pages)} flagged pages have no qualifying non-brand query in the GSC (page, query) data pulled — listed with position/CTR only, not a generic recommendation.")
    insights.append(f"Showing top {len(shown)} of {len(opportunity_pages)} pages ranked by impression volume.")
    _insights_strip(slide, left, y, width, insights, max_y=SLIDE_H - Inches(0.4))
    return slide


def add_search_opportunities_countries_slide(prs: Presentation, high_countries: dict | None, source: str) -> object | None:
    """High-potential countries, split out to its own slide (2026-09-11
    user spec — was combined with the pages half above). Still renders as
    two visually distinct tiers: a full "Fix" table for material
    opportunities, and one muted, structurally separate summary line for
    low-signal (single-digit-click) countries — never the same table
    weight as a real opportunity. Full slide to itself now, so the
    material table's row_cap goes from the shared slide's 5 up to 9."""
    high_countries = high_countries or {}
    material_countries = high_countries.get("material") or []
    low_signal = high_countries.get("low_signal")
    if not material_countries and not low_signal:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Search Opportunities — Countries")
    _textbox(slide, Inches(8.0), Inches(0.3), Inches(4.7), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)

    left, width = Inches(0.6), Inches(12.1)
    y = Inches(1.05)

    _textbox(slide, left, y, width, Inches(0.24), "High-Potential Countries", size=12.5, bold=True, color=_accent())
    y += Inches(0.28)
    if material_countries:
        rows = [
            (r["country"], f"{r['impressions']:,}", f"{r['clicks']:,}", f"{r['ctr_pct']:.1f}%", r["fix"])
            for r in material_countries
        ]
        y = _draw_table(
            slide, ["Country", "Impressions", "Clicks", "CTR", "Fix"], rows, y,
            col_widths=[1.6, 1.5, 1.3, 1.2, 6.5], left=left, width=width, row_cap=9, row_height=0.4, wrap_cols={4},
        ) + Inches(0.25)
    else:
        _textbox(slide, left, y, width, Inches(0.3), "No countries met the material-opportunity bar this period.", size=11.5, color=TEXT_MUTED)
        y += Inches(0.4)

    # 2026-09-21 fix — confirmed live on a real Lumber regen: the low-
    # signal summary (a country list like "United Kingdom, Australia,
    # Indonesia, Singapore: ...") was drawn in a FIXED Inches(0.5) box
    # regardless of how many lines it actually wrapped to, and with no
    # check against how far the material table above had already pushed
    # `y` — a long country list or a tall material table put this text
    # box right on top of the footer's own "{client} · {domain}" text.
    # Sized to its real wrapped-line count and skipped outright if there's
    # no room left before the footer, same "stay clear of the footer"
    # floor _insights_strip and the Programmatic SEO slide already use.
    max_y = SLIDE_H - Inches(0.5)
    if low_signal and y < max_y - Inches(0.46):
        _textbox(slide, left, y, width, Inches(0.22), "Low-Signal, Monitor Only", size=10.5, bold=True, color=TEXT_MUTED)
        y += Inches(0.24)
        lines = _wrap_lines(low_signal["summary"], width, size_pt=10)
        line_h = Inches(0.2)
        _textbox(slide, left, y, width, min(line_h * lines, max_y - y), low_signal["summary"], size=10, color=TEXT_MUTED)

    return slide


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


# Bounce-rate gap (percentage points) above which the spike day counts as
# materially less engaged than the period (Traffic Spike spec 2026-09-23).
_SPIKE_BOUNCE_MATERIAL_PP = 5


def _traffic_spike_hypothesis(spike: dict) -> list[str]:
    """Key Insights for the Traffic Spike slide (spec 2026-09-23): traffic
    VOLUME (dominant channel + landing page, dominant geography) and traffic
    QUALITY (spike-day bounce rate vs the period average, then engagement
    and key events). Order matters — _insights_strip caps at 5 lines, so
    the bounce-rate quality signal comes right after the source lines and
    can never be pushed off. Every number traces to ga4_service.
    get_traffic_spike_breakdown; a missing metric is omitted, never assumed.
    Attribution language only ("accounted for", "coincided with"), and a
    high bounce rate is a quality signal, never called bot traffic."""
    lines: list[str] = []

    # 1. Volume — channel + landing page.
    top_channel = (spike.get("by_channel") or [None])[0]
    top_landing = (spike.get("by_landing_page") or [None])[0]
    if top_channel and top_landing:
        lines.append(
            f"{top_channel['label']} accounted for {top_channel['pct']:.0f}% of spike-day sessions, landing mostly on "
            f"\"{top_landing['label']}\" ({top_landing['pct']:.0f}% of that day's sessions)."
        )
    elif top_channel:
        lines.append(f"{top_channel['label']} accounted for {top_channel['pct']:.0f}% of spike-day sessions.")

    # 2. Volume — geography.
    top_country = (spike.get("by_country") or [None])[0]
    if top_country:
        lines.append(f"{top_country['label']} was the dominant geography, with {top_country['pct']:.0f}% of spike-day sessions.")

    # 3. Quality — bounce rate (GA4 bounceRate is a 0-1 fraction).
    avg_bounce, spike_bounce = spike.get("avg_bounce_rate"), spike.get("spike_bounce_rate")
    if spike_bounce is not None:
        spike_bounce_pct = spike_bounce * 100
        if avg_bounce is None:
            lines.append(f"Bounce rate on the spike day was {spike_bounce_pct:.0f}%; no period baseline is available to compare it with.")
        else:
            avg_bounce_pct = avg_bounce * 100
            diff = spike_bounce_pct - avg_bounce_pct
            compare = f"Bounce rate on the spike day was {spike_bounce_pct:.0f}% versus {avg_bounce_pct:.0f}% for the period ({diff:+.0f} percentage points)"
            if diff > _SPIKE_BOUNCE_MATERIAL_PP:
                lines.append(
                    f"{compare}, indicating lower engagement — review the concentrated traffic sources, landing pages, "
                    "geography, and referral patterns for anomalies."
                )
            else:
                lines.append(f"{compare} — bounce-rate behaviour does not indicate a clear quality deterioration.")

    # 4. Quality — engagement and key events, when measured.
    avg_eng, spike_eng = spike.get("avg_engagement_rate"), spike.get("spike_engagement_rate")
    avg_ke, spike_ke = spike.get("avg_key_events"), spike.get("spike_key_events")
    have_engagement = avg_eng is not None and spike_eng is not None
    have_key_events = avg_ke is not None and spike_ke is not None
    quality_bits = []
    if have_engagement:
        quality_bits.append(f"engagement rate {spike_eng * 100:.0f}% vs {avg_eng * 100:.0f}% average")
    avg_dur, spike_dur = spike.get("avg_session_duration_sec"), spike.get("spike_session_duration_sec")
    if avg_dur is not None and spike_dur is not None:
        quality_bits.append(f"average session duration {spike_dur:.0f}s vs {avg_dur:.0f}s")
    if have_key_events:
        quality_bits.append(f"key events {spike_ke:.0f} vs a {avg_ke:.0f}/day average")
    if quality_bits:
        lines.append("On the spike day: " + "; ".join(quality_bits) + ".")

    # 5. Combined read — only when engagement AND key events both exist and
    # point the same way; otherwise the evidence above stands on its own.
    if have_engagement and have_key_events:
        engagement_held = spike_eng >= avg_eng * 0.9
        key_events_held = spike_ke >= avg_ke * 0.9
        if engagement_held and key_events_held:
            lines.append("Engagement and key events rose in line with sessions, consistent with genuine demand coinciding with the spike.")
        elif not engagement_held and not key_events_held:
            lines.append("Sessions rose but engagement and key events did not follow — review the top source's referrer and landing-page detail before treating this traffic as normal demand.")
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
    # Columns sized to their real row count (max 5 each), not a fixed 3.7in
    # — the fixed height left room for only ONE Key Insights line below,
    # so the bounce-rate quality insight never actually rendered.
    columns = [(label, rows[:5]) for label, rows in columns]
    max_rows = max(len(rows) for _label, rows in columns)
    col_top = Inches(2.45)
    col_height = Inches(0.75) + Inches(0.36) * max_rows
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
        _insights_strip(slide, Inches(0.6), col_top + col_height + Inches(0.15), Inches(11.9), hypothesis, title="Key Insights")
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
    insights = _traffic_channel_breakdown_insights(rows_data)

    # 2026-09-11 user spec: the country-abbreviation legend must NOT live
    # inside Key Insights (that section is findings, not a glossary) —
    # drawn as its own small caption below the table/insights instead of
    # appended to the insights list the way it used to be.
    slide = _blank_slide(prs)
    _content_header(slide, "Traffic Breakdown — Monthly Average")
    if source:
        _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)
    bottom = _draw_table(
        slide, ["Channel", "Avg Sessions/mo", "% Share", "Top Countries", "Top Devices"], rows, Inches(1.2),
        col_widths=[2.0, 1.7, 1.1, 3.5, 3.8], insights=insights, insights_max=6,
    )
    if used_abbreviations:
        legend = ", ".join(f"{abbr} - {full}" for abbr, full in sorted(used_abbreviations.items()))
        _textbox(
            slide, Inches(0.6), bottom + Inches(0.1), Inches(11.9), Inches(0.3),
            f"* {legend}", size=9, color=TEXT_MUTED,
        )
    return slide


# Judgment-call thresholds (2026-09-11 user spec didn't give exact numbers
# for "high concentration" / "meaningfully above or below" / "low" vs.
# "high" share — spec gave the LOGIC, these are the concrete cutoffs
# applying it, same pattern as this file's other named threshold constants
# e.g. _TRAFFIC_SOURCES_OVER_RELIANCE_PCT).
_TCB_CONCENTRATION_RISK_PCT = 80  # spec's own example: "80%+"
_TCB_BOUNCE_OUTLIER_DELTA_PTS = 10  # points from the weighted average to count as "meaningful"
_TCB_LOW_SHARE_PCT = 10  # below this = "low session share" for the mismatch checks
_TCB_HIGH_SHARE_PCT = 15  # at/above this = "large share of traffic" for the mismatch checks
_TCB_NAMED_EXCLUDED_TOKENS = ("unassigned", "other", "cross-network")
# Below this volume, a 0%/100% bounce rate is just small-sample noise (e.g.
# 1 session total), not a real tracking problem worth flagging.
_TCB_IMPLAUSIBLE_MIN_SESSIONS = 20


def _traffic_channel_breakdown_insights(rows_data: list[dict]) -> list[str]:
    """2026-09-11 user spec: 4-6 bullets, each strictly derivable from this
    data (bounce rate included, even though it isn't a table column — the
    spec requires each such bullet be self-contained with both numbers
    stated, since the reader can't see a hidden column). 6 is a ceiling,
    not a quota — every bullet below is its own independently-true-or-false
    check, so a healthy/quiet channel mix can legitimately produce fewer
    than 4. rows_data is assumed sorted by pct_share descending (GA4 query
    orders by sessions desc — see get_traffic_channel_breakdown).

    Data-quality check (2026-09-11 addendum): every bounce citation below
    uses the SAME baseline (the session-weighted average, always named
    that way, never a plain/unlabeled "average") so two bullets never
    silently mean two different things by "average." Bounce is rounded to
    1 decimal place when cited; session-share percentages stay whole
    numbers (the source data itself is only whole-number precision there,
    per _pct_text/ga4_service, except sub-1% shares which already get a
    decimal)."""
    insights: list[str] = []
    have_bounce = bool(rows_data) and all(r.get("bounce_rate_pct") is not None for r in rows_data)

    # 1. Concentration check.
    if len(rows_data) >= 2:
        top2 = rows_data[:2]
        combined = top2[0]["pct_share"] + top2[1]["pct_share"]
        if combined >= _TCB_CONCENTRATION_RISK_PCT:
            insights.append(
                f"{top2[0]['channel']} and {top2[1]['channel']} together account for {combined:.0f}% of all "
                "sessions — a concentrated acquisition mix with real exposure if either channel is disrupted."
            )

    # 1b. Data-quality flag — an exactly-0%/100% bounce rate at real volume
    # is a tracking/tagging red flag, not a genuine finding, and it would
    # also distort every weighted-average bullet below if left unflagged.
    if have_bounce:
        implausible = [
            r for r in rows_data
            if r["avg_sessions_month"] >= _TCB_IMPLAUSIBLE_MIN_SESSIONS and r["bounce_rate_pct"] in (0, 100)
        ]
        if implausible:
            flagged = max(implausible, key=lambda r: r["avg_sessions_month"])
            insights.append(
                f"{flagged['channel']}'s bounce rate is exactly {flagged['bounce_rate_pct']:.1f}% despite "
                f"{flagged['avg_sessions_month']:,} sessions/month — statistically implausible at this volume, "
                "worth verifying GA4 tracking/tagging for this channel before trusting the bounce figures below."
            )

    weighted_avg_bounce = None
    if have_bounce:
        total_sessions = sum(r["avg_sessions_month"] for r in rows_data)
        if total_sessions:
            weighted_avg_bounce = sum(r["bounce_rate_pct"] * r["avg_sessions_month"] for r in rows_data) / total_sessions

    if weighted_avg_bounce is not None:
        # 2. Bounce rate outlier vs. the session-weighted average — named
        # explicitly with both numbers since bounce rate isn't a visible
        # table column.
        outlier = max(rows_data, key=lambda r: abs(r["bounce_rate_pct"] - weighted_avg_bounce))
        delta = outlier["bounce_rate_pct"] - weighted_avg_bounce
        if abs(delta) >= _TCB_BOUNCE_OUTLIER_DELTA_PTS:
            direction = "above" if delta > 0 else "below"
            insights.append(
                f"{outlier['channel']}'s {outlier['bounce_rate_pct']:.1f}% bounce rate is {abs(delta):.1f} points "
                f"{direction} the session-weighted average of {weighted_avg_bounce:.1f}% across all channels."
            )

        # 3. Low bounce + low share — underused, high-quality channel.
        underused = [
            r for r in rows_data
            if r["pct_share"] < _TCB_LOW_SHARE_PCT and (weighted_avg_bounce - r["bounce_rate_pct"]) >= _TCB_BOUNCE_OUTLIER_DELTA_PTS
        ]
        if underused:
            best = min(underused, key=lambda r: r["bounce_rate_pct"])
            insights.append(
                f"{best['channel']} has a low {best['bounce_rate_pct']:.1f}% bounce rate ({weighted_avg_bounce - best['bounce_rate_pct']:.1f} "
                f"points below the {weighted_avg_bounce:.1f}% session-weighted average) but only {_pct_text(best['pct_share'])} of sessions "
                f"({best['avg_sessions_month']:,}/month) — an underused, high-quality channel worth investing more into."
            )

        # 4. High bounce + high volume — highest-priority quality issue
        # since it touches the most sessions.
        at_risk = [
            r for r in rows_data
            if r["pct_share"] >= _TCB_HIGH_SHARE_PCT and (r["bounce_rate_pct"] - weighted_avg_bounce) >= _TCB_BOUNCE_OUTLIER_DELTA_PTS
        ]
        if at_risk:
            worst = max(at_risk, key=lambda r: r["pct_share"])
            worst_delta = worst["bounce_rate_pct"] - weighted_avg_bounce
            insights.append(
                f"{worst['channel']} carries {_pct_text(worst['pct_share'])} of sessions ({worst['avg_sessions_month']:,}/month) "
                f"and its {worst['bounce_rate_pct']:.1f}% bounce rate runs {worst_delta:.1f} points above the "
                f"{weighted_avg_bounce:.1f}% session-weighted average — the highest-priority quality issue here since it "
                "affects the most sessions."
            )

    # 5. Device-mix anomaly — a channel whose top device inverts the
    # pattern every other channel shares.
    def _top_device(r):
        devices = [d for d in (r.get("top_devices") or []) if round(d["pct"]) > 0]
        return max(devices, key=lambda d: d["pct"]) if devices else None

    with_top_device = [(r, _top_device(r)) for r in rows_data]
    with_top_device = [(r, d) for r, d in with_top_device if d]
    if len(with_top_device) >= 3:
        device_counts = Counter(d["label"].title() for _, d in with_top_device)
        majority_device, majority_count = device_counts.most_common(1)[0]
        # Only a real shared pattern (all-but-one channel agrees) counts as
        # "the rest of the table" — a 3-way split has no pattern to invert.
        if majority_count >= len(with_top_device) - 1:
            anomaly = next(((r, d) for r, d in with_top_device if d["label"].title() != majority_device), None)
            if anomaly:
                r, d = anomaly
                insights.append(
                    f"{r['channel']}'s traffic is majority-{d['label'].title()} ({d['pct']:.0f}%), unlike every other "
                    f"channel here which skews {majority_device} — worth checking this channel's "
                    f"{d['label'].lower()} landing experience specifically."
                )

    # 6. Small-but-named channel — low volume, distinctly labeled (not
    # Unassigned/Other/cross-network). Ending states a concrete action tied
    # to the direction of the deviation, not a generic "worth tracking."
    if weighted_avg_bounce is not None:
        small_named = [
            r for r in rows_data
            if r["pct_share"] < _TCB_LOW_SHARE_PCT
            and not any(tok in r["channel"].strip().lower() for tok in _TCB_NAMED_EXCLUDED_TOKENS)
        ]
        if small_named:
            s = max(small_named, key=lambda r: abs(r["bounce_rate_pct"] - weighted_avg_bounce))
            s_delta = s["bounce_rate_pct"] - weighted_avg_bounce
            direction = "worse" if s_delta > 0 else "better"
            action = (
                "the weak quality is masked by its small volume today — worth fixing before scaling spend into this channel"
                if s_delta > 0 else
                "an efficient channel at small scale — worth testing whether that quality holds if you invest more into it"
            )
            insights.append(
                f"{s['channel']} is small at {_pct_text(s['pct_share'])} of sessions ({s['avg_sessions_month']:,}/month) "
                f"but its {s['bounce_rate_pct']:.1f}% bounce rate is {abs(s_delta):.1f} points {direction} than the "
                f"{weighted_avg_bounce:.1f}% session-weighted average — {action}."
            )

    return insights[:6]


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


_COMPETITOR_MEANINGFUL_GAP_MULTIPLE = 1.5


def add_competitor_table_slide(prs: Presentation, competitor_rows: list[dict], keyword_sheet_link: str | None = None):
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
        # 2026-09-16 user spec: 1.5x is the trigger for a "meaningful"
        # competitor gap, applied the SAME way across every metric below —
        # not "any positive gap" (the old logic), which called out a 3%
        # lead as if it were as significant as a 5x one. DR is excluded
        # deliberately — Semrush's Authority Score is already a 0-100
        # composite index, not a countable quantity, so a ratio between two
        # index scores doesn't mean what a ratio of raw counts means; kept
        # as its own always-shown gap (still real, just not multiple-gated).
        def _meaningful_gap(metric_leader, own_value, label, unit_fmt=lambda v: f"{v:,.0f}"):
            leader_value = _num(metric_leader.get(metric_key))
            if own_value <= 0:
                return f"{metric_leader.get('domain')} has {unit_fmt(leader_value)} {label} — you have none tracked." if leader_value > 0 else None
            multiple = leader_value / own_value
            if multiple < _COMPETITOR_MEANINGFUL_GAP_MULTIPLE:
                return None
            return f"{metric_leader.get('domain')} has {multiple:.1f}x your {label} ({unit_fmt(leader_value)} vs {unit_fmt(own_value)})."

        for metric_key, label in (
            ("organic_traffic", "organic traffic"),
            ("referring_domains", "referring domains"),
            ("backlinks_total", "backlinks"),
            ("organic_keywords", "ranking keywords"),
        ):
            if metric_key == "referring_domains" and not any(r.get("referring_domains") is not None for r in competitor_rows):
                continue  # not every Domain Overview export carries this field — skip rather than compare against a silent 0
            leader = max(competitors, key=lambda r: _num(r.get(metric_key)))
            own_value = _num(own_row.get(metric_key))
            text = _meaningful_gap(leader, own_value, label)
            if text:
                insights.append(text)

        if has_rich_data:
            dr_leader = max(competitors, key=lambda r: _num(r.get("authority_score")))
            own_dr = _num(own_row.get("authority_score"))
            if _num(dr_leader.get("authority_score")) > own_dr:
                insights.append(f"Domain authority gap: {dr_leader.get('domain')} sits at DR {int(_num(dr_leader.get('authority_score')))} vs your {int(own_dr)}.")

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

    slide = _blank_slide(prs)
    _content_header(slide, "Competitor Analysis")
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)
    # Full client + competitor keyword lists (2026-09-10 user spec) live in
    # one combined Google Sheet, linked via a button at the bottom instead
    # of their own dedicated slide — reserves its own fixed zone ABOVE the
    # standing page footer (_footer draws at SLIDE_H-0.4 to SLIDE_H-0.1;
    # confirmed live on report 54: the button was drawn straight over that
    # footer text, an actual visible overwrite, not just a close call) and
    # caps how far the insights strip below the table may render so a long
    # insights list can't grow down into the same reserved zone either.
    insights_max_y = (SLIDE_H - Inches(1.10)) if keyword_sheet_link else None
    bottom = _draw_table(slide, headers, rows, Inches(1.2), col_widths=col_widths, row_cap=9 if insights else 14)
    if insights:
        _insights_strip(slide, Inches(0.6), bottom + Inches(0.15), Inches(12.1), insights, max_y=insights_max_y)

    if keyword_sheet_link:
        _textbox(
            slide, Inches(0.6), SLIDE_H - Inches(1.00), Inches(3.0), Inches(0.20),
            "KEYWORD LIST", size=9.5, bold=True, color=TEXT_MUTED,
        )
        btn = slide.shapes.add_shape(5, Inches(0.6), SLIDE_H - Inches(0.76), Inches(4.3), Inches(0.32))
        try:
            btn.adjustments[0] = 0.35
        except (IndexError, AttributeError):
            pass
        btn.fill.solid()
        btn.fill.fore_color.rgb = _accent()
        btn.line.fill.background()
        btn.shadow.inherit = False
        tf = btn.text_frame
        tf.word_wrap = False
        tf.margin_left = Pt(10)
        tf.margin_right = Pt(10)
        tf.margin_top = Pt(2)
        tf.margin_bottom = Pt(2)
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        run = p.add_run()
        run.text = "Open full keyword list →"
        run.font.size = Pt(11.5)
        run.font.bold = True
        run.font.color.rgb = WHITE
        btn.click_action.hyperlink.address = keyword_sheet_link
    return slide


_COMPETITOR_THIN_KEYWORD_COUNT = 5


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
        insights = []
        # Global Rule 11 (2026-09-16 spec): a domain tracked on only a
        # handful of keywords isn't a real head-to-head comparison — "100%
        # of their visible footprint" off 1 tracked keyword reads as a
        # confident finding when it's actually just too little data to
        # judge this competitor's relevance from at all.
        if len(rows) < _COMPETITOR_THIN_KEYWORD_COUNT:
            insights.append(
                f"Relevance unconfirmed — only {len(rows)} tracked keyword(s) for this domain, too few to treat "
                "as a representative head-to-head comparison."
            )
        insights.append(
            f"Ranks in the top 10 for {top1_10} of {len(rows)} tracked keywords — {top1_10 / len(rows) * 100:.0f}% of their visible footprint."
        )
        insights.append(
            f"Highest-volume keyword: \"{top_kw.get('keyword')}\" at position {top_kw.get('position')}, {int(_num(top_kw.get('search_volume'))):,} monthly searches."
        )
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


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# Strict CTR-by-position decay model (2026-09-10 user spec) — deliberately
# NOT an AI call: the spec requires exact tier adherence ("do not deviate"),
# which code guarantees and an LLM only approximates. (lo, hi, ctr_pct).
_CTR_DECAY_TIERS = [
    (1, 1, 22.0), (2, 2, 13.0), (3, 3, 9.0), (4, 4, 6.0), (5, 5, 4.5),
    (6, 10, 1.5), (11, 20, 0.2), (21, 10**9, 0.0),
]
# Position just inside the next-better tier — for flagging a keyword one
# step from a real CTR jump (11->10 is 0.2%->1.5%, not a rounding blip).
_CTR_TIER_CROSSING_POSITIONS = {11: 10, 6: 5, 21: 20}

# Step 3 of the 2026-09-18 Competitor Keyword Gap Analysis spec: keep the
# table to keywords realistically climbable rather than aspirational
# page-1-of-a-saturated-niche terms. No per-client override exists yet —
# a fixed, documented default rather than silently unfiltered.
_KEYWORD_GAP_MAX_KD = 70


def _ctr_decay_pct(position) -> float:
    try:
        pos = int(position)
    except (TypeError, ValueError):
        return 0.0
    if pos <= 0:
        return 0.0
    for lo, hi, ctr in _CTR_DECAY_TIERS:
        if lo <= pos <= hi:
            return ctr
    return 0.0


def _short_display_url(url: str | None) -> str:
    if not url:
        return "—"
    return url.replace("https://", "").replace("http://", "").rstrip("/")


_URL_PATH_MAX_CHARS = 26


def _short_path(url: str | None, max_chars: int = _URL_PATH_MAX_CHARS) -> str | None:
    """Just the path, domain stripped — the Position+URL cells sit under a
    column header that already names the domain (or under "My Position",
    where the domain is obviously the client's own), so repeating it in
    every cell only added width with no new information. Truncated with an
    ellipsis rather than wrapped, so a long slug can't blow a table row to
    multiple lines (2026-09-18 gap-analysis redesign)."""
    if not url:
        return None
    stripped = url.replace("https://", "").replace("http://", "").rstrip("/")
    path = "/" + stripped.split("/", 1)[1] if "/" in stripped else "/"
    if len(path) > max_chars:
        path = path[: max_chars - 1].rstrip() + "…"
    return path


def _gap_position_cell(position) -> str:
    """"NA" is always exactly that string — never a variant like "Not
    ranking" or a trailing dash (2026-09-23 spec hard rule: use NA
    consistently for "client/competitor doesn't rank" everywhere on this
    slide). Position and URL render as two separate table columns
    (2026-09-22 — "#N · /path" crammed into one line/cell read poorly once
    every ranking column had its own URL, own+competitors both), so this
    only ever needs the number."""
    return f"#{int(position)}" if position else "NA"


def _gap_url_cell(url: str | None) -> str:
    """The URL column next to a position column — "—" when there's no
    position at all (that column's own "Not ranking" already says so) or
    no URL on record for an otherwise-real position."""
    path = _short_path(url)
    return path if path and path != "/" else "—"


# Gap Category dropped as a text column (2026-09-18 redesign) — with up to
# 3 competitor columns already competing for width, spelling out
# "Missing"/"Untapped"/"Shared" in every row cost a full column for
# information a color chip conveys at a glance. The chip column keeps only
# a one-letter code (still readable if color rendering is ever lost, e.g.
# printed greyscale); a legend under the header spells out what each color
# means once, not per row. Module-level (2026-09-22) since both the
# summary slide and every per-status dedicated slide draw the same legend
# and chip colors now.
_GAP_CHIP = {"Missing": ("M", BAD), "Untapped": ("U", GOOD), "Shared": ("S", TEXT_MUTED)}
# 2026-09-22 spec: a status with 6+ relevant keywords gets its OWN
# dedicated slide ("Competitor Keyword Gap — Missing/Shared/Untapped"),
# never merged with another status. 1-5 stays inline on the summary slide
# only; 0 gets no slide/table row for that status at all.
_GAP_DEDICATED_THRESHOLD = 6
# Same "top N by volume, rest via the full-list Sheet link" escape hatch
# every other capped table in this file uses — a dedicated slide can still
# have hundreds of keywords (e.g. 440 Missing), so this stays a cap, not a
# promise every row is shown. 2026-09-23 spec: max 6-7 rows per status
# slide, prefer 7 — down from 9.
_GAP_DEDICATED_ROW_CAP = 7
_GAP_STATUS_SLIDE_TITLE = {
    "Missing": "Competitor Keyword Gap — Missing",
    "Shared": "Competitor Keyword Gap — Shared",
    "Untapped": "Competitor Keyword Gap — Untapped",
}


def _gap_row_competitors_text(r: dict) -> str:
    cps = r.get("competitor_positions") or []
    return ", ".join(f"{cp.get('competitor')} (#{cp.get('position')})" for cp in cps) or "none tracked"


def _draw_gap_legend(slide) -> None:
    legend_x = Inches(0.6)
    for label, (letter, color) in _GAP_CHIP.items():
        sq = slide.shapes.add_shape(1, legend_x, Inches(0.98), Inches(0.13), Inches(0.13))
        _fill(sq, color)
        sq.shadow.inherit = False
        _textbox(slide, legend_x + Inches(0.18), Inches(0.935), Inches(1.1), Inches(0.2), f"{letter} — {label}", size=9, color=TEXT_MUTED)
        legend_x += Inches(1.25)


def _add_gap_sheet_link_button(slide, link: str) -> None:
    _textbox(slide, Inches(0.6), SLIDE_H - Inches(1.00), Inches(3.0), Inches(0.20), "KEYWORD LIST", size=9.5, bold=True, color=TEXT_MUTED)
    btn = slide.shapes.add_shape(5, Inches(0.6), SLIDE_H - Inches(0.76), Inches(4.3), Inches(0.32))
    try:
        btn.adjustments[0] = 0.35
    except (IndexError, AttributeError):
        pass
    btn.fill.solid()
    btn.fill.fore_color.rgb = _accent()
    btn.line.fill.background()
    btn.shadow.inherit = False
    tf = btn.text_frame
    tf.word_wrap = False
    tf.margin_left = Pt(10)
    tf.margin_right = Pt(10)
    tf.margin_top = Pt(2)
    tf.margin_bottom = Pt(2)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = "Open full keyword list →"
    run.font.size = Pt(11.5)
    run.font.bold = True
    run.font.color.rgb = WHITE
    btn.click_action.hyperlink.address = link


def _gap_table_row(r: dict, competitor_columns: list[str]) -> tuple[tuple, list]:
    """One table row's cell values, plus the parallel list of real
    (untruncated) ranking URLs for hyperlinking — index-aligned with the
    row's URL columns (My URL, then each competitor's URL, in that
    order), None where there's no position or no URL on record. Never
    invents/reconstructs a URL (2026-09-22 spec rule 7) — only ever
    what's already on the row.

    Position and URL are two separate columns per ranking source (own +
    each competitor) — 2026-09-22 layout change: "#N · /path" packed into
    one cell/line was hard to scan once every column carried both; the
    user asked for Position and URL split out explicitly, and confirmed
    the resulting narrower per-competitor URL width (vs. the combined
    cell) is an acceptable trade for readability. My Position's own URL
    used to be omitted from hyperlinking entirely even when a real
    your_url was on record — fixed in the same pass."""
    by_domain = {cp.get("competitor"): cp for cp in (r.get("competitor_positions") or [])}
    category = r.get("gap_category") or "Missing"
    your_position = r.get("your_position")
    row = [
        _GAP_CHIP[category][0], r.get("keyword"),
        f"{int(_num(r.get('search_volume'))):,}", f"{int(_num(r.get('keyword_difficulty')))}",
        _gap_position_cell(your_position), _gap_url_cell(r.get("your_url")) if your_position else "—",
    ]
    urls: list = [r.get("your_url") if your_position else None]
    for domain in competitor_columns:
        cp = by_domain.get(domain)
        position = cp.get("position") if cp else None
        url = cp.get("ranking_url") if cp else None
        row.append(_gap_position_cell(position))
        row.append(_gap_url_cell(url) if position else "—")
        urls.append(url if position else None)
    return tuple(row), urls


def _render_gap_table(slide, top, rows: list[dict], competitor_columns: list[str], headers: list[str], col_widths: list[float]):
    """Draws the table, colors each row's Status chip, and hyperlinks every
    URL cell — My URL included, not just competitor columns — to its real
    source URL (2026-09-22 spec rule 7). Position and URL are separate
    columns per ranking source (My Position/My URL, then one Position/URL
    pair per competitor), so the link target lands on the URL cell
    specifically, never the position number next to it. Returns
    (bottom_y, table)."""
    table_rows, row_categories, row_urls = [], [], []
    for r in rows:
        row, urls = _gap_table_row(r, competitor_columns)
        table_rows.append(row)
        row_categories.append(r.get("gap_category") or "Missing")
        row_urls.append(urls)

    wrap_cols = set(range(1, len(headers)))  # keyword + every Position/URL cell can wrap, not the chip column
    # Smaller than _draw_table's 0.4in default (2026-09-22) — the summary
    # slide can now carry up to 15 inline rows (5 keywords x 3 statuses,
    # each still under the dedicated-slide threshold) instead of the old
    # single-table's 12-row max, and a dedicated slide's own row cap is
    # taller too; empirically confirmed via _audit_slide_geometry that
    # 0.4in overflows the slide by ~0.22in at 15 wrapped rows, 0.32 does not.
    bottom, table = _draw_table(
        slide, headers, table_rows, top, col_widths=col_widths, wrap_cols=wrap_cols,
        row_cap=len(table_rows), row_height=0.32, return_table=True,
    )
    # row_urls[j=0] is My URL, row_urls[j=1..] is each competitor's URL in
    # competitor_columns order. Columns run Status, Keyword, Volume, KD,
    # My Position, My URL, then a Position/URL pair per competitor — so
    # the URL column for pair j always sits at a fixed offset of 2 from
    # the previous one, starting at column 5 (My URL).
    first_url_col = 5
    for i in range(1, len(table.rows)):
        _, color = _GAP_CHIP[row_categories[i - 1]]
        cell = table.cell(i, 0)
        cell.fill.solid()
        cell.fill.fore_color.rgb = color
        para = cell.text_frame.paragraphs[0]
        para.alignment = PP_ALIGN.CENTER
        para.font.color.rgb = WHITE
        para.font.bold = True
        for j, url in enumerate(row_urls[i - 1]):
            if not url:
                continue
            runs = table.cell(i, first_url_col + 2 * j).text_frame.paragraphs[0].runs
            if not runs:
                continue
            runs[0].hyperlink.address = url
            runs[0].font.color.rgb = _accent()
            runs[0].font.underline = True
    return bottom, table


def add_keyword_gap_insights_slide(
    prs: Presentation,
    overview_insights: list[str],
    insights_by_category: dict[str, list[str]],
) -> object | None:
    """Every Keyword Gap Key Insight, off the table slides entirely and
    onto its own slide (2026-09-23 user request) — the Analysis/Missing/
    Shared/Untapped slides previously each carried their own Key Insights
    card at the bottom, competing for space with the table above it (the
    direct cause of a reported text/table overlap on a large Missing
    table). Those slides now render table-only, with real headroom; every
    insight that used to live on one of them moves here instead, onto a
    dedicated slide with nothing else competing for its space — "no
    overwrite" by construction, not by a tighter height estimate.

    One column per status that has any rows (1-3 columns, same _card
    layout as add_core_problem_slide) — never an empty column for a status
    with nothing to say. overview_insights (off-topic/relevant/split,
    ambiguous-review, KD-unavailable) render above the columns since they
    describe the whole analysis, not one status."""
    if not overview_insights and not any(insights_by_category.values()):
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Competitor Keyword Gap — Key Insights")
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), "Source: Semrush Keyword Gap export", size=11, color=TEXT_MUTED)

    y = Inches(1.15)
    if overview_insights:
        overview_h = Inches(0.32) * len(overview_insights[:3]) + Inches(0.2)
        _card(slide, Inches(0.6), y, Inches(12.1), overview_h)
        iy = y + Inches(0.15)
        for line in overview_insights[:3]:
            lines = _wrap_lines(line, Inches(11.5), size_pt=11.5)
            _icon_dot(slide, Inches(0.85), iy + Inches(0.07), Inches(0.08), _accent())
            _textbox(slide, Inches(1.0), iy, Inches(11.4), Inches(0.24) * lines, line, size=11.5)
            iy += Inches(0.24) * lines + Inches(0.08)
        y += overview_h + Inches(0.25)

    categories = [c for c in ("Missing", "Shared", "Untapped") if insights_by_category.get(c)]
    if categories:
        col_top, col_height = y, SLIDE_H - Inches(0.55) - y
        gap = Inches(0.2)
        total_width = Inches(12.1)
        col_width = Emu(int((total_width - gap * (len(categories) - 1)) / len(categories)))
        left = Inches(0.6)
        for cat in categories:
            color = _GAP_CHIP[cat][1]
            _card(slide, left, col_top, col_width, col_height)
            cy = col_top + Inches(0.2)
            _textbox(slide, left + Inches(0.25), cy, col_width - Inches(0.5), Inches(0.35), cat, size=14, bold=True, color=color)
            rule = slide.shapes.add_shape(1, left + Inches(0.25), cy + Inches(0.36), col_width - Inches(0.5), Pt(1.5))
            _fill(rule, color)
            rule.shadow.inherit = False
            cy += Inches(0.55)
            max_cy = col_top + col_height - Inches(0.15)
            for point in insights_by_category[cat]:
                lines = _wrap_lines(point, col_width - Inches(0.5), size_pt=11)
                item_h = Inches(0.2) * lines + Inches(0.1)
                if cy + item_h > max_cy:
                    break
                _textbox(slide, left + Inches(0.25), cy, col_width - Inches(0.5), Inches(0.2) * lines, point, size=11)
                cy += item_h
            left += col_width + gap
    return slide


def _gap_status_insights(status: str, status_rows: list[dict], shown_count: int, off_topic_count: int, client_name: str | None) -> list[str]:
    """Key Insights for a dedicated status slide — built ONLY from that
    status's own keywords and data (2026-09-22 spec rule 9), never
    referencing another status."""
    topic_ref = f"{client_name.strip()}'s business" if client_name and client_name.strip() else "the client's business"
    total = len(status_rows)
    insights = [
        f"{total:,} {status} keyword(s) identified in the relevant, KD-filtered set "
        f"({off_topic_count} off-topic or competitor-brand keyword(s) excluded as not relevant to {topic_ref})."
    ]
    top = status_rows[0]
    if status == "Missing":
        insights.append(
            f"Highest-volume Missing keyword: \"{top['keyword']}\" ({int(_num(top.get('search_volume'))):,}/mo) — "
            f"ranking competitors: {_gap_row_competitors_text(top)}."
        )
        insights.append(
            "Prioritize validation of high-volume Missing keywords where competitors already have a relevant "
            "ranking page before treating any of them as a confirmed SEO target."
        )
    elif status == "Shared":
        insights.append(
            f"Highest-volume Shared keyword: \"{top['keyword']}\" ({int(_num(top.get('search_volume'))):,}/mo) — "
            f"you and {_gap_row_competitors_text(top)} both rank."
        )
    elif status == "Untapped":
        insights.append(
            f"Highest-volume Untapped keyword: \"{top['keyword']}\" ({int(_num(top.get('search_volume'))):,}/mo) — "
            "no tracked domain ranks for it yet."
        )
    if shown_count < total:
        insights.append(f"Showing top {shown_count:,} of {total:,} {status} keyword(s), ranked by volume.")
    else:
        # 2026-09-23 spec: fewer than the row cap available — say so plainly
        # rather than the "top N of M" phrasing, which implies more exist.
        insights.append(f"Showing {shown_count:,} of {total:,} {status} keyword(s).")
    return insights


_KEYWORD_GAP_MAX_COMPETITOR_COLS = 3


def _prepare_keyword_gap_rows(rows: list[dict], max_kd: float = _KEYWORD_GAP_MAX_KD):
    """Shared by add_keyword_gap_slide and the Keyword Gap Sheet export
    (site_audit.create_combined_keyword_sheet call) so both apply identical
    relevance/KD filtering and use the identical fixed competitor-to-column
    mapping (2026-09-18 spec: "same competitor-to-column assignment across
    all rows" — the slide and the full Sheet list must agree on which
    domain is "Competitor 1"). Returns (kd_filtered_rows, ambiguous_rows,
    kd_unavailable_count, competitor_columns) — kd_filtered_rows sorted by
    volume descending; competitor_columns is the ordered list of up to
    _KEYWORD_GAP_MAX_COMPETITOR_COLS domains, picked by how many surviving
    keywords each one ranks for (most-covered first), so a domain that only
    shows up on a couple of long-tail rows doesn't bump one that's a real,
    consistent competitor across the dataset."""
    rows = [
        r for r in rows
        if not any(
            _is_branded_keyword(r.get("keyword", ""), _brand_token(cp.get("competitor", "")))
            for cp in (r.get("competitor_positions") or [])
        )
    ]
    ambiguous_rows = [r for r in rows if r.get("relevance") == "potentially_relevant"]
    # Rows with no "relevance" key (non-matrix fallback, or a failed-open AI
    # classification) are treated as RELEVANT, not AMBIGUOUS — this file's
    # established fail-open discipline: a missing signal must never cause
    # real gap keywords to vanish from the slide.
    relevant_rows = [r for r in rows if r.get("relevance") != "potentially_relevant"]

    kd_unavailable_count = sum(1 for r in relevant_rows if r.get("keyword_difficulty") in (None, ""))
    # 2026-09-21 spec rule 7: "highest volume" selection (and the table
    # itself) must only ever draw from rows with real, valid search volume
    # — a zero/missing-volume row has no volume signal to rank on and must
    # never surface as a "highest-volume" pick by default-to-zero luck.
    kd_filtered = [
        r for r in relevant_rows
        if r.get("keyword_difficulty") not in (None, "") and _num(r.get("keyword_difficulty")) <= max_kd
        and _num(r.get("search_volume")) > 0
    ]
    kd_filtered.sort(key=lambda r: -_num(r.get("search_volume")))

    coverage: dict[str, int] = {}
    for r in kd_filtered:
        for cp in (r.get("competitor_positions") or []):
            domain = cp.get("competitor")
            if domain:
                coverage[domain] = coverage.get(domain, 0) + 1
    competitor_columns = sorted(coverage, key=lambda d: -coverage[d])[:_KEYWORD_GAP_MAX_COMPETITOR_COLS]

    return kd_filtered, ambiguous_rows, kd_unavailable_count, competitor_columns


_GAP_SHARED_BLUE = RGBColor(0x3E, 0x6B, 0x99)


def _soften(color: RGBColor, amount: float = 0.85) -> RGBColor:
    """Blends `color` toward white by `amount` (0-1) — the soft KPI-card
    tint the 2026-09-23 exec-summary spec asks for, without a second set of
    hardcoded pastel constants to keep in sync with BAD/GOOD/_accent()."""
    return RGBColor(
        int(color[0] + (255 - color[0]) * amount),
        int(color[1] + (255 - color[1]) * amount),
        int(color[2] + (255 - color[2]) * amount),
    )


def _gap_kpi_card(slide, left, top, width, height, label, value, sub, color):
    card = slide.shapes.add_shape(5, left, top, width, height)  # rounded rectangle
    try:
        card.adjustments[0] = 0.06
    except (IndexError, AttributeError):
        pass
    card.fill.solid()
    card.fill.fore_color.rgb = _soften(color)
    card.line.color.rgb = color
    card.line.width = Pt(1)
    card.shadow.inherit = False
    pad = Inches(0.22)
    _textbox(slide, left + pad, top + Inches(0.18), width - pad * 2, Inches(0.4), label, size=12, bold=True, color=TEXT_MUTED)
    _textbox(slide, left + pad, top + Inches(0.58), width - pad * 2, Inches(0.55), value, size=24, bold=True, color=color)
    if sub:
        lines = _wrap_lines(sub, width - pad * 2, size_pt=9.5)
        _textbox(slide, left + pad, top + height - Inches(0.2) - Inches(0.16) * lines, width - pad * 2, Inches(0.16) * lines, sub, size=9.5, color=TEXT_MUTED)


def _gap_exec_action_items(
    by_category: dict[str, list[dict]], counts: dict[str, int], total_relevant: int, client_name: str | None,
) -> list[tuple[str, str]]:
    """Exactly 3 (fewer only if the data genuinely doesn't support 3)
    heading+sentence action items for the Executive Summary slide
    (2026-09-23 spec) — never a generic action for a status with no real
    rows, never a repeat of the KPI numbers already on the cards above.
    Priority order: close the Missing gap (competitors already rank, no
    page exists) -> narrow the widest Shared ranking gap (visible today,
    losing to a competitor) -> defend Untapped wins (no competitor there
    yet). by_category rows are already volume-sorted (_prepare_keyword_gap_
    rows), so by_category[cat][0] is each status's highest-volume row.
    Every number cited is read straight off the row data — nothing
    invented, no keyword-validation advice (relevance is already validated
    upstream by _prepare_keyword_gap_rows)."""
    topic_ref = f"{client_name.strip()}'s" if client_name and client_name.strip() else "the client's"
    items: list[tuple[str, str]] = []

    missing_rows = [r for r in by_category.get("Missing", []) if r.get("competitor_positions")]
    if missing_rows:
        top = missing_rows[0]
        pct = round(100 * counts["Missing"] / total_relevant) if total_relevant else 0
        items.append((
            "Close the Missing-Keyword Gap",
            f"{counts['Missing']} keywords ({pct}%) have competitors ranking with no page on {topic_ref} site yet — "
            f"start with \"{top['keyword']}\" ({int(_num(top.get('search_volume'))):,}/mo), where "
            f"{_gap_row_competitors_text(top)} already rank.",
        ))

    behind_rows = []
    for r in by_category.get("Shared", []):
        your_pos = r.get("your_position")
        if not your_pos:
            continue
        comp_positions = [cp.get("position") for cp in (r.get("competitor_positions") or []) if cp.get("position")]
        if not comp_positions:
            continue
        best_comp = min(comp_positions)
        if best_comp < your_pos:
            behind_rows.append((your_pos - best_comp, r, best_comp))
    if behind_rows:
        behind_rows.sort(key=lambda t: t[0], reverse=True)
        gap, row, best_comp = behind_rows[0]
        items.append((
            "Strengthen the Weakest Shared Ranking",
            f"Of {counts['Shared']} keywords where both sides rank, \"{row['keyword']}\" shows the widest gap — "
            f"{topic_ref} site sits at #{int(row['your_position'])} vs. #{int(best_comp)} for the closest-ranking "
            "competitor.",
        ))

    untapped_rows = by_category.get("Untapped", [])
    if untapped_rows and len(items) < 3:
        top_u = untapped_rows[0]
        pct = round(100 * counts["Untapped"] / total_relevant) if total_relevant else 0
        items.append((
            "Defend Untapped Keyword Wins",
            f"{counts['Untapped']} keywords ({pct}%) rank for {topic_ref} site with no tracked competitor present "
            f"yet — reinforce these pages, starting with \"{top_u['keyword']}\" "
            f"({int(_num(top_u.get('search_volume'))):,}/mo), before a competitor moves in.",
        ))

    return items[:3]


def add_keyword_gap_executive_summary_slide(
    prs: Presentation,
    by_category: dict[str, list[dict]],
    counts: dict[str, int],
    off_topic_count: int,
    total_relevant: int,
    client_name: str | None = None,
) -> object | None:
    """C-level Executive Summary slide (2026-09-23 spec), rendered ahead of
    the detailed Competitor Keyword Gap slides that add_keyword_gap_slides
    draws. KPI cards for whichever statuses have real data — the Untapped
    card is omitted entirely (never a "0 Untapped" empty card) when its
    count is 0, and the remaining cards rebalance across the full row
    width automatically since card width is computed from len(cards). One
    slide, no detailed keyword tables — those are the slides that follow."""
    if not total_relevant:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Competitor Keyword Gap — Executive Summary")
    _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), "Source: Semrush Keyword Gap export", size=11, color=TEXT_MUTED)

    def pct(n: int) -> int:
        return round(100 * n / total_relevant) if total_relevant else 0

    cards = [
        ("Total Relevant Keywords", f"{total_relevant:,}", f"({off_topic_count:,} off-topic / competitor-brand excluded)", _accent()),
        ("Missing Keywords", f"{counts['Missing']:,} ({pct(counts['Missing'])}%)",
         "Keywords where competitors rank and the target website does not.", BAD),
        ("Shared Keywords", f"{counts['Shared']:,} ({pct(counts['Shared'])}%)",
         "Keywords where the target website and competitors both rank.", _GAP_SHARED_BLUE),
    ]
    if counts.get("Untapped"):
        cards.append((
            "Untapped Keywords", f"{counts['Untapped']:,} ({pct(counts['Untapped'])}%)",
            "Keywords where the target website ranks and competitors do not.", GOOD,
        ))

    top = Inches(1.3)
    card_h = Inches(1.9)
    gap = Inches(0.2)
    card_w = (Inches(12.1) - gap * (len(cards) - 1)) / len(cards)
    for i, (label, value, sub, color) in enumerate(cards):
        left = Inches(0.6) + i * (card_w + gap)
        _gap_kpi_card(slide, left, top, card_w, card_h, label, value, sub, color)

    max_y = SLIDE_H - Inches(0.55)
    action_top = top + card_h + Inches(0.35)
    _textbox(slide, Inches(0.6), action_top, Inches(6), Inches(0.3), "Key Action Items", size=15, bold=True, color=TEXT_DARK)
    action_top += Inches(0.42)

    row_w = Inches(12.1)
    for heading, sentence in _gap_exec_action_items(by_category, counts, total_relevant, client_name):
        lines = _wrap_lines(sentence, row_w, size_pt=12)
        item_h = Inches(0.3) + Inches(0.19) * lines + Inches(0.24)
        if action_top + item_h > max_y:
            break
        _textbox(slide, Inches(0.6), action_top, row_w, Inches(0.3), heading, size=13.5, bold=True, color=_accent())
        action_top += Inches(0.3)
        _textbox(slide, Inches(0.6), action_top, row_w, Inches(0.19) * lines, sentence, size=12, color=TEXT_DARK)
        action_top += Inches(0.19) * lines + Inches(0.14)
        rule = slide.shapes.add_shape(1, Inches(0.6), action_top, row_w, Pt(0.75))
        _fill(rule, CARD_BORDER)
        rule.shadow.inherit = False
        action_top += Inches(0.1)

    return slide


def add_keyword_gap_slides(
    prs: Presentation, competitor_analysis: dict, client_name: str | None = None,
    keyword_gap_sheet_link: str | None = None,
) -> list:
    """Competitor Keyword Gap Analysis (2026-09-18 spec base design, split
    into a summary + per-status slides 2026-09-22): every tracked
    competitor's position/URL shown side by side with the client's own,
    using the SAME competitor-to-column mapping on every row (never "show
    whichever competitor ranks best for this particular keyword").

    2026-09-22 spec: a status (Missing/Shared/Untapped) with 6+ relevant
    keywords gets its OWN dedicated slide ("Competitor Keyword Gap —
    Missing"/"— Shared"/"— Untapped"), never merged with another status on
    the same slide. A status with 1-5 keywords stays inline on the summary
    slide only (no dedicated slide of its own); a status with 0 gets no
    slide and no table row at all. Classification itself (gap_category,
    computed upstream in semrush_analysis_service.py) is untouched — this
    only changes how the already-classified rows are laid out across
    slides. Every competitor ranking cell is hyperlinked to its real
    source URL (rule 7) on every table this function draws. Returns the
    list of slides added, summary first (always, if there's any data at
    all), then dedicated slides in Missing -> Shared -> Untapped order for
    whichever crossed the threshold."""
    off_topic_count = competitor_analysis.get("keyword_gap_off_topic_count") or 0
    rows = competitor_analysis.get("keyword_gap_rows") or []
    if not rows:
        return []

    kd_filtered, ambiguous_rows, kd_unavailable_count, competitor_columns = _prepare_keyword_gap_rows(rows)
    if not kd_filtered:
        return []

    by_category: dict[str, list[dict]] = {"Missing": [], "Shared": [], "Untapped": []}
    for r in kd_filtered:
        by_category.setdefault(r.get("gap_category") or "Missing", []).append(r)
    # kd_filtered already volume-sorted by _prepare_keyword_gap_rows, so
    # each by_category bucket inherits that same order.

    counts = {cat: len(by_category[cat]) for cat in ("Missing", "Shared", "Untapped")}
    dedicated_categories = [c for c in ("Missing", "Shared", "Untapped") if counts[c] >= _GAP_DEDICATED_THRESHOLD]
    inline_categories = [c for c in ("Missing", "Shared", "Untapped") if 1 <= counts[c] < _GAP_DEDICATED_THRESHOLD]

    # Position and URL as separate columns per ranking source (2026-09-22,
    # user request) — "#N · /path" packed into one cell read poorly once
    # every ranking column (own + each competitor) carried both; a narrow
    # fixed-width Position column plus its own URL column reads clearer,
    # even though each URL column ends up narrower than the old combined
    # cell (user confirmed that trade-off is fine).
    headers = ["Status", "Keyword", "Volume", "KD", "My Position", "URL"]
    for domain in competitor_columns:
        headers += [domain, "URL"]
    col_widths = [0.5, 1.85, 0.55, 0.4]
    # 0.85in, not something narrower like 0.45 — "Not ranking" (11 chars)
    # needs ~0.8in to fit on one line at 11pt (_wrap_lines-confirmed); any
    # narrower and every Position cell wraps to 2 lines, doubling every
    # row's height and starving the Key Insights strip below the table of
    # room (confirmed live: it silently dropped ALL insight bullets on a
    # worst-case dedicated slide once row height doubled).
    position_col_width = 0.85
    n_pairs = 1 + len(competitor_columns)
    remaining = 12.1 - sum(col_widths) - position_col_width * n_pairs
    url_col_width = round(remaining / n_pairs, 2)
    col_widths += [position_col_width, url_col_width]  # My Position, My URL
    for _ in competitor_columns:
        col_widths += [position_col_width, url_col_width]

    slides = []

    exec_summary_slide = add_keyword_gap_executive_summary_slide(
        prs, by_category, counts, off_topic_count, len(kd_filtered), client_name,
    )
    if exec_summary_slide:
        slides.append(exec_summary_slide)

    # ---- Summary slide: whichever statuses have 1-5 keywords, shown
    # inline. A status that got its own dedicated slide contributes no
    # table rows here. When EVERY status went to its own slide there is
    # nothing left to show, so the slide is skipped (Geopits 2026-09-24:
    # 300 Missing + 11 Shared, 0 Untapped rendered a slide with only the
    # legend and the Sheet button) — the Executive Summary already gives
    # the distribution, and every dedicated slide carries the button. ----
    inline_rows = [r for cat in inline_categories for r in by_category[cat]]
    if inline_rows:
        summary_slide = _blank_slide(prs)
        _content_header(summary_slide, "Competitor Keyword Gap Analysis")
        _textbox(summary_slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), "Source: Semrush Keyword Gap export", size=11, color=TEXT_MUTED)
        _draw_gap_legend(summary_slide)
        _render_gap_table(summary_slide, Inches(1.32), inline_rows, competitor_columns, headers, col_widths)
        # Table-only — no Key Insights card on this slide (2026-09-23 user
        # request: insights competed for space with the table and one long
        # line overlapped it on a large Missing table). Every insight lives
        # on add_keyword_gap_insights_slide, appended after the tables.
        if keyword_gap_sheet_link:
            _add_gap_sheet_link_button(summary_slide, keyword_gap_sheet_link)
        slides.append(summary_slide)

    # ---- One dedicated slide per status with 6+ keywords, never merged.
    # Table-only, same reasoning as the summary slide above. ----
    for category in ("Missing", "Shared", "Untapped"):
        if category not in dedicated_categories:
            continue
        status_rows = by_category[category]
        shown = status_rows[:_GAP_DEDICATED_ROW_CAP]

        slide = _blank_slide(prs)
        _content_header(slide, _GAP_STATUS_SLIDE_TITLE[category])
        _textbox(slide, Inches(8.3), Inches(0.3), Inches(4.5), Inches(0.4), "Source: Semrush Keyword Gap export", size=11, color=TEXT_MUTED)
        _draw_gap_legend(slide)

        _render_gap_table(slide, Inches(1.32), shown, competitor_columns, headers, col_widths)
        if keyword_gap_sheet_link:
            _add_gap_sheet_link_button(slide, keyword_gap_sheet_link)
        slides.append(slide)

    # ---- One consolidated Key Insights slide, covering every status that
    # has any rows — dedicated or inline — in its own column. ----
    overview_insights = []
    # 2026-09-21 spec rule 5: the gap-scale bullet must explain what the
    # split means, not just restate the counts — the trailing clause is the
    # only addition; every number is still the same real count. Shortened
    # 2026-09-23 (was a single ~180-char run-on sentence, the likeliest
    # candidate for wrapping to more real lines in PowerPoint than
    # _insights_strip's own line-count estimate reserved for it) — same
    # numbers, same interpretive framing, half the length.
    overview_insights.append(
        f"{len(kd_filtered)} relevant keyword(s) analyzed ({off_topic_count} off-topic / competitor-brand excluded) — "
        f"{counts['Shared']} Shared / {counts['Missing']} Missing / {counts['Untapped']} Untapped, "
        "showing the scale of the competitive gap."
    )
    if ambiguous_rows:
        review_examples = ", ".join(f"\"{r.get('keyword')}\"" for r in ambiguous_rows[:3])
        overview_insights.append(f"Needs manual relevance review: {review_examples} — could not confidently judge against the client's business.")
    if kd_unavailable_count:
        overview_insights.append(f"{kd_unavailable_count} keyword(s) excluded — KD_UNAVAILABLE (keyword difficulty missing in the source export).")

    insights_by_category: dict[str, list[str]] = {}
    for category in ("Missing", "Shared", "Untapped"):
        status_rows = by_category[category]
        if not status_rows:
            continue
        # Same shown-count every status's own table actually displays: the
        # dedicated row cap for a status that crossed the threshold, every
        # row (1-5) for one that stayed inline on the summary table.
        shown_count = min(len(status_rows), _GAP_DEDICATED_ROW_CAP) if category in dedicated_categories else len(status_rows)
        insights_by_category[category] = _gap_status_insights(category, status_rows, shown_count, off_topic_count, client_name)

    insights_slide = add_keyword_gap_insights_slide(prs, overview_insights, insights_by_category)
    if insights_slide:
        slides.append(insights_slide)

    return slides


# best_at bullets are written to cite "(homepage_url)" per the AI prompt's
# own grounding instruction (competitor_narrative_service.py) — good for
# keeping the model honest, not for a client-facing slide. Stripped here at
# render time only, so the citation still exists in the stored narrative
# for any future re-render/debugging.
_TRAILING_URL_CITATION_RE = re.compile(r"\s*[\(\-–—]*\s*https?://\S+?\)?\.?\s*$")


def _strip_trailing_url_citation(text: str) -> str:
    stripped = _TRAILING_URL_CITATION_RE.sub("", text).rstrip()
    if stripped and not stripped.endswith((".", "!", "?")):
        stripped += "."
    return stripped or text


_SCREENSHOT_EMBED_MAX_WIDTH_PX = 700
_SCREENSHOT_EMBED_JPEG_QUALITY = 78


def _compress_screenshot_for_embed(raw_bytes: bytes) -> bytes:
    """Competitor homepage screenshots come back from Playwright as a raw
    1280x800 lossless PNG (screenshot_client.py) but render at only ~3.9in
    wide on the slide — embedding the untouched capture bloats the PPTX by
    hundreds of KB to a couple MB per competitor for pixel detail nobody
    ever sees at that display size. Capture success/failure also varies
    run to run (bot detection, timeouts, network flakiness), which is why
    the SAME client's report can swing wildly in file size between two
    separate generations even though the actual report content — text,
    tables, insights — is identical (flagged live 2026-09-18: one run's
    competitor screenshots alone pushed a report from ~700KB to ~2MB).
    Downscaled and re-encoded as JPEG right before embedding — purely
    decorative, never fed to any AI call (the vision-analysis path uses
    its own separate, uncompressed capture of the CLIENT's own homepage,
    site_audit.py's homepage_shot — untouched by this), so lossy
    recompression here costs nothing but file size. Falls back to the raw
    bytes on any decode failure so a corrupt capture still gets a chance
    at add_picture's own try/except rather than silently vanishing here."""
    try:
        from PIL import Image

        img = Image.open(BytesIO(raw_bytes)).convert("RGB")
        if img.width > _SCREENSHOT_EMBED_MAX_WIDTH_PX:
            ratio = _SCREENSHOT_EMBED_MAX_WIDTH_PX / img.width
            img = img.resize((_SCREENSHOT_EMBED_MAX_WIDTH_PX, max(1, int(img.height * ratio))), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="JPEG", quality=_SCREENSHOT_EMBED_JPEG_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:
        return raw_bytes


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
            slide.shapes.add_picture(BytesIO(_compress_screenshot_for_embed(screenshot)), img_left, card_top, width=img_width)
        except Exception:
            pass
        else:
            _textbox(slide, img_left, card_top + Inches(2.6), img_width, Inches(0.3), competitor_domain, size=10.5, color=TEXT_MUTED, align=PP_ALIGN.CENTER)

    bullets_max_y = card_top + card_height - Inches(0.15)
    chars_per_line = max(20, int(text_width / 914400 * 14))
    line_h = Inches(0.24)
    for raw_item in best_at[:6]:
        item = _strip_trailing_url_citation(raw_item)
        lines = max(1, -(-len(item) // chars_per_line))
        item_h = line_h * lines + Inches(0.08)
        if y + item_h > bullets_max_y:
            break
        _icon_dot(slide, Inches(0.9), y + Inches(0.08), Inches(0.09), DEFAULT_ACCENT)
        _textbox(slide, Inches(1.15), y, text_width - Inches(0.25), line_h * lines, item, size=12.5)
        y += item_h
    return slide


def add_competitor_opportunity_slide(prs: Presentation, client_name: str, competitor_domain: str, narrative: dict):
    """Competitor Opportunity Analysis — rebuilt as a single-differentiator
    slide (2026-09-11 user spec): Headline ("{competitor}'s Unique Angle:
    ___") -> Unique Angle (1-2 bullets, specific mechanics, never marketing
    adjectives) -> Gap for {client_name} (exactly one bullet, tied
    specifically to that angle) -> Shared Advantage (rendered ONLY when
    the AI found genuine overlap with another competitor already covered
    in this batch — never invented, omitted entirely otherwise). Replaces
    the old three-quadrant (WHAT COMPETITOR HAS / WHAT CLIENT LACKS / WHY
    IT MATTERS) + areas_of_focus + growth_opportunity structure, which
    routinely surfaced baseline hygiene tactics most competitors share
    rather than isolating what's genuinely distinct about this one — see
    competitor_narrative_service's rewritten prompt. Website screenshot
    placement/sizing is UNCHANGED from before this rewrite, per explicit
    user instruction. Grounded only in narrative["headline"] /
    ["unique_angle"] / ["gap"] / ["shared_advantage"] (same batched AI
    call) — absent (no slide) if the AI produced none of it, same
    silent-skip pattern as every other AI-derived slide in this file."""
    headline = narrative.get("headline")
    unique_angle = narrative.get("unique_angle") or []
    evidence = narrative.get("evidence") or []
    why_it_matters = narrative.get("why_it_matters")
    gap = narrative.get("gap")
    opportunity = narrative.get("opportunity")
    implementation = narrative.get("implementation") or []
    shared_advantage = narrative.get("shared_advantage")
    if not headline and not unique_angle and not gap:
        return None
    screenshot = narrative.get("screenshot")

    slide = _blank_slide(prs)
    _content_header(slide, f"Competitor Opportunity Analysis: {competitor_domain}")

    card_top = Inches(1.1)
    card_height = Inches(7.15) - card_top
    card_width = Inches(7.8) if screenshot else Inches(12.1)
    text_width = card_width - Inches(0.75)
    _card(slide, Inches(0.6), card_top, card_width, card_height)
    y = card_top + Inches(0.25)

    if screenshot:
        img_left, img_width = Inches(8.7), Inches(3.9)
        try:
            slide.shapes.add_picture(BytesIO(_compress_screenshot_for_embed(screenshot)), img_left, card_top, width=img_width)
        except Exception:
            pass  # corrupt/unreadable capture — skip the image, text side is unaffected
        else:
            _textbox(slide, img_left, card_top + Inches(2.6), img_width, Inches(0.3), competitor_domain, size=10.5, color=TEXT_MUTED, align=PP_ALIGN.CENTER)

    # Every section below stops writing once it would cross this line —
    # standing rule (2026-09-11): a slide's content must never overlap or
    # run off the card, so each section re-checks remaining room rather
    # than assuming its own text always fits.
    max_y = card_top + card_height - Inches(0.15)
    line_h = Inches(0.22)

    def _wrapped(text: str, size: float = 11.5) -> int:
        chars_per_line = max(20, int(text_width / 914400 * 14 * (11 / size)))
        return max(1, -(-len(text) // chars_per_line))

    if headline:
        headline_text = f"{competitor_domain}'s Unique Angle: {headline}"
        h_lines = _wrapped(headline_text, size=15)
        h_h = Inches(0.28) * h_lines
        if y + h_h <= max_y:
            _textbox(slide, Inches(0.9), y, text_width, h_h, headline_text, size=15, bold=True, color=DEFAULT_ACCENT)
            y += h_h + Inches(0.18)

    def _section(label: str, label_color, bullets: list[str], dot_color=None) -> None:
        nonlocal y
        if not bullets or y >= max_y:
            return
        # Whole section or nothing — precompute the full block height (label
        # + every bullet) before drawing anything. Confirmed live: the old
        # version checked room for the label alone, drew it unconditionally,
        # THEN checked each bullet's room in a loop — so "GAP FOR
        # BHARATBENZ" and "OPPORTUNITY" printed as orphan labels with zero
        # bullet text under them once EVIDENCE/WHY IT MATTERS had already
        # eaten the remaining room. A label with nothing under it is worse
        # than no label at all.
        item_heights = [line_h * _wrapped(b) + Inches(0.08) for b in bullets]
        block_h = Inches(0.26) + sum(item_heights, Emu(0)) + Inches(0.12)
        if y + block_h > max_y:
            return
        _textbox(slide, Inches(0.9), y, text_width, Inches(0.22), label, size=10.5, bold=True, color=label_color)
        y += Inches(0.26)
        for bullet, item_h in zip(bullets, item_heights):
            lines = _wrapped(bullet)
            if dot_color:
                _icon_dot(slide, Inches(0.9), y + Inches(0.07), Inches(0.08), dot_color)
                _textbox(slide, Inches(1.15), y, text_width - Inches(0.25), line_h * lines, bullet, size=11.5)
            else:
                _textbox(slide, Inches(0.9), y, text_width, line_h * lines, bullet, size=11, color=TEXT_MUTED)
            y += item_h
        y += Inches(0.12)

    # Priority order: the actionable chain (what's the angle, what's
    # missing, what to build, how) renders before the supporting
    # justification (Evidence/Why It Matters) — a client reading this slide
    # cares most about what to DO; the evidence backing it up is the first
    # thing that can be trimmed if the card runs out of room, not the
    # action items themselves. _section() now either draws a section
    # completely or skips it entirely (see above), so whichever section
    # runs out of room first is always the LEAST important one still
    # queued, cleanly absent rather than a half-rendered orphan.
    _section("UNIQUE ANGLE", _accent(), unique_angle[:2], dot_color=DEFAULT_ACCENT)
    _section(f"GAP FOR {(client_name or 'CLIENT').upper()}", BAD, [gap] if gap else [], dot_color=BAD)
    _section("OPPORTUNITY", GOOD, [opportunity] if opportunity else [], dot_color=GOOD)
    _section("IMPLEMENTATION", GOOD, implementation[:3], dot_color=GOOD)
    _section("EVIDENCE", TEXT_MUTED, evidence[:3], dot_color=TEXT_MUTED)
    _section("WHY IT MATTERS", TEXT_MUTED, [why_it_matters] if why_it_matters else [])
    if shared_advantage:
        _section("SHARED ADVANTAGE", TEXT_MUTED, [shared_advantage])

    return slide


def _strategic_cluster_insights(keywords: list[dict]) -> list[str]:
    """Key Insights for one manual-sheet cluster, computed only from the
    sheet's own values on this slide's visible keywords (never the wider
    Semrush universe), so every number here is checkable on the table."""
    out = []
    vols = [(k, _num(k.get("search_volume"))) for k in keywords if k.get("search_volume") is not None]
    kds = [_num(k.get("keyword_difficulty")) for k in keywords if k.get("keyword_difficulty") is not None]
    # "Highest demand"/"quickest wins" only ever name a keyword that passed
    # the relevance check — never a flagged (†) one.
    verified = [k for k in keywords if k.get("relevance_status") not in _EXCLUDED_RELEVANCE_STATUSES]
    verified_vols = [(k, v) for k, v in vols if k in verified]
    if vols:
        out.append(f"{len(keywords)} priority keyword(s), {sum(v for _, v in vols):,.0f} combined monthly searches.")
    if verified_vols:
        top_kw, top_vol = max(verified_vols, key=lambda kv: kv[1])
        kd_text = f", KD {int(_num(top_kw['keyword_difficulty']))}" if top_kw.get("keyword_difficulty") is not None else ""
        out.append(f"Highest demand: \"{top_kw['keyword']}\" — {top_vol:,.0f} searches/month{kd_text}.")
    easy = [k for k in verified if k.get("keyword_difficulty") is not None and _num(k["keyword_difficulty"]) < 30
            and _num(k.get("search_volume")) > 0]
    if easy:
        best = max(easy, key=lambda k: _num(k.get("search_volume")))
        out.append(f"{len(easy)} keyword(s) under KD 30 — quickest wins, led by \"{best['keyword']}\" "
                   f"({_num(best['search_volume']):,.0f}/mo, KD {int(_num(best['keyword_difficulty']))}).")
    elif kds:
        out.append(f"Avg. KD {sum(kds) / len(kds):.0f} — no low-difficulty entry point; needs content depth and links.")
    commercial = [k for k in keywords if (k.get("display_intent") or k.get("intent")) and any(
        m in (k.get("display_intent") or k["intent"]).lower() for m in ("commercial", "transactional"))]
    if commercial and len(commercial) < len(keywords):
        out.append(f"{len(commercial)} of {len(keywords)} keyword(s) carry commercial/transactional intent — "
                   "map these to product/landing pages, the rest to guides.")
    elif commercial:
        out.append("Every keyword here carries commercial/transactional intent — target with a product/landing page, not a blog post.")
    return out


def _short_exclusion_reason(reason: str) -> str:
    r = (reason or "").lower()
    if "adult" in r or "explicit" in r:
        return "adult/explicit"
    if "brand" in r:
        return "another company's brand"
    if "career" in r or "recruitment" in r:
        return "job search"
    if "navigation" in r or "login" in r:
        return "navigation query"
    if "geographic" in r:
        return "out of market"
    return "not relevant to this business"


def _quoted_list(items: list[str], limit: int = 3) -> str:
    shown = ", ".join(f'"{i}"' for i in items[:limit])
    more = len(items) - limit
    return f"{shown} (+{more} more)" if more > 0 else shown


def _temporal_keywords_line(keywords: list[str]) -> str | None:
    """§44: year/latest searches get one evergreen page refreshed each
    year, never a new page per year."""
    temporal = [k for k in keywords if is_temporal(k)]
    if not temporal:
        return None
    return (f"Time-sensitive: {_quoted_list(temporal, 2)} — keep one evergreen page and refresh its year/latest "
            "details annually; don't create a new page per year.")


def _validated_strategic_cluster_insights(c: dict) -> list[str]:
    """Universal SEO Keyword engine (2026-09-23) insights for one client-
    sheet cluster: the sheet's grouping is kept, and these lines report
    what the engine's validation found — target page + action + confidence
    (§53/§55/§30), what the relevance/brand gate removed (§21/§45), where
    the sheet's intent label disagrees with the keyword's own wording (§8),
    and a split-test flag (§42). Capped at 5 lines, most decision-relevant
    first; the plain demand lines from _strategic_cluster_insights fill any
    space left."""
    keywords = c["keywords"]
    out: list[str] = []
    base = _strategic_cluster_insights(keywords)
    target = c.get("target_url")
    if target:
        target_text = f"existing page {target}"
    elif c.get("closest_url"):
        target_text = f"closest existing page is only a weak match ({c['closest_url']})"
    else:
        target_text = "new page (no existing page covers this)"
    ranking = c.get("ranking_evidence")
    if target and ranking:
        target_text += f" (already ranks #{ranking['position']} for {ranking['keywords']} of these keywords)"
    priority_text = (
        f" Priority {c['roadmap_priority']} (opportunity {c.get('opportunity')}/100)." if c.get("roadmap_priority") else ""
    )
    page_type = c.get("recommended_page_type") or "dedicated page"
    if c.get("cluster_type"):
        page_type = f"{page_type} ({c['cluster_type']})"
    gap_text = f" Gap: {c['content_gap_type']}." if c.get("content_gap_type") and not target else ""
    out.append(
        f"Target: {page_type} — {target_text}. "
        f"Action: {c.get('decision') or c.get('recommended_action')}.{gap_text} "
        f"Confidence {c.get('confidence_level')} ({c.get('confidence')}/100).{priority_text}"
    )
    if c.get("primary_keyword"):
        # §55/§66-D: the page's one primary keyword and the searcher's need.
        need = f" · User need: {c['user_need']}" if c.get("user_need") else ""
        out.append(f'Primary keyword: "{c["primary_keyword"]}"{need}.')
    excluded = c.get("excluded") or []
    if excluded:
        out.append(
            "Removed from this cluster: "
            + ", ".join(f'"{e["keyword"]}" ({_short_exclusion_reason(e["reason"])})' for e in excluded[:3])
            + (f" (+{len(excluded) - 3} more)" if len(excluded) > 3 else "") + "."
        )
    flags = c.get("relevance_flags") or []
    other_sites = [f["keyword"] for f in flags if (f.get("reason") or "").startswith("Other Website Search")]
    doubts = [f["keyword"] for f in flags if f["keyword"] not in other_sites]
    if other_sites or doubts:
        bits = []
        if other_sites:
            bits.append(f"{_quoted_list(other_sites)} are searches for another website")
        if doubts:
            bits.append(f"{_quoted_list(doubts)} may not match this business (possible other brand or product)")
        out.append(
            "Relevance check (marked † in the table): " + "; ".join(bits)
            + " — confirm with the client before targeting."
        )
    mismatches = c.get("intent_mismatches") or []
    if mismatches:
        detected = Counter(m["detected"] for m in mismatches).most_common(1)[0][0]
        sheet = mismatches[0].get("sheet")
        out.append(
            f"Intent corrected: {_quoted_list([m['keyword'] for m in mismatches])} read as {detected.lower()} "
            f"(sheet said {sheet}) — cover these in a guide/spec section, not the main product page."
        )
    # A keyword already called out by the relevance check isn't repeated as
    # a split-test outlier (no repeated lines, 2026-09-08 rule).
    flagged_lower = {f["keyword"].lower() for f in flags}
    outliers = [o for o in c.get("outliers") or [] if o.lower() not in flagged_lower]
    if len(outliers) >= 2:
        out.append(
            f"Split test: {_quoted_list(outliers)} share no core term with the rest of this cluster — "
            "likely separate search needs; consider separate pages."
        )
    temporal_line = _temporal_keywords_line([k["keyword"] for k in keywords])
    if temporal_line:
        out.append(temporal_line)
    # Decision lines first; the plain demand lines fill what room is left.
    for line in base:
        if mismatches and line.startswith("Every keyword here carries commercial"):
            continue  # contradicted by the intent check above
        out.append(line)
    return out[:5]


def add_strategic_keyword_clusters_slide(prs: Presentation, strategic_keyword_clusters: list[dict] | None) -> list:
    """SEO Cluster & Keyword Selection for Presentation (2026-09-21 spec):
    one table slide per cluster the client's own manually-uploaded
    keyword-cluster sheet supports, already pre-selected and ranked by
    strategic_keyword_selection_service.select_strategic_clusters (business
    relevance/demand/commercial value/SEO opportunity/intent diversity —
    never volume or keyword count alone). Absent entirely when no manual
    cluster file was uploaded or nothing in it clears the selection floor —
    when present it REPLACES the Semrush/AI-clustered Target Keywords
    slides (2026-09-23 user instruction — see build_report's call site).

    Table layout is this deck's own established house style (matches
    add_keyword_research_slide's Keyword/Search Volume/Keyword Difficulty
    columns) pending the client's ClearTouch reference deck for a final
    visual pass — the column set and per-cluster grouping already follow
    the spec's structural ask (cluster name, sub-category where available,
    keyword + real metrics, one section per cluster), so this renders a
    real, usable slide today rather than a placeholder."""
    if not strategic_keyword_clusters:
        return []

    any_sub_category = any(kw.get("sub_category") for c in strategic_keyword_clusters for kw in c["keywords"])
    any_intent = any(kw.get("intent") for c in strategic_keyword_clusters for kw in c["keywords"])

    headers = ["Keyword"]
    col_widths = [4.6]
    if any_sub_category:
        headers.append("Sub-Category")
        col_widths.append(2.6)
    headers += ["Search Volume", "KD"]
    col_widths += [2.4, 1.3]
    if any_intent:
        headers.append("Intent")
        col_widths.append(1.2)
    # Pad/trim so widths always sum to the same 12.1in every other table in
    # this file uses, regardless of which optional columns are present.
    scale = 12.1 / sum(col_widths)
    col_widths = [round(w * scale, 2) for w in col_widths]

    slides = []
    for c in strategic_keyword_clusters:
        rows = []
        flagged_keywords = {f["keyword"].lower() for f in c.get("relevance_flags") or []}
        for kw in c["keywords"]:
            # † = flagged by the relevance check (explained in the insights).
            row = [kw["keyword"] + (" †" if kw["keyword"].lower() in flagged_keywords else "")]
            if any_sub_category:
                row.append(kw.get("sub_category") or "—")
            row.append(f"{int(kw['search_volume']):,}" if kw.get("search_volume") is not None else "—")
            row.append(str(int(kw["keyword_difficulty"])) if kw.get("keyword_difficulty") is not None else "—")
            if any_intent:
                # §8/§34: the engine's corrected intent where the sheet's
                # label contradicts the keyword's own wording.
                row.append(kw.get("display_intent") or kw.get("intent") or "—")
            rows.append(tuple(row))
        slides.append(_table_slide(
            prs, f"Target Keywords: {c['cluster']}", headers, rows,
            col_widths=col_widths, source="Client-provided keyword cluster sheet",
            insights=_validated_strategic_cluster_insights(c) if "confidence" in c else _strategic_cluster_insights(c["keywords"]),
        ))
    return slides


_TARGET_KEYWORDS_EXCLUDED_CLUSTERS = {
    _NEEDS_REVIEW_CLUSTER_LABEL, _CAREER_ROUTE_CLUSTER_LABEL, _GEO_ROUTE_CLUSTER_LABEL,
}
_EXCLUDED_CLUSTER_NOUN = {
    _NEEDS_REVIEW_CLUSTER_LABEL: "keyword(s) awaiting relevance review",
    _CAREER_ROUTE_CLUSTER_LABEL: "job/career search(es)",
    _GEO_ROUTE_CLUSTER_LABEL: "out-of-market location search(es)",
}


def _metric_cell(value) -> str:
    """Search volume / KD table cell: a real number formatted, or "—" when
    the row has none (Search Console-only rows) — never the literal "None"
    a raw str() of a missing value printed (LumberFi deck, 2026-09-23)."""
    if value in (None, "") or str(value).strip().lower() in ("none", "nan", "n/a"):
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{int(f):,}" if f.is_integer() else f"{f:,.1f}"


def _short_page(label: str) -> str:
    """A target page for an insight line: its path for an existing URL
    (the domain is the client's own, already in the footer), or "(new
    page)" — the cluster name is already on the line."""
    if label.startswith("new page:"):
        return "(new page)"
    path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+", "", label, flags=re.I)
    return path or "/"


def add_keyword_topic_map_slide(prs: Presentation, keyword_strategy: dict | None):
    """Universal SEO engine §18 (topic hierarchy), §38 (topical coverage)
    and §37 (internal linking) on ONE slide, built from the same clusters
    and target pages as the Target Keywords slides before it — one row per
    parent topic: its pages (clusters), how many already have a real page,
    and which search intents have no page yet. Insights carry the internal
    links. Skipped when there is no strategy (no clusters)."""
    topics = (keyword_strategy or {}).get("topics") or []
    if not topics:
        return None
    headers = ["Parent Topic", "Pages in this Topic (clusters)", "Coverage", "Intent with no page yet"]
    rows = []
    # Six topics max, three clusters named per topic: the table must leave
    # room for the internal-link insights (Geopits: 8 rows of 20+ cluster
    # names each pushed every insight off the slide).
    for t in topics[:6]:
        names = t["clusters"]
        cluster_text = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
        rows.append((
            t["parent"],
            cluster_text,
            f"{t['coverage']} ({t['covered']} of {t['total']} have a page)",
            ", ".join(t["intents_without_page"]) or "—",
        ))
    insights = []
    links = [l for t in topics for l in t["links"]]
    for l in links[:2]:
        insights.append(
            f"Internal link ({l['relation']}): \"{l['from_cluster']}\" {_short_page(l['from'])} → "
            f"\"{l['to_cluster']}\" {_short_page(l['to'])}."
        )
    if len(links) > 2:
        insights.append(f"{len(links) - 2} more internal link(s) follow the same topic → hub pattern (full list in the keyword Sheet's Page Map).")
    model = (keyword_strategy or {}).get("business_model") or {}
    if model.get("models") and model["models"] != ["Undetermined"]:
        insights.insert(0, f"Website model: {', '.join(model['models'])} — read from the site's own sections and target market.")
    weak = [t for t in topics if t["coverage"] in ("Weak", "Missing")]
    if weak:
        w = weak[0]
        have = f"only {w['covered']} of its {w['total']} page(s) exist" if w["covered"] else             f"none of its {w['total']} page(s) exist yet"
        insights.append(f"Weakest topic: {w['parent']} — {have}; build these before expanding stronger topics.")
    return _table_slide(
        prs, "Keyword Topic Map", headers, rows, col_widths=[2.2, 5.2, 2.6, 2.1],
        source="Keyword clusters + crawled pages", insights=insights[:5], wrap_cols={1},
    )


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
    # Universal SEO Keyword engine (2026-09-23): per-keyword detected intent
    # (§8) shown when the pipeline computed it.
    show_intent = any(r.get("detected_intent") for r in keyword_rows)
    if show_intent:
        headers = ["Keyword", "Role", "Intent", "Search Volume", "KD"]
        col_widths = [4.9, 1.4, 2.4, 2.0, 1.4]

    # §21/§53: routing buckets (job searches, out-of-market geography,
    # unconfirmed relevance) are exclusions, never "Target Keywords" — the
    # Lumber report showed "Jobs / Careers" and "Geographic Mismatch" as
    # target slides. Counted into one visible note instead.
    excluded_counts: Counter = Counter()
    clusters: dict[str, list[dict]] = {}
    for r in keyword_rows:
        label = (r.get("cluster") or "").strip()
        if label in _TARGET_KEYWORDS_EXCLUDED_CLUSTERS:
            excluded_counts[label] += 1
            continue
        clusters.setdefault(label, []).append(r)

    def _keyword_insights(rows_for_group: list[dict]) -> list[str]:
        volumes = [_num(r.get("search_volume")) for r in rows_for_group]
        kds = [_num(r.get("keyword_difficulty")) for r in rows_for_group if r.get("keyword_difficulty") not in (None, "")]
        total_volume = sum(volumes)
        # Prefer the upstream pipeline's own Primary keyword selection (which
        # weighs commercial intent and ranking opportunity, not just volume)
        # when present, so "Top opportunity" always names the same keyword
        # the Role column marks Primary — falls back to highest-volume when
        # no primary_or_secondary field was set (e.g. a real Semrush Cluster
        # column was uploaded and the pipeline never ran).
        top = next((r for r in rows_for_group if r.get("primary_or_secondary") == "Primary"), None) \
            or max(rows_for_group, key=lambda r: _num(r.get("search_volume")))
        easy_wins = [r for r in rows_for_group if _num(r.get("keyword_difficulty"), default=100) < 20 and _num(r.get("search_volume")) > 0]
        if total_volume > 0:
            out = [f"{len(rows_for_group)} keywords, {total_volume:,.0f} combined monthly searches."]
            kd = top.get("keyword_difficulty")
            kd_text = f", KD {kd}" if kd not in (None, "") else ""
            # §55/§66-D: the searcher's need behind the primary keyword.
            need_text = f" User need: {top['user_need']}." if top.get("user_need") else ""
            out.append(f"Top opportunity: \"{top.get('keyword')}\" — {_num(top.get('search_volume')):,.0f} searches/month{kd_text}.{need_text}")
        else:
            # Search Console-only rows carry no Semrush search volume —
            # never print "0 searches/month, KD n/a" as if that were data
            # (LumberFi deck, 2026-09-23).
            impressions = sum(_num(r.get("gsc_impressions")) for r in rows_for_group)
            out = [f"{len(rows_for_group)} keyword(s) from Search Console"
                   + (f" — {impressions:,.0f} impressions in the report window." if impressions else ".")
                   + " No Semrush search volume uploaded for these."]
            out.append(f"Top keyword: \"{top.get('keyword')}\".")
        # §22 page type (strategy layer) when computed, else the pipeline's
        # coarse page category; §29 cluster type alongside it.
        page_category = top.get("recommended_page_type") or top.get("page_category")
        if page_category and top.get("cluster_type"):
            page_category = f"{page_category} ({top['cluster_type']})"
        existing_url = top.get("existing_page_url")
        # Universal SEO Keyword engine (2026-09-23) §53/§55: the cluster's
        # own target decision — only added when the pipeline computed it.
        # The §53 decision label wins once the strategy layer set it.
        action = top.get("decision") or top.get("recommended_action")
        action_text = f" Action: {action}." if action else ""
        if top.get("content_gap_type") and not (existing_url and top.get("existing_page_match_strength") in ("strong", "partial")):
            action_text += f" Gap: {top['content_gap_type']}."
        if page_category and existing_url and top.get("existing_page_match_strength") == "weak":
            out.append(f"Recommended format: {page_category} — closest existing page is only a weak match ({existing_url}).{action_text}")
        elif page_category and existing_url:
            out.append(f"Recommended format: {page_category} — an existing page already covers this: {existing_url}.{action_text}")
        elif page_category:
            out.append(f"Recommended format: {page_category} — no existing page covers this yet, new page opportunity.{action_text}")
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
        # §30/§60/§61: confidence + the evidence behind the grouping, placed
        # ahead of the KD/CPC colour lines so the 5-line insight cap never
        # drops it.
        if top.get("cluster_confidence") is not None:
            priority_text = (
                f" Priority {top['roadmap_priority']} (opportunity {top.get('cluster_opportunity')}/100)."
                if top.get("roadmap_priority") else ""
            )
            out.append(
                f"Confidence: {top.get('cluster_confidence_level')} ({top.get('cluster_confidence')}/100) — "
                f"{top.get('cluster_reason') or 'grouped by shared entity and intent'}.{priority_text}"
            )
        temporal_line = _temporal_keywords_line([r.get("keyword") or "" for r in rows_for_group])
        if temporal_line:
            out.append(temporal_line)
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
            (r.get("keyword", ""), "Primary" if i == 0 else "Secondary", _metric_cell(r.get("search_volume")), _metric_cell(r.get("keyword_difficulty")))
            for i, r in enumerate(deduped)
        ]
        insights = _keyword_insights(deduped) if deduped else []
        return [_table_slide(prs, "Target Keywords", headers, rows, col_widths=col_widths, source="Semrush export", insights=insights)]

    # Cluster order (2026-09-20 spec steps 19-21): lead with the client's
    # strongest CORE commercial opportunity, never simply whichever cluster
    # has the most combined search volume — keyword_cluster_pipeline's
    # _assign_core_category_and_priority already scored this (commercial
    # intent + real ranking signal, ahead of raw volume) and stamped
    # cluster_priority (1 = highest) on every row. Falls back to the old
    # volume-desc sort only when cluster_priority was never set at all
    # (e.g. a raw Semrush-native Cluster column with no pipeline run, or
    # existing tests/callers that don't set it) — every real pipeline run
    # sets it on every clustered row, so this is a compatibility path, not
    # the normal case.
    _tier_rank = {"High": 0, "Medium": 1, "Low": 2, "Human Review": 3}

    def _cluster_sort_key(kv: tuple[str, list[dict]]) -> tuple:
        _label, cluster_rows = kv
        # §66-K/§31: the priority roadmap decides slide order when the
        # strategy layer scored these rows; the older core-category rank
        # and volume only order rows scored before it existed.
        tier = cluster_rows[0].get("roadmap_priority")
        if tier in _tier_rank:
            return (-1, _tier_rank[tier], -_num(cluster_rows[0].get("cluster_opportunity")))
        priorities = [r.get("cluster_priority") for r in cluster_rows if r.get("cluster_priority") is not None]
        if priorities:
            return (0, min(priorities))
        return (1, -sum(_num(r.get("search_volume")) for r in cluster_rows))

    ranked = sorted(clusters.items(), key=_cluster_sort_key)
    # §41 minimum useful pages: multi-keyword clusters first, then single-
    # keyword pages only if slide room is left over.
    multi = [kv for kv in ranked if len({r.get("keyword") for r in kv[1]}) > 1 or not kv[0]]
    single = [kv for kv in ranked if kv not in multi]
    ranked = multi + single
    slides = []
    shown = ranked[:max_clusters]
    for idx, (label, rows_for_cluster) in enumerate(shown):
        sorted_rows = sorted(rows_for_cluster, key=lambda r: _num(r.get("search_volume")), reverse=True)
        seen = set()
        deduped = []
        for r in sorted_rows:
            kw = r.get("keyword", "")
            if kw in seen:
                continue
            seen.add(kw)
            deduped.append(r)
        if show_intent:
            rows = [
                (
                    r.get("keyword", ""),
                    r.get("primary_or_secondary") or ("Primary" if i == 0 else "Secondary"),
                    r.get("detected_intent") or r.get("intent") or "—",
                    _metric_cell(r.get("search_volume")), _metric_cell(r.get("keyword_difficulty")),
                )
                for i, r in enumerate(deduped)
            ]
        else:
            rows = [
                (
                    r.get("keyword", ""),
                    r.get("primary_or_secondary") or ("Primary" if i == 0 else "Secondary"),
                    _metric_cell(r.get("search_volume")), _metric_cell(r.get("keyword_difficulty")),
                )
                for i, r in enumerate(deduped)
            ]
        # An empty label here means real clustering DID run (this loop only
        # runs when `clusters` has at least one non-"" key — the fully-flat
        # no-clustering-at-all case returns early above) but these specific
        # keywords couldn't be confidently grouped with anything — confirmed
        # live 2026-09-19: rendering that bucket as a bare "Target Keywords"
        # title made it look identical to (and get confused with) the
        # separate no-clustering-ran-at-all fallback, when every OTHER slide
        # in the same deck clearly has a real cluster subheading. "Other /
        # Ungrouped Keywords" makes the distinction explicit instead.
        title = f"Target Keywords: {label}" if label else "Target Keywords: Other / Ungrouped Keywords"
        insights = _keyword_insights(deduped) if deduped else []
        if idx == len(shown) - 1 and excluded_counts:
            # §62 review queue / exclusions, stated once on the last slide
            # (kept within the 5-line insight cap).
            parts = ", ".join(
                f"{n} {_EXCLUDED_CLUSTER_NOUN.get(lbl, lbl.lower())}" for lbl, n in excluded_counts.most_common()
            )
            insights = insights[:4] + [f"Not targeted: {parts} — kept in the full keyword list for review, not on any target page."]
        has_volume = any(_num(r.get("search_volume")) > 0 for r in deduped)
        source = "Semrush export" if has_volume else "Google Search Console queries"
        slides.append(_table_slide(prs, title, headers, rows, col_widths=col_widths, source=source, insights=insights))
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
        # Same Primary-selection preference as add_keyword_research_slide
        # above — use the upstream pipeline's own pick when available.
        primary = next((r for r in rows_for_cluster if r.get("primary_or_secondary") == "Primary"), None) \
            or max(rows_for_cluster, key=lambda r: _num(r.get("search_volume")))
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
            "data_source": "KEYWORD_MODEL",
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
        ["Keyword", "Current Position", "Priority", "Recommendation", "Target", "Projected Monthly Clicks", "Projected Growth"],
        rows, col_widths=[3.1, 1.6, 1.2, 2.0, 1.0, 1.8, 1.4],
        source="Semrush Keyword Gap + industry-benchmark CTR (modeled, not measured GSC traffic)", insights=insights,
    )


def _same_domain(a: str | None, b: str | None) -> bool:
    """Loose domain match (strip scheme/www/trailing slash, case-insensitive)
    — used to confirm a Domain Overview row is really the client's own site
    before trusting its backlinks_total, since a row's own "domain" field can
    disagree with website_url on "www." (see site_audit.py's own-row
    handling in domain_overview_rows)."""
    def _norm(d: str | None) -> str:
        d = (d or "").lower().strip()
        for prefix in ("https://", "http://"):
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.removeprefix("www.").rstrip("/")
    return bool(a) and bool(b) and _norm(a) == _norm(b)


def add_backlink_profile_slide(
    prs: Presentation, backlink_rows: list[dict], row_count: int, backlink_summary: dict | None = None,
    own_domain_rating: int | None = None, domain_overview_backlinks_total: int | None = None,
):
    """Ahrefs/Semrush-widget-style summary — Backlinks, Referring Domains,
    Domain Rating, % dofollow, plus a Link Attributes breakdown when a
    Semrush Backlink List PDF summary was uploaded (backlink_summary). That
    export's aggregate stats are a real site-wide count, more authoritative
    than what's computed from a possibly-partial backlinks CSV, so they're
    preferred wherever both are available. Total backlinks falls back next
    to a Domain Overview upload's own aggregate (domain_overview_backlinks_
    total) before the raw CSV row_count — row_count is the literal number of
    rows in the uploaded Backlink List export, which silently equals
    Semrush's export-tier row cap (e.g. exactly 10,000) rather than the
    site's real total whenever the real count exceeds that cap (2026-09-17
    fix — this mismatch against the Competitor Analysis table's own
    Domain-Overview-sourced backlinks_total for the same site is why this
    slide was pulled from the report entirely on 2026-09-08).

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

    if backlink_summary:
        total_backlinks = backlink_summary["backlinks_total"]
    elif domain_overview_backlinks_total is not None:
        total_backlinks = domain_overview_backlinks_total
    else:
        total_backlinks = row_count or None
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
        source = "Homepage screenshot analysis" if ux_findings.get("ui_fixes_source") == "vision" else "Manual UX walkthrough"
        slides.append(_table_slide(
            prs, "UI-Level Fixes", ["Issue", "Where", "Fix", "Severity"], rows,
            col_widths=[3.4, 2.8, 4.4, 1.5], source=source,
        ))
    elif ux_findings.get("note"):
        slide = _blank_slide(prs)
        _content_header(slide, "UI-Level Fixes")
        _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(2.0))
        _textbox(slide, Inches(0.9), Inches(1.4), Inches(11.4), Inches(1.4), ux_findings["note"], size=13)
        slides.append(slide)

    # Onboarding breakdown of the landing page — separate slide from
    # UI-Level Fixes (that one is broken/missing things; this one is "the
    # page works but is fighting the visitor's psychology"). 2026-09-20
    # spec: "Principle / Heuristic" is the column name (not "Bias" — the
    # analysis spans UX/CRO/usability/trust/brand-consistency principles,
    # not only psychological biases), item count is whatever the evidence
    # supports (never padded to a fixed 5), and the summary line must not
    # claim a measured "ranked by conversion impact" finding — neither the
    # manual-notes pass nor the screenshot vision pass has analytics/
    # experiment data to back that. Source is reported accurately per
    # which pass actually produced this list (see onboarding_breakdown_
    # source, set only by the vision pass — its absence means the manual-
    # notes pass supplied it instead).
    breakdown = ux_findings.get("onboarding_breakdown") or []
    if breakdown:
        rows = [
            (b.get("principle") or b.get("bias") or "", b.get("where", ""), b.get("suggestion", ""))
            for b in breakdown[:5]
        ]
        source = "Homepage screenshot analysis" if ux_findings.get("onboarding_breakdown_source") == "vision" else "Manual UX walkthrough"
        principle_names = [r[0] for r in rows if r[0]]
        if principle_names:
            joined = principle_names[0] if len(principle_names) == 1 else ", ".join(principle_names[:-1]) + f", and {principle_names[-1]}"
            summary = f"{joined} are the key friction areas identified on the landing page from {source.lower()}."
        else:
            summary = None
        slides.append(_table_slide(
            prs, "Onboarding Breakdown — Landing Page", ["Principle / Heuristic", "Where It Shows Up", "Directional Suggestion"], rows,
            # row_height was 0.6in/line (2026-09-22 fix) — real Directional
            # Suggestion text runs 2-3 wrapped lines, and at 0.6/line even 5
            # rows blew past _draw_table's available-height budget, so its
            # own (3,2,1) fallback collapsed every row to 1 line and
            # ellipsis-truncated the cell — the reference deck (a real
            # manual example) shows full, untruncated 3-line suggestions.
            # 0.35in/line leaves enough budget for genuine 3-line wraps.
            col_widths=[2.6, 3.6, 5.9], source=source, row_height=0.35, wrap_cols={0, 1, 2},
            insights=[summary] if summary else None,
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
# 2026-09-21 spec section 11 — show only the strongest validated
# opportunities, never every cluster that happens to clear the eligibility
# floor. Never manufactured up to this count when fewer qualify.
_PROGRAMMATIC_MAX_OPPORTUNITIES = 5
# Two sub-keywords whose token sets overlap this much are the same search
# intent wearing different phrasing (e.g. "certified payroll software" vs
# "certified payroll software tool") — templating them as two separate
# pages is exactly the thin/duplicate-content risk (SERP non-uniqueness)
# the eligibility flow asks to rule out before recommending page
# generation, not a second real sub-page.
_PROGRAMMATIC_DEDUP_OVERLAP = 0.6
# String-similarity floor for "this is a typo/near-identical spelling of
# that word," e.g. "certifed" vs "certified" (2026-09-16 user spec, Step 1)
# — high enough that genuinely different short words don't false-positive.
_PROGRAMMATIC_TYPO_RATIO = 0.82


def _keyword_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _fuzzy_token_match(token: str, other_tokens: set[str]) -> bool:
    return any(difflib.SequenceMatcher(None, token, t).ratio() >= _PROGRAMMATIC_TYPO_RATIO for t in other_tokens)


# §59 "Content Requirements" per programmatic dimension kind.
_PROGRAMMATIC_CONTENT_NEEDS = {
    "Audience": "audience-specific use cases, examples and pricing",
    "Geographic": "real local details (address, service area, local proof)",
    "Product / Attribute": "a specs/comparison table unique to that variant",
    "Problem": "the specific problem's causes and fix steps",
    "Commercial Investigation": "real evaluation criteria and proof for that option",
    "Temporal": "one evergreen page refreshed yearly, not a page per year",
}


def add_programmatic_seo_slide(prs: Presentation, keyword_rows: list[dict] | None):
    """Hub + sub-page content-architecture recommendations — matches the
    manual reference deck's "Programmatic SEO Opportunities" slide (one main
    hub page per topic, sub-pages beneath it targeting specific keywords).
    Built from the keyword clusters already identified for the Target
    Keywords slides, gated by real eligibility rules first: enough
    genuinely distinct sub-keywords to justify a template (not just 2 near-
    duplicate phrasings), and real demand behind the cluster as a whole.

    2026-09-21 spec rebuild: internal validation terminology
    (NEAR_DUPLICATE_PAIR, SUBPAGE_DUPLICATES_HUB, etc.) drove the exclusion
    logic below but must never reach the rendered PPT — this now returns a
    concise Opportunity/Search Demand/Structure/Recommended Subpages table
    plus a plain-language "why it qualifies" line per row, capped to the
    _PROGRAMMATIC_MAX_OPPORTUNITIES strongest opportunities and ranked by
    distinct validated sub-intents first, combined demand second — never
    search volume alone."""
    # Programmatic SEO spec 2026-09-23 section 13: the same validated-
    # relevant keyword set as Content SEO — never a routing bucket
    # (competitor-brand, careers, unconfirmed, out-of-market) or a keyword
    # excluded as irrelevant elsewhere in the report.
    keyword_rows = _content_seo_eligible_rows(keyword_rows or [])
    if not keyword_rows:
        return None
    clusters: dict[str, list[dict]] = {}
    for r in keyword_rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)
    if not clusters:
        return None

    candidates = []
    low_demand_count = 0
    too_few_subpages_count = 0
    for label, rows_for_cluster in clusters.items():
        cluster_volume = sum(_num(r.get("search_volume")) for r in rows_for_cluster)
        if cluster_volume < _PROGRAMMATIC_MIN_CLUSTER_VOLUME:
            low_demand_count += 1
            continue  # demand eligibility: not enough real search volume behind this topic to template

        hub_slug = _slugify(label)
        hub_tokens = _keyword_tokens(label)
        top_keywords = sorted(rows_for_cluster, key=lambda r: _num(r.get("search_volume")), reverse=True)
        sub_slugs: list[str] = []
        sub_keywords: list[str] = []
        sub_token_sets: list[set[str]] = []
        consolidated_any = False
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
            # A leftover token that's just a MISSPELLING of a hub-topic word
            # (e.g. "certifed" vs "certified") isn't a distinct sub-intent —
            # it's the hub topic with a typo, so it gets folded in (one-page
            # satisfaction), not turned into a second page.
            genuinely_new = {t for t in tokens if not _fuzzy_token_match(t, hub_tokens)}
            if not genuinely_new:
                if tokens:
                    consolidated_any = True
                continue  # nothing left but the cluster topic (or a misspelling of it) — same intent as the hub page
            # A keyword whose leftover tokens overlap an already-kept
            # sub-page's — either by exact jaccard overlap (word-order
            # variants) or fuzzy per-token match (tense/spelling variants,
            # e.g. "calculating" vs "calculate") — is the same underlying
            # intent wearing different phrasing, consolidated into the page
            # already kept rather than counted as a second sub-page.
            dup_of = None
            for seen_keyword, seen in zip(sub_keywords, sub_token_sets):
                jaccard = len(tokens & seen) / max(1, min(len(tokens), len(seen)))
                fuzzy_all = tokens and all(_fuzzy_token_match(t, seen) for t in tokens)
                if jaccard >= _PROGRAMMATIC_DEDUP_OVERLAP or fuzzy_all:
                    dup_of = seen_keyword
                    break
            if dup_of:
                consolidated_any = True
                continue
            sub_slugs.append(slug)
            sub_keywords.append(keyword)
            sub_token_sets.append(tokens)
            if len(sub_slugs) == 4:
                break
        if len(sub_slugs) < _PROGRAMMATIC_MIN_SUBPAGES:
            too_few_subpages_count += 1
            continue  # not enough genuinely distinct sub-pages to call this a template pattern

        # Existing-URL check (spec section 7) — the cluster's own upstream
        # existing-page decision: a strong/partial match means the hub
        # already exists and only the sub-pages are new.
        sample = rows_for_cluster[0]
        existing_hub = (
            normalize_source_url(sample.get("existing_page_url"))
            if sample.get("existing_page_match_strength") in ("strong", "partial") else None
        )
        # The template variable is what actually differs between the kept
        # sub-keywords (spec section 8) — never an invented dimension.
        modifiers = sorted({t for toks in sub_token_sets for t in toks})[:6]
        hub_text = f"existing hub page {existing_hub}" if existing_hub else "no existing hub page yet"
        # §59 programmatic output: pattern (Entity × dimension kind), page
        # uniqueness potential, thin-page risk and what each page needs.
        kinds = Counter(k for kw in sub_keywords for k in modifier_types(kw) if k not in ("Intent", "Informational"))
        dimension = kinds.most_common(1)[0][0] if kinds else "Variant"
        sub_volumes = [_num(r.get("search_volume")) for r in rows_for_cluster if r.get("keyword") in sub_keywords]
        avg_sub = sum(sub_volumes) / len(sub_volumes) if sub_volumes else 0
        risk = "low" if avg_sub >= 100 else "medium" if avg_sub >= 30 else "high"
        needs = _PROGRAMMATIC_CONTENT_NEEDS.get(dimension, "a genuinely different section per variant")
        why = (
            f"{label}: {len(sub_keywords)} distinct sub-intents varying by {', '.join(modifiers)} "
            f"(pattern: {label} × {dimension.lower()}), "
            f"{int(cluster_volume):,} combined monthly searches; {hub_text}; thin-page risk {risk}; each page needs {needs}"
            + ("; near-duplicate variants folded in." if consolidated_any else ".")
            + " SERP not validated — review before building."
        )
        candidates.append({
            "label": label, "volume": cluster_volume,
            "subpage_names": [kw.title() for kw in sub_keywords],
            "why": why, "existing_hub": existing_hub,
        })

    if not candidates:
        # Universal SEO Audit Engine spec (2026-09-20) section 27, restated
        # by the 2026-09-21 spec's own required wording: "Not suitable" is
        # an explicit, stated finding — every real cluster was actually
        # evaluated against the demand/distinct-sub-intent eligibility
        # gates above and failed, so this states why rather than silently
        # dropping the slide (which reads identically to "programmatic SEO
        # was never considered at all").
        reasons = []
        if low_demand_count:
            reasons.append(f"{low_demand_count} cluster(s) fell short of the minimum combined search volume")
        if too_few_subpages_count:
            reasons.append(f"{too_few_subpages_count} cluster(s) didn't have enough genuinely distinct sub-intents (near-duplicates only)")
        reason_text = "; ".join(reasons) if reasons else "no cluster had enough real search demand or distinct sub-intents"
        return _next_steps_category_slide(
            prs, "Programmatic SEO Opportunities",
            "No validated programmatic SEO opportunity identified from the analyzed keyword set.",
            [f"Evaluated {len(clusters)} keyword cluster(s) against hub+sub-page eligibility — {reason_text}."],
        )

    # Ranked by distinct validated sub-intents first, combined demand
    # second (2026-09-21 spec section 12 — never search volume alone), then
    # capped to the strongest few (section 11) rather than showing every
    # cluster that merely cleared the eligibility floor.
    candidates.sort(key=lambda c: (-len(c["subpage_names"]), -c["volume"]))
    top = candidates[:_PROGRAMMATIC_MAX_OPPORTUNITIES]

    def _demand_label(volume: float) -> str:
        return f"{volume / 1000:.1f}K/mo" if volume >= 1000 else f"{int(volume)}/mo"

    rows = [
        (
            c["label"], _demand_label(c["volume"]),
            f"{'Existing hub' if c['existing_hub'] else 'New hub'} + {len(c['subpage_names'])} subpages",
            " · ".join(c["subpage_names"]),
        )
        for c in top
    ]
    insights = [c["why"] for c in top]
    return _table_slide(
        prs, "Programmatic SEO Opportunities",
        ["Opportunity", "Search Demand", "Structure", "Recommended Subpages"], rows,
        col_widths=[2.5, 1.6, 1.8, 6.2], source="Semrush keyword clustering (validated clusters only)",
        insights=insights, wrap_cols={0, 3}, row_height=0.5,
    )


_GOALS_TIMEFRAME = "Next 6–12 months"


def _goals_report_period(analytics: dict | None) -> str | None:
    """The GA4/GSC report period as supplied, only when it's real ISO dates
    (never a relative placeholder like "30daysAgo" and never a template
    date)."""
    date_range = (analytics or {}).get("date_range") or {}
    start, end = date_range.get("start"), date_range.get("end")
    if not (start and end and re.match(r"^\d{4}-\d{2}-\d{2}$", str(start)) and re.match(r"^\d{4}-\d{2}-\d{2}$", str(end))):
        return None
    from datetime import datetime as _datetime

    try:
        s_dt = _datetime.strptime(str(start), "%Y-%m-%d")
        e_dt = _datetime.strptime(str(end), "%Y-%m-%d")
    except ValueError:
        return None
    return f"{s_dt:%d %b %Y} – {e_dt:%d %b %Y}"


def build_goals_kpis(
    own_domain_rating: int | None,
    competitor_rows: list[dict] | None,
    keyword_rows: list[dict] | None,
    analytics: dict | None = None,
    backlink_summary: dict | None = None,
    schema_validation: dict | None = None,
    site_audit_overview: dict | None = None,
) -> list[dict]:
    """SEO Goals & Targets rows (spec 2026-09-23): each one Metric ->
    Source -> Current -> Target -> Timeframe, every value read from the same
    dataset the matching report slide uses (so the baseline can never
    disagree with that slide). No business targets are supplied to this
    tool, so a numeric target appears only where the data itself defines
    one (a named set of keywords, a named competitor's DR); everything else
    is a stated direction, never an invented number. A KPI with no measured
    baseline is omitted — except conversions, which get an explicit
    measurement objective instead of an assumed rate."""
    kpis: list[dict] = []

    # Organic traffic — GA4 Organic Search channel sessions, same source as
    # the Traffic Sources slide.
    source_rows = ((analytics or {}).get("traffic_sources") or {}).get("rows") or []
    organic = [r for r in source_rows if (r.get("channel") or "").strip().lower() == "organic search"]
    if organic:
        sessions = int(sum(_num(r.get("sessions")) for r in organic))
        if sessions:
            kpis.append({
                "metric": "Organic sessions", "source": "GA4 (Organic Search channel)",
                "current": f"{sessions:,} sessions in the report period",
                "target": "Grow above the current baseline", "timeframe": _GOALS_TIMEFRAME,
            })

    # Rankings — validated-relevant keywords only (same eligibility as
    # Content SEO), positions from the supplied ranking data.
    rows = _content_seo_eligible_rows(keyword_rows or [])

    def _pos(r: dict) -> float:
        raw = r.get("current_position") if r.get("current_position") not in (None, "") else r.get("position")
        return _num(raw)

    ranked = [r for r in rows if _pos(r) > 0]
    if ranked:
        top10 = sum(1 for r in ranked if _pos(r) <= 10)
        low_kd_not_top10 = [
            r for r in rows
            if r.get("keyword_difficulty") not in (None, "") and _num(r.get("keyword_difficulty"), default=100) < 30
            and not (0 < _pos(r) <= 10)
        ]
        if low_kd_not_top10:
            target = f"Page 1 for the {len(low_kd_not_top10)} low-difficulty (KD < 30) target keyword(s) not yet there"
        else:
            target = "Grow the number of target keywords on page 1"
        kpis.append({
            "metric": "Target keywords on page 1", "source": "Semrush / Search Console rankings",
            "current": f"{top10} of {len(ranked)} ranking target keyword(s) in the top 10",
            "target": target, "timeframe": _GOALS_TIMEFRAME,
        })

    # Domain Rating — the manually entered DR table, same as the Backlink
    # Profile and Competitor Analysis slides. Competitor DR comes from that
    # same table, so the comparison is like for like. A target, never a
    # promised outcome of any number of backlinks.
    if own_domain_rating is not None:
        competitor_drs = [
            (_num(r.get("authority_score")), r.get("domain")) for r in (competitor_rows or [])
            if r.get("authority_score") not in (None, "") and r.get("domain")
        ]
        leader = max(competitor_drs, key=lambda t: t[0]) if competitor_drs else None
        if leader and leader[0] > own_domain_rating:
            target = f"Narrow the gap to {leader[1]} (DR {int(leader[0])})"
        else:
            target = "Maintain the lead over tracked competitors"
        kpis.append({
            "metric": "Domain Rating", "source": "Domain Rating (Ahrefs)",
            "current": f"DR {own_domain_rating}", "target": target, "timeframe": _GOALS_TIMEFRAME,
        })

    # Referring domains / backlinks — the Semrush backlink summary, same as
    # the Backlink Profile slide. Never the uploaded file's row count.
    if backlink_summary and backlink_summary.get("referring_domains"):
        rd = int(_num(backlink_summary.get("referring_domains")))
        bl = backlink_summary.get("backlinks_total")
        current = f"{rd:,} referring domains" + (f" / {int(_num(bl)):,} backlinks" if bl not in (None, "") else "")
        kpis.append({
            "metric": "Referring domains", "source": "Semrush Backlinks",
            "current": current, "target": "Grow relevant referring domains", "timeframe": _GOALS_TIMEFRAME,
        })

    # Structured data — the schema validator's own measured coverage.
    if schema_validation and schema_validation.get("total_pages"):
        total = int(_num(schema_validation.get("total_pages")))
        with_schema = int(_num(schema_validation.get("pages_with_schema")))
        missing_types = schema_validation.get("missing_types") or []
        target = (
            "Add the applicable schema types flagged in the Structured Data slide"
            if missing_types else "Keep schema valid as pages are added"
        )
        kpis.append({
            "metric": "Structured data coverage", "source": "Site Audit crawl (JSON-LD)",
            "current": f"{with_schema:,} of {total:,} crawled pages carry schema",
            "target": target, "timeframe": _GOALS_TIMEFRAME,
        })

    # Technical health — the Site Health slide's own figure.
    health = (site_audit_overview or {}).get("site_health_pct")
    if health not in (None, ""):
        kpis.append({
            "metric": "Site health", "source": "Semrush Site Audit",
            "current": f"{int(_num(health))}%", "target": "Raise by resolving the errors in SEO Issues",
            "timeframe": _GOALS_TIMEFRAME,
        })

    # Conversions — no key-event totals are supplied to this report, so no
    # baseline and no assumed rate: a measurement objective only.
    if kpis:
        kpis.append({
            "metric": "Organic conversions", "source": "GA4 key events",
            "current": "Not measured in this report",
            "target": "Confirm key-event tracking and set a conversion baseline", "timeframe": "Next reporting period",
        })
    return kpis


def add_goals_slide(
    prs: Presentation,
    own_domain_rating: int | None,
    competitor_rows: list[dict] | None,
    keyword_rows: list[dict] | None,
    analytics: dict | None = None,
    backlink_summary: dict | None = None,
    schema_validation: dict | None = None,
    site_audit_overview: dict | None = None,
):
    """SEO Goals & Targets as a Current -> Target -> Timeframe table built by
    build_goals_kpis. Deterministic on purpose: an AI-written version kept
    inventing baselines (an upload's row cap as a "backlink baseline"),
    assumed conversion rates, and stale template dates."""
    kpis = build_goals_kpis(
        own_domain_rating, competitor_rows, keyword_rows, analytics,
        backlink_summary, schema_validation, site_audit_overview,
    )
    if not kpis:
        return None
    slide = _blank_slide(prs)
    _content_header(slide, "SEO Goals & Targets")
    period = _goals_report_period(analytics)
    intro = f"Baselines from the report period {period}." if period else "Baselines are the latest measured values in this report."
    _textbox(slide, Inches(0.6), Inches(1.05), Inches(12.1), Inches(0.4), intro, size=12, color=TEXT_MUTED)
    rows = [(k["metric"], k["source"], k["current"], k["target"], k["timeframe"]) for k in kpis]
    _draw_table(
        slide, ["Metric", "Source", "Current", "Target", "Timeframe"], rows, Inches(1.55),
        col_widths=[1.9, 2.1, 2.9, 3.4, 1.8], row_height=0.55, wrap_cols={1, 2, 3, 4},
    )
    return slide


# Semrush Site Audit issue name -> (concept, specific action). Order matters:
# the more specific phrase is checked first ("duplicate meta description"
# before "meta description"). The concept key de-duplicates the same
# problem reported by two sources (Semrush's sitewide rollup and this
# tool's own page crawl) so it's never recommended twice.
_TECH_ISSUE_ACTIONS: list[tuple[tuple[str, ...], str, str]] = [
    (("broken internal link",), "internal_links", "fix or remove the broken internal links on the affected pages"),
    (("broken external link",), "external_links", "update or remove the broken outbound links"),
    (("4xx",), "4xx", "restore these URLs or 301-redirect them to the closest relevant live page, then update internal links that point to them"),
    (("5xx", "server error"), "5xx", "investigate the server errors on these URLs with the hosting/development team"),
    (("redirect chain", "redirect loop"), "redirects", "point internal links and redirects straight at the final destination URL"),
    (("duplicate title",), "duplicate_title", "write a unique title for each affected page"),
    (("duplicate meta description",), "duplicate_meta", "write a unique meta description for each affected page"),
    (("duplicate content",), "duplicate_content", "consolidate or differentiate the duplicate pages"),
    (("title",), "title", "add or rewrite the title on each affected page"),
    (("meta description",), "meta_description", "add a relevant meta description to each affected page"),
    (("hreflang",), "hreflang", "correct the hreflang annotations on the affected pages"),
    (("canonical",), "canonical", "correct the canonical tags on the affected pages"),
    (("sitemap",), "sitemap", "correct the sitemap so it lists only live, canonical, indexable URLs"),
    (("robots.txt",), "robots", "fix the robots.txt problem flagged by the audit"),
    (("mixed content", "https", "http page", "not secure"), "https", "serve every page and resource over HTTPS"),
    (("structured data", "schema", "markup"), "schema", "fix the invalid structured data on the affected pages"),
    (("h1",), "h1", "give each affected page one descriptive H1"),
    (("viewport",), "viewport", "add a mobile viewport meta tag"),
]


def _tech_issue_concept(issue: str) -> tuple[str | None, str | None]:
    text = (issue or "").lower()
    for phrases, concept, action in _TECH_ISSUE_ACTIONS:
        if any(ph in text for ph in phrases):
            return concept, action
    return None, None


# Tier for prioritisation (spec section 7): crawl/indexation first, then
# errors that break pages or links, then on-page, then enhancements.
_TECH_CONCEPT_TIER = {
    "https": 0, "robots": 0, "sitemap": 0, "5xx": 0, "4xx": 0, "canonical": 0, "hreflang": 0,
    "internal_links": 1, "redirects": 1, "duplicate_content": 1,
    "duplicate_title": 2, "title": 2, "duplicate_meta": 2, "meta_description": 2, "h1": 2, "viewport": 2,
    "external_links": 3, "schema": 3,
}


def build_technical_next_steps(
    site_audit: dict | None,
    page_audit: dict | None,
    tech_stack: dict | None = None,
    technical_fix_items: list[str] | None = None,
    site_audit_issues: list[dict] | None = None,
    site_audit_pages_rows: list[dict] | None = None,
    schema_validation: dict | None = None,
    psi_mobile: dict | None = None,
    max_items: int = 7,
) -> list[str]:
    """Technical SEO Next Steps (spec 2026-09-23): only confirmed findings,
    each as Issue — evidence/affected scope — specific action, at the scope
    the source actually supports (sitewide rollup, named pages, homepage
    only). Sources, each the same one its own report slide uses: Semrush
    Site Audit error rollup (Critical Issues), this tool's page crawl
    (Tech Fixes), the homepage check, the schema validator's per-page-type
    applicability, and PageSpeed's own metric statuses. No domain/TLD
    migration, no best-practice-only advice, no "every page" claims from a
    homepage-only check."""
    candidates: list[tuple[int, float, str, str]] = []  # (tier, -weight, concept, text)
    seen_concepts: set[str] = set()

    # Semrush sitewide error rollup — authoritative affected counts.
    if site_audit_issues:
        errors, _warnings = classify_seo_issues(site_audit_issues)
        total_crawled = (_canonical_page_totals(site_audit_pages_rows, None) or {}).get("total")
        for e in errors:
            concept, action = _tech_issue_concept(e["issue"])
            key = concept or f"semrush:{e['issue'].lower()}"
            if key in seen_concepts:
                continue
            seen_concepts.add(key)
            action = action or "resolve this error on the affected URLs listed in the Site Audit export"
            scope = _issue_count_label(int(e["pages"]), total_crawled)
            candidates.append((
                _TECH_CONCEPT_TIER.get(concept, 1), -float(e["pages"]), key,
                f"Resolve \"{e['issue']}\" — flagged by the site audit on {scope} — {action}.",
            ))

    # This tool's page crawl, already grouped per issue+fix with real URLs.
    for item in technical_fix_items or []:
        concept, _action = _tech_issue_concept(item.split(" — ")[0])
        key = concept or f"page:{item.split(' — ')[0].lower()}"
        if key in seen_concepts:
            continue
        seen_concepts.add(key)
        text = item if item.rstrip().endswith(".") else item.rstrip() + "."
        candidates.append((_TECH_CONCEPT_TIER.get(concept, 2), 0.0, key, text))

    # Homepage check — sitewide only for issues that are sitewide by nature.
    https_off = bool(tech_stack and tech_stack.get("https") is False)
    homepage_issues = list((site_audit or {}).get("issues") or [])
    if https_off and not any("https" in i.lower() for i in homepage_issues):
        homepage_issues.append("Site is not served over HTTPS")
    sitewide_text = {
        "https": "The site isn't fully served over HTTPS (site-audit check) — move every page and resource to HTTPS with 301 redirects from HTTP.",
        "robots": "robots.txt is missing or unreadable (site-audit check) — publish a valid robots.txt so crawlers get predictable access rules.",
        "sitemap": "No valid XML sitemap was found (site-audit check) — publish sitemap.xml listing live, canonical URLs and submit it in Search Console.",
    }
    for issue in homepage_issues:
        concept, action = _tech_issue_concept(issue)
        if not concept or concept in seen_concepts:
            continue
        seen_concepts.add(concept)
        if concept in sitewide_text:
            candidates.append((0, 0.0, concept, sitewide_text[concept]))
        else:
            candidates.append((2, 0.0, concept, f"Homepage: {issue.rstrip('.')} — {action}."))

    # Schema — only page types the validator confirms are applicable and
    # under-covered; never "add schema to all pages".
    if schema_validation and "schema" not in seen_concepts:
        gaps = [
            b for b in (schema_validation.get("by_page_type") or [])
            if b.get("applicable_schema") not in (None, "", "—") and b.get("pages")
            and (b.get("valid_pages") or 0) < b["pages"]
        ][:2]
        for b in gaps:
            missing = b["pages"] - (b.get("valid_pages") or 0)
            candidates.append((
                3, -float(missing), "schema",
                f"Add valid {b['applicable_schema']} schema on {b['page_type']} — {missing:,} of {b['pages']:,} pages "
                "lack valid markup per the Structured Data validator.",
            ))
        if gaps:
            seen_concepts.add("schema")

    # Performance — only metrics PageSpeed itself rates Poor on mobile.
    poor = [m for m in ((psi_mobile or {}).get("metric_table") or []) if m.get("status") == "Poor"]
    if poor:
        detail = ", ".join(f"{m['label']} {m.get('display_value') or m['value']}" for m in poor[:3])
        candidates.append((
            1, 0.0, "performance",
            f"Improve mobile performance — PageSpeed rates {detail} as poor on the homepage — "
            "work through the PageSpeed opportunities for those metrics.",
        ))

    candidates.sort(key=lambda c: (c[0], c[1]))
    return [c[3] for c in candidates[:max_items]]


def add_technical_seo_next_steps_slide(
    prs: Presentation,
    site_audit: dict | None,
    page_audit: dict | None,
    tech_stack: dict | None,
    domain_strategy: dict | None = None,
    technical_fix_items: list[str] | None = None,
    site_audit_issues: list[dict] | None = None,
    site_audit_pages_rows: list[dict] | None = None,
    schema_validation: dict | None = None,
    psi_mobile: dict | None = None,
):
    """domain_strategy is accepted for call compatibility but never used —
    a domain/TLD migration is out of scope for this report."""
    items = build_technical_next_steps(
        site_audit, page_audit, tech_stack, technical_fix_items,
        site_audit_issues, site_audit_pages_rows, schema_validation, psi_mobile,
    )
    intro = "Confirmed technical issues from the audit, highest crawl and indexation impact first."
    return _next_steps_category_slide(prs, "Next Steps: Technical SEO", intro, items)


# Keyword-cluster routing buckets (keyword_cluster_pipeline) that exist so
# a keyword is never silently deleted — NOT validated business topics, so
# never a Content SEO recommendation (Content SEO spec 2026-09-23, sections
# 1, 5, 8): unconfirmed relevance, competitor-brand searches, job searches,
# and out-of-market geography.
_CONTENT_SEO_EXCLUDED_CLUSTERS = {
    _NEEDS_REVIEW_CLUSTER_LABEL, _COMPETITOR_ROUTE_CLUSTER_LABEL,
    _CAREER_ROUTE_CLUSTER_LABEL, _GEO_ROUTE_CLUSTER_LABEL,
}
_CONTENT_SEO_EXCLUDED_RELEVANCE = {
    "Irrelevant", "Irrelevant Competitor Query", "Competitor Brand Search",
    "Career / Recruitment Query", "Unknown / Needs Review",
}


def _content_seo_eligible_rows(keyword_rows: list[dict]) -> list[dict]:
    """The validated-relevant subset of keyword_rows a Content SEO
    recommendation may use: never a routing bucket, never a row the
    relevance classifier excluded, flagged as competitor intent, or left
    unjudged. Rows with no relevance_status at all (the classifier never
    ran — e.g. a manual clustering upload) are kept: their relevance was
    never contested, and the cluster itself is still required below."""
    eligible = []
    for r in keyword_rows:
        if not r.get("keyword"):
            continue
        if (r.get("cluster") or "").strip() in _CONTENT_SEO_EXCLUDED_CLUSTERS:
            continue
        if r.get("competitor_status"):
            continue
        # The client's own keyword-cluster sheet is their source of truth
        # for what's relevant — never second-guessed here.
        if r.get("cluster_status") == "Validated (Manual)":
            eligible.append(r)
            continue
        status = r.get("relevance_status")
        # "Unknown / Needs Review" only excludes a row the classifier
        # actually judged and couldn't confirm — NOT one simply outside the
        # classifier's top-demand candidate pool (never judged at all).
        # Excluding those emptied Content SEO and Programmatic entirely on
        # a real BharatBenz regen (2026-09-23).
        if status == "Unknown / Needs Review" and r.get("relevance_reason") == _UNJUDGED_REASON:
            eligible.append(r)
            continue
        if status and (status in _CONTENT_SEO_EXCLUDED_RELEVANCE or status.lower().startswith("irrelevant")):
            continue
        eligible.append(r)
    return eligible


def _content_seo_cluster_evidence(rows: list[dict]) -> str | None:
    """Supplied evidence for one cluster, as client-facing text — search
    volume, best current ranking, and GSC impressions/clicks, only what the
    rows actually carry. None when the cluster has no evidence at all, which
    means no recommendation (spec section 6: "No action when evidence does
    not support an opportunity")."""
    volume = sum(_num(r.get("search_volume")) for r in rows)
    positions = []
    for r in rows:
        raw = r.get("current_position") if r.get("current_position") not in (None, "") else r.get("position")
        if _num(raw) > 0:
            positions.append(_num(raw))
    impressions = sum(_num(r.get("gsc_impressions")) for r in rows)
    clicks = sum(_num(r.get("gsc_clicks")) for r in rows)
    parts = []
    if volume:
        parts.append(f"{int(volume):,} combined monthly searches")
    if positions:
        parts.append(f"currently ranking #{int(min(positions))} at best")
    if impressions:
        parts.append(f"{int(impressions):,} Search Console impressions" + (f" / {int(clicks):,} clicks" if clicks else ""))
    return ", ".join(parts) or None


def add_content_seo_next_steps_slide(prs: Presentation, keyword_rows: list[dict] | None, keyword_strategy: dict | None = None):
    """Content SEO Next Steps (spec 2026-09-23). Every bullet is traceable:
    validated-relevant keywords only -> their real cluster -> the upstream
    existing-page decision (keyword_cluster_pipeline, which already refuses
    utility pages, page-type/intent mismatches, and URL-only word overlap)
    -> the evidence the rows carry. A cluster with no evidence, or a
    routing bucket, gets no bullet — and there is no generic filler, so the
    slide is skipped entirely when nothing survives."""
    items = []
    rows = _content_seo_eligible_rows(keyword_rows or [])
    if rows:
        # WHAT KIND of page each keyword calls for (format), separate from
        # WHAT TOPIC it belongs to (the cluster bullets below) — a client
        # can need both a landing page AND a guide for the same topic.
        category_counts: dict[str, int] = {}
        category_volume: dict[str, float] = {}
        category_examples: dict[str, list[str]] = {}
        for r in rows:
            keyword = r["keyword"]
            category = r.get("page_category") or _classify_keyword_page_category(keyword, r.get("intent"))
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
            if not vol or label not in category_action:
                continue
            example_text = ", ".join(f"\"{e}\"" for e in category_examples.get(label, []))
            items.append(
                f"{count} relevant keyword(s) call for {label.lower()} content, {int(vol):,} combined monthly searches "
                f"(e.g. {example_text}) — {category_action[label]}."
            )

        n_category = len(items)
        cluster_rows: dict[str, list[dict]] = {}
        for r in rows:
            label = (r.get("cluster") or "").strip()
            if label:
                cluster_rows.setdefault(label, []).append(r)

        # Exact URL decision per cluster (spec section 20/47: the renderer
        # must not make this decision itself — existing_page_action is
        # already computed upstream by keyword_cluster_pipeline, from the
        # SAME match-strength and cannibalization evidence the Target
        # Keywords slide uses, so the two slides can never disagree). With
        # no upstream decision, or no URL behind an existing-page action,
        # the only honest reading is a new-page opportunity — a URL is
        # never guessed here.
        def _cluster_volume(label: str) -> float:
            return sum(_num(r.get("search_volume")) for r in cluster_rows[label])

        # §66-K priority roadmap / §33: topics in High -> Medium -> Low ->
        # Human Review order by the §31 opportunity score, never by volume
        # alone (volume only breaks a tie for rows scored before the
        # strategy layer existed).
        _tier_order = {"High": 0, "Medium": 1, "Low": 2, "Human Review": 3}

        def _roadmap_key(label: str) -> tuple:
            sample = cluster_rows[label][0]
            return (_tier_order.get(sample.get("roadmap_priority"), 4),
                    -_num(sample.get("cluster_opportunity")), -_cluster_volume(label))

        for label in sorted(cluster_rows, key=_roadmap_key):
            crow = cluster_rows[label]
            evidence = _content_seo_cluster_evidence(crow)
            if not evidence:
                continue
            sample = crow[0]
            pipeline_action = sample.get("existing_page_action") or ""
            existing_url = normalize_source_url(sample.get("existing_page_url"))
            if existing_url and pipeline_action == "Optimize Existing Page":
                action = f"optimize the existing page ({existing_url}) for this topic rather than creating a new one"
            elif existing_url and pipeline_action == "Expand Existing Page":
                action = f"expand the existing page ({existing_url}), which already partly covers this topic"
            elif existing_url and pipeline_action.startswith("Consolidate"):
                action = f"make {existing_url} the single primary page for this topic"
            elif pipeline_action.startswith("Differentiate or Redirect"):
                action = pipeline_action[0].lower() + pipeline_action[1:]
            else:
                action = "create a new page — no existing page covers this topic closely enough"
            if sample.get("decision") and sample.get("decision_reason"):
                # §53 decision from the strategy layer (REDIRECT / SECONDARY
                # TARGET / NO TARGET...) replaces the plain match-strength text.
                action = f"{sample['decision'].lower()} — {sample['decision_reason'][0].lower()}{sample['decision_reason'][1:].rstrip('.')}"
                if sample.get("content_gap_type"):
                    action += f" ({sample['content_gap_type']})"
            tier = sample.get("roadmap_priority")
            tier_label = "Needs human review" if tier == "Human Review" else f"{tier} priority"
            tier_text = f"[{tier_label}, opportunity {sample.get('cluster_opportunity')}/100] " if tier else ""
            primary = next((r.get("keyword") for r in crow if r.get("primary_or_secondary") == "Primary"), None)
            primary_text = f" (primary keyword \"{primary}\")" if primary else ""
            items.append(f"{tier_text}\"{label}\" topic{primary_text} — {len(crow)} keyword(s), {evidence}; {action}.")

        # Cannibalization notes — already computed upstream per cluster
        # (cannibalization_status), one bullet per affected existing URL
        # (not one per cluster, so a 2-cluster conflict doesn't print the
        # same conflict twice from each cluster's own point of view).
        cannibal_notes_by_url: dict[str, str] = {}
        for label, rows_for_cluster in cluster_rows.items():
            sample = rows_for_cluster[0]
            note = sample.get("cannibalization_status")
            url = sample.get("existing_page_url")
            if note and url and url not in cannibal_notes_by_url:
                cannibal_notes_by_url[url] = note
        n_clusters = len(items)
        items.extend(cannibal_notes_by_url.values())
    else:
        n_category = n_clusters = len(items)
    strategy = keyword_strategy or {}
    extra: list[str] = []
    # §24/§58: real Search Console evidence of two own pages splitting one
    # query — preferred URL + the spec's action, highest risk first.
    for c in [c for c in strategy.get("cannibalization") or [] if c.get("risk") in ("High", "Medium")][:2]:
        extra.append(
            f"Cannibalization ({c['risk']} risk): \"{c['query']}\" is split across {len(c['other_urls']) + 1} pages — "
            f"{c['action'].lower()}, keeping {c['preferred_url']} as the preferred page. {c['evidence']}"
        )
    # §62: one line summarising what needs a person's decision.
    queue = strategy.get("review_queue") or []
    if queue and items:
        by_type = Counter(q["type"] for q in queue)
        extra.append(
            f"Human review queue: {len(queue)} item(s) — "
            + ", ".join(f"{n} {t.lower()}" for t, n in by_type.most_common(4))
            + " — full list in the keyword Sheet's Review Queue tab."
        )
    if extra:
        # The slide shows as many items as fit, so the order decides what a
        # reader sees: the biggest format line, the top two roadmap topics, then
        # the cannibalization evidence and the review-queue summary, then
        # everything else.
        category, clusters, notes = items[:n_category], items[n_category:n_clusters], items[n_clusters:]
        items = category[:1] + clusters[:2] + extra + clusters[2:] + category[1:] + notes
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


def _conversion_action_for_page(path: str) -> str:
    """Page-intent-matched next step (spec section 4) — never the same CTA
    forced onto every page type."""
    page_type = classify_page_type(path if "://" in path else f"https://x{path if path.startswith('/') else '/' + path}")
    if page_type == "blog":
        return "add a contextual next step from this article to the most relevant product or service page, and measure clicks on it"
    if page_type in ("commercial", "comparison"):
        return "review the enquiry/demo/quote call-to-action and form on this page and test one specific change to it"
    if page_type == "location":
        return "check the contact, directions, and call options on this page are prominent and tracked"
    if page_type == "home":
        return "review whether the primary call-to-action leads visitors clearly into the main product/service paths"
    return "review the call-to-action and next step on this page against its purpose"


def build_conversion_next_steps(
    conversion_evidence: dict | None,
    ux_findings: dict | None = None,
    max_items: int = 6,
) -> tuple[str, list[str]]:
    """Conversion SEO Next Steps (spec 2026-09-23): Evidence -> Opportunity
    -> Action, from GA4 key events/events and organic landing pages first,
    then any explicitly supplied UX walkthrough findings. Returns (intro,
    items). Never claims poor conversion without key-event data, never
    computes its own conversion rate (GA4's sessionKeyEventRate only),
    never invents functionality."""
    items: list[str] = []
    ev = conversion_evidence or {}
    events = ev.get("events") or []
    pages = ev.get("organic_landing_pages") or []
    key_configured = bool(ev.get("key_events_configured"))

    if key_configured:
        key_events = [e for e in events if e.get("key_events")]
        total = sum(e["key_events"] for e in key_events)
        names = ", ".join(e["name"] for e in key_events[:3])
        converting = [p for p in pages if p.get("sessions")]
        # Session-weighted mean of GA4's own per-page sessionKeyEventRate —
        # still converting-sessions / sessions, so like for like. Key-event
        # COUNT / sessions would not be (one session can fire several).
        total_sessions = sum(p["sessions"] for p in converting)
        site_rate = (
            sum((p.get("session_key_event_rate") or 0) * p["sessions"] for p in converting) / total_sessions
            if total_sessions else None
        )
        # High-traffic organic pages whose own GA4 session key-event rate is
        # well below the organic-landing-page average — a measured gap.
        for p in converting[:10]:
            rate = p.get("session_key_event_rate") or 0
            if site_rate and p["sessions"] >= 50 and rate < 0.5 * site_rate:
                items.append(
                    f"{p['path']} — {p['sessions']:,} organic sessions with a {rate * 100:.1f}% session key-event rate "
                    f"against {site_rate * 100:.1f}% across the top organic landing pages — "
                    f"{_conversion_action_for_page(p['path'])}."
                )
            if len(items) >= 3:
                break
        best = max(converting, key=lambda p: p.get("key_events") or 0, default=None)
        if best and best.get("key_events"):
            items.append(
                f"{best['path']} drives the most organic key events ({best['key_events']:,} from {best['sessions']:,} sessions) — "
                "keep its call-to-action and form unchanged while testing, and link to it from related high-traffic pages."
            )
        intro = f"Based on GA4 key events ({names}; {total:,} in the report period) and organic landing-page data."
    elif any(e.get("kind") == "conversion" for e in events):
        tracked = [e for e in events if e.get("kind") == "conversion"]
        listing = ", ".join(f"{e['name']} ({e['count']:,})" for e in tracked[:3])
        intro = "Completed conversions are tracked as GA4 events but not marked as key events, so they can't be tied to landing pages yet."
        items.append(
            f"Mark {listing} as GA4 key events so each organic landing page's conversion performance becomes measurable."
        )
    else:
        intro = "No GA4 key events are configured, so conversion performance can't be measured yet — these pages carry the organic traffic where a conversion path matters most."
        if pages:
            top = pages[:3]
            listing = ", ".join(f"{p['path']} ({p['sessions']:,} organic sessions)" for p in top)
            items.append(
                f"Mark the site's real enquiry, demo, quote, or purchase completions as GA4 key events — organic traffic lands on "
                f"{listing}, but no completed conversion is measured on any of them."
            )
            for p in top[:2]:
                items.append(
                    f"{p['path']} — {p['sessions']:,} organic sessions, conversion path not yet measured — "
                    f"{_conversion_action_for_page(p['path'])}."
                )

    # Path-signal drop-off: only when BOTH a start and a completion event
    # are actually measured (spec section 5).
    by_name = {e["name"].lower(): e for e in events}
    start = by_name.get("form_start")
    done = next((by_name[n] for n in ("form_submit", "generate_lead") if n in by_name), None)
    if start and done and start["count"] > 0 and done["count"] < 0.5 * start["count"]:
        items.append(
            f"Forms are started {start['count']:,} times but completed {done['count']:,} times ({done['name']}) — "
            "review the form's fields and error states for friction and measure completion after each change."
        )

    if ux_findings and not ux_findings.get("error") and not ux_findings.get("no_ux_pass_done"):
        items += [f"From the UX walkthrough: {c}" for c in (ux_findings.get("conversion_opportunities") or [])]

    return intro, items[:max_items]


def add_conversion_seo_next_steps_slide(
    prs: Presentation,
    ux_findings: dict | None,
    backlink_row_count: int = 0,
    conversion_evidence: dict | None = None,
):
    """backlink_row_count is accepted for call compatibility but unused —
    backlink volume isn't conversion evidence."""
    intro, items = build_conversion_next_steps(conversion_evidence, ux_findings)
    if not items:
        # Universal SEO Audit Engine spec (2026-09-20) section 39: state the
        # gap instead of assuming a conversion problem.
        return _next_steps_category_slide(
            prs, "Next Steps: Conversion SEO",
            "Tracking data required — no GA4 conversion data and no UX walkthrough available for this site yet.",
            ["Connect GA4 with the site's real enquiry, demo, quote, or purchase completions marked as key events, and run a "
             "UX walkthrough of the main conversion path, to unlock evidence-based conversion recommendations here."],
        )
    return _next_steps_category_slide(prs, "Next Steps: Conversion SEO", intro, items)


def add_aeo_geo_visibility_required_slide(prs: Presentation, uploaded_but_unavailable: bool = False):
    """AEO/GEO spec 2026-09-23: these recommendations come ONLY from an AI
    visibility-check report. Without one there is no evidence of what is
    missing in AI answers, so the gap is stated instead of generic
    AI-search advice (which the spec rules out)."""
    if uploaded_but_unavailable:
        intro = "An AI visibility-check file was uploaded, but it couldn't be analysed for this report run."
        items = ["Regenerate the report to retry the analysis of the uploaded AI visibility check; AEO and GEO recommendations are built only from its prompt, cluster, competitor, and citation results."]
    else:
        intro = "AI visibility check required — no AI visibility report has been uploaded for this site."
        items = ["Run an AI visibility check (branded and unbranded prompts across the site's core topics) and upload the export; AEO and GEO recommendations are built only from its prompt-level mentions, cluster visibility, competitor presence, and cited sources."]
    return _next_steps_category_slide(prs, "AEO & GEO — AI Visibility", intro, items)


def _shape_label(shape) -> str:
    if shape.has_text_frame:
        text = shape.text_frame.text.strip().replace("\n", " ")
        if text:
            return text[:40]
    return shape.shape_type


def _estimate_text_extent(shape):
    """Best-effort rendered (width, height) for a text box's actual content,
    since most title/label boxes here are declared far bigger than their
    real single-line content in BOTH dimensions (a wide title box for a
    two-word title; a tall hero-title box sized for wrapping that a short
    client name never uses) — using the full declared box size as
    "occupied" for overlap purposes made every title+source-label pair,
    and the hero title vs. domain line, look like false collisions. Falls
    back to the box's own width/height when they can't be safely measured
    (multi-line, empty, or not left-aligned) — never UNDER-estimates."""
    tf = shape.text_frame
    paras = [p for p in tf.paragraphs if p.text.strip()]
    if len(paras) != 1:
        return shape.width, shape.height
    p = paras[0]
    text = p.text
    size_pt = 14
    for run in p.runs:
        if run.font.size:
            size_pt = run.font.size.pt
            break
    # Single line's real height ≈ 1.3x font size (typical line-height
    # factor) — always safe to use regardless of alignment.
    est_height = min(Emu(int(Pt(size_pt) * 1.3)), shape.height)
    width = shape.width
    if p.alignment in (None, PP_ALIGN.LEFT):
        # ~0.55x font size per average proportional-font Latin character,
        # padded 25% to stay conservative — approximate, but enough to
        # tell "short title in a wide box" from genuinely occupied width.
        estimated_w = Emu(int(Pt(size_pt) * 0.55 * len(text) * 1.25))
        if estimated_w < shape.width:
            width = estimated_w  # else likely wraps onto multiple lines — box width is the safer bound
    return width, est_height


def _audit_slide_geometry(prs: Presentation, tolerance=Emu(18288)) -> list[str]:
    """Read-only pass over the finished deck: flags shapes that spill past
    the slide edges, and text boxes that visibly overlap each other. Every
    add_*_slide function is supposed to track its own max_y/x-cursor by
    hand, but with 58+ slide functions and ~5000 lines that bookkeeping has
    repeatedly drifted — content running off the bottom edge, two text
    boxes landing on the same row — and each time it's the client (or the
    user, reviewing the downloaded deck) who has to spot it and report it
    back, rather than it showing up anywhere before the report ships. This
    runs on every real build_report() call so a layout regression shows up
    in the server logs immediately instead of only in a downloaded file.
    Tolerance (~0.02in) absorbs float/EMU rounding, not real overflow.
    Overlap is checked only between actual text boxes (add_textbox shapes)
    — cards/rules/icons are AUTO_SHAPE backgrounds that text boxes are
    deliberately drawn on top of, so including them would flag every
    card+label pair as a false positive."""
    issues = []
    for slide_idx, slide in enumerate(prs.slides, 1):
        text_boxes = []
        for shape in slide.shapes:
            left, top, width, height = shape.left, shape.top, shape.width, shape.height
            if left is None or top is None or width is None or height is None:
                continue
            if (
                left < -tolerance
                or top < -tolerance
                or left + width > SLIDE_W + tolerance
                or top + height > SLIDE_H + tolerance
            ):
                issues.append(
                    f"slide {slide_idx}: '{_shape_label(shape)}' out of bounds "
                    f"(right={Emu(left + width).inches:.2f}in bottom={Emu(top + height).inches:.2f}in, "
                    f"page is {Emu(SLIDE_W).inches:.2f}x{Emu(SLIDE_H).inches:.2f}in)"
                )
            if shape.shape_type == MSO_SHAPE_TYPE.TEXT_BOX:
                eff_width, eff_height = _estimate_text_extent(shape)
                text_boxes.append((shape, left, top, eff_width, eff_height))
        for i in range(len(text_boxes)):
            s1, l1, t1, w1, h1 = text_boxes[i]
            for s2, l2, t2, w2, h2 in text_boxes[i + 1 :]:
                overlap_w = min(l1 + w1, l2 + w2) - max(l1, l2)
                overlap_h = min(t1 + h1, t2 + h2) - max(t1, t2)
                if overlap_w <= 0 or overlap_h <= 0:
                    continue
                smaller_area = min(w1 * h1, w2 * h2)
                if smaller_area and (overlap_w * overlap_h) / smaller_area > 0.25:
                    issues.append(
                        f"slide {slide_idx}: text boxes overlap: "
                        f"'{_shape_label(s1)}' <-> '{_shape_label(s2)}'"
                    )
    return issues


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
    keyword_sheet_link: str | None = None,
    seo_issues_ai_insights: dict | None = None,
    page_wise_ai: dict | None = None,
    page_wise_exclude_paths: set[str] | None = None,
    schema_ai_insights: dict | None = None,
    branded_vs_nonbranded_comparison: dict | None = None,
    branded_vs_nonbranded_narrative: dict | None = None,
    branded_vs_nonbranded_ai_insights: dict | None = None,
    high_potential_pages: list[dict] | None = None,
    high_potential_countries: list[dict] | None = None,
    competitor_top_opportunities: list[str] | None = None,
    content_issues: list[str] | None = None,
    strategic_keyword_clusters: list[dict] | None = None,
    keyword_strategy: dict | None = None,
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
            brand_citations, brand_wikipedia, geopulse_analysis, keyword_sheet_link,
            seo_issues_ai_insights, page_wise_ai, page_wise_exclude_paths,
            schema_ai_insights, branded_vs_nonbranded_comparison, branded_vs_nonbranded_narrative,
            branded_vs_nonbranded_ai_insights, high_potential_pages, high_potential_countries,
            competitor_top_opportunities=competitor_top_opportunities,
            content_issues=content_issues,
            strategic_keyword_clusters=strategic_keyword_clusters,
            keyword_strategy=keyword_strategy,
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
    keyword_sheet_link: str | None = None,
    seo_issues_ai_insights: dict | None = None,
    page_wise_ai: dict | None = None,
    page_wise_exclude_paths: set[str] | None = None,
    schema_ai_insights: dict | None = None,
    branded_vs_nonbranded_comparison: dict | None = None,
    branded_vs_nonbranded_narrative: dict | None = None,
    branded_vs_nonbranded_ai_insights: dict | None = None,
    high_potential_pages: list[dict] | None = None,
    high_potential_countries: list[dict] | None = None,
    competitor_top_opportunities: list[str] | None = None,
    content_issues: list[str] | None = None,
    strategic_keyword_clusters: list[dict] | None = None,
    keyword_strategy: dict | None = None,
) -> bytes:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    # Standing rule for every client (2026-09-18): the client's own ranking-
    # keyword export can legitimately contain a competitor's brand name in
    # the query text — that's a real ranking, never a real target — so it
    # must be stripped before it reaches ANY slide built off keyword_rows
    # (Target Keywords, Keyword Opportunity, Content SEO, Programmatic SEO,
    # Goals), not just the one slide that first surfaced the bug.
    if keyword_rows:
        keyword_rows = filter_other_brand_keywords(
            keyword_rows, website_url, [r.get("domain") for r in (competitor_rows or [])]
        )

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

    # Understanding Current Scenario section — locked to the 2026-09-18
    # canonical slide order (same deck every generation, no drift): Website
    # Performance (PageSpeed) -> Score Breakdown -> JS Bundle Breakdown ->
    # Script Weight Breakdown -> the "Understanding Current Scenario"
    # crawl/site-health card -> Website Structure -> SEO Issues (+ Critical
    # Issues, not in the canonical list but kept at its existing position
    # right after SEO Issues per 2026-09-18 user instruction) -> Priority
    # Issues - Page-wise -> Structured Data & Schema Validator -> Tech
    # Stack & Hosting -> UI-Level Fixes -> Onboarding Breakdown. Tech Stack
    # moved here (was previously rendering before SEO Issues) to match the
    # canonical position; site-health/Website Structure swapped to match it
    # too (previously Website Structure rendered first).
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

    if site_audit:
        add_seo_issues_slide(prs, site_audit, page_audit, site_audit_issues, site_audit_pages_rows, seo_issues_ai_insights, analytics)
        # Not in the canonical list — kept at its existing position (right
        # after SEO Issues) per 2026-09-18 user instruction: Semrush ERROR-
        # severity issues get their own standalone slide, distinct from SEO
        # Issues' capped Errors column.
        # Critical Issues slide cut 2026-09-18 per user request. Function
        # kept below for fast re-enable.
        # add_critical_issues_slide(prs, site_audit_issues, site_audit_pages_rows)
        # Priority Issues - Page Wise slide cut 2026-09-23 per user request
        # (repeats what SEO Issues + Next Steps: Technical SEO already say).
        # Function kept for fast re-enable.
        # add_tech_fixes_slide(prs, page_audit, analytics, site_audit_pages_rows, page_wise_ai, page_wise_exclude_paths)
        if schema_validation and schema_validation.get("total_pages"):
            add_schema_combined_slide(prs, schema_validation, schema_ai_insights)
        elif structured_data_rows:
            add_structured_data_slide(prs, structured_data_rows, site_audit_pages_rows)

    if tech_stack:
        add_tech_stack_slide(prs, tech_stack)

    if ux_findings:
        add_ux_findings_slides(prs, ux_findings)

    # Not in the 2026-09-18 canonical slide-order list — kept at its
    # existing position (right after Onboarding Breakdown, before the
    # Traffic & Search Performance divider) per 2026-09-18 user instruction.
    # Backlink Profile slide re-enabled 2026-09-17 — was pulled 2026-09-08
    # over a real backlink-total mismatch (10,000 vs 33,800 on Lumber): this
    # slide's own total fell back to `row_count`, the literal row count of
    # the uploaded Backlink List CSV, which is Semrush's export-tier row cap
    # on free/lower tiers — not the site's real total. Competitor Analysis
    # (add_competitor_table_slide) never had this bug: it reads
    # `backlinks_total` off a Domain Overview upload, a real site-wide
    # aggregate stat. competitor_rows[0] is that same own-domain Domain
    # Overview row when one was uploaded (see site_audit.py's
    # domain_overview_rows, sorted own-row-first) — matched by domain here,
    # not just assumed to be index 0, in case no own-site Domain Overview
    # was ever uploaded and competitor_rows[0] is a real competitor instead.
    # Backlink Profile slide cut 2026-09-18 per user request. Function kept
    # below for fast re-enable.
    # if backlink_rows or backlink_summary or own_domain_rating is not None:
    #     own_domain_overview_total = next(
    #         (
    #             r.get("backlinks_total") for r in (competitor_rows or [])
    #             if r.get("backlinks_total") is not None and _same_domain(r.get("domain"), website_url)
    #         ),
    #         None,
    #     )
    #     add_backlink_profile_slide(
    #         prs, backlink_rows or [], backlink_row_count, backlink_summary, own_domain_rating,
    #         domain_overview_backlinks_total=own_domain_overview_total,
    #     )

    # Brand Citation Opportunities slide cut again 2026-09-09 per user
    # request ("remove as of now, will suggest if needed") — disambiguation
    # bug fix from earlier today (brand_citation_service, same date) is
    # untouched and function kept below for fast re-enable.
    # add_brand_mentions_slide(prs, client_name, brand_citations, brand_wikipedia)

    traffic_divider_at: int | None = None
    if analytics:
        traffic_divider_at = len(prs.slides)
        add_section_slide(prs, client_name, "Traffic & Search Performance")
        ga4_span = _ga4_date_span(analytics.get("date_range"))
        gsc_span = _gsc_date_span(analytics.get("date_range"))
        channel_breakdown_span = _channel_breakdown_date_span(analytics.get("date_range"))
        ga4_source = f"Google Analytics ({ga4_span})" if ga4_span else "Google Analytics"
        gsc_source = f"Google Search Console ({gsc_span})" if gsc_span else "Google Search Console"
        channel_breakdown_source = (
            f"Google Analytics ({channel_breakdown_span}, monthly avg.)" if channel_breakdown_span else "Google Analytics (monthly avg.)"
        )
        if analytics.get("traffic_overview"):
            add_traffic_overview_slide(prs, analytics)
        # Top Pages — Branded vs Non-Branded slide removed 2026-09-09 per
        # user request — redundant with GSC's own query-level Branded/
        # Non-Branded split below (search_queries), which classifies real
        # search intent directly instead of inferring it from a page-path
        # signal list.
        #
        # Slide order (2026-09-11 user spec): Overview -> Sources -> Spike
        # -> Monthly Average, so the two same-window (30-day) slides sit
        # together right after the master total, before the two slides
        # that each use a DIFFERENT window (Spike's single day, Monthly
        # Average's ~4 months).
        sources = (analytics.get("traffic_sources") or {}).get("rows", [])
        if sources:
            # Traffic Overview is the master total (2026-09-11 user spec) —
            # Traffic Sources is a SPLIT of that same number, not its own
            # independently-summed total, so the two slides can never
            # silently disagree on total sessions. Falls back to summing
            # this query's own rows only if Traffic Overview has no data
            # (e.g. GA4 call failed) so the table still renders sane
            # percentages rather than all-zero.
            overview_rows = (analytics.get("traffic_overview") or {}).get("rows", [])
            master_total_sessions = sum(float(r.get("sessions", 0) or 0) for r in overview_rows)
            total_sessions = int(master_total_sessions) if master_total_sessions else sum(int(float(s.get("sessions", 0) or 0)) for s in sources)
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
        if analytics.get("traffic_spike"):
            add_traffic_spike_slide(prs, analytics["traffic_spike"])
        if analytics.get("traffic_channel_breakdown"):
            add_traffic_channel_breakdown_slide(prs, analytics["traffic_channel_breakdown"], source=channel_breakdown_source)
        # Branded vs Non-Branded (2026-09-10 user spec) — deliberately
        # non-overlapping text: comparison + Key Insights on
        # add_branded_vs_nonbranded_slide, the full high-potential-page/
        # country detail those insights reference on the two Search
        # Opportunities slides below (split into Pages/Countries,
        # 2026-09-11 user spec — was one combined slide). Comparison/
        # high-potential rows are computed upstream in site_audit.py
        # (build_branded_vs_nonbranded_comparison / build_high_potential_
        # pages / build_high_potential_countries) — this file only renders
        # them.
        if branded_vs_nonbranded_comparison:
            add_branded_vs_nonbranded_slide(
                prs, branded_vs_nonbranded_comparison, branded_vs_nonbranded_narrative, branded_vs_nonbranded_ai_insights, gsc_source,
            )
        if high_potential_pages:
            add_search_opportunities_pages_slide(prs, high_potential_pages, gsc_source)
        if high_potential_countries:
            add_search_opportunities_countries_slide(prs, high_potential_countries, gsc_source)

    _drop_divider_if_section_empty(prs, traffic_divider_at)

    research_divider_at: int | None = None
    if (competitor_rows or keyword_rows or backlink_rows or backlink_summary or competitor_positions
            or competitor_narratives or strategic_keyword_clusters):
        research_divider_at = len(prs.slides)
        add_section_slide(prs, client_name, "Competitor & Keyword Research")
        # Two scenarios, never both (2026-09-23 user instruction, replaces
        # the 2026-09-22 "both always coexist" rule): a manually uploaded
        # keyword-cluster file IS the Target Keywords section, built by its
        # own selection spec (strategic_keyword_selection_service). Only
        # when no manual file exists do the Semrush/GSC + AI-clustered
        # Target Keywords slides (incl. "Other / Ungrouped") render.
        if strategic_keyword_clusters:
            add_strategic_keyword_clusters_slide(prs, strategic_keyword_clusters)
        elif keyword_rows:
            add_keyword_research_slide(prs, keyword_rows)
        # §18/§37/§38 — one topic map for whichever path rendered above.
        add_keyword_topic_map_slide(prs, keyword_strategy)
        if competitor_rows:
            # 2026-09-20 user request: the "Open full keyword list" button
            # must appear only on Competitor Keyword Gap Analysis (see
            # add_keyword_gap_slide below), not here too — Competitor
            # Analysis no longer receives keyword_sheet_link.
            add_competitor_table_slide(prs, competitor_rows)
        if competitor_positions and not keyword_sheet_link:
            # Old per-competitor capped-table fallback — only when the
            # combined Sheet (client + all competitors, multiple tabs)
            # wasn't created (Sheets not connected, or creation failed),
            # same graceful-degradation discipline as before.
            # Competitor Keywords per-domain slides cut 2026-09-18 per user
            # request. Function kept below for fast re-enable.
            # add_competitor_positions_slides(prs, competitor_positions)
            pass
        if competitor_narratives:
            for domain, narrative in competitor_narratives.items():
                if "error" not in narrative:
                    add_competitor_best_at_slide(prs, domain, narrative)
                    add_competitor_opportunity_slide(prs, client_name, domain, narrative)
            # Cross-Competitor Opportunity Summary slide removed per
            # 2026-09-20 user request ("remove ... from here onward from
            # any of my report") — no longer rendered.
        if competitor_analysis and competitor_analysis.get("keyword_gap_rows"):
            add_keyword_gap_slides(
                prs, competitor_analysis, client_name=client_name,
                keyword_gap_sheet_link=keyword_sheet_link,
            )

    _drop_divider_if_section_empty(prs, research_divider_at)

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
    }

    def _next_steps_slide(key: str, fallback_fn, *fallback_args, extra_items: list[str] | None = None):
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
        if extra_items:
            items = list(items) + extra_items
        title = _ai_category_titles.get(key, key)
        return _next_steps_category_slide(prs, title, category.get("intro") or None, items)

    _next_steps_slide("local_seo", add_local_seo_next_steps_slide, prs, structured_data_rows)
    # Tech Fixes' technical-category rows merge into this slide instead of
    # their own standalone one (2026-09-16 user request) — passed both as a
    # fallback_fn arg (static-fallback path) and as extra_items (AI-
    # generated path), so the merge holds regardless of which one renders.
    technical_fix_items = _tech_fixes_next_steps_items(page_audit, analytics, site_audit_pages_rows)
    # Always deterministic, never the AI category — every bullet must trace
    # to a confirmed finding (Technical SEO spec 2026-09-23).
    add_technical_seo_next_steps_slide(
        prs, site_audit, page_audit, tech_stack, None, technical_fix_items,
        site_audit_issues, site_audit_pages_rows, schema_validation, psi_mobile,
    )
    # Always the deterministic keyword-page-category classifier below, never
    # the AI path — this needs an EXACT, guaranteed-consistent rule applied
    # every time (comparison-shaped vs. blog-shaped vs. landing-page-shaped
    # keywords), not an AI's variable phrasing of the same idea.
    add_content_seo_next_steps_slide(prs, keyword_rows, keyword_strategy)
    add_programmatic_seo_slide(prs, keyword_rows)
    # Always deterministic, never the AI category — Evidence -> Opportunity
    # -> Action from GA4 key events (Conversion SEO spec 2026-09-23).
    add_conversion_seo_next_steps_slide(
        prs, ux_findings, backlink_row_count, (analytics or {}).get("conversion_evidence"),
    )
    # GeoPulse-grounded content (the client's own AI-visibility tool export)
    # outranks both the generic next_steps_ai category AND the static
    # schema-only fallback below — it's the only source of these two slides
    # actually backed by real AI-search-visibility data rather than an LLM
    # guessing from site/competitor data alone.
    # AEO/GEO only from the uploaded AI visibility check (spec 2026-09-23);
    # one list may legitimately be empty (fewer recommendations rather than
    # padding). No check, or no usable result -> one stated-gap slide.
    aeo_items = (geopulse_analysis or {}).get("aeo_items") or []
    geo_items = (geopulse_analysis or {}).get("geo_items") or []
    if aeo_items:
        _next_steps_category_slide(prs, "Answer Engine Optimization (AEO)", "From the AI visibility check: prompt- and answer-level gaps.", aeo_items)
    if geo_items:
        _next_steps_category_slide(prs, "Generative Engine Optimization (GEO)", "From the AI visibility check: entity, discovery, competitor, and citation gaps.", geo_items)
    if not aeo_items and not geo_items:
        add_aeo_geo_visibility_required_slide(prs, bool((geopulse_analysis or {}).get("uploaded_but_unavailable")))
    # Always deterministic, never the AI category — see add_goals_slide.
    add_goals_slide(
        prs, own_domain_rating, competitor_rows, keyword_rows, analytics,
        backlink_summary, schema_validation, site_audit_overview,
    )

    geometry_issues = _audit_slide_geometry(prs)
    if geometry_issues:
        logger.warning(
            "pptx layout issues in generated report for %s (%d): %s",
            client_name, len(geometry_issues), "; ".join(geometry_issues),
        )
        # Surfaced into the same content_issues list every other content-
        # generation gap already reports through to the job record
        # (2026-09-17 fix) — logger.warning alone meant a layout regression
        # only ever showed up if someone happened to be reading server logs
        # at generation time; it never reached the user who actually
        # downloads and reviews the deck. Same list, same visibility as a
        # failed AI-insights call or a failed keyword-sheet creation.
        if content_issues is not None:
            content_issues.append(f"Slide layout ({len(geometry_issues)} issue(s)): {'; '.join(geometry_issues)}")

    # Hard rule (2026-09-23): no 18+ content in any report — last safety
    # net over the finished deck (content_safety.py, layer 3). Data was
    # already scrubbed upstream, so this normally removes nothing.
    removed = redact_presentation(prs)
    if removed:
        logger.warning("Adult-content safety net removed %d item(s) from the report for %s", removed, client_name)

    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()
