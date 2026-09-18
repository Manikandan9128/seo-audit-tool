"""AI-based topic clustering for Target Keywords. Semrush's Keyword Gap
export (unlike some of its other tools) carries no Cluster/Topic column at
all, so a real client's keyword list otherwise renders as one flat,
unsorted table instead of the manual report's grouped-by-topic slides
(confirmed against a real Lumber export — no Cluster/Topic/Group column
anywhere in the header). Pure classification of already-uploaded
keywords into topic labels — never invents new keywords, volumes, or any
other data, only groups what's already there."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

logger = logging.getLogger(__name__)

PROMPT_TEMPLATE = """Group the following SEO keywords into topic clusters - the same kind of \
grouping an SEO strategist uses to organize a keyword research report by theme (e.g. \
"Unemployment Insurance", "Redundancy Insurance"). Use as many or as few clusters as the real \
distinct themes in this list actually require - never force a fixed number of clusters, don't \
force unrelated keywords together, and don't invent a cluster for just one keyword unless it \
truly doesn't fit anywhere else. Every keyword in the list must be assigned to exactly one \
cluster. Cluster names should be short (2-4 words) and specific to the actual themes present in \
this list - never invent a keyword that isn't below.

Keywords:
{keyword_list}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
[{{"cluster": string, "keywords": [string]}}]
"""


def generate_candidate_clusters(keywords: list[str], context_hint: str | None = None) -> dict[str, str]:
    """Candidate clustering scoped to a single (business_theme, search_intent,
    page_category) bucket — the FINAL PIPELINE's "which keywords can
    realistically be satisfied by the SAME page?" step. Reuses
    generate_keyword_clusters' exact call/parse/fail-open behavior; the only
    difference is an optional `context_hint` (e.g. "business theme:
    Certified Payroll; search intent: Informational; page format: Blog /
    Guide") that tells the AI these keywords already share theme, intent,
    and page format, so it should only split further on genuine semantic/
    pattern differences (e.g. "certified payroll" vs. "certified payroll
    services" vs. "how certified payroll works" within the same bucket),
    not re-decide the bucket boundary itself - that boundary is enforced
    deterministically by the caller regardless of what the AI returns."""
    if not keywords:
        return {}
    if len(keywords) == 1:
        return {}
    prompt = PROMPT_TEMPLATE.format(keyword_list="\n".join(f"- {k}" for k in keywords))
    if context_hint:
        prompt = (
            f"These keywords already share the same {context_hint}, so a single page could plausibly "
            "satisfy all of them - only split further if genuinely different sub-topics or page needs "
            "are present within that shared context.\n\n"
        ) + prompt
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        logger.warning("Candidate keyword clustering AI call failed: %s", e)
        return {}
    return _parse_cluster_response(raw, keywords)


def generate_keyword_clusters(keywords: list[str]) -> dict[str, str]:
    """Returns {keyword: cluster_label}. Empty dict on any failure (no AI
    key configured, bad JSON, empty list) - caller falls back to the
    existing flat, ungrouped table, same behavior as when no Cluster
    column exists at all. Keywords the AI didn't echo back verbatim are
    silently dropped from the mapping rather than trusted, so a
    hallucinated/altered keyword can't attach to a real row."""
    if not keywords:
        return {}
    prompt = PROMPT_TEMPLATE.format(keyword_list="\n".join(f"- {k}" for k in keywords))
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        logger.warning("Keyword clustering AI call failed: %s", e)
        return {}
    return _parse_cluster_response(raw, keywords)


def _parse_cluster_response(raw: str, keywords: list[str]) -> dict[str, str]:
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        clusters = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Keyword clustering returned invalid JSON: %s", raw[:300])
        return {}
    if not isinstance(clusters, list):
        logger.warning("Keyword clustering returned non-list JSON: %s", raw[:300])
        return {}

    keyword_set = set(keywords)
    mapping: dict[str, str] = {}
    for group in clusters:
        if not isinstance(group, dict):
            continue
        label = group.get("cluster")
        if not label:
            continue
        for kw in group.get("keywords") or []:
            if kw in keyword_set:
                mapping[kw] = label
    return mapping
