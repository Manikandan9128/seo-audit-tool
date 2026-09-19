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


def generate_batched_candidate_clusters(groups: list[tuple[str, list[str]]]) -> dict[str, str]:
    """Candidate clustering (the FINAL PIPELINE's "which keywords can
    realistically be satisfied by the SAME page?" step) for EVERY
    (business_theme, search_intent, page_category) bucket in ONE AI call,
    not one call per bucket. Confirmed real (2026-09-19, live report
    stall): a client with many distinct buckets turned into that many
    sequential Groq calls, each also waiting on Groq's shared per-minute
    token budget (see text_ai_client._reserve_groq_budget) alongside this
    same report's other AI calls (company overview, core problem,
    competitor narratives, next steps) — easily chaining past the
    15-minute stale-job threshold. `groups` is [(context_label, keywords)];
    bucket boundaries are still a HARD constraint enforced by the caller
    after parsing (label_owner disambiguation in
    keyword_cluster_pipeline.py), not by trusting the AI to respect the
    "never combine different groups" instruction below — this call is
    best-effort grouping input, the boundary itself is structural
    regardless of what comes back."""
    groups = [(label, kws) for label, kws in groups if kws]
    if not groups:
        return {}
    all_keywords = [kw for _label, kws in groups for kw in kws]

    sections = []
    for i, (label, kws) in enumerate(groups, 1):
        sections.append(f"Group {i} ({label}):\n" + "\n".join(f"- {k}" for k in kws))
    prompt = (
        "Below are several pre-grouped sets of SEO keywords. Each group already shares the same "
        "business theme, search intent, and recommended page format - keywords in DIFFERENT groups "
        "must NEVER be combined into the same cluster, even if they look related or share words. "
        "Within each group, split it further into finer clusters only if genuinely different "
        "sub-topics or page needs exist within that group (the same kind of split an SEO strategist "
        "would make between, e.g., \"certified payroll\" and \"how certified payroll works\"); "
        "otherwise return the whole group as one cluster. Never force a fixed number of clusters, "
        "and never invent a keyword that isn't listed below. Cluster names should be short (2-5 "
        "words) and specific to the group's actual topic.\n\n" + "\n\n".join(sections) +
        "\n\nReturn ONLY valid JSON, no markdown fences, no commentary, matching this shape:\n"
        '[{"cluster": string, "keywords": [string]}]\n'
    )
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        logger.warning("Batched candidate clustering AI call failed: %s", e)
        return {}
    return _parse_cluster_response(raw, all_keywords)


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
