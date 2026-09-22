"""Synthesizes everything already gathered for the report (crawl issues,
backlink stats, competitor gap findings) into ONE diagnostic "Core Problem"
thesis for the report's executive-summary slide — the single root-cause
statement plus category breakdown, matching the real manual-report format
confirmed from a SPOTONIX audit. Not cached: unlike Company Overview (a
static description), this reflects CURRENT metrics/issues, and a cached
diagnosis would go stale exactly when a client has fixed something."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

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


def _parse(raw: str) -> dict | None:
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Groq's gpt-oss-120b sometimes prepends stray commentary before the
        # JSON object despite the "return ONLY valid JSON" instruction — see
        # the same fallback in competitor_narrative_service.py.
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    # json.loads succeeds on any valid JSON value, not just objects — a
    # degenerate completion (e.g. the literal token "null") parses cleanly
    # to None/a list/a string with no exception raised, and .get() below
    # would then crash instead of being treated as a bad response.
    if not isinstance(data, dict) or not data.get("thesis"):
        return None
    return data


def generate_core_problem(findings: dict) -> dict:
    """Returns {"thesis": str, "categories": [...]} or {"error": str}.

    Tries every configured provider in order (same fix as
    structured_data_insights_service, 2026-09-20), not just the first one
    to answer — confirmed real 2026-09-22: with OpenRouter added as a
    fallback provider, its auto-router can land on a free model that
    doesn't reliably follow "return ONLY valid JSON," and generate_text()'s
    own cross-provider fallback only triggers on a transport-level
    failure, never on "the provider answered but wasn't parseable JSON" —
    so a stricter provider later in the order never got a chance."""
    prompt = CORE_PROBLEM_PROMPT.replace("{findings}", json.dumps(findings, indent=2, default=str)[:6000])
    errors: list[str] = []
    last_raw = ""
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=1024, errors=errors):
            last_raw = raw
            data = _parse(raw)
            if data is not None:
                return data
            errors.append(f"{provider} did not return valid JSON")
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    return {"error": " | ".join(errors) if errors else "Model returned no thesis", "raw": last_raw[:500]}
