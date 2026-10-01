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
from app.integrations.ai_usage import parse_json
from app.services.prompt_json import compact_json

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

Keyword-gap metrics come ONLY from "keyword_gap" (the report's single authoritative Competitor Keyword Gap \
dataset): total_relevant, missing, shared, untapped, search_volume (combined monthly searches), off_topic_excluded. \
Cite them exactly as given — never recount them, never take them from any other finding. If "keyword_gap" is null, \
state no keyword-gap count at all.

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
    # Shared JSON validation + repair (ai_usage.parse_json): fences, stray
    # prose around the object, and answers cut off mid-JSON.
    data, _repaired = parse_json(raw)
    if data is None:
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


# CORE PROBLEM — METRIC SOURCE RULE (2026-09-28 team review): the prompt
# asks for exact copying, but the model can still recount or recall a
# number. Any number the output ties to keywords/searches must be one of
# the authoritative keyword_gap values (or a number another, non-gap
# finding actually states, e.g. "ranks for 1,200 more keywords"). A problem
# citing any other keyword number is dropped rather than published.
_GAP_METRIC_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(%)?\s+(?:[A-Za-z/'-]+\s+){0,3}?"
    r"(?:keywords?|keyword gaps?|monthly searches|searches|search volume)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _to_number(text: str) -> float:
    return float(text.replace(",", ""))


def _allowed_gap_numbers(findings: dict) -> tuple[set[float], set[float]]:
    """(plain numbers, percentages) the output may tie to keywords."""
    gap = findings.get("keyword_gap") or None
    plain: set[float] = set()
    pcts: set[float] = set()
    if gap:
        plain |= {float(v) for v in gap.values() if isinstance(v, (int, float))}
        total = gap.get("total_relevant") or 0
        if total:
            pcts |= {float(round(100 * gap.get(k, 0) / total)) for k in ("missing", "shared", "untapped")}
    if findings.get("target_keyword_count"):
        plain.add(float(findings["target_keyword_count"]))
    # Numbers other (non-keyword-gap) findings genuinely state.
    other = {k: v for k, v in findings.items() if k not in ("keyword_gap", "competitor_gap_findings")}
    other_gap_findings = [f for f in findings.get("competitor_gap_findings") or [] if f.get("type") != "keyword_gap"]
    for m in _NUMBER_RE.finditer(json.dumps([other, other_gap_findings], default=str)):
        plain.add(_to_number(m.group(0)))
    return plain, pcts


def _violates_gap_metrics(text: str, allowed: tuple[set[float], set[float]]) -> bool:
    plain, pcts = allowed
    for m in _GAP_METRIC_RE.finditer(text or ""):
        value = _to_number(m.group(1))
        if (value not in pcts) if m.group(2) else (value not in plain):
            return True
    return False


def _enforce_gap_metrics(data: dict, findings: dict) -> dict | None:
    """Drops problems with an unsourced keyword-gap number. None when the
    thesis itself carries one, or fewer than 2 problems survive."""
    allowed = _allowed_gap_numbers(findings)
    if _violates_gap_metrics(data.get("thesis", ""), allowed):
        return None
    original = data.get("problems")
    if not isinstance(original, list):
        return data
    problems = [p for p in original if not _violates_gap_metrics(str(p), allowed)]
    if len(problems) < len(original) and len(problems) < 2:
        return None
    return {**data, "problems": problems}


def generate_core_problem(findings: dict) -> dict:
    """Returns {"thesis": str, "categories": [...]} or {"error": str}.

    Takes up to two responses from the selected Report AI Provider (same fix as
    structured_data_insights_service, 2026-09-20), not just the first one
    to answer — confirmed real 2026-09-22: with OpenRouter added as a
    fallback provider, its auto-router can land on a free model that
    doesn't reliably follow "return ONLY valid JSON," and generate_text()'s
    own cross-provider fallback only triggers on a transport-level
    failure, never on "the provider answered but wasn't parseable JSON" —
    so a stricter provider later in the order never got a chance."""
    prompt = CORE_PROBLEM_PROMPT.replace("{findings}", compact_json(findings, default=str)[:6000])
    errors: list[str] = []
    last_raw = ""
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=1024, errors=errors):
            last_raw = raw
            data = _parse(raw)
            if data is None:
                errors.append(f"{provider} did not return valid JSON")
                continue
            checked = _enforce_gap_metrics(data, findings)
            if checked is not None:
                return checked
            errors.append(f"{provider} cited a keyword-gap number not in the authoritative dataset")
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    return {"error": " | ".join(errors) if errors else "Model returned no thesis", "raw": last_raw[:500]}
