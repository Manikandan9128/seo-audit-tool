"""Filters and classifies competitor keywords before they ever reach the
PPTX — raw Semrush exports are noisy (competitor brand names, nav/login
queries, careers queries, typos, unrelated industries) and the report
should only surface keywords with real strategic value.

Two-stage, hybrid: cheap rule-based excludes run first (brand, nav/login,
careers, typo-of-brand) since those are mechanical and cost nothing; only
whatever survives goes through one AI call that judges the semantic cases
rules can't reliably catch (industry mismatch, irrelevant informational
queries). Same "one batched call, fail open on error" discipline as
competitor_narrative_service — this app's binding constraint is free-tier
AI quota, not compute, and losing a classification call should never mean
silently dropping keywords the user might want to see.

Also home to _classify_keyword_page_category (keyword-text -> page FORMAT
signal: Comparison/Blog/Landing) — shared with pptx_builder's Content SEO
Next Steps slide and reused here to give the competitor-opportunity AI
prompt real evidence (e.g. "ranks for 3 comparison-shaped keywords") instead
of guessing blind from homepage text alone.
"""

import json
import logging
import re

from app.integrations.text_ai_client import NoAIProviderConfigured, generate_text

logger = logging.getLogger(__name__)

_VALID_LABELS = {"highly_relevant", "potentially_relevant", "exclude"}

_NAV_LOGIN_WORDS = [
    "login", "log in", "sign in", "sign up", "signin", "signup", "portal", "dashboard",
    "download app", "app download", "customer service number", "customer care number",
    "phone number", "contact number", "helpline",
]
_CAREERS_WORDS = [
    "careers", "career", "jobs", "job openings", "job vacancy", "vacancies", "hiring",
    "salary", "salaries", "glassdoor", "indeed", "linkedin jobs", "internship", "internships",
    "recruitment", "work from home jobs",
]


def _brand_token(domain: str) -> str:
    """Best-effort brand name extracted from a domain, e.g.
    "www.taskus.com" -> "taskus". Semrush's exports have no per-keyword
    branded flag, so this is the only signal available short of a manual
    list."""
    host = re.sub(r"^https?://", "", (domain or "").strip().lower())
    host = re.sub(r"^www\.", "", host).split("/")[0]
    return host.split(".")[0] if host else ""


def _is_branded_keyword(keyword: str, brand: str) -> bool:
    if not brand or not keyword:
        return False
    return re.search(rf"\b{re.escape(brand)}\b", keyword.lower()) is not None


def _edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


def _rule_exclude(keyword: str, brand_tokens: set[str]) -> str | None:
    """Mechanical exclude reasons that need no AI judgment. Returns a short
    reason string, or None if nothing matched (the keyword should proceed
    to AI classification)."""
    text = keyword.lower().strip()
    if not text:
        return "empty"
    for brand in brand_tokens:
        if brand and _is_branded_keyword(text, brand):
            return "brand"
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in _NAV_LOGIN_WORDS):
        return "nav_login"
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in _CAREERS_WORDS):
        return "careers"
    words = text.split()
    if 0 < len(words) <= 3:
        first = words[0]
        if len(first) > 3:
            for brand in brand_tokens:
                if brand and first != brand and len(brand) > 3 and _edit_distance(first, brand) <= 2:
                    return "typo"
    return None


_CLASSIFY_PROMPT_TEMPLATE = """You are an SEO analyst filtering a raw competitor keyword export for {client_name} \
({client_domain}) before it goes into a client-facing report. Classify EACH of the {keyword_count} keywords below \
into exactly one label:

- "highly_relevant": a real prospect/topic search directly tied to {client_name}'s industry, products, or services \
— the kind of query an actual customer or prospect would type.
- "potentially_relevant": tangentially related — an adjacent topic, broader category, or informational query that \
could still support content strategy, even though it isn't a direct product/service match.
- "exclude": an unrelated industry or product, an irrelevant informational query with no strategic value, or \
anything that reads as noise. (Brand names, navigation/login queries, careers queries, and obvious typos are \
already stripped before you see this list — focus on relevance judgment, not those mechanical cases.)

Keywords:
{keywords_json}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "classifications": {{
    "<keyword text, EXACTLY as given>": "highly_relevant" | "potentially_relevant" | "exclude"
  }}
}}
Every keyword listed above must appear as a key, using its exact original text.
"""


def _call_and_parse(prompt: str, max_tokens: int) -> dict:
    try:
        raw, _provider = generate_text(prompt, max_tokens=max_tokens)
    except NoAIProviderConfigured as e:
        return {"error": str(e)}

    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {"error": "AI did not return valid JSON", "raw": raw[:500]}


