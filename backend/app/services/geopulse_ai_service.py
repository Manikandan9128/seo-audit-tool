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


def generate_aeo_geo_content(raw_text: str) -> dict:
    """Returns {"aeo_items": [...], "geo_items": [...]}. Empty dict on any
    failure (no AI key, bad JSON, empty input) — caller falls back to the
    existing generic slide content, same as when no GeoPulse file is
    uploaded at all."""
    if not raw_text or not raw_text.strip():
        return {}
    prompt = PROMPT_TEMPLATE.format(raw_text=raw_text)
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
