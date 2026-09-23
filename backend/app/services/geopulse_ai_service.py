"""Turns raw GeoPulse export text (see geopulse_parser.py) into grounded
content for the AEO and GEO Next Steps slides. Before this, both slides were
static boilerplate identical across every client report (same bullets
regardless of the site) — this makes them reflect the client's actual
GeoPulse findings (AI-visibility mentions, citation gaps, answer-engine
presence, etc.) once a GeoPulse file is uploaded. It is the ONLY source of
AEO/GEO recommendations (spec 2026-09-23): with no file, or no usable
result, pptx_builder renders add_aeo_geo_visibility_required_slide instead
of generic AI-search advice."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are an SEO strategist analyzing a GeoPulse AI-visibility report for a client's \
website. GeoPulse tracks how a brand shows up in AI answer engines (ChatGPT, Perplexity, Google AI \
Overviews, etc.) — citation frequency, sentiment, competitor mentions, and content gaps.

Below is the raw export content. Read it and produce two short lists of specific, actionable \
recommendations grounded ONLY in what this data actually shows — never invent a statistic, prompt, \
competitor name, mention rate, citation count, or finding that isn't in the text below.

CORE RULE: do not infer a causal relationship between an observed visibility gap and a specific \
SEO/GEO tactic unless the data itself provides evidence for that relationship. Reason through each \
recommendation internally as OBSERVATION (what the data shows) -> GAP (what topic/entity/use case/ \
prompt type/citation opportunity is missing, based on that evidence) -> ACTION (what to create, \
improve, structure, or strengthen) — but output only the final 1-2 sentence recommendation, not the \
three labeled steps.

GeoPulse's own export sometimes includes a data-quality warning of its own — e.g. "Automated \
consistency check failed" or a line stating one number "doesn't match the dashboard's" \
independently-computed value. Treat that as authoritative: NEVER cite the disputed/flagged number \
as settled fact. When the export also states the dashboard's corrected value, use that corrected \
value instead, phrased exactly as: "Dashboard baseline: {{dashboard_value}}; exported report value: \
{{disputed_value}} — consistency validation required." If no corrected value is given, drop that \
specific metric from your output rather than repeating the disputed number.
{disputed_metrics_note}
- "aeo_items": Answer Engine Optimization — answerable informational queries, prompt-level content \
gaps, structured/clear answer formats where genuinely applicable, direct question coverage, and \
entity/product explanations.
- "geo_items": Generative Engine Optimization — entity visibility, unbranded discovery, topical \
coverage, external sources/citations, brand/entity associations, and competitive presence in AI \
responses.

Do NOT put the same recommendation on both lists unless the underlying evidence is genuinely \
different for each — AEO and GEO must stay meaningfully differentiated, not two phrasings of the \
same finding.

3-5 items per list (fewer if the data doesn't support more — never pad to reach 3), each 1-2 \
sentences, following the shape [ACTION] + [SPECIFIC TOPIC/ENTITY] + [EVIDENCE-BASED REASON]. Never a \
generic "create more content" — name the actual missing topics/subjects/entities/prompts found in \
the data. When a cluster shows low/zero visibility, inspect the actual prompts where the brand was \
not mentioned and build the recommendation from the real subjects those prompts cover.

Example — unbranded cluster with named missed-prompt subjects:
GOOD: "Create practical fleet-operation resources covering tipper capacity, fuel efficiency, \
servicing, telematics and vehicle selection to address the unbranded Workflow / How-To visibility \
gap (0/10 mentioned)."
BAD: "Create more content." / "Implement FAQ schema to increase AI answer-box visibility."

Example — branded cluster with one missed prompt (high mention rate elsewhere):
GOOD: "Expand authoritative Truckonnect content covering its purpose, features and use cases to \
address the single missed branded query (\"What is Truckonnect and how does it work?\") in an \
otherwise 9/10 mentioned cluster."
Never describe the AI engine as having "failed", and never guess why that one prompt was missed.

Example — citation/source data showing zero citations in this run:
GOOD: "No web sources were cited for the client's domains in this report run; strengthen \
authoritative third-party references and citations around key commercial-vehicle topics."
BAD: "The brand has zero web citations on the internet." (the dataset only reflects this report run)

SCHEMA CLAIMS: only recommend a specific schema type (FAQPage, HowTo, Product, Article, etc.) when \
the data shows an actual page/content type it would apply to — never merely because a cluster shows \
0% visibility. Never claim schema will increase AI answer-box eligibility, improve AI parsing, cause \
generative visibility, or increase citations — "eligibility"/"prerequisite" framing only, never a \
promised outcome.

BANNED phrasing anywhere in either list (do not use, in any tense/wording): "will increase \
[visibility/citations/ranking/discovery/answer-box eligibility]", "will improve AI parsing", "will \
guarantee", "guarantees [a result]". Use instead: "to address...", "to strengthen coverage of...", \
"to improve completeness around...", "to support clearer entity understanding...", "to address the \
observed visibility gap...", "to strengthen external source coverage...".

For unbranded clusters, do not simply recommend adding the brand name to pages — prioritize missing \
topics/use cases/entities/comparisons/product coverage/informational content/citations instead.

COMPETITORS: name only competitors that actually appear in this export. Never bring in a competitor \
from anywhere else.

SCHEMA: do not recommend any schema/structured-data markup at all — this export carries no \
technical/schema applicability data, and schema is covered by the report's Technical SEO and \
Structured Data slides.

URLS: never write a URL unless it appears verbatim in the export below.

CAUSES: never state or guess why the AI engine did or didn't mention the brand ("was not mentioned \
because..."). Describe what the check shows ("The visibility check shows...", "The tested prompts \
indicate...", "The cluster recorded...").

GENERIC (never output): "create more content", "optimize for ChatGPT", "improve AI visibility", "add \
schema to all pages", "use more keywords", "build backlinks".

GeoPulse export content:
{raw_text}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{"aeo_items": [string], "geo_items": [string]}}
"""

