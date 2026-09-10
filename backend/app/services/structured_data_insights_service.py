"""Structured Data & Schema Validator slide's Part 3 (Key Insights),
2026-09-10 user spec. Part 1/Part 2 table numbers are computed
deterministically (pptx_builder.build_schema_report_parts) — this only
writes the consultative prose grouping those exact numbers by shared root
cause, it never derives or invents a number itself."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

STRUCTURED_DATA_INSIGHTS_PROMPT = """You are an SEO consultant preparing a "Structured data & schema validator" \
slide for a client audit report. Below are two tables already computed from a real crawl + validation pass — \
treat every number in them as ground truth, never invent a schema type, page count, or pageview number not \
present in this data.

Part 1 — Applicable schema by page type:
{part1}

Part 2 — Validation results (Applicable Pages | Valid | Errors | Coverage %; the denominator for Coverage % is \
always the pages that type applies to, never total site pages; baseline site-wide schema is never blended into \
a content type's coverage):
{part2}

Pageviews affected per page type (0 means no analytics data was available, not zero real traffic):
{pageviews}

Google rich-result eligibility notes (only listed for a type that is NOT fully eligible — retired, restricted, \
or unverified; a type with no note here is assumed eligible):
{eligibility_notes}

Write 3-5 Key Insights bullets, each covering ONE schema gap or ONE confirmed win:
- Name the schema type and how many pages/pageviews are affected.
- State the business impact (rich-result eligibility, CTR, crawl efficiency) — respect the eligibility notes \
above; never claim a rich-result or CTR benefit for a type flagged as ineligible or retired there.
- For a gap, include a one-line fix directly in the bullet (e.g. "Fix: add Article schema to the blog \
template.").
- Call out any schema type that's fully valid (Errors=0 and Coverage=100%) as a confirmed win — no fix needed, \
don't invent one.
- Call out any page bucket correctly excluded from the coverage math (e.g. "Other Pages" with no content-\
specific schema requirement) as NOT a gap.
- If a point needs no action, don't write a filler "no action needed" bullet for it unless it's the confirmed-\
win callout above — every other bullet must name a real gap or win.

Never invent traffic numbers or issue types — infer only from the numbers given. Write in plain, confident \
agency language — this is client-facing content, not an AI-generated draft. Never mention that you are an AI, a \
language model, or any tool by name.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "insights": [string]
}}
"""


def generate_structured_data_insights(
    part1: list[dict], part2: list[dict], pageviews_by_page_type: dict[str, int], eligibility_notes: dict[str, str]
) -> dict:
    """Returns {"insights": [str]} or {"error": str}."""
    if not part1 and not part2:
        return {"error": "No schema data to analyze"}

    prompt = STRUCTURED_DATA_INSIGHTS_PROMPT.format(
        part1=json.dumps(part1, indent=2),
        part2=json.dumps(part2, indent=2),
        pageviews=json.dumps(pageviews_by_page_type, indent=2) if pageviews_by_page_type else "(no analytics data)",
        eligibility_notes=json.dumps(eligibility_notes, indent=2) if eligibility_notes else "(all types fully eligible)",
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
