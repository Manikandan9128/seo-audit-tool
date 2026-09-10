"""Turns raw GeoPulse export text (see geopulse_parser.py) into grounded
content for the AEO and GEO Next Steps slides. Before this, both slides were
static boilerplate identical across every client report (same bullets
regardless of the site) — this makes them reflect the client's actual
GeoPulse findings (AI-visibility mentions, citation gaps, answer-engine
presence, etc.) once a GeoPulse file is uploaded. Falls back to the existing
generic bullets when no GeoPulse data is uploaded or the AI call fails — see
add_aeo_slide/add_geo_slide in pptx_builder.py."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are an SEO strategist analyzing a GeoPulse AI-visibility report for a client's \
website. GeoPulse tracks how a brand shows up in AI answer engines (ChatGPT, Perplexity, Google AI \
Overviews, etc.) — citation frequency, sentiment, competitor mentions, and content gaps.

Below is the raw export content. Read it and produce two short lists of specific, actionable \
recommendations grounded ONLY in what this data actually shows — never invent a statistic, \
competitor name, or finding that isn't in the text below.

GeoPulse's own export sometimes includes a data-quality warning of its own — e.g. "Automated \
consistency check failed" or a line stating one number "doesn't match the dashboard's" \
independently-computed value. Treat that as authoritative: NEVER cite the disputed/flagged number \
as settled fact. When the export also states the dashboard's corrected value, use that corrected \
value instead (and you may note briefly that the export's own headline figure didn't match it). \
If no corrected value is given, drop that specific metric from your output rather than repeating \
the disputed number.
{disputed_metrics_note}
- "aeo_items": Answer Engine Optimization — structured-data/schema and content-format \
recommendations for appearing in AI Overviews and answer boxes.
- "geo_items": Generative Engine Optimization — entity-building, brand-citation, and topical- \
authority recommendations for being cited by generative AI engines.

3-5 items per list, each 1-2 sentences, specific to what's in the data (cite real numbers/findings \
from it where present) rather than generic advice.

GeoPulse export content:
{raw_text}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{"aeo_items": [string], "geo_items": [string]}}
"""

# GeoPulse's own "Automated consistency check failed" banner names the exact
# disputed headline value and the dashboard's independently-computed
# correction in one sentence (confirmed real, e.g. "Report organic discovery
# (1.1%) doesn't match the dashboard's (0.0%)."). Pulling these out here and
# handing them to the prompt explicitly is more reliable than trusting the AI
# to notice one warning line buried in a long raw export — this is GeoPulse's
# own fixed banner wording, not something that varies with the rest of the
# export's format (which genuinely can be anything, per geopulse_parser.py).
_DISPUTED_METRIC_RE = re.compile(
    r"([A-Za-z][A-Za-z /]*?)\s*\(([\d.]+%?)\)\s*doesn'?t match the dashboard'?s[^(\n]*\(([\d.]+%?)\)",
    re.IGNORECASE,
)


def _find_disputed_metrics(raw_text: str) -> list[tuple[str, str, str]]:
    """[(metric_label, disputed_value, dashboard_value), ...] pulled from a
    GeoPulse "consistency check failed" banner, if present. Empty when the
    export has no such warning."""
    return [(label.strip(), disputed.strip(), corrected.strip()) for label, disputed, corrected in _DISPUTED_METRIC_RE.findall(raw_text or "")]


def generate_aeo_geo_content(raw_text: str) -> dict:
    """Returns {"aeo_items": [...], "geo_items": [...]}. Empty dict on any
    failure (no AI key, bad JSON, empty input) — caller falls back to the
    existing generic slide content, same as when no GeoPulse file is
    uploaded at all.

    2026-09-10: GeoPulse can flag its own headline numbers as disputed
    (an "Automated consistency check failed" banner naming a mismatch with
    its dashboard) — confirmed real on a Lumber export where the AEO/GEO
    slide cited the flagged 1.1% organic-discovery figure as plain fact with
    no caveat, even though the export itself said the real value was 0.0%.
    Any disputed metrics found are named explicitly in the prompt (with the
    dashboard's corrected value) so the AI uses the correction instead of
    repeating the flagged number."""
    if not raw_text or not raw_text.strip():
        return {}
    disputed = _find_disputed_metrics(raw_text)
    if disputed:
        lines = "\n".join(
            f'- "{label}": the export\'s own headline says {disputed_val}, but its consistency check '
            f"says the dashboard's real value is {dashboard_val}. Use {dashboard_val}, never {disputed_val}."
            for label, disputed_val, dashboard_val in disputed
        )
        disputed_note = f"\nThis export flagged these specific metrics as disputed:\n{lines}\n"
    else:
        disputed_note = ""
    prompt = PROMPT_TEMPLATE.format(raw_text=raw_text, disputed_metrics_note=disputed_note)
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        logger.warning("GeoPulse AEO/GEO content generation failed: %s", e)
        return {}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("GeoPulse AEO/GEO content returned invalid JSON: %s", raw[:300])
        return {}
    if not isinstance(data, dict):
        return {}

    aeo_items = [str(x).strip() for x in (data.get("aeo_items") or []) if str(x).strip()]
    geo_items = [str(x).strip() for x in (data.get("geo_items") or []) if str(x).strip()]
    if not aeo_items and not geo_items:
        return {}
    return {"aeo_items": aeo_items, "geo_items": geo_items}
