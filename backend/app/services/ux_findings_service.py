"""UI-Level Fixes + Conversion Opportunities, generated from manual UX/QA
notes a reviewer typed in by hand (text notes only — no screenshot/vision
analysis). If no notes were supplied, the caller uses static_no_ux_pass()
instead of skipping the dimension, per the report spec's Rule 8.

The onboarding-bias breakdown (generate_onboarding_breakdown below) is a
separate, independent pass that does NOT need those manual notes — it runs
against a real screenshot of the site's own homepage instead, so it's
available even when no reviewer has done a manual walkthrough yet."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text, generate_text_with_image

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

Also do an onboarding breakdown of the landing page: walk it the way a first-time visitor experiences it and \
flag where recognized onboarding/conversion psychology biases (e.g. cognitive load / Hick's law, choice \
overload, social proof, anchoring, loss aversion, scarcity/urgency, default bias, Zeigarnik effect / \
incomplete-task tension, framing, primacy-recency) are either missing where they'd help or working against \
the visitor. Only report biases you can actually ground in the notes above — do not invent generic advice \
that isn't tied to something described. Return the top 5, ranked by likely impact on sign-up/purchase \
completion.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "ui_fixes": [
    {{"issue": string, "where": string, "fix": string, "severity": "Critical" | "High" | "Medium" | "Low"}}
  ],
  "conversion_opportunities": [string],
  "onboarding_breakdown": [
    {{"bias": string, "where": string, "suggestion": string}}
  ]
}}

Mark anything that blocks a purchase (broken checkout, dead call-to-action, broken form) as "Critical" \
severity. conversion_opportunities should cover trust signals, reviews, bundling, and engagement content —
3 to 6 items. onboarding_breakdown must have at most 5 items, each a real bias name (not a generic UX tip), \
where it shows up on the page, and one directional (not prescriptive-pixel-level) suggestion.
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


ONBOARDING_PROMPT_TEMPLATE = """You are a conversion-rate/UX consultant writing part of a client-facing SEO/web \
audit report. Below is a real screenshot of {client_name}'s homepage ({website_url}) — treat only what is \
actually visible in it as ground truth, do not invent page elements, copy, or flows you cannot see.

Do an onboarding breakdown of this landing page: walk it the way a first-time visitor experiences it and flag \
where recognized onboarding/conversion psychology biases (e.g. cognitive load / Hick's law, choice overload, \
social proof, anchoring, loss aversion, scarcity/urgency, default bias, Zeigarnik effect / incomplete-task \
tension, framing, primacy-recency) are either missing where they'd help or working against the visitor. Only \
report biases you can actually ground in something visible in the screenshot — do not invent generic advice \
that isn't tied to a real element on the page. Return the top 5, ranked by likely impact on sign-up/purchase \
completion.

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. Never \
mention that you are an AI, a language model, or any tool by name. Every sentence must be complete, with \
terminal punctuation.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "onboarding_breakdown": [
    {{"bias": string, "where": string, "suggestion": string}}
  ]
}}

At most 5 items, each a real bias name (not a generic UX tip), where it shows up on the page, and one \
directional (not prescriptive-pixel-level) suggestion."""


def generate_onboarding_breakdown(client_name: str, website_url: str, screenshot_bytes: bytes, mime_type: str = "image/png") -> dict:
    """Vision pass over a real homepage screenshot — independent of the
    manual-QA-notes path above, so it doesn't need a reviewer to have typed
    anything in. Returns {"onboarding_breakdown": [...]} or {"error": str},
    same shape as the field inside generate_ux_findings()'s result so the
    caller can merge either source into one ux_findings dict."""
    prompt = ONBOARDING_PROMPT_TEMPLATE.format(client_name=client_name, website_url=website_url)
    try:
        raw, _provider = generate_text_with_image(prompt, screenshot_bytes, mime_type)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    return data
