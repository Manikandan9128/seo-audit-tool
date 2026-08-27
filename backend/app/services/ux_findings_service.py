"""UI-Level Fixes + Conversion Opportunities, generated from manual UX/QA
notes a reviewer typed in by hand (text notes only — no screenshot/vision
analysis). If no notes were supplied, the caller uses static_no_ux_pass()
instead of skipping the dimension, per the report spec's Rule 8."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

PROMPT_TEMPLATE = """You are a conversion-rate/UX consultant writing part of a client-facing SEO/web audit \
report. You are given manual QA notes a reviewer wrote while walking through {client_name}'s site \
({website_url}) by hand — treat these notes as ground truth, do not invent issues beyond what they describe.

Manual QA notes:
---
{ux_notes}
---

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. Never \
mention that you are an AI, a language model, or any tool by name. Every sentence must be complete, with \
terminal punctuation — if you're about to run out of room, drop a less-important point entirely rather than \
truncate one mid-sentence.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "ui_fixes": [
    {{"issue": string, "where": string, "fix": string, "severity": "Critical" | "High" | "Medium" | "Low"}}
  ],
  "conversion_opportunities": [string]
}}

Mark anything that blocks a purchase (broken checkout, dead call-to-action, broken form) as "Critical" \
severity. conversion_opportunities should cover trust signals, reviews, bundling, and engagement content —
3 to 6 items.
"""


def static_no_ux_pass() -> dict:
    """Rule 8 fallback when no manual UX/QA input was provided — state that
    explicitly rather than silently omitting the dimension."""
    return {
        "no_ux_pass_done": True,
        "note": (
            "A manual UX pass has not yet been done for this site. We recommend a hands-on walkthrough of "
            "the core purchase/signup flow — checkout, forms, and primary calls-to-action — as a next step, "
            "since crawl and analytics data alone can't surface broken flows or on-page trust gaps."
        ),
    }


def generate_ux_findings(client_name: str, website_url: str, ux_notes: str) -> dict:
    """Returns {"ui_fixes": [...], "conversion_opportunities": [...]} or {"error": str}."""
    prompt = PROMPT_TEMPLATE.format(client_name=client_name, website_url=website_url, ux_notes=ux_notes[:6000])
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    return data
