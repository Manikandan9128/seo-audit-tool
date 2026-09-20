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

# Universal SEO Audit Engine spec (2026-09-20) sections 4 + 22: the full
# relevance-status vocabulary, keyed by the compact token the AI/rules
# return internally, mapped to the exact display string the spec uses.
# Sections 4 (general relevance) and 22 (competitor-keyword-specific
# statuses) are merged into one vocabulary here rather than two separate
# classifiers — a competitor-brand-mentioning keyword only ever needs ONE
# status, and the 4 competitor-specific values below are simply the
# competitor-flavored members of the same enum every other keyword is
# judged against.
_RELEVANCE_STATUSES = {
    "core_relevant": "Core Relevant",
    "relevant": "Relevant",
    "adjacent_potential": "Adjacent / Potential",
    "competitor_comparison_opportunity": "Competitor Comparison Opportunity",
    "relevant_competitor_intent": "Relevant Competitor Intent",
    "competitor_brand_search": "Competitor Brand Search",
    "irrelevant_competitor_query": "Irrelevant Competitor Query",
    "geographic_mismatch": "Geographic Mismatch",
    "product_service_mismatch": "Product/Service Mismatch",
    "audience_mismatch": "Audience Mismatch",
    "industry_mismatch": "Industry Mismatch",
    "unrelated": "Unrelated",
    "unknown_needs_review": "Unknown / Needs Review",
}

# The 4 statuses that are specifically about a competitor-brand-mentioning
# keyword (spec section 22) — used to populate a row's separate
# `competitor_status` field (None/"Not Applicable" for every other status).
_COMPETITOR_SPECIFIC_STATUSES = {
    "competitor_comparison_opportunity", "relevant_competitor_intent",
    "competitor_brand_search", "irrelevant_competitor_query",
}

# Coarse keep/exclude decision every existing caller (Target Keywords,
# Competitor Keyword Gap, Search Queries filters) already runs on —
# preserved as-is so this richer vocabulary is additive, not a behavior
# change to what gets kept vs. dropped. "Unknown / Needs Review" fails open
# (kept, flagged) rather than silently dropped — spec section 4's own
# explicit fallback is a review flag, never a guess to exclude.
_STATUS_TO_COARSE_LABEL = {
    "core_relevant": "highly_relevant",
    "relevant": "highly_relevant",
    "adjacent_potential": "potentially_relevant",
    "competitor_comparison_opportunity": "potentially_relevant",
    "relevant_competitor_intent": "potentially_relevant",
    "competitor_brand_search": "exclude",
    "irrelevant_competitor_query": "exclude",
    "geographic_mismatch": "exclude",
    "product_service_mismatch": "exclude",
    "audience_mismatch": "exclude",
    "industry_mismatch": "exclude",
    "unrelated": "exclude",
    "unknown_needs_review": "potentially_relevant",
}

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


_MULTI_PART_TLDS = {
    "co.uk", "co.in", "co.jp", "co.nz", "co.za", "co.id", "co.kr",
    "com.au", "com.br", "com.mx", "com.sg", "com.cn", "com.tw",
}


def _brand_token(domain: str) -> str:
    """Best-effort brand name extracted from a domain, e.g.
    "www.taskus.com" -> "taskus". Semrush's exports have no per-keyword
    branded flag, so this is the only signal available short of a manual
    list. Takes the label before the TLD, not always the first label — a
    competitor row can be a subdomain (e.g. "trucks.tatamotors.com", a real
    Semrush export value), and label[0] there would wrongly extract
    "trucks" as the brand instead of "tatamotors" (confirmed live 2026-09-18
    on a BharatBenz report: "trucks.tatamotors.com" is a real per-domain
    competitor row). Handles the common multi-part ccTLDs (co.uk, co.in,
    etc.) so "example.co.uk" still resolves to "example", not "co"."""
    host = re.sub(r"^https?://", "", (domain or "").strip().lower())
    host = re.sub(r"^www\.", "", host).split("/")[0]
    labels = host.split(".") if host else []
    if len(labels) < 2:
        return labels[0] if labels else ""
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_PART_TLDS:
        return labels[-3]
    return labels[-2]