# GeoPulse's own "Automated consistency check failed" banner names the exact
# disputed headline value and the dashboard's independently-computed
# correction in one sentence (confirmed real, e.g. "Report organic discovery
# (1.1%) doesn't match the dashboard's (0.0%)."). Pulling these out here and
# handing them to the prompt explicitly is more reliable than trusting the AI
# to notice one warning line buried in a long raw export — this is GeoPulse's
# own fixed banner wording, not something that varies with the rest of the
# export's format (which genuinely can be anything, per geopulse_parser.py).
_DISPUTED_METRIC_RE = re.compile(
    r"([A-Za-z][A-Za-z /]*?)\s*\(([\d.]+%?)\)\s*doesn'?t match the dashboard'?s[^(\n]*\(([\d.]+%?)\)",
    re.IGNORECASE,
)


def _find_disputed_metrics(raw_text: str) -> list[tuple[str, str, str]]:
    """[(metric_label, disputed_value, dashboard_value), ...] pulled from a
    GeoPulse "consistency check failed" banner, if present. Empty when the
    export has no such warning."""
    return [(label.strip(), disputed.strip(), corrected.strip()) for label, disputed, corrected in _DISPUTED_METRIC_RE.findall(raw_text or "")]


# 2026-09-21 spec rule 8 — mechanical backstop, not just a prompt request:
# an LLM can still slip into causal phrasing despite the prompt's explicit
# ban, so every returned item is checked here too. Matches the exact
# BAD-example shapes from the spec ("will increase/improve/boost/cause/
# drive/generate/ensure [outcome]") case-insensitively; a bare "guarantee"
# is only flagged when it's NOT part of an explicit disclaimer ("not a
# guarantee", "no guarantee") — that phrasing is the compliant, required
# framing ("not a guarantee of inclusion"), not a
# claim, and must never be stripped.
_CAUSAL_CLAIM_RE = re.compile(
    r"\bwill (?:definitely |certainly |likely )?(?:increase|improve|boost|cause|drive|generate|ensure)\b",
    re.IGNORECASE,
)
_BARE_GUARANTEE_RE = re.compile(r"(?<!not a )(?<!not an )(?<!no )\bguarantees?\b", re.IGNORECASE)


def _has_unsupported_causal_claim(item: str) -> bool:
    return bool(_CAUSAL_CLAIM_RE.search(item) or _BARE_GUARANTEE_RE.search(item))


def _drop_unsupported_claims(items: list[str], list_name: str) -> list[str]:
    kept = []
    for item in items:
        if _has_unsupported_causal_claim(item):
            logger.warning("GeoPulse %s item dropped for unsupported causal claim: %s", list_name, item[:200])
            continue
        kept.append(item)
    return kept


# 2026-09-23 AEO/GEO spec sections 11-13, 16 — mechanical backstops for the
# prompt rules above, same reasoning as _CAUSAL_CLAIM_RE: an LLM can slip
# despite the instruction, so the output is checked too.
_GENERIC_REC_RE = re.compile(
    r"^\W*(create more content|optimi[sz]e for chatgpt|improve (your |the )?ai visibility|add schema to all pages|"
    r"use more keywords|build (more )?backlinks)\W*$",
    re.IGNORECASE,
)
_SCHEMA_REC_RE = re.compile(r"\b(schema|structured data|json-ld|faqpage|howto markup|markup)\b", re.IGNORECASE)
_ASSUMED_CAUSE_RE = re.compile(
    r"\b(was|were|is|are|wasn'?t|weren'?t|isn'?t|aren'?t)\s+(not\s+)?(mentioned|cited|recommended)\s+because\b"
    r"|\bfailed to (mention|cite|recommend)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s)\]\"']+|\bwww\.[^\s)\]\"']+", re.IGNORECASE)
