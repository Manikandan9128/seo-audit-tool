"""Structured Data & Schema Validator slide's Key Insights section,
2026-09-20 user spec. Part 1/Part 2 table numbers are computed
deterministically (pptx_builder.build_schema_report_parts) — this only
writes the consultative prose grouping those exact numbers by shared root
cause, it never derives or invents a number itself."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

STRUCTURED_DATA_INSIGHTS_PROMPT = """You are an SEO consultant preparing a "Structured data & schema validator" \
slide for a client audit report. Below are two tables already computed from a real crawl + validation pass — \
treat every number in them as ground truth, never invent a schema type, page count, or pageview number not \
present in this data. There is no separate Action column — your Key Insights are the only place fixes appear.

Part 1 — Applicable schema by page type:
{part1}

Part 2 — Validation results (Schema Type | Applicable | Present | Valid | Invalid | Missing | Coverage %). \
These five statuses are NOT interchangeable — read them exactly:
- Missing: applicable but not detected at all.
- Present: schema block exists (Present count includes both Valid and Invalid pages).
- Valid: schema exists and passes the available validation checks.
- Invalid: schema exists but fails a required-field check.
- For WebSite/Organization, Applicable reads "Site-level" and Present/Valid read Yes/No/"—" instead of a page \
count — these are entity-level facts, never write "N pages are missing WebSite schema"; write that the site-\
level schema itself is absent.
The denominator for Coverage % is always the pages that type applies to, never total site pages; baseline \
site-wide schema is never blended into a content type's coverage. Zero Invalid does NOT mean Valid — always \
check whether the row is actually a Missing case (Invalid=0 AND Missing=Applicable) before writing an "invalid \
schema" insight.

Pageviews affected per page type (0 means no analytics data was available, not zero real traffic):
{pageviews}

Google rich-result eligibility notes (only listed for a type that is NOT fully eligible — retired, restricted, \
or unverified; a type with no note here is assumed eligible):
{eligibility_notes}

Write as many Key Insights bullets as the data actually supports (typically 3-5), each covering ONE schema gap \
or ONE confirmed win. Every bullet must follow ISSUE -> EVIDENCE -> ACTION:
- ISSUE: name the schema type and whether it's Missing, Invalid, or a confirmed Valid win.
- EVIDENCE: cite the exact Applicable/Present/Valid/Invalid/Missing numbers (or Yes/No for site-level) behind it.
- ACTION: for a gap, end with "Fix: ..." naming the concrete implementation step (e.g. "Fix: add Article schema \
to the blog template."). For a confirmed win (Invalid=0 and Coverage=100%, or a site-level type present and \
valid), state it as a win — no fix needed, don't invent one.
- Never write an insight for a row that's simply Applicable=0 or not present in the tables above.
- Call out any page bucket correctly excluded from the coverage math (e.g. "Other Pages" with no content-\
specific schema requirement) as NOT a gap, not as a finding needing a bullet.
- Do not create a bullet for every table row — only surface rows with a real gap or a real win; skip rows with \
nothing meaningful to say.

Wording rules — never write an unsupported outcome claim. Banned phrasings (and their equivalents): "is \
preventing rankings", "will increase rankings", "will increase CTR", "is hurting traffic", "improves crawl \
efficiency", "increases brand authority", "cannot rank without this". Use evidence-based phrasing instead, e.g. \
"the structured-data pathway for applicable rich-result eligibility is currently absent" — and respect the \
eligibility notes above; never claim a rich-result or CTR benefit for a type flagged as ineligible or retired \
there, or for FAQPage (Google retired that SERP dropdown) or a type with no eligibility relationship established.

