"""AI-based search-intent classification for keyword rows that don't already
carry one. Semrush's own "Intent" column (Informational/Commercial/
Transactional/Navigational) doesn't always survive into the Keyword Gap
export a client actually uploads (same gap as the missing Cluster/Topic
column — see keyword_cluster_service.py's module docstring), and two
existing consumers already read a row's "intent" field expecting it to be
populated: keyword_relevance_service._classify_keyword_page_category (maps
keywords to Blog/Guide vs Landing Page) and the Target Keywords slide's
commercial/transactional insight bullet in pptx_builder.py. Both silently
fall back to a cruder keyword-text word-list heuristic when intent is
missing — this fills the real field instead, so those existing consumers
get better input for free, no changes needed on their end. Pure
classification of keywords that are already there — never invents a
keyword, only assigns one of Semrush's own 4 intent labels to each."""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, iter_text_attempts

logger = logging.getLogger(__name__)

_VALID_INTENTS = {"Informational", "Commercial", "Transactional", "Navigational"}

PROMPT_TEMPLATE = """Classify each of the following SEO keywords by search intent - the same \
4-way classification Semrush's own "Intent" column uses. For each keyword, pick exactly one:

- Informational: the searcher wants to learn something ("what is X", "how does X work", "X guide")
- Commercial: the searcher is researching/comparing options before a purchase decision \
("best X", "X reviews", "X vs Y", "top X for Y")
- Transactional: the searcher wants to buy, sign up, or act right now ("X pricing", "buy X", \
"X near me", "X discount")
- Navigational: the searcher wants a specific known brand, product, or site ("X login", \
"X homepage", a brand name alone)

Keywords:
{keyword_list}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
[{{"keyword": string, "intent": "Informational"|"Commercial"|"Transactional"|"Navigational"}}]
"""


def generate_search_intents(keywords: list[str]) -> dict[str, str]:
    """Returns {keyword: intent_label}. Empty dict on any failure (no AI key
    configured, bad JSON, empty list) — caller falls back to the existing
    word-list heuristic, same behavior as when Semrush's own Intent column
    is missing. Keywords the AI didn't echo back verbatim, or that came back
    with anything other than one of Semrush's 4 real intent labels, are
    silently dropped rather than trusted."""
    if not keywords:
        return {}
    prompt = PROMPT_TEMPLATE.format(keyword_list="\n".join(f"- {k}" for k in keywords))
    # Tries every configured provider in order, not just the first one to
    # answer (2026-09-22, same fix as structured_data_insights_service,
    # 2026-09-20; see core_problem_service.generate_core_problem's
    # docstring for why): a bad response from one provider now falls
    # through to the next instead of this call failing open to {} outright.
    errors: list[str] = []
    items = None
    try:
        for raw, provider in iter_text_attempts(prompt, max_tokens=4096, errors=errors):
            cleaned = raw.strip()
            cleaned = re.sub(r"^```(json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("Search intent classification: %s returned invalid JSON: %s", provider, cleaned[:300])
                continue
            if not isinstance(parsed, list):
                logger.warning("Search intent classification: %s returned non-list JSON: %s", provider, cleaned[:300])
                continue
            items = parsed
            break
    except NoAIProviderConfigured as e:
        logger.warning("Search intent classification AI call failed: %s", e)
        return {}
    if items is None:
        return {}

    keyword_set = set(keywords)
    mapping: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        kw = item.get("keyword")
        intent = item.get("intent")
        if kw in keyword_set and intent in _VALID_INTENTS:
            mapping[kw] = intent
    return mapping
