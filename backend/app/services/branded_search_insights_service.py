"""Branded vs Non-Branded Search Performance slide's Key Insights section
(2026-09-11 outcome-case rebuild). The headline, demand-gap estimate, cost-
of-inaction paragraph, and concrete example are all computed deterministically
in pptx_builder (build_branded_vs_nonbranded_comparison /
build_branded_dependency_narrative / build_high_potential_pages /
build_high_potential_countries) and already render on the slide itself —
this only writes the Key Insights bullets underneath them, grounded in
those exact same numbers, never deriving or inventing a new one.
high_countries here is only the "material" tier (2026-09-11 country-tiering
split) — the low-signal, single-digit-click countries never reach this
prompt."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

BRANDED_SEARCH_INSIGHTS_PROMPT = """You are an SEO consultant writing the Key Insights for a "Branded vs \
Non-Branded Search Performance" slide, covering a single {date_range} Search Console snapshot — there is no \
prior period to compare against, so never claim anything grew, dropped, improved, or declined.

Branded vs Non-Branded comparison (ground truth, already shown in a table on this same slide):
{comparison}

This slide already states the following, above where your bullets will render — do not restate them verbatim, \
build on them:
Headline (business-dependency finding): {headline}
Demand-gap estimate (modeled, assumption already stated on-slide): {demand_gap}
Cost of inaction (already written on-slide): {cost_of_inaction}
Concrete example (already written on-slide, if any): {concrete_example}

High-potential landing pages already flagged by rule-based logic (shown in full on a separate slide — name at \
most the single best one here, don't restate the whole list):
{high_pages}

High-potential countries already flagged the same way (also shown in full on the separate slide — name at most \
the single best one here):
{high_countries}

Top branded queries this period (for spotting cannibalization only):
{top_branded_queries}

Top non-branded queries this period (for spotting cannibalization only):
{top_nonbranded_queries}

Write 3-5 Key Insights bullets:
1. The branded-dependency finding, building on the headline above rather than repeating it word for word — add \
what it implies for the business (healthy diversification vs. over-reliance on brand recognition alone).
2. The demand-gap estimate — if one is given above, restate its number exactly (never recompute or round \
differently) and add the business implication of leaving it uncaptured. If none is given (the field says so), \
skip this point entirely rather than inventing a number.
3. One forward-looking recommendation — a concrete next action to reduce branded dependency (e.g. what content \
or pages to prioritize), grounded only in the pages/countries/examples given above, never a generic platitude.
4. Optionally call out the single highest-opportunity page or country from the lists above and its realistic \
upside, if not already covered by the concrete example above.
5. ONLY if you can point to a real pair of queries in the lists above — a branded query whose traffic looks \
suppressed by a generic/dictionary term that could be cannibalizing it — flag it. If no such pair is visible in \
the data given, skip this point entirely rather than inventing one.

Never invent a number, page, country, or query not present in the data above. Do not use "grew," "up," \
"declining," "improving," or any other trend word — this is one snapshot, not a comparison. Write in plain, \
confident agency language — this is client-facing content, not an AI-generated draft. Never mention that you \
are an AI, a language model, or any tool by name.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "insights": [string]
}}
"""


def generate_branded_search_insights(
    comparison: dict, narrative: dict | None, high_pages: list[dict], high_countries: list[dict],
    top_branded_queries: list[dict], top_nonbranded_queries: list[dict], date_range: str,
) -> dict:
    """Returns {"insights": [str]} or {"error": str}."""
    if not comparison:
        return {"error": "No branded/non-branded comparison data to analyze"}
    narrative = narrative or {}
    demand_gap = narrative.get("demand_gap")
    concrete_example = narrative.get("concrete_example")

    prompt = BRANDED_SEARCH_INSIGHTS_PROMPT.format(
        date_range=date_range or "recent 30-day",
        comparison=json.dumps(comparison, indent=2),
        headline=narrative.get("headline") or "(none computed)",
        demand_gap=demand_gap["text"] if demand_gap else "(no click-through-rate gap found — non-branded already meets its position benchmark)",
        cost_of_inaction=narrative.get("cost_of_inaction") or "(none computed)",
        concrete_example=concrete_example["text"] if concrete_example else "(none found)",
        high_pages=json.dumps(high_pages[:1], indent=2) if high_pages else "(none met the flagging bar)",
        high_countries=json.dumps(high_countries[:1], indent=2) if high_countries else "(none met the flagging bar)",
        top_branded_queries=json.dumps(top_branded_queries[:10], indent=2) if top_branded_queries else "(none)",
        top_nonbranded_queries=json.dumps(top_nonbranded_queries[:10], indent=2) if top_nonbranded_queries else "(none)",
    )
    try:
        raw, _provider = generate_text(prompt, max_tokens=1024)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    if not data.get("insights"):
        return {"error": "Model returned no insights"}
    return data
