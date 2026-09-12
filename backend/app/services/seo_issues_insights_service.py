"""SEO Issues slide AI insights — turns the raw Errors/Warnings rollup
(shown on the slide's own table) into a client-facing headline + root-cause
analysis + one-line executive takeaway. Prompt structure is user-specified
(2026-09-10 spec), not a house template like the other *_service.py files.

errors/warnings passed in here must be the SAME classification the slide
itself renders (see pptx_builder.classify_seo_issues) — otherwise the AI
could reason about numbers the reader never actually sees on the slide."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

SEO_ISSUES_INSIGHTS_PROMPT = """You are an SEO analyst writing insights for a client audit report. I will give \
you a list of SEO errors and warnings with the number of affected pages, plus the total crawled pages and total \
pages with at least one issue.

Turn this raw data into a client-facing "Insights" section with the following structure:
Headline insight - One sentence stating the % and count of pages affected, framed as a severity statement \
(systemic vs. isolated).
Supporting insights (3-4 bullets) - For the highest-impact errors/warnings, don't just restate the number. \
Explain the likely root cause (e.g., templating issue, migration artifact, shared theme/script, URL parameters) \
and why it matters for rankings, crawl budget, UX, or Core Web Vitals. Group related issues together where they \
likely share a cause.
One-line executive takeaway — A single sentence a non-technical stakeholder could read to understand priority \
and framing (site-wide root cause vs. page-by-page fixes).

Keep the tone consultative, not just descriptive — infer why the issue is happening, not just that it's \
happening. Avoid generic SEO advice; ground every insight in the specific numbers given. Never mention that you \
are an AI, a language model, or any tool by name.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "headline": string,
  "supporting": [string],
  "takeaway": string
}}

Total crawled pages: {total_pages}
Total pages with at least one issue: {pages_with_issues}

Errors:
{errors}

Warnings:
{warnings}
"""


def generate_seo_issues_insights(
    errors: list[dict], warnings: list[dict], total_pages: int | None, pages_with_issues: int | None
) -> dict:
    """errors/warnings: [{"issue": str, "pages": int}, ...] — same shape
    classify_seo_issues returns. Returns {"headline": str, "supporting":
    [str], "takeaway": str} or {"error": str}."""
    if not errors and not warnings:
        return {"error": "No errors or warnings to analyze"}

    prompt = SEO_ISSUES_INSIGHTS_PROMPT.format(
        total_pages=total_pages if total_pages is not None else "unknown",
        pages_with_issues=pages_with_issues if pages_with_issues is not None else "unknown",
        errors=json.dumps(errors, indent=2) if errors else "(none)",
        warnings=json.dumps(warnings, indent=2) if warnings else "(none)",
    )
    try:
        raw, _provider = generate_text(prompt, max_tokens=768)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Groq's gpt-oss-120b sometimes prepends stray commentary before the
        # JSON object despite the "return ONLY valid JSON" instruction — see
        # the same fallback in competitor_narrative_service.py.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {"error": "AI did not return valid JSON", "raw": raw[:500]}
        else:
            return {"error": "AI did not return valid JSON", "raw": raw[:500]}
    if not data.get("headline"):
        return {"error": "Model returned no headline"}
    return data