def classify_keywords(
    client_name: str, client_domain: str, brand_tokens: set[str], keywords: list[str]
) -> dict[str, str]:
    """Returns {lowercased keyword: "highly_relevant" | "potentially_relevant" | "exclude"}
    for every unique keyword in `keywords`. Rule-based excludes are free and
    run first; whatever survives gets one AI classification call (with one
    retry on a malformed/failed response). If the AI call fails outright,
    every surviving keyword fails open as "potentially_relevant" — a failed
    classification must never silently vanish keywords the rules didn't
    already catch."""
    unique: dict[str, str] = {}  # lowercased -> original text (first seen)
    for kw in keywords:
        k = (kw or "").strip()
        if k and k.lower() not in unique:
            unique[k.lower()] = k
    if not unique:
        return {}

    result: dict[str, str] = {}
    remaining: list[str] = []  # original-cased text, for the AI prompt
    for lower, original in unique.items():
        if _rule_exclude(original, brand_tokens):
            result[lower] = "exclude"
        else:
            remaining.append(original)
    if not remaining:
        return result

    prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_domain=client_domain,
        keyword_count=len(remaining),
        keywords_json=json.dumps(remaining, indent=2)[:12000],
    )
    max_tokens = min(200 + 20 * len(remaining), 8000)

    def _apply(parsed: dict) -> bool:
        labels = parsed.get("classifications") if isinstance(parsed, dict) else None
        if not isinstance(labels, dict):
            return False
        for original in remaining:
            label = labels.get(original)
            result[original.lower()] = label if label in _VALID_LABELS else "potentially_relevant"
        return True

    parsed = _call_and_parse(prompt, max_tokens)
    if "error" not in parsed and _apply(parsed):
        return result

    # One retry on a fresh sample — same non-deterministic-hiccup discipline
    # as competitor_narrative_service — before failing open.
    retry_parsed = _call_and_parse(prompt, max_tokens)
    if "error" not in retry_parsed and _apply(retry_parsed):
        return result

    logger.warning(
        "Keyword relevance classification failed for %s (%d keywords) — failing open, "
        "all rule-surviving keywords kept as potentially_relevant: %s",
        client_domain, len(remaining), retry_parsed.get("error") or parsed.get("error"),
    )
    for original in remaining:
        result[original.lower()] = "potentially_relevant"
    return result


# Page-type classification for target/competitor keywords — maps a
# keyword's own text to the page FORMAT its search intent calls for
# (landing page for commercial terms, blog/guide for informational terms,
# comparison page for vs./alternative terms). Generic signal words only —
# nothing industry-specific hardcoded. Checked in this order because a
# comparison-shaped keyword ("X vs Y") is a more specific, higher-intent
# signal than the generic commercial terms a landing page would also match
# on the same keyword.
_COMPARISON_KEYWORD_SIGNALS = [
    "vs", "versus", "comparison", "compare", "compared to", "difference", "difference between",
    "alternative", "alternatives", "competitor", "competitors", "similar to", "replacement", "substitute",
]
_BLOG_KEYWORD_SIGNALS = [
    "how to", "how do", "what is", "what are", "why", "guide", "basics", "explained", "definition",
    "meaning", "steps", "step-by-step", "tutorial", "tips", "best practices", "checklist", "mistakes",
    "benefits", "advantages", "disadvantages", "process", "calculate", "calculation", "requirements",
    "rules", "regulations", "compliance", "trends", "statistics", "research", "report", "examples",
]
_LANDING_PAGE_KEYWORD_SIGNALS = [
    "software", "platform", "system", "tool", "solution", "service", "services", "provider",
    "pricing", "price", "cost", "quote", "demo", "company", "vendor",
]
_KEYWORD_PAGE_CATEGORIES = [
    (
        "Comparison / Alternative", _COMPARISON_KEYWORD_SIGNALS,
        "build dedicated vs./alternative comparison pages targeting these high-intent searches",
    ),
    (
        "Blog / Guide", _BLOG_KEYWORD_SIGNALS,
        "publish blog or guide content directly answering these informational searches",
    ),
    (
        "Landing Page", _LANDING_PAGE_KEYWORD_SIGNALS,
        "build or strengthen a dedicated landing/product page targeting these commercial-intent searches",
    ),
]


def _classify_keyword_page_category(keyword: str) -> str | None:
    text = keyword.lower()
    for label, signals, _action in _KEYWORD_PAGE_CATEGORIES:
        if any(re.search(rf"\b{re.escape(signal)}\b", text) for signal in signals):
            return label
    return None
