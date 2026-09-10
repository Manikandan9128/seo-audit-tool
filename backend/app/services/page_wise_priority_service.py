"""Priority Issues - Page Wise slide AI content (2026-09-10 user spec):
per-page Fix text inferred from URL pattern + issue count (no invented
issue names), plus insights that group pages by shared probable root cause
instead of repeating "likely cause" once per table row."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

PAGE_WISE_PRIORITY_PROMPT = """You are an SEO analyst writing a "Priority Issues - Page Wise" section of a \
client audit report. Below is a list of pages flagged by a full-site crawl, each with only a raw issue COUNT — \
no specific issue names are available for these pages (that detail lives on a separate site-wide slide). \
{shown_count} of these pages will be shown in a Page | Issues | Fix table; you are writing the Fix for each of \
those {shown_count}, plus insights drawn from the FULL list below.

Part 1 — Fix per shown page: for each of the pages listed under SHOWN PAGES, write one short, specific, \
actionable fix inferred from its URL pattern and issue count (e.g. "audit meta tags and internal links on this \
post", "check page load speed and structured data on this product page"). Never invent a specific issue type or \
number that isn't given — infer only from the URL's apparent page type (blog post, product page, job listing, \
webinar, case study, etc.) and how many issues it has. Do not write a generic disclaimer.

Part 2 — Insights (3-5 bullets): group pages from the FULL list by shared probable root cause instead of listing \
a cause per row — e.g. "N blog pages account for X of these issues, likely thin content or duplicate meta" or \
"/a and /b likely share broken-link or load-speed issues — both conversion-critical." Each bullet must:
- Name the specific pages or URL pattern involved
- State the probable cause, clearly flagged as inferred/likely, not confirmed as fact
- Connect it to business impact (rankings, conversions, crawl budget) where relevant

Never invent traffic numbers or issue types — infer cause only from URL pattern and issue count given. Do not \
repeat "likely cause" language in the Fix text — that reasoning belongs only in the insights, Fix stays a \
concrete action. Write in plain, confident agency language — this is client-facing content. Never mention that \
you are an AI, a language model, or any tool by name.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "fixes": {{"<page path>": "<fix text>"}},
  "insights": [string]
}}

SHOWN PAGES (write a fix for each):
{shown_pages}

FULL LIST (for insights pattern-grouping only, {total_count} pages total):
{full_list}
"""


def generate_page_wise_priority_content(shown: list[dict], full_list: list[dict]) -> dict:
    """shown/full_list: [{"page": str, "issue_count": int}, ...]. Returns
    {"fixes": {page: str}, "insights": [str]} or {"error": str}."""
    if not shown:
        return {"error": "No pages to analyze"}

    prompt = PAGE_WISE_PRIORITY_PROMPT.format(
        shown_count=len(shown),
        shown_pages=json.dumps(shown, indent=2),
        total_count=len(full_list),
        full_list=json.dumps(full_list[:80], indent=2),
    )
    try:
        raw, _provider = generate_text(prompt, max_tokens=2048)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    if not data.get("fixes") and not data.get("insights"):
        return {"error": "Model returned neither fixes nor insights"}
    return data