_CORPORATE_SUFFIX_WORDS = (
    "motors", "motor", "group", "industries", "industry", "corp", "corporation",
    "international", "enterprises", "holdings", "ventures", "systems", "technologies", "technology", "solutions",
)


def brand_token_variants(domain: str) -> set[str]:
    """`_brand_token` alone catches a keyword only when it repeats the
    domain's exact registrable label — but "tatamotors.com" ranks in real
    search queries as "tata" (e.g. "tata sierra"), not "tatamotors", and a
    plain substring/domain match never bridges that gap. Strips a small set
    of common corporate suffix words (Motors, Group, Industries, Corp, ...)
    off the domain-derived token to also catch the shorter, colloquial brand
    name customers actually type — confirmed needed live 2026-09-18 on a
    BharatBenz report, where "tata sierra" / "tata nexon" kept surviving the
    filter as legitimate own-keyword targets purely because
    "tatamotors.com" doesn't literally contain the substring "tata sierra"
    without this split."""
    token = _brand_token(domain)
    if not token:
        return set()
    variants = {token}
    for suffix in _CORPORATE_SUFFIX_WORDS:
        if token.endswith(suffix) and len(token) > len(suffix):
            variants.add(token[: -len(suffix)])
    return variants


def _is_branded_keyword(keyword: str, brand: str) -> bool:
    if not brand or not keyword:
        return False
    return re.search(rf"\b{re.escape(brand)}\b", keyword.lower()) is not None


def is_branded_or_near_brand(keyword: str, brand_tokens) -> bool:
    """True for an exact whole-word brand match, or — for a short query —
    a likely misspelling of one (first word within edit distance 2 of a
    brand token), e.g. "lumberfy" catching as a near-miss of "lumberfi"
    even though it's not a whole-word match. Public so callers outside
    this module's own AI-classification pipeline (e.g. the Search Queries
    Branded/Non-Branded split in pptx_builder.py, which only ever did
    exact matching and let real near-brand query variants fall into
    Non-Branded) can reuse the same typo tolerance as the competitor-
    keyword filter's "typo" exclude reason below."""
    text = (keyword or "").lower().strip()
    if not text:
        return False
    for brand in brand_tokens:
        if brand and _is_branded_keyword(text, brand):
            return True
    words = text.split()
    if 0 < len(words) <= 3:
        first = words[0]
        if len(first) > 3:
            for brand in brand_tokens:
                if brand and first != brand and len(brand) > 3 and _edit_distance(first, brand) <= 2:
                    return True
    return False


def filter_other_brand_keywords(keyword_rows: list[dict], client_domain: str, competitor_domains) -> list[dict]:
    """Standing rule for every client, not just one: an export of the
    CLIENT's own ranking keywords can still legitimately contain another
    company's brand name in the query text (e.g. a comparison/blog page on
    the client's own site ranking for "tata sierra") — that's a real
    ranking, but a client should never be told to TARGET a competitor's
    brand name as if it were their own keyword opportunity, so rows like
    that must never reach the Target Keywords slide. Excludes only
    competitor brand tokens — never the client's own brand, since an
    own-branded keyword is a normal, legitimate target."""
    client_brands = brand_token_variants(client_domain)
    competitor_brands: set[str] = set()
    for d in competitor_domains or []:
        competitor_brands |= brand_token_variants(d)
    competitor_brands -= client_brands
    if not competitor_brands:
        return keyword_rows
    return [r for r in keyword_rows if not is_branded_or_near_brand(r.get("keyword", ""), competitor_brands)]


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


