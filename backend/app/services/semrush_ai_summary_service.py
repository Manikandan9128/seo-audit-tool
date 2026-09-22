"""Turns the rule-based Semrush gap analysis (semrush_analysis_service.analyze)
into a short narrative summary — an executive-summary paragraph plus a
prioritized action list, grounded only in the issues/data already found.
Tries Gemini first, falls back to Claude — either key alone is enough."""

import json
import re
import time

from app.config import settings
from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

PROMPT_TEMPLATE = """You are an SEO consultant writing a short narrative summary for a client report. \
Base everything ONLY on the structured findings below — never invent numbers, competitors, or issues \
that aren't listed. Do not repeat every issue verbatim; synthesize them into a coherent picture.

Never write a vague count like "N pages have issues" — name the specific page, domain, or keyword the \
finding is about whenever the findings data gives you one. If a finding only has a bare count with no \
identifiable page or domain, say so plainly rather than presenting the number as if it were actionable on \
its own. If any figure is a country-specific number (vs. global), keep that distinction — don't blur a \
country-filtered figure and a global one together as if they were the same kind of number.

Write in plain, confident agency language — this is client-facing content, not an AI-generated draft. \
Never mention that you are an AI, a language model, or any tool by name; write as the agency's own analysis. \
Avoid hedging ("may," "could potentially") where the data supports a direct statement — flag genuine \
uncertainty explicitly instead of hedging every sentence. Every sentence and bullet must be complete, with \
terminal punctuation — if you're about to run out of room, drop a less-important point entirely rather than \
truncate one mid-sentence.

Client: {client_name} ({website_url})

Findings (from Semrush data comparison and a site technical crawl):
{issues_json}

Data coverage (what was and wasn't uploaded, so you know what the findings can and can't tell you):
{coverage_json}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "summary": string,          // 3-5 sentence executive summary of where this site stands vs. competitors and technically, in plain English
  "priorities": [string]      // 3-5 short, specific, prioritized action items ordered by impact, each one sentence
}}
"""


def generate_ai_summary(client_name: str, website_url: str, analysis: dict) -> dict:
    """Returns {"summary": str, "priorities": [str]} or {"error": str}.

    Tries every configured provider in order (Groq, OpenRouter, Gemini,
    Claude), not just the first one to answer (2026-09-22, same fix as
    structured_data_insights_service, 2026-09-20; see
    core_problem_service.generate_core_problem's docstring for why)."""
    if not (settings.gemini_api_key or settings.groq_api_key or settings.claude_api_key or settings.openrouter_api_key):
        return {"error": "No Groq, OpenRouter, Gemini, or Claude API key configured — add one in Settings"}

    issues = analysis.get("issues") or []
    if not issues:
        return {"error": "No findings to summarize yet — run the analysis first (or upload more Semrush data)."}

    prompt = PROMPT_TEMPLATE.format(
        client_name=client_name,
        website_url=website_url,
        issues_json=json.dumps(issues, indent=2)[:6000],
        coverage_json=json.dumps(analysis.get("coverage") or {}, indent=2),
    )

    errors: list[str] = []
    last_raw = None
    last_error = None
    for attempt in range(3):
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
            break
        except NoAIProviderConfigured as e:
            last_error = e
            if "UNAVAILABLE" in str(e) or "503" in str(e):
                time.sleep(2 * (attempt + 1))
                continue
            return {"error": str(e)}
    if last_raw is None:
        return {"error": str(last_error) if last_error else "AI request failed after retries"}

    return {"error": " | ".join(errors) if errors else "AI did not return valid JSON", "raw": last_raw[:500]}
