"""Builds a client-facing PPTX audit report styled after the Cyces deck format:
dark top bar, blue section-title band, light-gray body, white content cards."""

from __future__ import annotations

from io import BytesIO
from urllib.parse import urlparse

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt, Emu

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
# every slide-drawing function. Reset to the default after each build.
_theme = {"accent": DEFAULT_ACCENT, "footer": ""}


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
    high-level API for outer shadows."""
    from pptx.oxml.ns import qn

    sp_pr = shape._element.spPr
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


def add_title_slide(
    prs: Presentation, client_name: str, website_url: str = "", subtitle: str = "Web and SEO Audit",
    logo_bytes: bytes | None = None,
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

    _textbox(
        slide, Inches(1.5), Inches(5.15), Inches(10), Inches(0.4),
        date.today().strftime("%B %Y"), size=13, color=RGBColor(0xE0, 0xE0, 0xE0),
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


def _issue_row(slide, left, top, width, text, severity="warn"):
    color = BAD if severity == "error" else WARN
    dot = slide.shapes.add_shape(9, left, top + Inches(0.06), Inches(0.12), Inches(0.12))
    _fill(dot, color)
    dot.shadow.inherit = False
    _textbox(slide, left + Inches(0.25), top, width - Inches(0.25), Inches(0.35), text, size=13)


def add_site_health_slide(prs: Presentation, audit: dict, page_audit: dict | None):
    slide = _blank_slide(prs)
    _content_header(slide, "Understanding Current Scenario")

    pages_checked = page_audit.get("pages_checked") if page_audit else None
    pages_with_issues = page_audit.get("pages_with_issues") if page_audit else None
    healthy = (pages_checked - pages_with_issues) if pages_checked is not None and pages_with_issues is not None else None
    health_pct = round(100 * healthy / pages_checked) if pages_checked else None

    card1 = _card(slide, Inches(0.6), Inches(1.2), Inches(3.6), Inches(2.6))
    _textbox(slide, Inches(0.8), Inches(1.35), Inches(3), Inches(0.4), "Crawled Pages", size=15, bold=True)
    _textbox(slide, Inches(0.8), Inches(1.8), Inches(2.5), Inches(0.6), str(pages_checked if pages_checked is not None else "—"), size=32, bold=True, color=_accent())
    _icon_dot(slide, Inches(0.85), Inches(2.58), Inches(0.13), GOOD)
    _textbox(slide, Inches(1.08), Inches(2.5), Inches(3.0), Inches(0.35), f"Healthy: {healthy if healthy is not None else '—'}", size=13, color=TEXT_DARK)
    _icon_dot(slide, Inches(0.85), Inches(2.93), Inches(0.13), WARN)
    _textbox(slide, Inches(1.08), Inches(2.85), Inches(3.0), Inches(0.35), f"With issues: {pages_with_issues if pages_with_issues is not None else '—'}", size=13, color=TEXT_DARK)

    card2 = _card(slide, Inches(4.5), Inches(1.2), Inches(3.6), Inches(2.6))
    _textbox(slide, Inches(4.7), Inches(1.35), Inches(3), Inches(0.4), "Site Health", size=15, bold=True)
    color2 = GOOD if (health_pct or 0) >= 80 else (WARN if (health_pct or 0) >= 50 else BAD)
    _score_ring(slide, Inches(5.6), Inches(1.85), Inches(1.4), health_pct, "on-page checks")

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
        for k in kpis[:6]:
            _icon_dot(slide, Inches(0.6) + pad, y + Inches(0.07), Inches(0.08), _accent())
            _textbox(slide, Inches(0.6) + pad + Inches(0.18), y, left_width - pad * 2 - Inches(0.18), Inches(0.28), k, size=11.5)
            y += Inches(0.28)
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
            box = slide.shapes.add_textbox(Inches(1.1), y, Inches(11.1), Inches(0.5))
            tf = box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = f"•  {s}"
            run.font.size = Pt(12)
            run.font.color.rgb = TEXT_DARK
            y += Inches(0.32)
        y += Inches(0.15)

    if products_by_category:
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.35), "Products (by category)", size=15, bold=True, color=_accent())
        y += Inches(0.4)
        for category, items in list(products_by_category.items())[:6]:
            if y > Inches(6.6):
                break
            _textbox(slide, Inches(0.9), y, Inches(11.1), Inches(0.3), category, size=13, bold=True)
            y += Inches(0.32)
            box = slide.shapes.add_textbox(Inches(1.0), y, Inches(11.3), Inches(0.6))
            tf = box.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = ", ".join(items)
            run.font.size = Pt(11)
            run.font.color.rgb = TEXT_MUTED
            y += Inches(0.55)

    if industries and y < Inches(6.7):
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.35), "Industries", size=15, bold=True, color=_accent())
        y += Inches(0.4)
        _textbox(slide, Inches(0.9), y, Inches(11.3), Inches(0.5), ", ".join(industries[:12]), size=12)

    return slide


def add_tech_stack_slide(prs: Presentation, tech_stack: dict):
    """Detected CMS/framework/hosting/CDN/analytics — from response headers,
    DNS/PTR, and HTML markers. No credentials involved."""
    slide = _blank_slide(prs)
    _content_header(slide, "Tech Stack & Hosting")
    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))
    y = Inches(1.35)

    facts = [
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
    detected = tech_stack.get("detected") or []
    if detected:
        _textbox(slide, Inches(0.9), y, Inches(11.5), Inches(0.35), "Detected technologies", size=14, bold=True, color=_accent())
        y += Inches(0.42)
        by_category: dict[str, list[str]] = {}
        for item in detected:
            by_category.setdefault(item["category"], []).append(item["name"])
        for category, names in by_category.items():
            if y > Inches(6.5):
                break
            _textbox(slide, Inches(0.9), y, Inches(2.4), Inches(0.3), category.title(), size=12, bold=True)
            _textbox(slide, Inches(3.2), y, Inches(9.0), Inches(0.3), ", ".join(names), size=12, color=TEXT_DARK)
            y += Inches(0.36)
    return slide


def add_seo_issues_slide(prs: Presentation, audit: dict, page_audit: dict | None):
    slide = _blank_slide(prs)
    _content_header(slide, "SEO Issues")

    issues = list(audit.get("issues", []))
    errors = [i for i in issues if any(k in i.lower() for k in ["not reachable", "https", "robots", "sitemap"])]
    warnings = [i for i in issues if i not in errors]

    card = _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))
    y = Inches(1.3)
    if not issues:
        _textbox(slide, Inches(0.9), y, Inches(10), Inches(0.4), "No issues found on the homepage checks.", size=14, color=GOOD)
        return slide

    for group_label, group_issues, color in [("Errors", errors, BAD), ("Warnings", warnings, WARN)]:
        if not group_issues:
            continue
        _textbox(slide, Inches(0.9), y, Inches(4), Inches(0.35), f"{group_label} ({len(group_issues)})", size=14, bold=True, color=color)
        rule = slide.shapes.add_shape(1, Inches(0.9), y + Inches(0.36), Inches(11.3), Pt(1.5))
        _fill(rule, color)
        rule.shadow.inherit = False
        y += Inches(0.55)
        for issue in group_issues[:10]:
            _issue_row(slide, Inches(0.9), y, Inches(11.3), issue, severity=("error" if color == BAD else "warn"))
            y += Inches(0.38)
        y += Inches(0.15)
    return slide


def _insights_strip(slide, left, top, width, insights, title="Key Insights"):
    """2-4 bullet takeaways mechanically derived from the slide's own data —
    no free-text generation, every line traces back to a number on the same
    slide. Returns the bottom y (Emu) after the strip."""
    if not insights:
        return top
    _textbox(slide, left, top, width, Inches(0.24), title.upper(), size=9.5, bold=True, color=_accent())
    y = top + Inches(0.26)
    # Width-aware wrap estimate (~14 chars/inch at size 11) — a fixed
    # chars-per-line regardless of column width caused text in narrow
    # columns (e.g. the PageSpeed sidebar) to under-reserve height and
    # overlap the next bullet.
    text_width_in = max(width - Inches(0.18), Inches(0.5)) / 914400
    chars_per_line = max(20, int(text_width_in * 14))
    for item in insights[:4]:
        _icon_dot(slide, left, y + Inches(0.07), Inches(0.08), _accent())
        lines = max(1, -(-len(item) // chars_per_line))
        line_h = Inches(0.22)
        _textbox(slide, left + Inches(0.18), y, width - Inches(0.18), line_h * lines, item, size=11)
        y += line_h * lines + Inches(0.05)
    return y


def _table_slide(prs, title, headers, rows, col_widths=None, source=None, insights=None):
    slide = _blank_slide(prs)
    _content_header(slide, title)
    if source:
        _textbox(slide, Inches(9.5), Inches(0.3), Inches(3.3), Inches(0.4), f"Source: {source}", size=11, color=TEXT_MUTED)

    row_cap = 9 if insights else 14
    n_cols = len(headers)
    n_rows = min(len(rows), row_cap) + 1
    left, top, width, height = Inches(0.6), Inches(1.2), Inches(12.1), Inches(0.4) * n_rows
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
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(11)
            para.font.color.rgb = TEXT_DARK

    if insights:
        _insights_strip(slide, left, top + height + Inches(0.15), width, insights)
    return slide


def add_traffic_overview_slide(prs: Presentation, analytics: dict):
    slide = _blank_slide(prs)
    _content_header(slide, "Traffic Overview")
    _textbox(slide, Inches(9.5), Inches(0.3), Inches(3.3), Inches(0.4), "Source: Google Analytics 4", size=11, color=TEXT_MUTED)

    traffic = (analytics.get("traffic_overview") or {}).get("rows", [])
    if not traffic:
        _textbox(slide, Inches(0.6), Inches(1.4), Inches(8), Inches(0.5), "No GA4 data available for this period.", size=14, color=TEXT_MUTED)
        return slide

    def total(key):
        return sum(float(r.get(key, 0) or 0) for r in traffic)

    sessions = total("sessions")
    users = total("total_users")
    pageviews = total("page_views")
    avg_engagement = (sum(float(r.get("engagement_rate", 0) or 0) for r in traffic) / len(traffic)) * 100
    avg_bounce = (sum(float(r.get("bounce_rate", 0) or 0) for r in traffic) / len(traffic)) * 100

    metrics = [
        ("Sessions", f"{sessions:,.0f}"),
        ("Users", f"{users:,.0f}"),
        ("Page views", f"{pageviews:,.0f}"),
        ("Avg. engagement", f"{avg_engagement:.1f}%"),
        ("Bounce rate", f"{avg_bounce:.1f}%"),
    ]
    card_width = Inches(2.28)
    gap = Inches(0.15)
    for i, (label, value) in enumerate(metrics):
        left = Inches(0.6) + Emu(i * (card_width + gap))
        _card(slide, left, Inches(1.3), card_width, Inches(1.6))
        _textbox(slide, left + Inches(0.15), Inches(1.45), card_width - Inches(0.3), Inches(0.4), label, size=12, color=TEXT_MUTED)
        _textbox(slide, left + Inches(0.15), Inches(1.85), card_width - Inches(0.3), Inches(0.7), value, size=22, bold=True, color=_accent())

    pages_per_session = pageviews / sessions if sessions else 0
    insights = []
    if avg_bounce >= 55:
        insights.append(f"Bounce rate is {avg_bounce:.0f}% — high; visitors are leaving without exploring, check landing-page relevance and load speed.")
    elif avg_bounce <= 35:
        insights.append(f"Bounce rate is {avg_bounce:.0f}% — strong, visitors are engaging well past the landing page.")
    else:
        insights.append(f"Bounce rate is {avg_bounce:.0f}% — typical range, room to improve landing-page engagement.")
    insights.append(f"Visitors view {pages_per_session:.1f} pages per session on average.")
    if users and sessions:
        sessions_per_user = sessions / users
        if sessions_per_user > 1.3:
            insights.append(f"{sessions_per_user:.1f} sessions per user — a meaningful share of visitors are returning, not just first-time traffic.")
    _insights_strip(slide, Inches(0.6), Inches(3.15), Inches(11.9), insights)
    return slide


def add_page_performance_slide(prs: Presentation, page_performance: dict):
    """Top vs. poor performing pages, side by side, with each page's % share
    of total pageviews and how many pages contributed traffic in the period."""
    top_pages = page_performance.get("top_pages") or []
    bottom_pages = page_performance.get("bottom_pages") or []
    if not top_pages and not bottom_pages:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Top vs. Poor Performing Pages")
    _textbox(slide, Inches(9.5), Inches(0.3), Inches(3.3), Inches(0.4), "Source: Google Analytics 4", size=11, color=TEXT_MUTED)

    total_pages = page_performance.get("total_pages", 0)
    total_views = page_performance.get("total_page_views", 0)
    summary = f"{total_pages} pages contributed {total_views:,} pageviews in this period."
    if page_performance.get("truncated"):
        summary += " (based on the top pages GA4 returned)"
    _textbox(slide, Inches(0.6), Inches(0.9), Inches(12.1), Inches(0.3), summary, size=11, color=TEXT_MUTED)

    def _mini_table(left, title, rows, color):
        _textbox(slide, left, Inches(1.35), Inches(5.9), Inches(0.35), title, size=14, bold=True, color=color)
        n_rows = min(len(rows), 8) + 1
        gframe = slide.shapes.add_table(n_rows, 3, left, Inches(1.75), Inches(5.9), Inches(0.4) * n_rows)
        table = gframe.table
        table.first_row = False
        table.columns[0].width = Inches(3.5)
        table.columns[1].width = Inches(1.3)
        table.columns[2].width = Inches(1.1)
        for j, h in enumerate(["Page", "Views", "% of total"]):
            cell = table.cell(0, j)
            cell.text = h
            cell.fill.solid()
            cell.fill.fore_color.rgb = HEADER_ROW_BG
            para = cell.text_frame.paragraphs[0]
            para.font.size = Pt(10)
            para.font.bold = True
            para.font.color.rgb = HEADER_ROW_TEXT
        for i, r in enumerate(rows[:8], start=1):
            values = [r["path"], f"{r['page_views']:,}", f"{r['pct_of_total']:.1f}%"]
            for j, val in enumerate(values):
                cell = table.cell(i, j)
                cell.text = str(val)
                cell.fill.solid()
                cell.fill.fore_color.rgb = ROW_ALT if i % 2 == 0 else WHITE
                para = cell.text_frame.paragraphs[0]
                para.font.size = Pt(10)
                para.font.color.rgb = TEXT_DARK

    _mini_table(Inches(0.6), "Top performing", top_pages, GOOD)
    _mini_table(Inches(6.8), "Poor performing", bottom_pages, BAD)
    return slide


def add_competitor_table_slide(prs: Presentation, competitor_rows: list[dict]):
    """Matches the reference deck's Competitor Analysis table. Renders the
    full DR/Backlinks/Top Countries/Branded-split columns when the rows come
    from Domain Overview uploads; falls back to a slimmer traffic/keywords/
    common-keywords table when only an Organic Competitors export is available
    (that file type has no DR, backlinks, or branded-split data at all)."""
    has_rich_data = any(r.get("authority_score") or r.get("backlinks_total") for r in competitor_rows)

    if has_rich_data:
        headers = ["Domain", "Organic Traffic", "Organic Keywords", "Paid Traffic", "DR", "Backlinks", "Top Countries", "Branded", "Non-Branded"]
        col_widths = [2.6, 1.3, 1.3, 1.2, 0.9, 1.2, 1.3, 1.1, 1.2]
        rows = [
            (
                r.get("domain", ""),
                r.get("organic_traffic", ""),
                r.get("organic_keywords", ""),
                r.get("paid_traffic", ""),
                r.get("authority_score", ""),
                r.get("backlinks_total", ""),
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
                r.get("organic_traffic", ""),
                r.get("organic_keywords", ""),
                r.get("common_keywords", ""),
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

    return _table_slide(prs, "Competitor Analysis", headers, rows, col_widths=col_widths, source="Semrush export", insights=insights)


def add_competitor_positions_slides(prs: Presentation, competitor_positions: dict[str, list[dict]]):
    """One table slide per competitor domain from a Semrush Organic Research
    > Positions export — what that domain ranks for, and at what position.
    Matches the "Competitor Keywords: {domain}" slide format."""
    slides = []
    for domain, rows in competitor_positions.items():
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


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def add_competitive_gaps_slide(prs: Presentation, competitor_analysis: dict):
    """Renders the own-vs-competitor comparison from semrush_analysis_service
    — concrete gaps (traffic, keywords, backlinks) with what to do about each."""
    issues = competitor_analysis.get("issues") or []
    if not issues:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, "Competitive Gaps & Opportunities")
    _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.9))
    y = Inches(1.35)

    severity_color = {"warn": BAD, "opportunity": GOOD, "info": WARN}
    severity_label = {"warn": "BEHIND", "opportunity": "OPPORTUNITY", "info": "MISSING DATA"}

    for issue in issues[:8]:
        if y > Inches(6.6):
            break
        color = severity_color.get(issue["severity"], WARN)
        _icon_dot(slide, Inches(0.9), y + Inches(0.09), Inches(0.13), color)
        _textbox(slide, Inches(1.15), y, Inches(2.0), Inches(0.3), severity_label.get(issue["severity"], ""), size=10, bold=True, color=color)
        _textbox(slide, Inches(0.9), y + Inches(0.28), Inches(11.4), Inches(0.35), issue["summary"], size=13, bold=True)
        y += Inches(0.62)
        _textbox(slide, Inches(0.9), y, Inches(11.4), Inches(0.35), issue["detail"], size=11.5, color=TEXT_MUTED)
        y += Inches(0.32)
        do_box = slide.shapes.add_textbox(Inches(0.9), y, Inches(11.4), Inches(0.5))
        tf = do_box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        r1 = p.add_run()
        r1.text = "Do: "
        r1.font.size = Pt(11.5)
        r1.font.bold = True
        r1.font.color.rgb = _accent()
        r2 = p.add_run()
        r2.text = issue["recommendation"]
        r2.font.size = Pt(11.5)
        r2.font.color.rgb = TEXT_DARK
        y += Inches(0.5)
    return slide


def add_competitor_narrative_slide(prs: Presentation, client_name: str, competitor_domain: str, narrative: dict):
    """Matches the reference deck's "Areas of Focus for {Client} (vs
    {Competitor})" slide — a bulleted recommendation list followed by a
    closing "Strategic Growth Opportunity" paragraph. Always rendered in
    Cyces' own brand red (not the client's brand color) — this is
    agency-authored strategic content, not the client-branded data slides.
    Height-budgeted so long AI-generated bullets can't overflow into the
    footer: bullets stop once the budget is spent, and the closing
    paragraph is truncated with an ellipsis rather than overflowing."""
    areas = narrative.get("areas_of_focus") or []
    opportunity = narrative.get("growth_opportunity")
    if not areas and not opportunity:
        return None

    slide = _blank_slide(prs)
    _content_header(slide, f"Areas of Focus for {client_name} (vs {competitor_domain})")
    card_top, card_height = Inches(1.1), Inches(5.9)
    _card(slide, Inches(0.6), card_top, Inches(12.1), card_height)
    y = Inches(1.35)

    # Reserve room for the heading + at least 2 lines of the closing
    # paragraph before bullets are allowed to eat into that space.
    bullets_max_y = card_top + card_height - (Inches(0.7) if opportunity else Inches(0.15))
    chars_per_line = 120
    line_h = Inches(0.24)
    for item in areas[:9]:
        lines = max(1, -(-len(item) // chars_per_line))
        item_h = line_h * lines + Inches(0.08)
        if y + item_h > bullets_max_y:
            break
        _icon_dot(slide, Inches(0.9), y + Inches(0.08), Inches(0.09), DEFAULT_ACCENT)
        _textbox(slide, Inches(1.15), y, Inches(11.35), line_h * lines, item, size=12.5)
        y += item_h

    if opportunity:
        y += Inches(0.15)
        _textbox(slide, Inches(0.9), y, Inches(11.4), Inches(0.3), "Strategic Growth Opportunity:", size=13, bold=True, color=DEFAULT_ACCENT)
        y += Inches(0.34)
        chars_per_line = 130
        available_h = (card_top + card_height) - y - Inches(0.1)
        max_lines = max(1, int(available_h / Inches(0.24)))
        max_chars = max_lines * chars_per_line
        text = opportunity if len(opportunity) <= max_chars else opportunity[: max(0, max_chars - 1)].rsplit(" ", 1)[0] + "…"
        lines = max(1, -(-len(text) // chars_per_line))
        _textbox(slide, Inches(0.9), y, Inches(11.4), Inches(0.24) * lines, text, size=12, color=TEXT_DARK)
    return slide


def add_keyword_research_slide(prs: Presentation, keyword_rows: list[dict], max_clusters: int = 10):
    """One table slide per keyword cluster (Educational Toys, Development
    Skills, etc.), matching the reference deck's "Target Keywords" format —
    instead of one flat 14-row table that drowns thousands of uploaded rows
    into a single slide. Falls back to a flat table when no Cluster column
    was present in the uploaded export."""
    headers = ["Keyword", "Search Volume", "Keyword Difficulty"]
    col_widths = [7.0, 2.6, 2.5]

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
        if kds:
            out.append(f"Avg. keyword difficulty {sum(kds) / len(kds):.0f} — {'competitive cluster, prioritize content depth over volume' if sum(kds) / len(kds) > 40 else 'low-competition cluster, faster to rank in'}.")
        if easy_wins:
            out.append(f"{len(easy_wins)} low-difficulty (KD<20) keyword(s) with real search volume — quick-win content targets.")
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
        rows = [(r.get("keyword", ""), r.get("search_volume", ""), r.get("keyword_difficulty", "")) for r in deduped]
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
        rows = [(r.get("keyword", ""), r.get("search_volume", ""), r.get("keyword_difficulty", "")) for r in deduped]
        title = f"Target Keywords: {label}" if label else "Target Keywords"
        insights = _keyword_insights(deduped) if deduped else []
        slides.append(_table_slide(prs, title, headers, rows, col_widths=col_widths, source="Semrush export", insights=insights))
    return slides


def add_backlink_profile_slide(prs: Presentation, backlink_rows: list[dict], row_count: int):
    """Ahrefs/Semrush-widget-style summary — Backlinks, Referring Domains,
    Avg. Domain Score, % dofollow — computed from the uploaded backlink rows,
    no separate API call needed."""
    slide = _blank_slide(prs)
    _content_header(slide, "Backlink Profile")
    _card(slide, Inches(0.6), Inches(1.2), Inches(12.1), Inches(2.4))

    dofollow = sum(1 for r in backlink_rows if str(r.get("nofollow", "")).strip().lower() not in ("1", "true", "yes"))
    pct_dofollow = round(100 * dofollow / len(backlink_rows)) if backlink_rows else None

    ref_domains = {urlparse(r["source_url"]).netloc for r in backlink_rows if r.get("source_url")}
    _MISSING = object()
    domain_scores = [
        v for v in (_num(r.get("domain_score"), default=_MISSING) for r in backlink_rows) if v is not _MISSING
    ]
    avg_score = round(sum(domain_scores) / len(domain_scores)) if domain_scores else None

    stats = [
        ("Backlinks", f"{row_count:,}", f"{pct_dofollow}% dofollow" if pct_dofollow is not None else None),
        ("Referring domains", f"{len(ref_domains):,}", None),
        ("Avg. domain score", str(avg_score) if avg_score is not None else "—", None),
    ]
    for i, (label, value, sub) in enumerate(stats):
        left = Inches(0.9) + Emu(i * Inches(4.0))
        _textbox(slide, left, Inches(1.4), Inches(3.6), Inches(0.35), label, size=13, color=TEXT_MUTED)
        _textbox(slide, left, Inches(1.8), Inches(3.6), Inches(0.8), value, size=34, bold=True, color=_accent())
        if sub:
            _textbox(slide, left, Inches(2.65), Inches(3.6), Inches(0.3), sub, size=12, color=TEXT_MUTED)

    insights = []
    if row_count and ref_domains:
        links_per_domain = row_count / len(ref_domains)
        if links_per_domain > 5:
            insights.append(f"{links_per_domain:.1f} backlinks per referring domain — link profile is concentrated in a few sources, worth diversifying.")
        else:
            insights.append(f"{len(ref_domains)} distinct referring domains behind {row_count} backlinks — reasonably diverse source spread.")
    if pct_dofollow is not None:
        if pct_dofollow < 50:
            insights.append(f"Only {pct_dofollow}% of backlinks are dofollow — most links here aren't passing ranking authority.")
        else:
            insights.append(f"{pct_dofollow}% of backlinks are dofollow — the majority are passing ranking authority.")
    if avg_score is not None:
        insights.append(f"Average domain score of referring sites is {avg_score} — {'strong-authority sources' if avg_score >= 40 else 'low-authority sources, prioritize outreach to higher-DR sites'}.")
    _insights_strip(slide, Inches(0.6), Inches(3.85), Inches(11.9), insights)
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


def add_next_steps_detail_slide(prs: Presentation, site_audit: dict | None, page_audit: dict | None):
    slide = _blank_slide(prs)
    _content_header(slide, "Recommended Next Steps")

    steps = _derive_next_steps(site_audit, page_audit)
    card = _card(slide, Inches(0.6), Inches(1.1), Inches(12.1), Inches(5.6))
    if not steps:
        _textbox(slide, Inches(0.9), Inches(1.3), Inches(10), Inches(0.4), "No outstanding technical issues found.", size=14, color=GOOD)
        return slide
    for i, step in enumerate(steps[:12]):
        y = Inches(1.3) + Emu(i * Inches(0.42))
        num = slide.shapes.add_textbox(Inches(0.9), y, Inches(0.4), Inches(0.35))
        p = num.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = f"{i+1}."
        r.font.bold = True
        r.font.size = Pt(13)
        r.font.color.rgb = _accent()
        _textbox(slide, Inches(1.3), y, Inches(11.2), Inches(0.4), step, size=13)
    return slide


def add_aeo_geo_slide(prs: Presentation, site_audit: dict | None, page_audit: dict | None):
    """AEO (Answer Engine Optimization) / GEO (Generative Engine Optimization)
    Opportunities — matches the reference deck's dedicated AEO & GEO slide.
    Left column reacts to real schema/structured-data findings when present;
    right column is standard GEO practice, since AI-citation entity-building
    isn't something a crawl can measure directly."""
    slide = _blank_slide(prs)
    _content_header(slide, "AEO & GEO Opportunities")

    col_width = Inches(5.75)
    gap = Inches(0.3)
    left_x = Inches(0.6)
    right_x = left_x + col_width + gap
    top = Inches(1.1)
    height = Inches(5.6)
    _card(slide, left_x, top, col_width, height)
    _card(slide, right_x, top, col_width, height)

    schema_present = None
    if site_audit and site_audit.get("meta"):
        schema_present = bool(site_audit["meta"].get("structured_data_present"))
    missing_schema_pages = None
    if page_audit and page_audit.get("pages_with_issues"):
        missing_schema_pages = page_audit.get("pages_with_issues")

    aeo_items = [
        "Add structured FAQ sections to every key page, answering the questions customers actually ask before buying.",
    ]
    if schema_present is False:
        aeo_items.append("Homepage has no schema.org (JSON-LD) markup — add Organization, Product, and FAQ schema so AI Overviews and rich results can parse the page.")
    elif missing_schema_pages:
        aeo_items.append(f"{missing_schema_pages} crawled page(s) are missing schema markup that other pages already have — bring them in line.")
    else:
        aeo_items.append("Implement Organization, Product/Service, FAQ, and Breadcrumb schema site-wide for AI Overview eligibility.")
    aeo_items += [
        "Write concise, extractable answer blocks (2-3 sentences) near the top of key pages — this is what LLMs quote directly.",
        "Build a dedicated FAQ hub covering the full buyer journey: eligibility, pricing, process, and comparisons.",
    ]

    geo_items = [
        "Publish authoritative content that positions the brand as a specialist in its core category — the framing AI engines reuse when answering category questions.",
        "Check whether the brand appears on the sources LLMs actually cite for this category (industry directories, comparison sites, press) — competitors already do.",
        "Create comparison-friendly content (\"X vs Y\", \"how to choose\") that AI systems can reference directly when answering evaluation queries.",
        "Interlink product pages, guides, and FAQs into topic clusters — stronger contextual relevance improves AI-driven discovery.",
    ]

    def _column(x, label, items):
        _textbox(slide, x + Inches(0.25), top + Inches(0.2), col_width - Inches(0.5), Inches(0.35), label, size=15, bold=True, color=_accent())
        y = top + Inches(0.65)
        for item in items:
            _icon_dot(slide, x + Inches(0.25), y + Inches(0.08), Inches(0.09), _accent())
            box = _textbox(slide, x + Inches(0.45), y, col_width - Inches(0.7), Inches(0.9), item, size=11.5)
            chars_per_line = 42
            lines = max(1, -(-len(item) // chars_per_line))
            y += Emu(lines * Inches(0.2)) + Inches(0.18)

    _column(left_x, "Answer Engine Optimization (AEO)", aeo_items)
    _column(right_x, "Generative Engine Optimization (GEO)", geo_items)
    return slide


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
            competitor_narratives,
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
) -> bytes:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    add_title_slide(prs, client_name, website_url, logo_bytes=logo_bytes)

    if company_overview:
        add_company_overview_extracted_slide(prs, client_name, company_overview)
        add_solutions_products_slide(prs, company_overview)
    elif site_audit and site_audit.get("company_summary"):
        add_company_overview_slide(prs, client_name, site_audit["company_summary"])

    if tech_stack:
        add_tech_stack_slide(prs, tech_stack)

    if site_audit or page_audit:
        add_section_slide(prs, client_name, "Understanding Current Scenario")
        if site_audit:
            add_site_health_slide(prs, site_audit, page_audit)
            add_seo_issues_slide(prs, site_audit, page_audit)

    if psi_mobile or psi_desktop:
        add_pagespeed_slide(prs, psi_mobile, psi_desktop)

    if analytics:
        add_section_slide(prs, client_name, "Traffic & Search Performance")
        if analytics.get("traffic_overview"):
            add_traffic_overview_slide(prs, analytics)
        top_pages = (analytics.get("top_pages") or {}).get("rows", [])
        if top_pages:
            total_views = sum(int(float(p.get("page_views", 0) or 0)) for p in top_pages)
            rows = [
                (p["path"], f"{int(float(p['page_views'])):,}", f"{int(float(p.get('active_users', 0) or 0)):,}")
                for p in top_pages[:14]
            ]
            top = top_pages[0]
            top_share = int(float(top["page_views"])) / total_views * 100 if total_views else 0
            insights = [
                f"\"{top['path']}\" is the top page, {top_share:.0f}% of all tracked pageviews.",
                f"Top {min(3, len(top_pages))} pages account for {sum(int(float(p['page_views'])) for p in top_pages[:3]) / total_views * 100:.0f}% of total traffic." if total_views else "",
            ]
            insights = [i for i in insights if i]
            _table_slide(
                prs, "Top Pages", ["Page", "Pageviews", "Users"], rows,
                col_widths=[7.0, 2.5, 2.6], source="Google Analytics 4", insights=insights,
            )
        if analytics.get("page_performance"):
            add_page_performance_slide(prs, analytics["page_performance"])
        sources = (analytics.get("traffic_sources") or {}).get("rows", [])
        if sources:
            total_sessions = sum(int(float(s.get("sessions", 0) or 0)) for s in sources)
            rows = [
                (s["channel"], f"{int(float(s['sessions'])):,}", f"{int(float(s.get('users', 0) or 0)):,}")
                for s in sources[:14]
            ]
            top_channel = max(sources, key=lambda s: float(s.get("sessions", 0) or 0))
            top_share = float(top_channel["sessions"]) / total_sessions * 100 if total_sessions else 0
            organic = next((s for s in sources if "organic search" in s["channel"].lower()), None)
            insights = [f"{top_channel['channel']} drives {top_share:.0f}% of sessions — the dominant channel by far." if top_share > 40 else f"Traffic is split fairly evenly, {top_channel['channel']} leads at {top_share:.0f}%."]
            if organic:
                organic_share = float(organic["sessions"]) / total_sessions * 100 if total_sessions else 0
                insights.append(f"Organic Search is {organic_share:.0f}% of sessions — {'a healthy share of true search-driven discovery' if organic_share > 15 else 'a thin slice, most traffic is not coming from search yet'}.")
            else:
                insights.append("No Organic Search sessions in this period — SEO isn't driving measurable traffic yet.")
            _table_slide(
                prs, "Traffic Sources", ["Channel", "Sessions", "Users"], rows,
                col_widths=[6.0, 3.05, 3.05], source="Google Analytics 4", insights=insights,
            )
        queries = (analytics.get("search_queries") or {}).get("rows", [])
        if queries:
            top_q = sorted(queries, key=lambda q: q.get("clicks", 0), reverse=True)[:14]
            rows = [(q["query"], q["clicks"], q["impressions"], f"{q['ctr']*100:.1f}%", f"{q['position']:.1f}") for q in top_q]
            total_clicks = sum(q.get("clicks", 0) for q in queries)
            total_impressions = sum(q.get("impressions", 0) for q in queries)
            avg_ctr = total_clicks / total_impressions * 100 if total_impressions else 0
            best_positioned = min((q for q in queries if q.get("clicks", 0) > 0), key=lambda q: q.get("position", 999), default=None)
            insights = [f"Overall CTR is {avg_ctr:.1f}% across {total_impressions:,} impressions — {'strong' if avg_ctr > 3 else 'below the ~3% search-average, titles/descriptions may need work'}."]
            if best_positioned:
                insights.append(f"Best-ranking clicked query: \"{best_positioned['query']}\" at position {best_positioned['position']:.1f}.")
            _table_slide(
                prs, "Search Queries", ["Query", "Clicks", "Impressions", "CTR", "Avg. position"], rows,
                col_widths=[5.5, 1.5, 1.9, 1.5, 1.7], source="Google Search Console", insights=insights,
            )

    if competitor_rows or keyword_rows or backlink_rows or competitor_positions or competitor_narratives:
        add_section_slide(prs, client_name, "Competitor & Keyword Research")
        if competitor_rows:
            add_competitor_table_slide(prs, competitor_rows)
        if competitor_positions:
            add_competitor_positions_slides(prs, competitor_positions)
        if competitor_narratives:
            for domain, narrative in competitor_narratives.items():
                if "error" not in narrative:
                    add_competitor_narrative_slide(prs, client_name, domain, narrative)
        if keyword_rows:
            add_keyword_research_slide(prs, keyword_rows)
        if backlink_rows:
            add_backlink_profile_slide(prs, backlink_rows, backlink_row_count)
        if competitor_analysis:
            add_competitive_gaps_slide(prs, competitor_analysis)

    add_section_slide(prs, client_name, "Next Steps")
    add_next_steps_detail_slide(prs, site_audit, page_audit)
    add_aeo_geo_slide(prs, site_audit, page_audit)

    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()
