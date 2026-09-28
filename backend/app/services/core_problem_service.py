"""Synthesizes everything already gathered for the report (crawl issues,
backlink stats, competitor gap findings) into ONE diagnostic "Core Problem"
thesis plus the 2-3 most consequential confirmed problems, for the report's
executive-summary slide. Not cached: unlike Company Overview (a static
description), this reflects CURRENT metrics/issues, and a cached diagnosis
would go stale exactly when a client has fixed something.

2026-09-28 redesign: the slide previously matched a SPOTONIX manual-report
format of 3 fixed categories (On-page/Off-page/Content & Keyword Strategy)
each with 2-4 findings, up to ~12 total. The report's own final-
implementation rule for this slide ("show only the 2-3 most consequential
confirmed problems... Core Problem summarizes established evidence, it
does not create new evidence") explicitly conflicts with that format, so
the output is now a flat, ranked list instead of a category breakdown —
user decision, not a unilateral rewrite."""

import json
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

CORE_PROBLEM_PROMPT = """You are a senior SEO strategist writing the "Core Problem" slide for a client-facing \
Web & SEO Audit report — the single diagnostic thesis explaining why the site isn't ranking or converting as well \
as it could, based ONLY on the findings below. Never invent a finding that isn't present in the data.

Write ONE thesis sentence (the root cause, plain confident agency language, no hedging like "may" or "could"), \
then identify the 2-3 MOST CONSEQUENTIAL confirmed problems across the entire audit — on-page, off-page, and \
content/keyword issues all compete for the same 2-3 slots, ranked by real impact (how much traffic/how many \
pages/keywords/rankings it affects, or how severe the confirmed issue is), never one mandatory pick per topic \
area. If the evidence genuinely only supports 2 solid, consequential problems, return 2 — never pad to 3 with a \
weaker or less-supported finding. "sample_page_level_issues"/"homepage_issues" cover only a small \
sample of pages (pages_checked) — never generalize them to "every page" or "all pages". For any site-wide \
technical/on-page issue count, use "technical_issues_full_crawl" when present instead of the small sample (e.g. \
"47 errors and 112 warnings across 823 crawled pages") — it's the same real multi-page crawl the report's own SEO \
Issues slide is built from, and is more accurate than the small sample. For any site-wide structured data / schema \
claim use \
"structured_data_full_crawl" when present (e.g. "26 of 823 crawled pages carry schema; FAQPage only"), and if it \
shows schema on some pages, never say the site has none.

Any number already stated in a finding (a keyword-gap count, a backlink count, an issue count, etc.) must be \
copied EXACTLY as given, never recalculated or combined with another number in the same finding. In particular, a \
count of items EXCLUDED from a total ("X off-topic/competitor-brand excluded") is not part of that total and must \
never be added back into it — if a finding says "301 relevant keyword gap(s) ... (14 off-topic/competitor-brand \
excluded)", the only valid total to cite is 301, never 315.

Avoid unsupported causal claims the data doesn't actually establish — never write "poor rankings stem from...", \
"this prevents rankings...", "this causes...", or "this will improve rankings...". State what the audit found, not \
a causal chain it can't prove: prefer phrasing like "The audit identified...", "Organic visibility is currently \
constrained by...", "The site currently has...", or "This represents a gap in...".

Return ONLY valid JSON, no markdown fences, no commentary:
{
  "thesis": string,
  "problems": [string]
}
"problems" must have exactly 2 or 3 items, ranked most consequential first.

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
    if isinstance(data.get("problems"), list):
        data["problems"] = [p for p in data["problems"] if p and not _leaks_internal_field(p)][:3]
    return data


# The findings JSON is keyed by internal field names; the model sometimes
# narrates a missing one instead of skipping it — confirmed real on a
# BharatBenz deck (2026-09-23): "backlink_summary returned null — no
# anchor/text or domain-quality data available". Such a point is about the
# report's own inputs, not the client's site, so it's dropped.
_INTERNAL_FIELD_RE = re.compile(r"(?<![/.\-])\b[a-z]+(?:_[a-z]+)+\b(?![/.\-]\w)|\breturned (?:null|none|empty)\b|\bis null\b", re.IGNORECASE)


def _leaks_internal_field(point) -> bool:
    text = str(point or "")
    return any(m.group(0).lower() not in _ALLOWED_UNDERSCORE_TERMS for m in _INTERNAL_FIELD_RE.finditer(text))


_ALLOWED_UNDERSCORE_TERMS: set[str] = set()


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