def _rule_exclude(keyword: str, brand_tokens: set[str]) -> tuple[str, str] | None:
    """Mechanical exclude reasons that need no AI judgment. Returns
    (status_key, reason) — status_key is a key into _RELEVANCE_STATUSES —
    or None if nothing matched (the keyword should proceed to AI
    classification)."""
    text = keyword.lower().strip()
    if not text:
        return ("unrelated", "Empty keyword text.")
    for brand in brand_tokens:
        if brand and _is_branded_keyword(text, brand):
            # Universal SEO Audit Engine spec (2026-09-20) section 22:
            # competitor keywords are NOT automatically irrelevant —
            # comparison/alternative-intent queries mentioning a
            # competitor's brand ("mailchimp vs constant contact",
            # "mailchimp alternative") are a real content opportunity
            # (Competitor Comparison Opportunity), not noise. Confirmed
            # real: the old blanket "any brand-token match -> exclude"
            # rule discarded these before the AI classification prompt —
            # which already explicitly judges this exact case — ever saw
            # them. Let a comparison-shaped brand mention through to that
            # AI judgment instead of mechanically dropping it here.
            if not any(re.search(rf"\b{re.escape(sig)}\b", text) for sig in _COMPARISON_KEYWORD_SIGNALS):
                return ("competitor_brand_search", f'Contains competitor brand "{brand}", not a comparison/alternative-intent query.')
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in _NAV_LOGIN_WORDS):
        return ("unrelated", "Navigation/login query — not a search opportunity.")
    if any(re.search(rf"\b{re.escape(w)}\b", text) for w in _CAREERS_WORDS):
        return ("unrelated", "Careers/recruitment query — not a search opportunity.")
    words = text.split()
    if 0 < len(words) <= 3:
        first = words[0]
        if len(first) > 3:
            for brand in brand_tokens:
                if brand and first != brand and len(brand) > 3 and _edit_distance(first, brand) <= 2:
                    return ("unrelated", f'Likely a typo/near-miss of brand "{brand}".')
    return None


