"""Synthesizes everything already gathered for the report (crawl issues,
backlink stats, competitor gap findings) into ONE diagnostic "Core Problem"
thesis for the report's executive-summary slide — the single root-cause
statement plus category breakdown, matching the real manual-report format
confirmed from a SPOTONIX audit. Not cached: unlike Company Overview (a
static description), this reflects CURRENT metrics/issues, and a cached
diagnosis would go stale exactly when a client has fixed something."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

CORE_PROBLEM_PROMPT = """You are a senior SEO strategist writing the "Core Problem" slide for a client-facing \
Web & SEO Audit report — the single diagnostic thesis explaining why the site isn't ranking or converting as well \
as it could, based ONLY on the findings below. Never invent a finding that isn't present in the data.

Write ONE thesis sentence (the root cause, plain confident agency language, no hedging like "may" or "could") \
plus 2-4 short findings under each of these three categories, grounded only in what the data actually supports:
- On-page SEO
- Off-page SEO
- Content & Keyword Strategy

If a category genuinely has nothing to flag in the data, return an empty "points" array for it rather than padding \
with generic advice not backed by the findings.

Return ONLY valid JSON, no markdown fences, no commentary:
{
  "thesis": string,
  "categories": [
    {"name": "On-page SEO", "points": [string]},
    {"name": "Off-page SEO", "points": [string]},
    {"name": "Content & Keyword Strategy", "points": [string]}
  ]
}

FINDINGS:
{findings}
"""


def generate_core_problem(findings: dict) -> dict:
    """Returns {"thesis": str, "categories": [...]} or {"error": str}."""
    prompt = CORE_PROBLEM_PROMPT.replace("{findings}", json.dumps(findings, indent=2, default=str)[:6000])
    try:
        raw, _provider = generate_text(prompt, max_tokens=1024)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "Model did not return valid JSON", "raw": raw[:500]}
    if not data.get("thesis"):
        return {"error": "Model returned no thesis"}
    return data
