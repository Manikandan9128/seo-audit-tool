"""Generates the report's "Next Steps" section as genuinely bespoke,
business-aware advice instead of a fixed checklist. Confirmed against 3 real
manual-report references (BEST Insurance, EJTOY, SPOTONIX): a category that
doesn't fit the business — Local SEO for a national B2B SaaS with no
physical locations — is dropped entirely rather than padded with generic
checklist filler, and every surviving bullet names a real product,
competitor, or number specific to that business. A prospect reading a
templated checklist reads it as templated — this report is a sales asset,
not just a diagnostic, so genericness has a direct cost. Grounded only in
the data given — never invents a fact, competitor, or number. Same
generate-then-retry-once discipline as competitor_narrative_service: a
single malformed-JSON response is a non-deterministic model hiccup, not a
systemic failure."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

CATEGORY_TITLES = {
    "local_seo": "Next Steps: Local SEO",
    "technical_seo": "Next Steps: Technical SEO",
    "content_seo": "Next Steps: Content SEO",
    "conversion_seo": "Next Steps: Conversion SEO",
    "aeo": "Answer Engine Optimization (AEO)",
    "geo": "Generative Engine Optimization (GEO)",
    "goals": "SEO Goals & Targets",
}

PROMPT_TEMPLATE = """You are a senior SEO/growth strategist writing the "Next Steps" section of a client-facing \
Web & SEO Audit report for {client_name} ({client_domain}). This report is used to help win {client_name} as a \
client — generic checklist advice that could apply to any business reads as templated and kills trust. Every \
bullet must sound like an analyst who actually studied this specific business: name real products, real \
competitors, real numbers, real customer-question types — using ONLY the facts given below. Never invent a \
fact, number, competitor name, or product that isn't in the data.

For EACH of these 7 categories, decide first whether it genuinely applies to this business, THEN write it:
- local_seo — only applies to a business with physical locations, regional service areas, or city-level search \
intent. A national or global B2B/SaaS/e-commerce business with no physical storefront must get \
"applicable": false — never recommend Google Business Profile, NAP consistency, or local citations to a \
business that has no physical location. When it IS applicable, favor geo-targeted CONTENT strategy (e.g. \
city-specific landing pages for real service areas) over generic listing-hygiene advice.
- technical_seo — foundational crawl/on-page fixes; applicable whenever technical findings exist.
- content_seo — content strategy grounded in the actual keyword clusters/topics found.
- conversion_seo — turning traffic into leads/sales, using real trust signals, real competitor conversion \
tactics, or real top-traffic pages if given.
- aeo — schema/FAQ eligibility for AI Overviews and answer boxes, referencing real customer-question types for \
this specific product/service where possible.
- geo — being cited/recommended by AI assistants (ChatGPT, Gemini, Claude), grounded in the real industry and \
real named competitors.
- goals — near-term and advanced-stage targets, using real current numbers (Domain Rating, keyword counts) \
where given.

When a category is NOT applicable to this business, set "applicable": false and give a one-line "reason" \
explaining why (e.g. "No physical locations or service areas — sells nationally, not locally.") — leave "items" \
empty in that case.

When a category IS applicable, write 3-6 bullets specific enough that they could ONLY apply to this business — \
each one naming a real product, competitor, number, or customer-question type from the data. Never write a \
bullet that would be equally true of every business in this industry.

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. Never \
mention that you are an AI, a language model, or any tool by name. Avoid hedging ("may," "could potentially") \
where the data supports a direct statement. Every bullet must be a complete sentence with terminal punctuation.

DATA:
{data_json}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "categories": {{
    "local_seo": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}},
    "technical_seo": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}},
    "content_seo": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}},
    "conversion_seo": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}},
    "aeo": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}},
    "geo": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}},
    "goals": {{"applicable": bool, "reason": string, "intro": string, "items": [string]}}
  }}
}}
"reason" is only needed when "applicable" is false. "intro" is one short sentence framing the category (shown \
as the slide's subtitle) — only needed when applicable.
"""


def _call_and_parse(prompt: str) -> dict:
    """One generate+parse attempt. Returns the parsed dict or {"error": ...}."""
    try:
        raw, _provider = generate_text(prompt, max_tokens=4096)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some models prepend stray commentary before the JSON object despite
        # the "return ONLY valid JSON" instruction — same recovery as
        # competitor_narrative_service: grab the outermost {...} span.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}


def generate_next_steps(client_name: str, client_domain: str, findings: dict) -> dict:
    """Returns {"categories": {key: {"applicable", "reason", "intro", "items"}}} or {"error": str}."""
    prompt = PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_domain=client_domain,
        data_json=json.dumps(findings, indent=2, default=str)[:12000],
    )
    result = _call_and_parse(prompt)
    if "error" not in result and result.get("categories"):
        return result
    # A single malformed-JSON response is a non-deterministic model hiccup,
    # not a systemic failure (same pattern confirmed with competitor
    # narratives) — one retry on a fresh sample recovers most of these.
    retry_result = _call_and_parse(prompt)
    if "error" not in retry_result and retry_result.get("categories"):
        return retry_result
    return result if "error" in result else {"error": "AI returned no categories"}