_CLASSIFY_PROMPT_TEMPLATE = """You are an SEO analyst filtering a raw competitor keyword export for {client_name} \
({client_domain}){business_context} before it goes into a client-facing report. Classify EACH of the \
{keyword_count} keywords below into exactly one status, using ONLY the status keys listed:

- "core_relevant": a direct, high-confidence prospect/topic search tied to {client_name}'s ACTUAL industry, \
products, or services (per the business context above) — the kind of query a real customer or prospect would type.
- "relevant": clearly tied to {client_name}'s business but less central than core_relevant (a supporting topic, \
not the main product/service itself).
- "adjacent_potential": tangentially related — an adjacent topic or broader category still within or near \
{client_name}'s own industry, that could support content strategy even though it isn't a direct product/service \
match.
- "competitor_comparison_opportunity": a vs./alternative/comparison-shaped query naming a competitor — a real \
content opportunity (comparison/alternative page), not noise.
- "relevant_competitor_intent": mentions a competitor but reflects a prospect researching the SAME kind of \
product/service {client_name} offers (e.g. migration intent, competitor research) — real strategic value.
- "competitor_brand_search": is really a search FOR the competitor's own brand/product with no comparison or \
migration angle — {client_name} has no legitimate claim to this query.
- "irrelevant_competitor_query": mentions a competitor but for something entirely outside {client_name}'s own \
industry/business (the competitor serves other markets too) — no strategic value to {client_name}.
- "geographic_mismatch": targets a location {client_name} does not serve.
- "product_service_mismatch": names a specific product/service {client_name} does not offer.
- "audience_mismatch": targets a buyer/user type {client_name} does not serve.
- "industry_mismatch": belongs to a DIFFERENT industry or product category than {client_name}'s entirely.
- "unrelated": no strategic value to {client_name} specifically, reads as noise, or an irrelevant informational \
query with no connection to the business.
- "unknown_needs_review": not enough evidence in the keyword text + business context to judge confidently either way.

A keyword a competitor ranks for is not automatically relevant just because the competitor ranks for it — judge it \
against {client_name}'s own business, not the competitor's. When genuinely unsure whether a keyword is inside or \
outside {client_name}'s industry, prefer "unknown_needs_review" over guessing. (Brand names, navigation/login \
queries, careers queries, and obvious typos are already stripped before you see this list — focus on relevance \
judgment, not those mechanical cases.)

Keywords:
{keywords_json}

Return ONLY valid JSON, no markdown fences, no commentary, matching this shape:
{{
  "classifications": {{
    "<keyword text, EXACTLY as given>": {{"status": "<one status key from the list above>", "reason": "<one short sentence of evidence-based justification>"}}
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
    client_name: str,
    client_domain: str,
    brand_tokens: set[str],
    keywords: list[str],
    client_description: str | None = None,
) -> dict[str, dict]:
    """Returns {lowercased keyword: {"label": "highly_relevant" | "potentially_relevant" | "exclude",
    "status": <spec's relevance_status display string, sections 4+22>, "reason": <short evidence-based
    sentence>}} for every unique keyword in `keywords`. "label" is the coarse keep/drop decision every
    existing caller filters on (unchanged behavior); "status"/"reason" are the finer Universal SEO Audit
    Engine spec fields (relevance_status/relevance_reason, and — for the 4 competitor-specific statuses —
    competitor_status) callers can stamp onto rows for reporting.

    Rule-based excludes are free and run first; whatever survives gets one AI classification call (with one
    retry on a malformed/failed response). If the AI call fails outright, every surviving keyword fails open
    as "potentially_relevant" / "Unknown / Needs Review" — a failed classification must never silently vanish
    keywords the rules didn't already catch. client_description (the client's own 2-4 sentence
    company-overview summary, when available) grounds the AI's industry-relevance judgment in what the CLIENT
    actually does — without it, a keyword a broad-market competitor ranks for (e.g. "cloud security tips" from
    an HR platform that also does IT) has no signal to be judged against and tends to survive as a false
    "potentially_relevant"."""
    unique: dict[str, str] = {}  # lowercased -> original text (first seen)
    for kw in keywords:
        k = (kw or "").strip()
        if k and k.lower() not in unique:
            unique[k.lower()] = k
    if not unique:
        return {}

    def _entry(status_key: str, reason: str) -> dict:
        status_key = status_key if status_key in _RELEVANCE_STATUSES else "unknown_needs_review"
        return {
            "label": _STATUS_TO_COARSE_LABEL[status_key],
            "status": _RELEVANCE_STATUSES[status_key],
            "reason": reason,
        }

    result: dict[str, dict] = {}
    remaining: list[str] = []  # original-cased text, for the AI prompt
    for lower, original in unique.items():
        rule_hit = _rule_exclude(original, brand_tokens)
        if rule_hit:
            result[lower] = _entry(*rule_hit)
        else:
            remaining.append(original)
    if not remaining:
        return result

    business_context = f" — {client_description.strip()}" if client_description and client_description.strip() else ""
    prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
        client_name=client_name,
        client_domain=client_domain,
        business_context=business_context,
        keyword_count=len(remaining),
        keywords_json=json.dumps(remaining, indent=2)[:12000],
    )
    max_tokens = min(300 + 30 * len(remaining), 8000)

    def _apply(parsed: dict) -> bool:
        classifications = parsed.get("classifications") if isinstance(parsed, dict) else None
        if not isinstance(classifications, dict):
            return False
        for original in remaining:
            entry = classifications.get(original)
            if isinstance(entry, dict):
                status_key = entry.get("status") or "unknown_needs_review"
                reason = (entry.get("reason") or "").strip() or "AI classification, no reason given."
            elif isinstance(entry, str):
                # Tolerate a bare status-string response (older/looser model
                # output) instead of failing the whole batch over one
                # keyword's shape.
                status_key, reason = entry, "AI classification, no reason given."
            else:
                status_key, reason = "unknown_needs_review", "AI returned no classification for this keyword."
            result[original.lower()] = _entry(status_key, reason)
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
        "all rule-surviving keywords kept as potentially_relevant/Unknown-Needs-Review: %s",
        client_domain, len(remaining), retry_parsed.get("error") or parsed.get("error"),
    )
    for original in remaining:
        result[original.lower()] = _entry("unknown_needs_review", "AI classification unavailable — needs manual review.")
    return result


# Geographic keyword evaluation (spec section 23) — deterministic, no AI:
# flags a keyword only when it explicitly names a real country/major region
# that conflicts with the client's own known target market. No gazetteer of
# cities/states is used (that would risk false positives on ordinary product
# words), and a client with no known target_country (Global, or extraction
# never ran) gets no geographic_mismatch verdicts at all — spec's own
# discipline: never invent geographic mismatch without real evidence of the
# client's actual service area.
_COUNTRY_NAMES = {
    "usa", "united states", "america", "uk", "united kingdom", "britain", "canada", "australia",
    "india", "germany", "france", "spain", "italy", "netherlands", "ireland", "new zealand",
    "singapore", "japan", "china", "brazil", "mexico", "south africa", "uae", "dubai",
    "saudi arabia", "philippines", "indonesia", "malaysia", "vietnam", "thailand", "pakistan",
    "bangladesh", "nigeria", "kenya", "egypt", "russia", "poland", "sweden", "norway", "denmark",
    "switzerland", "austria", "belgium", "portugal", "greece", "turkey", "israel", "south korea",
    "argentina", "chile", "colombia", "peru",
}
# A target_country string like "Primary USA" needs to resolve to the same
# canonical token a keyword's own text would use.
_COUNTRY_ALIASES = {
    "usa": {"usa", "united states", "america", "us"},
    "uk": {"uk", "united kingdom", "britain"},
}


def _country_tokens_in(text: str) -> set[str]:
    text_l = f" {text.lower()} "
    found = set()
    for name in _COUNTRY_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", text_l):
            found.add(name)
    return found


def assign_geo_status(rows: list[dict], target_country: str | None, keyword_field: str = "keyword") -> None:
    """Stamps `geo_status` onto every row in place. Only ever sets
    "Geographic Mismatch" — spec section 4's vocabulary has no positive
    "local match" status, and this function never invents one. Leaves
    geo_status as None (not applicable / no geographic signal, or no known
    target market to judge against) on every other row."""
    target = (target_country or "").strip().lower()
    if not target or target in ("global", "worldwide", "international"):
        for r in rows:
            r["geo_status"] = None
        return

    target_aliases = set()
    for name in _COUNTRY_NAMES:
        if name in target:
            target_aliases.add(name)
    for canonical, aliases in _COUNTRY_ALIASES.items():
        if any(a in target for a in aliases):
            target_aliases |= aliases

    for r in rows:
        mentioned = _country_tokens_in(r.get(keyword_field) or "")
        foreign = mentioned - target_aliases if target_aliases else mentioned
        r["geo_status"] = "Geographic Mismatch" if foreign else None


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


def _classify_keyword_page_category(keyword: str, intent: str | None = None) -> str | None:
    """Comparison is a page-format signal Semrush's own Intent column has no
    concept of, so that word-list check always runs first and wins outright.
    For everything else, trust Semrush's real search-intent data (grounded in
    actual query behavior) over guessing from keyword text — it cleanly
    separates informational ("pricing guide" -> Commercial, not Blog, despite
    the word "guide") from commercial/transactional. Only fall back to the
    word-list for Blog vs. Landing when Semrush intent is missing/blank or
    Navigational (which doesn't map to either)."""
    text = keyword.lower()
    comparison_signals = _KEYWORD_PAGE_CATEGORIES[0][1]
    if any(re.search(rf"\b{re.escape(signal)}\b", text) for signal in comparison_signals):
        return _KEYWORD_PAGE_CATEGORIES[0][0]

    intent_norm = (intent or "").lower()
    if "informational" in intent_norm:
        return "Blog / Guide"
    if "commercial" in intent_norm or "transactional" in intent_norm:
        return "Landing Page"

    for label, signals, _action in _KEYWORD_PAGE_CATEGORIES[1:]:
        if any(re.search(rf"\b{re.escape(signal)}\b", text) for signal in signals):
            return label
    return None


_MATCH_STOPWORDS = {
    "the", "a", "an", "for", "to", "of", "in", "on", "and", "or", "with", "is", "are",
    "how", "what", "why", "do", "does", "best", "top", "your", "you", "vs",
}


def _match_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _MATCH_STOPWORDS and len(w) > 2}