_MAX_ITEMS = 5


def _item_violation(item: str, raw_text: str) -> str | None:
    if _GENERIC_REC_RE.search(item):
        return "generic recommendation"
    if _SCHEMA_REC_RE.search(item):
        return "schema recommendation without applicability data"
    if _ASSUMED_CAUSE_RE.search(item):
        return "assumed cause"
    raw_lower = (raw_text or "").lower()
    for url in _URL_RE.findall(item):
        if url.rstrip(".,;").lower() not in raw_lower:
            return f"URL not in the visibility report ({url})"
    return None


def _drop_spec_violations(items: list[str], list_name: str, raw_text: str) -> list[str]:
    kept = []
    for item in items:
        reason = _item_violation(item, raw_text)
        if reason:
            logger.warning("GeoPulse %s item dropped (%s): %s", list_name, reason, item[:200])
            continue
        kept.append(item)
    return kept


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3}


def _drop_cross_list_duplicates(aeo_items: list[str], geo_items: list[str]) -> list[str]:
    """Section 3: a GEO item that restates an AEO item (>= 80% shared
    significant words) is the same recommendation twice — kept on AEO only."""
    kept = []
    for g in geo_items:
        gt = _tokens(g)
        if any(gt and len(gt & _tokens(a)) / max(1, min(len(gt), len(_tokens(a)))) >= 0.8 for a in aeo_items):
            logger.warning("GeoPulse geo_items item dropped (duplicates an AEO item): %s", g[:200])
            continue
        kept.append(g)
    return kept


def generate_aeo_geo_content(raw_text: str) -> dict:
    """Returns {"aeo_items": [...], "geo_items": [...]}. Empty dict on any
    failure (no AI key, bad JSON, empty input) — caller falls back to the
    existing generic slide content, same as when no GeoPulse file is
    uploaded at all.

    2026-09-10: GeoPulse can flag its own headline numbers as disputed
    (an "Automated consistency check failed" banner naming a mismatch with
    its dashboard) — confirmed real on a Lumber export where the AEO/GEO
    slide cited the flagged 1.1% organic-discovery figure as plain fact with
    no caveat, even though the export itself said the real value was 0.0%.
    Any disputed metrics found are named explicitly in the prompt (with the
    dashboard's corrected value) so the AI uses the correction instead of
    repeating the flagged number."""
    if not raw_text or not raw_text.strip():
        return {}
    disputed = _find_disputed_metrics(raw_text)
    if disputed:
        lines = "\n".join(
            f'- "{label}": the export\'s own headline says {disputed_val}, but its consistency check '
            f"says the dashboard's real value is {dashboard_val}. Use {dashboard_val}, never {disputed_val}."
            for label, disputed_val, dashboard_val in disputed
        )
        disputed_note = f"\nThis export flagged these specific metrics as disputed:\n{lines}\n"
    else:
        disputed_note = ""
    prompt = PROMPT_TEMPLATE.format(raw_text=raw_text, disputed_metrics_note=disputed_note)
    # Tries every configured provider in order, not just the first one to
    # answer (2026-09-22, same fix as structured_data_insights_service,
    # 2026-09-20; see core_problem_service.generate_core_problem's
    # docstring for why).
    errors: list[str] = []
    data = None
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=4096, errors=errors):
            cleaned = raw.strip()
            cleaned = re.sub(r"^```(json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("GeoPulse AEO/GEO content: %s returned invalid JSON: %s", provider, cleaned[:300])
                continue
            if not isinstance(parsed, dict):
                continue
            data = parsed
            break
    except NoAIProviderConfigured as e:
        logger.warning("GeoPulse AEO/GEO content generation failed: %s", e)
        return {}
    if data is None:
        return {}

    aeo_items = [str(x).strip() for x in (data.get("aeo_items") or []) if str(x).strip()]
    geo_items = [str(x).strip() for x in (data.get("geo_items") or []) if str(x).strip()]
    aeo_items = _drop_unsupported_claims(aeo_items, "aeo_items")
    geo_items = _drop_unsupported_claims(geo_items, "geo_items")
    aeo_items = _drop_spec_violations(aeo_items, "aeo_items", raw_text)[:_MAX_ITEMS]
    geo_items = _drop_cross_list_duplicates(aeo_items, _drop_spec_violations(geo_items, "geo_items", raw_text))[:_MAX_ITEMS]
    if not aeo_items and not geo_items:
        return {}
    return {"aeo_items": aeo_items, "geo_items": geo_items}
