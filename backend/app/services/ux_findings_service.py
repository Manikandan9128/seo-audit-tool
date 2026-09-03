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


ONBOARDING_BIAS_PROMPT_TEMPLATE = """You are a conversion-rate/UX consultant writing part of a client-facing SEO/web audit \
report. Do an onboarding breakdown of {client_name}'s landing page ({website_url}), grounded only in the text below \
scraped from the live page — treat it as ground truth, do not invent copy, offers, or elements that aren't present in it.

Landing page text:
---
{homepage_text}
---

Walk through the standard onboarding/persuasion cognitive biases and assess whether the page currently uses each one, \
based only on what's in the text above:
- Social Proof (testimonials, customer logos, review counts, user numbers)
- Authority (credentials, certifications, awards, press mentions, expert endorsements)
- Scarcity / Urgency (limited-time offers, low-stock language, deadlines)
- Anchoring (a reference/original price shown next to a discounted one, tiered pricing)
- Loss Aversion (copy framed around what the visitor loses by not acting)
- Reciprocity (free trial, free resource, or something given before asking for anything)
- Default Effect (a pre-selected or clearly recommended option among choices)
- Cognitive Load / Simplicity (how much the visitor must read or decide before the primary action is clear)

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. Never mention \
that you are an AI, a language model, or any tool by name. Every sentence must be complete, with terminal \
punctuation — if you're about to run out of room, drop a less-important point entirely rather than truncate one \
mid-sentence.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "biases": [
    {{"bias": string, "present": boolean, "assessment": string}}
  ],
  "top_suggestions": [string]
}}

biases must cover all 8 listed above, in that order. top_suggestions must have exactly 5 items, each a concrete, \
directional change to the landing page (not a vague principle) ordered by expected impact, highest first.
"""


def generate_onboarding_breakdown(client_name: str, website_url: str, homepage_text: str) -> dict:
    """Returns {"biases": [...], "top_suggestions": [...]} or {"error": str}.
    Unlike generate_ux_findings above, this needs no manual QA notes — it
    runs off the landing page's own scraped text, so it's generated
    unconditionally whenever an AI key is configured and the homepage
    fetch succeeded, independent of whether a manual UX pass was done."""
    prompt = ONBOARDING_BIAS_PROMPT_TEMPLATE.format(
        client_name=client_name, website_url=website_url, homepage_text=homepage_text[:6000]
    )
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
