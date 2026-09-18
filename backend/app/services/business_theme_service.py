"""AI-based business-theme classification for keyword rows — the layer
between raw keywords and clustering that the lead's FINAL PIPELINE spec
calls for: "the underlying product, service, problem, industry, feature,
use case, or business topic represented by the keyword" (e.g. "Construction
Payroll", "Certified Payroll", "Construction Time Tracking" for a
construction-payroll SaaS). Distinct from search intent (which describes
WHY someone searches) and page category (which describes WHAT FORMAT of
page answers it) — business theme describes WHICH part of the client's
actual business the keyword belongs to, grounded in the client's own
company-overview description so themes reflect this client's real business
architecture instead of generic semantic word groups. Pure classification
of keywords that are already there — never invents a keyword, and a
keyword the AI didn't echo back verbatim is dropped rather than trusted.
A business theme is NOT the final cluster on its own — a single theme can
still split into several SEO clusters downstream once intent and page
category are also considered (see keyword_cluster_pipeline.py)."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

logger = logging.getLogger(__name__)

UNCLASSIFIED_THEME = "Unclassified"

PROMPT_TEMPLATE = """You are an SEO strategist mapping keywords to {client_name}'s own business \
architecture — the real products, services, problems, industries, features, and use cases {client_name} \
actually sells or addresses{business_context}.

For each keyword below, assign a short (2-5 word) Business Theme naming the specific part of \
{client_name}'s business it belongs to — e.g. for a construction-payroll SaaS: "Construction Payroll", \
"Certified Payroll", "Construction Workforce Management", "Construction Time Tracking", "Payroll \
Compliance". Themes must reflect {client_name}'s ACTUAL offerings and business areas, not generic \
semantic word groups, and must not be invented out of thin air — ground every theme in the business \
context given above. Use as many distinct themes as the keyword list actually needs — do not force a \
fixed number of themes, and do not merge two genuinely different business areas into one theme just \
because they share words. If a keyword's business relevance genuinely cannot be determined from the \
keyword and business context, assign it the theme "{unclassified}" rather than guessing.

Keywords:
{keyword_list}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
[{{"theme": string, "keywords": [string]}}]
"""


def generate_business_themes(
    client_name: str,
    client_description: str | None,
    keywords: list[str],
) -> dict[str, str]:
    """Returns {keyword: business_theme}. Empty dict on any failure (no AI
    key configured, bad JSON, empty list) — caller must fall back to
    UNCLASSIFIED_THEME for every row rather than skip theme assignment
    entirely, per the spec's "use an explicit Unclassified value" fallback
    rule. Keywords the AI didn't echo back verbatim are silently dropped
    from the mapping rather than trusted."""
    if not keywords:
        return {}
    business_context = f" — {client_description.strip()}" if client_description and client_description.strip() else ""
    prompt = PROMPT_TEMPLATE.format(
        client_name=client_name,
        business_context=business_context,
        unclassified=UNCLASSIFIED_THEME,
        keyword_list="\n".join(f"- {k}" for k in keywords),
    )
    try:
        raw, _provider = generate_text(prompt)
    except NoAIProviderConfigured as e:
        logger.warning("Business theme classification AI call failed: %s", e)
        return {}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        groups = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Business theme classification returned invalid JSON: %s", raw[:300])
        return {}
    if not isinstance(groups, list):
        logger.warning("Business theme classification returned non-list JSON: %s", raw[:300])
        return {}

    keyword_set = set(keywords)
    mapping: dict[str, str] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        theme = group.get("theme")
        if not theme:
            continue
        for kw in group.get("keywords") or []:
            if kw in keyword_set:
                mapping[kw] = theme
    return mapping