Never invent traffic numbers or issue types — infer only from the numbers given. Write in plain, confident \
agency language — this is client-facing content, not an AI-generated draft. Never mention that you are an AI, a \
language model, or any tool by name. JobPosting only ever appears in the tables above when at least one crawled \
URL is an actual individual job-detail page (not a careers index/listing page) — treat it exactly like any other \
page-level schema type when it does appear.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "insights": [string]
}}
"""


def _eligibility_clause(schema_type: str, eligibility_notes: dict[str, str]) -> str:
    note = eligibility_notes.get(schema_type)
    if not note:
        return ""
    if note.startswith("ELIGIBILITY_CHECK_STALE"):
        return " (rich-result eligibility for this type hasn't been verified against Google's current docs)"
    return f" ({note})"


def _deterministic_schema_insights(part2: list[dict], eligibility_notes: dict[str, str]) -> list[str]:
    """Non-AI fallback so Key Insights can never fully disappear (2026-09-20
    spec section 30: "MUST NOT disappear ... even when coverage is 0%").
    Mechanically derived straight from part2's own Missing/Invalid/Present/
    Valid/Coverage numbers — same ISSUE->EVIDENCE->ACTION shape and wording
    rules as the AI prompt above (no unsupported ranking/CTR claims, respects
    eligibility_notes; JobPosting is treated like any other page-level type,
    appearing only when part2 itself has a row for it). Used only when every configured AI provider
    failed or is unconfigured — real numbers only, nothing invented.
    Ordered gaps first (largest-affected first), then invalid-schema
    findings, confirmed wins last, capped to 4 per spec's own "2-4 concise
    insights" guidance."""
    items: list[tuple[int, int, str]] = []
    for row in part2:
        schema_type = row["schema_type"]
        clause = _eligibility_clause(schema_type, eligibility_notes)
        if row.get("site_level"):
            if row.get("present") == "No":
                items.append((0, 0, (
                    f"{schema_type} schema gap — no {schema_type} structured data was detected at the site "
                    f"level{clause}. Fix: implement {schema_type} structured data in the global site template."
                )))
            elif row.get("valid") == "No":
                items.append((1, 0, (
                    f"{schema_type} schema validation issue — present at the site level but fails a required-"
                    f"field check. Fix: correct the identified {schema_type} structured data errors and revalidate."
                )))
            elif row.get("present") == "Yes" and row.get("valid") == "Yes":
                items.append((3, 0, f"{schema_type} schema — confirmed present and valid at the site level. No fix needed."))
            continue

        applicable = row.get("applicable") or 0
        if not applicable:
            continue
        missing = row.get("missing") or 0
        invalid = row.get("invalid") or 0
        present = row.get("present") or 0
        coverage = row.get("coverage_pct")

        if missing and present == 0:
            items.append((0, -missing, (
                f"{schema_type} schema gap — {missing} applicable page(s) have no {schema_type} structured data "
                f"detected{clause}. Fix: implement {schema_type} schema through the relevant page template."
            )))
        elif invalid:
            items.append((1, -invalid, (
                f"{schema_type} schema validation issue — {invalid} of {present} page(s) with {schema_type} "
                f"markup contain validation errors. Fix: correct the identified {schema_type} structured data "
                "errors and revalidate the affected pages."
            )))
        elif invalid == 0 and coverage == 100:
            items.append((3, 0, f"{schema_type} schema — confirmed valid on all {applicable} applicable page(s) (Coverage: 100%). No fix needed."))

    items.sort(key=lambda it: (it[0], it[1]))
    return [text for _rank, _impact, text in items][:4]


def generate_structured_data_insights(
    part1: list[dict], part2: list[dict], pageviews_by_page_type: dict[str, int], eligibility_notes: dict[str, str]
) -> dict:
    """Returns {"insights": [str]} or {"error": str}.

    Tries every configured provider in order (2026-09-20 fix — confirmed
    real on two consecutive reports, Lumber and BharatBenz: Groq, first in
    the default order, returned syntactically valid JSON with an empty
    `{"insights": []}` for a report whose schema data had obvious real
    gaps, and generate_text()'s own cross-provider fallback only triggers
    on a transport-level failure, never on "the provider answered but my
    own parse of it came back empty" — so Gemini, which DOES handle this
    prompt correctly, never even got tried). Now keeps trying the next
    provider until one returns a non-empty insights list or a parseable-
    but-still-empty result, or every provider is exhausted. If every
    provider still fails, falls back to _deterministic_schema_insights
    (2026-09-20 spec section 30: Key Insights must never fully disappear)
    rather than surfacing an AI-failure error with no insights at all —
    only returns {"error": ...} when even that deterministic pass finds
    nothing meaningful to say (e.g. every schema type is Not Applicable)."""
    if not part1 and not part2:
        return {"error": "No schema data to analyze"}

    prompt = STRUCTURED_DATA_INSIGHTS_PROMPT.format(
        part1=json.dumps(part1, indent=2),
        part2=json.dumps(part2, indent=2),
        pageviews=json.dumps(pageviews_by_page_type, indent=2) if pageviews_by_page_type else "(no analytics data)",
        eligibility_notes=json.dumps(eligibility_notes, indent=2) if eligibility_notes else "(all types fully eligible)",
    )
    errors: list[str] = []
    empty_from: list[str] = []
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=1024, errors=errors):
            cleaned = raw.strip()
            cleaned = re.sub(r"^```(json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                errors.append(f"{provider} returned invalid JSON: {cleaned[:200]}")
                continue
            if data.get("insights"):
                return data
            empty_from.append(provider)
    except NoAIProviderConfigured:
        pass  # fall through to the deterministic pass below

    fallback = _deterministic_schema_insights(part2, eligibility_notes)
    if fallback:
        return {"insights": fallback, "fallback": True}
    if empty_from:
        return {"error": f"Model returned no insights (tried: {', '.join(empty_from)})"}
    return {"error": " | ".join(errors) if errors else "Model returned no insights"}
