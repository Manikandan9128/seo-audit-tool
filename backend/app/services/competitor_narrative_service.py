"""Per-competitor "Areas of Focus" narrative — matches the reference deck's
"Areas of Focus for {Client} (vs {Competitor})" slide format: specific
recommendations plus a closing Strategic Growth Opportunity paragraph.
Generated once per competitor domain, grounded only in whatever data was
actually uploaded for that competitor — same "don't invent numbers"
discipline as the AI Summary feature. Tries Gemini first, falls back to
Claude — either key alone is enough."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

PROMPT_TEMPLATE = """You are an SEO/growth consultant writing one slide of a competitive analysis for \
{client_name} ({client_domain}), comparing them against the competitor {competitor_domain}.

Base every claim ONLY on the data below — never invent traffic numbers, rankings, keywords, or product \
features that aren't stated. If the data is thin, write fewer, more general (but still grounded) bullets \
rather than inventing specifics.

Data on {competitor_domain} vs {client_name}:
{data_json}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "areas_of_focus": [string],   // 6-9 short, specific, actionable bullets — what {client_name} should do in response to this competitor, ordered by impact
  "growth_opportunity": string  // 2-4 sentence closing paragraph naming the strategic opening {client_name} has vs. this specific competitor
}}
"""


def generate_competitor_narrative(client_name: str, client_domain: str, competitor_domain: str, data: dict) -> dict:
    """Returns {"areas_of_focus": [str], "growth_opportunity": str} or {"error": str}."""
    prompt = PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_domain=client_domain,
        competitor_domain=competitor_domain,
        data_json=json.dumps(data, indent=2, default=str)[:4000],
    )
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data_out = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    return data_out