def match_existing_page(primary_keyword: str, site_audit_pages_rows: list[dict] | None) -> dict | None:
    """Best-effort match between a keyword's core terms and an
    already-crawled page's title/URL — word-overlap only, no AI, no
    embeddings (keeps this deterministic and free, like
    _classify_keyword_page_category above). Returns {"url", "title"} for
    the best-scoring page if it shares enough terms with the keyword, else
    None — meaning no existing page covers it, i.e. a New Page Opportunity
    per the lead's reference flow's "Existing URL check" branch. A false
    negative here just means the Next Steps recommendation reads "create
    new page" when a decent page already existed — safer than a false
    positive claiming an unrelated page already covers the keyword."""
    if not site_audit_pages_rows:
        return None
    kw_tokens = _match_tokens(primary_keyword)
    if not kw_tokens:
        return None

    best = None
    best_score = 0
    for row in site_audit_pages_rows:
        url = row.get("page_url") or ""
        title = row.get("page_title") or ""
        path = re.sub(r"[/\-_]", " ", url)
        page_tokens = _match_tokens(title) | _match_tokens(path)
        score = len(kw_tokens & page_tokens)
        if score > best_score:
            best_score = score
            best = {"url": url, "title": title}

    # Require at least half the keyword's significant terms to match (min
    # 1) — a single incidental shared word shouldn't count as "this page
    # already covers it".
    min_required = max(1, len(kw_tokens) // 2)
    if best and best_score >= min_required:
        return best
    return None


def match_existing_page_for_cluster(
    cluster_keywords: list[str], site_audit_pages_rows: list[dict] | None
) -> dict | None:
    """Cluster-level existing-page match (FINAL PIPELINE step 11) — pools
    token signal from every keyword in a validated cluster (primary +
    secondary), not just one keyword, since a page can legitimately match a
    cluster's overall topic more strongly than any single keyword's exact
    wording. Still pure word-overlap, no AI/embeddings, same determinism as
    match_existing_page above. Returns {"url", "title", "match_strength"}
    where match_strength is one of "strong" (most of the cluster's terms
    are on the page), "partial" (a meaningful chunk), or "weak" (some
    incidental overlap) — or None when there's no overlap at all, i.e. a
    genuine New Page Opportunity. Never returns a match just because a
    competitor page or unrelated page happens to share one word."""
    if not site_audit_pages_rows or not cluster_keywords:
        return None
    kw_tokens: set[str] = set()
    for kw in cluster_keywords:
        kw_tokens |= _match_tokens(kw)
    if not kw_tokens:
        return None

    best = None
    best_score = 0
    for row in site_audit_pages_rows:
        url = row.get("page_url") or ""
        title = row.get("page_title") or ""
        path = re.sub(r"[/\-_]", " ", url)
        page_tokens = _match_tokens(title) | _match_tokens(path)
        score = len(kw_tokens & page_tokens)
        if score > best_score:
            best_score = score
            best = {"url": url, "title": title}
    if not best or best_score == 0:
        return None

    ratio = best_score / len(kw_tokens)
    best["match_strength"] = "strong" if ratio >= 0.66 else "partial" if ratio >= 0.35 else "weak"
    return best
