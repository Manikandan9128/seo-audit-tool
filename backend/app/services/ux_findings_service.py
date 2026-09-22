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

from app.integrations.text_ai_client import (
    NoAIProviderConfigured, generate_text_with_image, iter_text_attempts,
)

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
flag where a recognized UX, CRO, usability, trust, or brand-consistency principle or heuristic (e.g. cognitive \
load / Hick's law, choice overload, social proof, anchoring, loss aversion, scarcity/urgency, default bias, \
framing, primacy-recency, risk reversal, brand consistency) is either missing where it would help or working \
against the visitor. Only report a principle you can actually ground in the notes above — do not invent \
generic advice that isn't tied to something described. Return as many items as the notes actually support \
(typically 3-5, never pad to a fixed count) — these are priority friction areas, not a measured \
conversion-impact ranking (that would need analytics/experiment data this pass doesn't have).

Suggestions must be directional ("may create friction", "could strengthen the CTA experience", "should be \
tested"), never a guaranteed-outcome claim like "will increase conversions" or "will increase sign-ups" — the \
notes alone can't prove a conversion outcome.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "ui_fixes": [
    {{"issue": string, "where": string, "fix": string, "severity": "Critical" | "High" | "Medium" | "Low"}}
  ],
  "conversion_opportunities": [string],
  "onboarding_breakdown": [
    {{"principle": string, "where": string, "suggestion": string}}
  ]
}}

