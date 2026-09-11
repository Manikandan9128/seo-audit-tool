"""Priority Issues - Page Wise slide AI content: per-page Fix text inferred
from URL pattern + issue count (no invented issue names). The slide's
insights are grouped by shared root cause with an EXACT count (2026-09-11
user spec) — computed deterministically in pptx_builder.py's
_page_wise_group_insights instead of asked of the model here, since an LLM
can only approximate a count like this ("over 40 entries"), not guarantee
it matches the table."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

PAGE_WISE_PRIORITY_PROMPT = """You are an SEO analyst writing the Fix column of a "Priority Issues - Page Wise" \
table in a client audit report. Below is a list of pages flagged by a full-site crawl, each with only a raw \
issue COUNT — no specific issue names are available for these pages (that detail lives on a separate site-wide \
slide).

For each page listed below, write one short, specific, actionable fix inferred from its URL pattern and issue \
count (e.g. "audit meta tags and internal links on this post", "check page load speed and structured data on \
this product page"). Never invent a specific issue type or number that isn't given — infer only from the URL's \
apparent page type (blog post, product page, job listing, webinar, case study, etc.) and how many issues it has. \
Do not write a generic disclaimer.

Write in plain, confident agency language — this is client-facing content. Never mention that you are an AI, a \
language model, or any tool by name.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "fixes": {{"<page path>": "<fix text>"}}
}}

PAGES:
{shown_pages}
"""


def generate_page_wise_priority_content(shown: list[dict]) -> dict:
    """shown: [{"page": str, "issue_count": int}, ...]. Returns
    {"fixes": {page: str}} or {"error": str}."""
    if not shown:
        return {"error": "No pages to analyze"}

    prompt = PAGE_WISE_PRIORITY_PROMPT.format(shown_pages=json.dumps(shown, indent=2))
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
    if not data.get("fixes"):
        return {"error": "Model returned no fixes"}
    return data