Mark anything that blocks a purchase (broken checkout, dead call-to-action, broken form) as "Critical" \
severity. conversion_opportunities should cover trust signals, reviews, bundling, and engagement content —
3 to 6 items. onboarding_breakdown: each item needs a real principle/heuristic name (not a generic UX tip or \
content-format label like "Overview"), exactly where it shows up on the page, and one directional suggestion.
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
    """Returns {"ui_fixes": [...], "conversion_opportunities": [...]} or
    {"error": str}.

    Tries every configured provider in order, not just the first one to
    answer (2026-09-22, same fix as structured_data_insights_service,
    2026-09-20; see core_problem_service.generate_core_problem's docstring
    for why)."""
    prompt = PROMPT_TEMPLATE.format(client_name=client_name, website_url=website_url, ux_notes=ux_notes[:6000])
    errors: list[str] = []
    last_raw = ""
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=4096, errors=errors):
            last_raw = raw
            cleaned = raw.strip()
            cleaned = re.sub(r"^```(json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                errors.append(f"{provider} did not return valid JSON")
                continue
            if isinstance(data, dict):
                return data
            errors.append(f"{provider} did not return a JSON object")
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    return {"error": " | ".join(errors) if errors else "AI did not return valid JSON", "raw": last_raw[:500]}


ONBOARDING_PROMPT_TEMPLATE = """You are a conversion-rate/UX consultant writing part of a client-facing SEO/web \
audit report. Below is a real screenshot of {client_name}'s homepage ({website_url}) — treat only what is \
actually visible in it as ground truth, do not invent page elements, copy, or flows you cannot see.

Do an onboarding breakdown of this landing page: walk it the way a first-time visitor experiences it and flag \
where a recognized UX, CRO, usability, trust, or brand-consistency principle or heuristic (e.g. cognitive load \
/ Hick's law, choice overload, social proof, anchoring, loss aversion, scarcity/urgency, default bias, framing, \
primacy-recency, risk reversal, brand consistency) is either missing where it would help or working against \
the visitor. These are not all "psychological biases" — treat "Principle / Heuristic" as the broader category, \
and give risk reversal and brand consistency equal weight to the others:
- Risk reversal: does the page remove the visitor's perceived risk of acting — a money-back guarantee, free \
  trial or no-card-required signup, visible refund policy, security/payment trust badges near the conversion \
  point? Flag it if a real conversion point (signup, purchase, demo request) has none of these nearby.
- Brand consistency: does the primary call-to-action look and read the same everywhere it repeats — same \
  color, shape, and copy in the header nav vs. the hero vs. anywhere else? Flag competing or inconsistently \
  styled CTAs, or a hero band whose visual treatment doesn't match the rest of the page's brand identity.
Only report a principle you can actually ground in something visible in the screenshot — do not invent generic \
advice that isn't tied to a real element on the page, and do not force every page to exhibit every principle \
listed above. Return as many items as the screenshot actually supports (typically 3-5, never pad to a fixed \
count) — these are priority friction areas identified from a screenshot, not a measured conversion-impact \
ranking (that needs analytics/experiment data this pass doesn't have).

"Where It Shows Up" must name the exact UI element or region (e.g. "top utility bar", "primary 'Enquire Now' \
CTA", "hero visual") — never a vague "on the homepage" or "throughout the page" when a more specific location \
is visible. Suggestions must be directional ("may create friction", "could strengthen the CTA experience", \
"should be tested"), never a guaranteed-outcome claim like "will increase conversions" or "will increase \
sign-ups" — a screenshot alone can't prove a conversion outcome, only point to a UX pattern.

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. Never \
mention that you are an AI, a language model, or any tool by name. Every sentence must be complete, with \
terminal punctuation. Keep each field short enough not to be truncated: "where" one concise phrase/sentence, \
"suggestion" 1-2 concise sentences.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "onboarding_breakdown": [
    {{"principle": string, "where": string, "suggestion": string}}
  ]
}}

Each item needs a real principle/heuristic name (not a generic UX tip or a content-format label like \
"Overview"/"Info"), exactly where it shows up on the page, and one directional suggestion."""


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


# "UI-Level Fixes" (Issue/Where/Fix/Severity) previously only ever filled in
# from generate_ux_findings() above, which needs a reviewer to have typed
# manual QA notes first — no reviewer has ever done that for most clients,
# so this slide sat on static_no_ux_pass()'s fallback message indefinitely.
# This vision pass fills the SAME ui_fixes shape from a real homepage
# screenshot instead, same independence-from-manual-notes pattern as
# generate_onboarding_breakdown above.
#
# Deliberately scoped to what's actually verifiable from a STATIC image —
# unlike a manual walkthrough, a screenshot can't confirm a form fails to
# submit or a checkout step is broken (that needs real interaction), so
# this prompt only asks for visual/layout/copy-clarity issues genuinely
# visible in the image, not invented interactive/functional bugs. Real
# functional QA (broken checkout, non-submitting forms) still needs an
# actual manual pass — this doesn't replace that, it fills the visual-
# usability gap that a screenshot alone CAN honestly answer.
UI_FIXES_PROMPT_TEMPLATE = """You are a UX/conversion consultant writing part of a client-facing SEO/web audit \
report. Below is a real screenshot of {client_name}'s homepage ({website_url}) — treat only what is actually \
visible in it as ground truth. Do not invent page elements, copy, or functionality you cannot see, and do not \
claim anything about interactive behavior (form submission, checkout flow, broken links) that a static image \
cannot verify — that needs a real manual walkthrough, which this is not a substitute for.

Find concrete, visible UI-level issues on this homepage — things like: no clear single primary call-to-action \
above the fold, low-contrast or hard-to-read text, cluttered or competing CTAs, missing visible trust signals \
(reviews, logos, badges, security marks) near a conversion point, an overloaded or unclear navigation menu, \
inconsistent button styling, unclear value proposition in the hero area, or a layout that looks cramped or \
visually broken. Only report an issue you can actually point to in the image — do not pad the list with generic \
best-practice advice that isn't tied to something visible.

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. Never \
mention that you are an AI, a language model, or any tool by name. Every sentence must be complete, with \
terminal punctuation.

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "ui_fixes": [
    {{"issue": string, "where": string, "fix": string, "severity": "Critical" | "High" | "Medium" | "Low"}}
  ]
}}

3 to 6 items. Mark something "Critical" only if it would plausibly block a visitor from converting at all \
(e.g. no visible way to start the core action); a static homepage screenshot alone can rarely justify Critical \
for a checkout/form issue specifically, since that needs real interaction to confirm — prefer "High" for a \
serious-looking but unconfirmed-by-interaction issue instead."""


def generate_ui_fixes_from_screenshot(client_name: str, website_url: str, screenshot_bytes: bytes, mime_type: str = "image/png") -> dict:
    """Vision pass finding concrete UI-level issues from a real homepage
    screenshot — independent of the manual-QA-notes path, same pattern as
    generate_onboarding_breakdown. Returns {"ui_fixes": [...]} or
    {"error": str}."""
    prompt = UI_FIXES_PROMPT_TEMPLATE.format(client_name=client_name, website_url=website_url)
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
