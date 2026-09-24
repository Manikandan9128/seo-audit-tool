"""Universal SEO Keyword Intelligence, Clustering & Targeting Engine
(user's spec, 2026-09-23) — the deterministic core both keyword scenarios
share: a client's manually-uploaded cluster sheet, and the Semrush/GSC
keyword set clustered by AI.

Everything in this module is pure Python, no AI call and no I/O, so it runs
over EVERY keyword in milliseconds. That is what lets the engine honour the
spec's "every keyword is clustered, excluded with a reason, or queued for
review" rule without adding report-generation time: the AI steps elsewhere
(keyword_semantic_cluster_service, keyword_relevance_service) keep their
existing call count and only ever see a bounded set of group
representatives, and their results are cached per client
(`KeywordIntelligenceCache`) so a regeneration re-uses them instead of
calling the AI again.

Spec sections implemented here:
  §5  normalization (singular/plural, word order, stop words)
  §7  modifier extraction (intent / geo / attribute modifiers)
  §8  intent classification with a confidence value
  §9  user need
  §17/§28/§41/§43  rule-based same-page grouping (entity + intent family),
      with a guarded parent-attach step so a minor modifier never creates
      its own page (§41) and a one-word head term never swallows every
      related keyword (§40)
  §30/§61  cluster confidence 0-100 + High/Medium/Low, capped below High-
      certainty when no SERP evidence exists (§15: SERP overlap is the
      strongest validation and this tool has no SERP data yet)
  §42  cluster coherence (split) check — flag only
  §53  keyword-to-URL decision vocabulary
  §60  explainability line per cluster
"""

import hashlib
import json
import re
from collections import Counter

_STOP_WORDS = {
    "a", "an", "the", "of", "for", "in", "on", "to", "and", "or", "with", "by", "at", "from", "as",
    "is", "are", "was", "be", "do", "does", "should", "my", "your", "our", "i", "you", "we",
    "it", "its", "this", "that", "these", "those", "into", "about", "per", "vs", "versus",
    "what", "how", "why", "when", "which", "who", "where",
}

# Intent markers, checked as whole words/phrases. Order matters in
# detect_intent (most specific first).
_NAVIGATIONAL_MARKERS = [
    "login", "log in", "sign in", "signin", "sign up", "portal", "official website", "website",
    "customer care", "customer service", "contact number", "phone number", "helpline", "app download",
]
_COMPARISON_MARKERS = [
    "vs", "versus", "compare", "comparison", "compared", "alternative", "alternatives",
    "difference between", "differences between", "better than", "competitors",
]
_LOCAL_MARKERS = [
    "near me", "nearby", "near by", "dealer", "dealers", "dealership", "showroom", "showrooms",
    "service center", "service centre", "store near",
]
_TRANSACTIONAL_MARKERS = [
    "buy", "purchase", "price", "prices", "pricing", "cost", "costs", "quote", "quotes", "for sale",
    "on road", "emi", "booking", "hire", "rent", "rental", "lease", "leasing", "how much",
    "cheap", "cheapest", "discount", "deal", "deals", "free trial", "demo",
    "subscription", "download",
]
_INVESTIGATION_MARKERS = [
    "best", "top", "review", "reviews", "rating", "ratings", "recommended", "provider", "providers",
    "company", "companies", "vendor", "vendors", "software", "platform", "services", "service",
    "solution", "solutions", "tool", "tools", "manufacturer", "manufacturers", "supplier", "suppliers",
]
_QUESTION_MARKERS = [
    "how", "what", "why", "when", "which", "who", "guide", "tutorial", "meaning", "definition", "define",
]
_INFORMATIONAL_MARKERS = _QUESTION_MARKERS + [
    "tips", "example", "examples", "ideas", "benefits", "advantages", "disadvantages",
    "pros and cons", "types of", "list of", "rules", "regulations", "requirements", "law", "laws",
    "calculate", "calculation", "calculator", "formula", "checklist", "template", "templates",
    "history", "explained", "mileage", "specification", "specifications", "specs", "size", "sizes",
    "dimensions", "capacity", "weight", "features", "process", "steps",
    "license", "licence", "certification", "course", "salary", "statistics", "trends",
]

# Modifier words stripped when finding a keyword's core entity. Intent
# markers are included (a price/guide/near-me modifier never names a
# different entity by itself, §7), plus audience/temporal fillers.
_EXTRA_MODIFIERS = {
    "online", "latest", "new", "current", "upcoming", "free", "full", "complete", "list",
    "type", "types", "option", "options", "basic", "basics", "overview", "info", "information",
    "plan", "plans", "feature", "benefit", "resource", "resources", "platforms", "details",
    # Filler qualifiers (2026-09-24, Geopits "cost based optimizer" made
    # "Based" a parent topic): they describe HOW, never WHAT.
    "based", "driven", "powered", "related", "using", "oriented", "enabled",
}
_MODIFIER_WORDS = (
    {w for w in _TRANSACTIONAL_MARKERS if " " not in w}
    | {w for w in _INVESTIGATION_MARKERS if " " not in w}
    | {w for w in _INFORMATIONAL_MARKERS if " " not in w}
    | {w for w in _COMPARISON_MARKERS if " " not in w}
    | {w for w in _LOCAL_MARKERS if " " not in w}
    | _EXTRA_MODIFIERS
)
_MULTIWORD_MODIFIERS = sorted(
    {m for m in (_TRANSACTIONAL_MARKERS + _INVESTIGATION_MARKERS + _INFORMATIONAL_MARKERS
                 + _COMPARISON_MARKERS + _LOCAL_MARKERS + _NAVIGATIONAL_MARKERS) if " " in m},
    key=len, reverse=True,
)

# Geography words stripped from the core entity (§12: geography is a
# modifier of the need, not a different entity). Same country list the
# relevance service's geo check uses, plus generic geo words.
_GEO_WORDS = {
    "usa", "us", "uk", "india", "indian", "canada", "australia", "germany", "france", "spain", "italy",
    "ireland", "singapore", "japan", "china", "brazil", "mexico", "uae", "dubai", "philippines",
    "indonesia", "malaysia", "vietnam", "thailand", "pakistan", "bangladesh", "nigeria", "kenya",
    "egypt", "america", "american", "british", "europe", "asia", "africa",
}
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")

_NO_SINGULAR = {
    "bus", "gas", "news", "series", "species", "chassis", "status", "analysis", "basis", "gps",
    "sales", "business", "process", "access", "class", "glass", "less", "plus", "canvas", "atlas",
    "us", "is", "its", "this", "ms", "os", "physics", "logistics", "economics", "mathematics",
}


def singularize(word: str) -> str:
    w = word.lower()
    if w in _NO_SINGULAR or len(w) <= 3 or w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "xes", "zes")):
        return w[:-2]
    if w.endswith("s"):
        return w[:-1]
    return w


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)?", (text or "").lower())


def _has_marker(text: str, markers) -> str | None:
    for m in markers:
        if re.search(rf"(?<![a-z0-9]){re.escape(m)}(?![a-z0-9])", text):
            return m
    return None


def normalize_keyword(keyword: str) -> dict:
    """§5/§6/§7 — canonical form + core entity + modifiers, deterministic.
    `core_key` is word-order-insensitive and singular, so "truck tipper" /
    "tipper trucks" land on the same key; `core_phrase` keeps the natural
    word order of the original keyword for display."""
    text = " ".join(_words(keyword))
    stripped = f" {text} "
    modifiers: list[str] = []
    for m in _MULTIWORD_MODIFIERS:
        if f" {m} " in stripped:
            modifiers.append(m)
            stripped = stripped.replace(f" {m} ", " ")
    core_words: list[str] = []
    geo: list[str] = []
    for w in stripped.split():
        if w in _GEO_WORDS:
            geo.append(w)
        elif _YEAR_RE.match(w):
            modifiers.append(w)
        elif w in _MODIFIER_WORDS:
            modifiers.append(w)
        elif w in _STOP_WORDS:
            continue
        else:
            core_words.append(w)
    if not core_words:
        # Every word was a modifier/geo/stop word — fall back to the
        # keyword's own content words so it never collapses into an
        # unrelated all-modifier keyword's group.
        core_words = [w for w in text.split() if w not in _STOP_WORDS] or text.split()
    singular = [singularize(w) for w in core_words]
    return {
        "canonical": " ".join(sorted(singularize(w) for w in text.split() if w not in _STOP_WORDS)),
        "core_phrase": " ".join(core_words),
        "core_key": " ".join(sorted(set(singular))),
        "core_tokens": frozenset(singular),
        "modifiers": modifiers,
        "geo": geo,
    }


# Intent families that genuinely need different pages (§17/§35). Commercial
# and transactional stay together on purpose: a product/model page is where
# both "tipper truck" and "tipper truck price" are satisfied.
INTENT_FAMILY = {
    "Informational": "Informational",
    "Commercial Investigation": "Commercial",
    "Transactional": "Commercial",
    "Commercial": "Commercial",
    "Comparison": "Comparison",
    "Local": "Local",
    "Navigational": "Navigational",
}


_NOT_A_PRICE_RE = re.compile(r"\bcost[- ](based|effective|efficient|optimization|optimisation|management|accounting)\b")

# "a or b" with short sides only — never a sentence that merely contains "or".
_EITHER_OR_RE = re.compile(r"^(?!.*\bor not\b)[a-z0-9][a-z0-9 ]{1,40}? or [a-z0-9][a-z0-9 ]{1,40}$")
_TROUBLESHOOT_MARKERS = [
    "not working", "error", "errors", "fix", "fixing", "troubleshoot", "troubleshooting", "issue", "issues",
    "problem", "problems", "failed", "failure", "stuck", "slow", "crash", "crashing",
]


def detect_intent(keyword: str, source_intent: str | None = None) -> dict:
    """§8/§9 — primary intent, confidence (0-100) and user need, from the
    keyword's own modifiers. An explicit marker gives a confident verdict;
    a bare entity term ("tipper truck") falls back to the upstream source
    intent (Semrush) when one exists, else product/service discovery at
    medium confidence — never presented as certain."""
    # "cost based optimizer", "cost-effective"... name a concept or quality,
    # not a price search — the compound is removed before marker checks.
    text = _NOT_A_PRICE_RE.sub(" ", " ".join(_words(keyword))).strip() or " ".join(_words(keyword))
    norm = normalize_keyword(keyword)
    core = norm["core_phrase"] or text
    # For a "learn" need the modifiers ARE the topic ("truck sizes", "bus
    # mileage"), so keep them — only question/stop/geo words are dropped.
    topic = " ".join(
        w for w in text.split() if w not in _STOP_WORDS and w not in _GEO_WORDS and w not in _QUESTION_MARKERS
    ) or core
    checks = [
        ("Navigational", _NAVIGATIONAL_MARKERS, 85, f"Reach a specific site or account page for {core}"),
        ("Comparison", _COMPARISON_MARKERS, 90, f"Compare {core} options before choosing"),
        ("Transactional", ["how much"], 85, f"Check price or buy {core}"),
        ("Informational", _QUESTION_MARKERS, 85, f"Learn about {topic}"),
        ("Local", _LOCAL_MARKERS, 85, f"Find a nearby {core} provider or dealer"),
        ("Transactional", _TRANSACTIONAL_MARKERS, 85, f"Check price or buy {core}"),
        ("Informational", _INFORMATIONAL_MARKERS, 80, f"Learn about {topic}"),
        ("Commercial Investigation", _INVESTIGATION_MARKERS, 75, f"Evaluate {core} options"),
    ]
    for intent, markers, confidence, need in checks:
        marker = _has_marker(text, markers)
        if marker:
            return {"intent": intent, "family": INTENT_FAMILY[intent], "confidence": confidence,
                    "marker": marker, "user_need": need}
        if intent == "Comparison" and _EITHER_OR_RE.match(text):
            # §8/§46: "mysql or sql server" — choosing between two options.
            return {"intent": "Comparison", "family": "Comparison", "confidence": 80, "marker": "or",
                    "user_need": f"Compare {core} options before choosing"}
        if intent == "Informational" and markers is _QUESTION_MARKERS and _has_marker(text, _TROUBLESHOOT_MARKERS):
            # §8 support/task: a problem to fix is answered by a guide.
            return {"intent": "Informational", "family": "Informational", "confidence": 80,
                    "marker": "troubleshooting", "user_need": f"Solve a problem: {text}"}
    src = (source_intent or "").lower()
    if "informational" in src and "commercial" not in src and "transactional" not in src:
        return {"intent": "Informational", "family": "Informational", "confidence": 60, "marker": None,
                "user_need": f"Learn about {core}"}
    if "navigational" in src and "commercial" not in src:
        return {"intent": "Navigational", "family": "Navigational", "confidence": 60, "marker": None,
                "user_need": f"Reach a specific site or page for {core}"}
    if len(text.split()) >= 5:
        return {"intent": "Informational", "family": "Informational", "confidence": 50, "marker": None,
                "user_need": f"Learn about {core}"}
    return {"intent": "Commercial", "family": "Commercial", "confidence": 55, "marker": None,
            "user_need": f"Discover {core} options"}


def source_intent_family(source_intent: str | None) -> str | None:
    """Maps a sheet/Semrush intent label to the same families as
    detect_intent, for the manual-sheet intent recheck. None when blank."""
    src = (source_intent or "").strip().lower()
    if not src:
        return None
    if "comparison" in src:
        return "Comparison"
    if "local" in src:
        return "Local"
    if "commercial" in src or "transactional" in src:
        return "Commercial"
    if "informational" in src:
        return "Informational"
    if "navigational" in src:
        return "Navigational"
    return None


def secondary_intent(keyword: str, primary: str | None) -> str | None:
    """§8 "Secondary Intent" — a second intent the keyword's own wording
    also carries ("best truck price": Commercial Investigation +
    Transactional). None when only one intent is evidenced."""
    text = " ".join(_words(keyword))
    for intent, markers in (
        ("Comparison", _COMPARISON_MARKERS), ("Local", _LOCAL_MARKERS),
        ("Transactional", _TRANSACTIONAL_MARKERS), ("Commercial Investigation", _INVESTIGATION_MARKERS),
        ("Informational", _INFORMATIONAL_MARKERS),
    ):
        if intent != primary and _has_marker(text, markers):
            return intent
    return None


_PROBLEM_MARKERS = [
    "problem", "problems", "issue", "issues", "not working", "fix", "repair", "troubleshoot", "error", "fault",
    "failure", "breakdown", "noise", "leak", "overheating",
]
_SUPPORT_MARKERS = [
    "login", "log in", "sign in", "customer care", "customer service", "helpline", "contact number", "support",
    "manual", "warranty", "service schedule", "user guide", "track order", "tracking", "renewal", "claim status",
]


def funnel_stage(keyword: str, intent: str | None) -> str:
    """§10 search journey stage, from the keyword's own wording and its
    detected intent. Problem/support wording wins over the intent default
    (a "truck overheating fix" searcher has a problem, whatever the page)."""
    text = " ".join(_words(keyword))
    if _has_marker(text, _SUPPORT_MARKERS):
        return "Retention / Support"
    if _has_marker(text, _PROBLEM_MARKERS):
        return "Problem Discovery"
    return {
        "Informational": "Education",
        "Comparison": "Comparison",
        "Commercial Investigation": "Commercial Investigation",
        "Transactional": "Transaction",
        "Local": "Transaction",
        "Navigational": "Retention / Support",
    }.get(intent or "", "Solution Discovery")


# §11 audience — only from explicit wording in the keyword. No marker means
# the audience is simply unknown from the keyword alone, never guessed.
_AUDIENCE_MARKERS = [
    ("Business / Enterprise", ["for business", "for businesses", "for small business", "for enterprise", "enterprise",
                               "b2b", "for companies", "for company", "fleet", "for contractors", "commercial use"]),
    ("Beginner", ["for beginners", "beginner", "beginners", "for dummies", "basics"]),
    ("Student", ["for students", "student", "students", "exam", "syllabus"]),
    ("Parent / Child", ["for kids", "for children", "kids", "children", "toddler", "toddlers", "baby", "for boys",
                        "for girls", "year old", "year olds"]),
    ("Job Seeker", ["jobs", "job", "vacancy", "vacancies", "salary", "career", "careers", "hiring", "recruitment"]),
    ("Developer", ["api", "sdk", "developer", "developers", "github", "documentation"]),
    ("Professional", ["for professionals", "professional", "for doctors", "for lawyers", "for accountants",
                      "for engineers"]),
    ("Senior", ["for seniors", "for elderly", "senior citizen", "senior citizens", "retirement"]),
]


def keyword_audience(keyword: str) -> str | None:
    text = " ".join(_words(keyword))
    for audience, markers in _AUDIENCE_MARKERS:
        if _has_marker(text, markers):
            return audience
    return None


_TEMPORAL_MARKERS = ["latest", "upcoming", "new launch", "this year", "next year", "current"]


def is_temporal(keyword: str) -> bool:
    """§44 — a time-sensitive search: a year, or latest/upcoming wording."""
    words = _words(keyword)
    return any(_YEAR_RE.match(w) for w in words) or bool(_has_marker(" ".join(words), _TEMPORAL_MARKERS))


def annotate_keyword_rows(rows: list[dict]) -> None:
    """Stamps the spec's per-keyword fields (§54 master dataset) on every
    row in place: canonical_keyword, core_entity, detected_intent,
    secondary_intent, intent_confidence, intent_family, user_need,
    funnel_stage, audience, temporal, geography. Never overwrites a row's
    own `intent` (upstream Semrush/sheet value)."""
    for r in rows:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        norm = normalize_keyword(kw)
        intent = detect_intent(kw, r.get("intent"))
        r["canonical_keyword"] = norm["canonical"]
        r["core_entity"] = norm["core_phrase"]
        r["_core_key"] = norm["core_key"]
        r["_core_tokens"] = norm["core_tokens"]
        r["detected_intent"] = intent["intent"]
        r["secondary_intent"] = secondary_intent(kw, intent["intent"])
        r["intent_family"] = intent["family"]
        r["intent_confidence"] = intent["confidence"]
        r["user_need"] = intent["user_need"]
        r["funnel_stage"] = funnel_stage(kw, intent["intent"])
        r["audience"] = keyword_audience(kw)
        r["temporal"] = is_temporal(kw)
        r["geography"] = ", ".join(norm["geo"]) if norm["geo"] else None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _demand(r: dict) -> float:
    return max(_num(r.get("search_volume")), _num(r.get("gsc_clicks")))


# A parent group absorbing more distinct one-word extensions than this is a
# category, not a page (§40).
_MAX_PAGE_EXTENSIONS = 4


def build_rule_groups(rows: list[dict]) -> list[dict]:
    """§17/§28/§41/§43 — deterministic same-page grouping over EVERY row
    (rows must already be annotated). Step 1 groups by (core entity key,
    intent family): "certified payroll" / "certified payroll software" /
    "certified payrolls" share one group, while "what is certified payroll"
    (Informational) gets its own. Step 2 attaches a child group to a parent
    whose core it fully contains plus exactly one extra word (same family) —
    "government certified payroll" joins "certified payroll" — but only
    when the parent's core has 2+ words: a one-word head term ("truck")
    never absorbs "tipper truck"/"mining truck", which are different
    products (§40, never over-cluster).

    Returns groups sorted by demand, each {"key", "family", "core_phrase",
    "rows", "demand", "representative"}."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = r.get("_core_key")
        if not key:
            continue
        gkey = (key, r.get("intent_family") or "Commercial")
        g = groups.setdefault(gkey, {"key": key, "family": gkey[1], "tokens": r.get("_core_tokens") or frozenset(),
                                     "rows": [], "demand": 0.0})
        g["rows"].append(r)
        g["demand"] += _demand(r)

    # Parent attach, smallest groups first so a chain (a ⊂ b ⊂ c) resolves
    # to the most specific real parent. Index parents by token for speed.
    by_family_token: dict[tuple, list[tuple]] = {}
    for gkey, g in groups.items():
        if len(g["tokens"]) >= 2:
            for t in g["tokens"]:
                by_family_token.setdefault((g["family"], t), []).append(gkey)
    merged_into: dict[tuple, tuple] = {}
    for gkey in sorted(groups, key=lambda k: len(groups[k]["tokens"]), reverse=True):
        g = groups[gkey]
        tokens = g["tokens"]
        if len(tokens) < 3:
            continue
        candidates = set()
        for t in tokens:
            candidates.update(by_family_token.get((g["family"], t), []))
        best = None
        for pkey in candidates:
            if pkey == gkey or pkey in merged_into:
                continue
            p = groups[pkey]
            extra = len(tokens) - len(p["tokens"])
            if p["tokens"] < tokens and extra == 1:
                rank = (len(p["tokens"]), p["demand"])
                if best is None or rank > best[0]:
                    best = (rank, pkey)
        if best:
            merged_into[gkey] = best[1]

    # §40 never over-cluster: a parent that would absorb many DIFFERENT
    # one-word extensions is a category head ("sql server" + etl /
    # encryption / replication / health check / service broker...), not
    # one page — Geopits got a 64-keyword "Sql Server Server" cluster from
    # exactly this. Its children then stay their own pages. A parent with a
    # few extensions ("certified payroll" + government / software) still
    # absorbs them, as before.
    child_extensions: dict[tuple, set] = {}
    for child, parent in merged_into.items():
        child_extensions.setdefault(parent, set()).add(groups[child]["tokens"] - groups[parent]["tokens"])
    category_heads = {p for p, ext in child_extensions.items() if len(ext) > _MAX_PAGE_EXTENSIONS}
    merged_into = {c: p for c, p in merged_into.items() if p not in category_heads}

    def _root(k):
        seen = set()
        while k in merged_into and k not in seen:
            seen.add(k)
            k = merged_into[k]
        return k

    final: dict[tuple, dict] = {}
    for gkey, g in groups.items():
        root = _root(gkey)
        f = final.setdefault(root, {"key": groups[root]["key"], "family": groups[root]["family"], "rows": [], "demand": 0.0})
        f["rows"].extend(g["rows"])
        f["demand"] += g["demand"]

    out = []
    for root, f in final.items():
        rep = max(f["rows"], key=_demand)
        # Display name: the parent group's own natural-order core phrase
        # (the entity every attached child contains), most common first.
        root_rows = groups[root]["rows"]
        phrase = Counter(r.get("core_entity") or "" for r in root_rows).most_common(1)[0][0] or rep.get("core_entity")
        f["core_phrase"] = phrase
        f["representative"] = rep
        out.append(f)
    out.sort(key=lambda g: g["demand"], reverse=True)
    return out


# Display casing for cluster/topic names (§55/§60). Common acronyms and
# mixed-case product names across industries — a word not listed simply
# gets normal Title Case, so a missing entry is cosmetic, never wrong.
_ACRONYMS = {
    "ai", "api", "apis", "aws", "b2b", "b2c", "bi", "cms", "cng", "cpu", "crm", "css", "dba", "dbas", "diy", "dns",
    "erp", "etl", "faq", "gcp", "gps", "gst", "hr", "hrms", "html", "http", "https", "iot", "it", "kpi", "lng", "lpg",
    "ml", "mri", "nbfc", "oci", "pdf", "php", "pos", "ppc", "qa", "rds", "roi", "saas", "sap", "sdk", "seo", "sla",
    "sms", "sql", "ssd", "ssl", "suv", "ui", "uk", "us", "usa", "uae", "ux", "vat", "vpn", "vps",
}
_CASED_NAMES = {
    "mysql": "MySQL", "postgresql": "PostgreSQL", "postgres": "Postgres", "mongodb": "MongoDB", "nosql": "NoSQL",
    "mariadb": "MariaDB", "dynamodb": "DynamoDB", "javascript": "JavaScript", "typescript": "TypeScript",
    "iphone": "iPhone", "ipad": "iPad", "ios": "iOS", "macos": "macOS", "youtube": "YouTube", "linkedin": "LinkedIn",
    "wordpress": "WordPress", "woocommerce": "WooCommerce", "github": "GitHub", "devops": "DevOps",
    "hubspot": "HubSpot", "quickbooks": "QuickBooks", "ecommerce": "eCommerce", "bs6": "BS6", "innodb": "InnoDB",
}
_NAME_DROP_WORDS = (
    {w for w in _TRANSACTIONAL_MARKERS + _QUESTION_MARKERS + _COMPARISON_MARKERS + _NAVIGATIONAL_MARKERS
     if " " not in w and w not in ("vs", "versus")}
    | {"is", "are", "the", "a", "an", "of", "for", "in", "to", "and", "near", "me", "best", "top", "review",
       "reviews", "cheap", "cheapest", "latest"}
)


def smart_title(phrase: str) -> str:
    """Title-cases a name, keeping acronyms/product casing, and drops a
    word already used earlier in the name ("sql server server")."""
    out, seen = [], set()
    for w in (phrase or "").split():
        lw = w.lower()
        if lw in seen:
            continue
        seen.add(lw)
        if lw in ("vs", "versus"):
            out.append("vs")
            continue
        out.append(_CASED_NAMES.get(lw) or (lw.upper() if lw in _ACRONYMS else lw[:1].upper() + lw[1:]))
    return " ".join(out)


def display_phrase(keyword: str) -> str:
    """The keyword's topic as a name: intent/question/geo/year words out,
    everything that names the thing kept ("what is azure data studio" ->
    "azure data studio", "aws aurora pricing" -> "aws aurora")."""
    text = " ".join(_words(keyword))
    protected = {w for m in _NOT_A_PRICE_RE.finditer(text) for w in m.group(0).replace("-", " ").split()}
    words = [
        w for w in text.split()
        if w in protected or not (w in _NAME_DROP_WORDS or w in _GEO_WORDS or _YEAR_RE.match(w))
    ]
    return " ".join(words) or text


def rule_group_name(group: dict) -> str:
    """§60 — a specific, evidence-based cluster name: the topic of the
    group's highest-demand keyword (§19: the page's primary search),
    suffixed by intent family when it isn't the default commercial page
    (so "Certified Payroll" and "Certified Payroll — Guides" read as the
    two different pages they are)."""
    rep_kw = group["representative"].get("keyword") or ""
    base = smart_title(display_phrase(rep_kw) if rep_kw else (group.get("core_phrase") or ""))
    suffix = {"Informational": " — Guides", "Comparison": " — Comparisons", "Local": " — Local",
              "Navigational": " — Navigation"}.get(group.get("family"), "")
    if " vs " in f" {base} ":
        suffix = ""  # "PostgreSQL vs MySQL" already says it's a comparison
    return f"{base}{suffix}"


# §53 keyword-to-URL decision vocabulary, mapped from the pipeline's own
# existing_page_action values.
_SPEC_ACTION = {
    "Optimize Existing Page": "Existing URL — Primary Target",
    "Expand Existing Page": "Existing URL — Primary Target (expand content)",
    "Differentiate": "Review — weak existing-page match",
    "Create New Page": "New URL Required",
}


def spec_action(existing_page_action: str | None, confidence_level: str | None = None) -> str:
    action = existing_page_action or ""
    if action.startswith("Consolidate"):
        return "Merge Existing URLs — this page is the primary"
    if action.startswith("Differentiate or Redirect"):
        return "Restructure — differentiate or merge into the primary page"
    mapped = _SPEC_ACTION.get(action, "Review")
    if confidence_level == "Low" and not mapped.startswith("Review"):
        return f"Review — {mapped}"
    return mapped


_PAGE_TYPE_BY_FAMILY = {
    "Informational": "Guide / Blog Article",
    "Comparison": "Comparison Page",
    "Local": "Location / Dealer Page",
    "Navigational": "Existing Destination Page",
    "Commercial": "Product / Service Page",
}


def recommended_page_type(family: str | None, page_category: str | None = None) -> str:
    if page_category == "Comparison / Alternative":
        return "Comparison Page"
    return _PAGE_TYPE_BY_FAMILY.get(family or "", "Product / Service Page")


_RELEVANT_STATUSES = {"Core Relevant", "Relevant", "Validated (Manual)"}

# No SERP dataset exists for any client yet, and §15 names SERP overlap as
# the strongest same-page evidence — so no cluster may claim more certainty
# than this without it.
_MAX_CONFIDENCE_WITHOUT_SERP = 85


# Only for the split test's "shares a core term" check — never used to
# merge keywords (§5: synonyms are not merged automatically).
_SPLIT_TEST_SYNONYMS = {"lorry": "truck", "lorrie": "truck", "trucking": "truck", "coach": "bus"}


def distinct_needs(rows: list[dict], cluster_name: str | None = None) -> list[str]:
    """§42 split test input — the distinct core entities inside one
    cluster that share no word with the cluster's dominant entity (its two
    most common core terms plus the cluster name's own terms). Two or more
    of these means the cluster mixes genuinely different needs."""
    def _canon(toks):
        return frozenset(_SPLIT_TEST_SYNONYMS.get(t, t) for t in toks)

    token_sets = [(r.get("core_entity") or "", _canon(r.get("_core_tokens") or frozenset())) for r in rows]
    if not token_sets:
        return []
    counts = Counter(t for _, toks in token_sets for t in toks)
    anchor = {t for t, _ in counts.most_common(2)}
    if cluster_name:
        anchor |= _canon(normalize_keyword(cluster_name)["core_tokens"])
    outliers = []
    seen = set()
    for phrase, toks in token_sets:
        if toks and not (toks & anchor) and phrase not in seen:
            seen.add(phrase)
            outliers.append(phrase)
    return outliers


def score_cluster(rows: list[dict], source: str, cluster_name: str | None = None) -> dict:
    """§30/§61 — cluster confidence 0-100 from real evidence only:
    grouping source (AI-validated / client sheet / rule-based), intent
    agreement across members, relevance evidence, existing-page match
    strength, real ranking signal, and a coherence penalty. Returns
    {"score", "level", "reason_bits", "outliers", "dominant_family"}."""
    base = {"ai": 55, "manual": 55, "rule": 45}.get(source, 40)
    families = Counter(r.get("intent_family") or "Commercial" for r in rows)
    dominant_family, dom_n = families.most_common(1)[0] if families else ("Commercial", 0)
    intent_share = dom_n / len(rows) if rows else 0
    relevant_share = sum(1 for r in rows if r.get("relevance_status") in _RELEVANT_STATUSES) / len(rows) if rows else 0
    strength = next((r.get("existing_page_match_strength") for r in rows if r.get("existing_page_match_strength")), None)
    has_ranking = any(r.get("current_position") not in (None, "") for r in rows)
    outliers = distinct_needs(rows, cluster_name) if len(rows) >= 3 else []

    # §30/§61: doubtful keywords (flagged other brand/product/site, out of
    # market...) are evidence against the cluster, not neutral — a cluster
    # where 3 of 8 keywords are doubtful must not read "High".
    flagged_share = sum(1 for r in rows if r.get("relevance_status") in _EXCLUDED_RELEVANCE_STATUSES) / len(rows) if rows else 0

    score = base + 15 * intent_share + 10 * relevant_share - 25 * flagged_share
    score += {"strong": 10, "partial": 5}.get(strength or "", 0)
    score += 5 if has_ranking else 0
    if len(outliers) >= 2:
        score -= 15
    if source == "rule" and len(rows) == 1:
        score -= 5
    score = int(max(0, min(_MAX_CONFIDENCE_WITHOUT_SERP, round(score))))
    level = "High" if score >= 70 else ("Medium" if score >= 45 else "Low")

    reason_bits = []
    if source == "ai":
        reason_bits.append("AI-validated as one search need")
    elif source == "manual":
        reason_bits.append("client's own cluster sheet")
    else:
        reason_bits.append("rule-based grouping (shared core entity), not AI-validated")
    if intent_share == 1 and rows:
        reason_bits.append(f"all {len(rows)} keyword(s) share {dominant_family.lower()} intent")
    elif rows:
        reason_bits.append(f"{dom_n} of {len(rows)} keyword(s) share {dominant_family.lower()} intent")
    if flagged_share:
        reason_bits.append(f"{round(flagged_share * len(rows))} keyword(s) flagged for relevance review")
    if strength in ("strong", "partial"):
        reason_bits.append(f"{strength} existing-page match")
    reason_bits.append("no SERP data to confirm")
    return {"score": score, "level": level, "reason_bits": reason_bits, "outliers": outliers,
            "dominant_family": dominant_family}


def apply_cluster_intelligence(rows: list[dict]) -> None:
    """Final spec pass over clustered rows (after page matching and
    cannibalization): stamps cluster_confidence, cluster_confidence_level,
    cluster_reason, cluster_outliers, recommended_page_type and
    recommended_action (§53 vocabulary) on every row of each cluster."""
    clusters: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("cluster") or "").strip()
        if label:
            clusters.setdefault(label, []).append(r)
    for _label, cluster_rows in clusters.items():
        status = (cluster_rows[0].get("cluster_status") or "")
        source = cluster_rows[0].get("cluster_source") or ("manual" if "Manual" in status else "ai")
        if source == "routing":
            # Jobs / out-of-market / needs-review buckets are exclusions,
            # not target pages — no confidence or URL action applies.
            for r in cluster_rows:
                r["recommended_action"] = "No Target" if r.get("cluster") != "Competitor / Comparison Opportunities" else "Review"
            continue
        result = score_cluster(cluster_rows, source, _label)
        category = Counter(r.get("page_category") for r in cluster_rows if r.get("page_category")).most_common(1)
        page_type = recommended_page_type(result["dominant_family"], category[0][0] if category else None)
        for r in cluster_rows:
            r["cluster_confidence"] = result["score"]
            r["cluster_confidence_level"] = result["level"]
            r["cluster_reason"] = "; ".join(result["reason_bits"])
            r["cluster_outliers"] = result["outliers"]
            r["recommended_page_type"] = page_type
            r["recommended_action"] = spec_action(r.get("existing_page_action"), result["level"])


# ---------------------------------------------------------------------------
# Per-client cache. Stored on clients.keyword_intelligence_cache (JSONB) so
# report regeneration re-uses earlier AI verdicts instead of re-calling the
# AI — the "no extra report time" requirement. Keyed by a hash of the
# client's business context: when the company overview changes, every
# cached verdict is dropped, since relevance was judged against it.
# ---------------------------------------------------------------------------

# v2 (2026-09-24): relevance prompt gained the client's own brand family,
# site sections and the "Other Website Search" status — verdicts judged
# under the old prompt are re-asked once. v3 (same day): prompt now says a
# price search for a vendor's own product is a product/service mismatch.
_CACHE_VERSION = 3
_MAX_CACHED_CLUSTER_RUNS = 6
_MAX_CACHED_RELEVANCE = 6000


def _context_hash(client_name: str, client_description: str | None) -> str:
    return hashlib.sha1(f"{client_name}|{client_description or ''}".encode("utf-8")).hexdigest()[:16]


class KeywordIntelligenceCache:
    def __init__(self, raw: dict | None, client_name: str, client_description: str | None):
        ctx = _context_hash(client_name, client_description)
        raw = raw if isinstance(raw, dict) else {}
        if raw.get("version") != _CACHE_VERSION or raw.get("context") != ctx:
            raw = {}
        self.data = {
            "version": _CACHE_VERSION,
            "context": ctx,
            "relevance": dict(raw.get("relevance") or {}),
            "clusters": dict(raw.get("clusters") or {}),
        }
        self.dirty = False

    # Relevance verdicts, one per lowercased keyword. A fail-open verdict
    # ("AI classification unavailable") is never cached, so the next run
    # gets a real chance at it.
    def get_relevance(self, keyword: str) -> dict | None:
        return self.data["relevance"].get((keyword or "").strip().lower())

    def put_relevance(self, verdicts: dict[str, dict]) -> None:
        for kw, entry in (verdicts or {}).items():
            if not isinstance(entry, dict):
                continue
            if "unavailable" in (entry.get("reason") or "").lower():
                continue
            self.data["relevance"][kw.strip().lower()] = entry
            self.dirty = True
        if len(self.data["relevance"]) > _MAX_CACHED_RELEVANCE:
            items = list(self.data["relevance"].items())[-_MAX_CACHED_RELEVANCE:]
            self.data["relevance"] = dict(items)

    @staticmethod
    def cluster_key(representatives: list[dict], client_description: str | None) -> str:
        payload = json.dumps(
            [[m.get("keyword"), m.get("source_cluster")] for m in representatives] + [client_description or ""],
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]

    def get_clusters(self, key: str) -> list[dict] | None:
        return self.data["clusters"].get(key)

    def put_clusters(self, key: str, final_clusters: list[dict]) -> None:
        if not final_clusters:
            return
        self.data["clusters"][key] = final_clusters
        if len(self.data["clusters"]) > _MAX_CACHED_CLUSTER_RUNS:
            for old in list(self.data["clusters"].keys())[:-_MAX_CACHED_CLUSTER_RUNS]:
                del self.data["clusters"][old]
        self.dirty = True


def classify_with_cache(classify_fn, cache: "KeywordIntelligenceCache | None", keywords: list[str], *args, **kwargs) -> dict[str, dict]:
    """Wraps keyword_relevance_service.classify_keywords: cached verdicts
    are returned as-is, and only the keywords with no cached verdict are
    sent to the AI (in one call, same as before). With no cache this is
    exactly classify_fn(...)."""
    if cache is None:
        return classify_fn(*_split_args(args, keywords), **kwargs)
    result: dict[str, dict] = {}
    uncached: list[str] = []
    for kw in keywords:
        hit = cache.get_relevance(kw)
        if hit:
            result[(kw or "").strip().lower()] = hit
        elif kw:
            uncached.append(kw)
    if uncached:
        fresh = classify_fn(*_split_args(args, uncached), **kwargs) or {}
        # Only AI verdicts are cached. Rule-based excludes are free to
        # recompute and depend on the caller's own brand list (own-brand vs
        # competitor-brand), so caching them would leak one caller's brand
        # verdict into another's — e.g. the client's own brand keyword
        # judged "competitor brand" in the manual-sheet check.
        from app.services.keyword_relevance_service import _rule_exclude

        brand_tokens = args[2] if len(args) > 2 else set()
        cache.put_relevance({
            k: v for k, v in fresh.items() if not _rule_exclude(k, set(brand_tokens or ()))
        })
        result.update(fresh)
    return result


def _split_args(args: tuple, keywords: list[str]) -> tuple:
    # classify_keywords(client_name, client_domain, brand_tokens, keywords, client_description)
    return (*args[:3], keywords, *args[3:])


# ---------------------------------------------------------------------------
# Scenario A — the client's own manually-uploaded cluster sheet. The
# client's grouping is kept (it is their strategist's work, not ours to
# re-cluster); the engine only VALIDATES it: a relevance/brand/junk gate
# per keyword (§21/§45), an intent recheck against the sheet's own label
# (§8), a split-test flag (§42), and the same target-URL / action /
# confidence output as the AI path (§53/§55/§30). Flag, never rewrite.
# ---------------------------------------------------------------------------

# §21/§67 check 1: every keyword a slide can show needs a verdict. The
# slide shows up to 8 per cluster chosen by relevance first, so each
# cluster's pool must be deeper than 8. Still ONE AI call, cached after.
_MANUAL_CLASSIFY_PER_CLUSTER = 25
_MANUAL_CLASSIFY_TOTAL = 200

_EXCLUDED_RELEVANCE_STATUSES = {
    "Competitor Brand Search", "Irrelevant Competitor Query", "Geographic Mismatch",
    "Product/Service Mismatch", "Audience Mismatch", "Industry Mismatch", "Unrelated",
    "Career / Recruitment Query", "Other Website Search",
}


def manual_classify_candidates(manual_rows: list[dict]) -> list[str]:
    """The manual-sheet keywords worth one relevance check: each cluster's
    top keywords by volume (the only ones the strategic selection can ever
    show), biggest clusters first, bounded so it is always ONE AI call —
    and after the first run every verdict comes from the per-client cache."""
    by_cluster: dict[str, list[dict]] = {}
    for r in manual_rows:
        c = (r.get("cluster") or "").strip()
        if c and (r.get("keyword") or "").strip():
            by_cluster.setdefault(c, []).append(r)
    ordered = sorted(by_cluster.values(), key=lambda rs: -sum(_num(r.get("search_volume")) for r in rs))
    out: list[str] = []
    for rs in ordered:
        top = sorted(rs, key=lambda r: -_num(r.get("search_volume")))[:_MANUAL_CLASSIFY_PER_CLUSTER]
        out.extend(r["keyword"].strip() for r in top)
        if len(out) >= _MANUAL_CLASSIFY_TOTAL:
            break
    return out[:_MANUAL_CLASSIFY_TOTAL]


def gate_manual_rows(
    manual_rows: list[dict], rule_exclude, competitor_brand_check, relevance: dict[str, dict] | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """§21/§45 gate over the client's sheet. The sheet is a strategist's
    curated work, so only DETERMINISTIC, certain cases are removed:
    `rule_exclude(keyword)` -> (status_key, reason) | None (adult/18+, nav/
    login, careers) and `competitor_brand_check(keyword)` (an exact compared
    competitor's brand). The AI relevance verdicts in `relevance` only FLAG
    a keyword for client confirmation — never remove it: on BharatBenz the
    AI judged "luxury bus", "cng trucks" and the client's own parent-group
    brands ("mercedes benz bus", "daimler india commercial vehicles") as not
    relevant, which silently dropped the client's own high-volume keywords.

    Returns (kept_rows, excluded, flagged); excluded/flagged entries are
    {"keyword", "cluster", "reason"}, both surfaced on the slide."""
    kept: list[dict] = []
    excluded: list[dict] = []
    flagged: list[dict] = []
    for r in manual_rows:
        kw = (r.get("keyword") or "").strip()
        if not kw:
            continue
        cluster = (r.get("cluster") or "").strip()
        hit = rule_exclude(kw)
        if hit:
            excluded.append({"keyword": kw, "cluster": cluster, "reason": hit[1]})
            continue
        if competitor_brand_check(kw):
            excluded.append({"keyword": kw, "cluster": cluster,
                             "reason": "Another company's brand — not a keyword this business can own."})
            continue
        verdict = (relevance or {}).get(kw.lower())
        if verdict:
            r = {**r, "relevance_status": verdict.get("status")}
            if verdict.get("status") in _EXCLUDED_RELEVANCE_STATUSES:
                flagged.append({"keyword": kw, "cluster": cluster,
                                "reason": f'{verdict["status"]}: {verdict.get("reason") or ""}'.strip()})
        kept.append(r)
    return kept, excluded, flagged


_PAGE_CATEGORY_BY_FAMILY = {
    "Informational": "Blog / Guide", "Comparison": "Comparison / Alternative", "Commercial": "Landing Page",
    "Local": "Landing Page",
}


def _norm_url(url: str | None) -> str:
    u = re.sub(r"^[a-z][a-z0-9+.-]*://", "", (url or "").strip().lower())
    return u.removeprefix("www.").rstrip("/")


def ranking_page_target(
    rows: list[dict], keyword_ranking: dict[str, tuple] | None = None, dead_urls: set[str] | None = None,
) -> dict | None:
    """§23 "existing rankings": the client page Google ALREADY ranks for a
    cluster's keywords is the strongest existing-URL evidence there is —
    stronger than any word overlap between keyword and page title. Reads
    each row's own current_position/current_url (Keyword Gap own-domain
    column / Organic Positions), else `keyword_ranking` {keyword lower:
    (position, url)}. Only top-20 rankings count; each URL is weighted by
    the demand it already ranks for (top-10 counts double). A URL in
    `dead_urls` (non-2xx / error page in the crawl) never qualifies.
    Returns {"url", "position", "match_strength", "keywords"} or None."""
    dead = {_norm_url(u) for u in dead_urls or ()}
    by_url: dict[str, dict] = {}
    for r in rows:
        pos, url = r.get("current_position"), r.get("current_url")
        if (pos in (None, "") or not url) and keyword_ranking:
            pos, url = keyword_ranking.get((r.get("keyword") or "").strip().lower(), (None, None))
        p = _num(pos)
        if not url or p <= 0 or p > 20 or _norm_url(url) in dead:
            continue
        entry = by_url.setdefault(url, {"url": url, "position": p, "weight": 0.0, "keywords": 0})
        entry["weight"] += max(_demand(r), 1.0) * (2.0 if p <= 10 else 1.0)
        entry["position"] = min(entry["position"], p)
        entry["keywords"] += 1
    if not by_url:
        return None
    best = max(by_url.values(), key=lambda e: (e["weight"], -e["position"]))
    return {
        "url": best["url"], "position": int(best["position"]), "keywords": best["keywords"],
        "match_strength": "strong" if best["position"] <= 10 else "partial",
    }


def select_primary_keyword(rows: list[dict], dominant_family: str | None) -> dict | None:
    """§19-20: the page's primary keyword is NOT simply the highest-volume
    one — business relevance (not flagged), intent fit with the cluster's
    dominant intent, an existing ranking, then demand as the tiebreaker."""
    if not rows:
        return None

    def _score(r: dict) -> tuple:
        not_flagged = 0 if r.get("relevance_status") in _EXCLUDED_RELEVANCE_STATUSES else 1
        relevant = 1 if r.get("relevance_status") in _RELEVANT_STATUSES else 0
        intent_fit = 1 if dominant_family and r.get("intent_family") == dominant_family else 0
        ranking = 1 if r.get("current_position") not in (None, "") else 0
        return (not_flagged, relevant, intent_fit, ranking, _demand(r))

    return max(rows, key=_score)


def enrich_manual_clusters(
    clusters: list[dict], site_audit_pages_rows: list[dict] | None, excluded: list[dict], match_fn, page_index=None,
    flagged: list[dict] | None = None, keyword_ranking: dict[str, tuple] | None = None,
    dead_urls: set[str] | None = None,
) -> None:
    """Adds the §55 cluster output to each selected manual cluster in
    place: target_url / match_strength / recommended_action /
    recommended_page_type / confidence / confidence_level / reason /
    primary_keyword / user_need, plus the validation flags
    intent_mismatches (sheet says one intent, the keyword's own wording
    clearly says another — the keyword's `display_intent` then carries the
    corrected one, §8/§34), outliers (§42: keywords that share nothing with
    the cluster's core entity) and excluded (what the gate removed from
    this cluster). The target URL prefers the page already ranking for the
    cluster (§23) over word overlap. Sheet values themselves are never
    modified."""
    excluded_by_cluster: dict[str, list[dict]] = {}
    for e in excluded:
        excluded_by_cluster.setdefault(e["cluster"], []).append(e)
    flagged_keywords = {f["keyword"].lower(): f for f in flagged or []}
    for c in clusters:
        rows = [dict(k) for k in c["keywords"]]
        annotate_keyword_rows(rows)
        for r in rows:
            if r.get("current_position") in (None, "") and keyword_ranking:
                pos, url = keyword_ranking.get(r["keyword"].strip().lower(), (None, None))
                if pos not in (None, ""):
                    r["current_position"], r["current_url"] = pos, url
        families = Counter(r.get("intent_family") for r in rows if r.get("intent_family"))
        dominant = families.most_common(1)[0][0] if families else "Commercial"
        ranking = ranking_page_target(rows, None, dead_urls)
        if ranking:
            match = {"url": ranking["url"], "match_strength": ranking["match_strength"]}
        else:
            # Matched on the cluster's top-3-by-volume keywords: pooling every
            # keyword's tokens dilutes the match ratio for a broad client
            # cluster until even its obvious hub page reads as "weak".
            head = [r["keyword"] for r in sorted(rows, key=lambda r: -_num(r.get("search_volume")))[:3]]
            match = match_fn(
                head, site_audit_pages_rows, _PAGE_CATEGORY_BY_FAMILY.get(dominant), page_index=page_index,
            ) if site_audit_pages_rows else None
        strength = match["match_strength"] if match else "none"
        for r in rows:
            r["existing_page_match_strength"] = strength
        result = score_cluster(rows, "manual", c["cluster"])
        action = {"strong": "Optimize Existing Page", "partial": "Expand Existing Page",
                  "weak": "Differentiate"}.get(strength, "Create New Page")

        mismatches = []
        for r in rows:
            sheet_family = source_intent_family(r.get("intent"))
            if sheet_family and r.get("intent_confidence", 0) >= 80 and r.get("intent_family") != sheet_family:
                mismatches.append({"keyword": r["keyword"], "sheet": r.get("intent"), "detected": r.get("detected_intent")})
        corrected = {m["keyword"]: m["detected"] for m in mismatches}
        for kw in c["keywords"]:
            kw["display_intent"] = corrected.get(kw["keyword"]) or kw.get("intent")

        primary = select_primary_keyword(rows, dominant)
        c["primary_keyword"] = primary["keyword"] if primary else None
        c["user_need"] = primary.get("user_need") if primary else None
        c["ranking_evidence"] = ranking
        c["target_url"] = match["url"] if match and strength in ("strong", "partial") else None
        c["closest_url"] = match["url"] if match and strength == "weak" else None
        c["match_strength"] = strength
        c["recommended_action"] = spec_action(action, result["level"])
        c["recommended_page_type"] = recommended_page_type(dominant)
        c["confidence"] = result["score"]
        c["confidence_level"] = result["level"]
        c["reason"] = "; ".join(result["reason_bits"])
        c["intent_mismatches"] = mismatches
        c["outliers"] = result["outliers"]
        c["excluded"] = excluded_by_cluster.get(c["cluster"], [])
        c["relevance_flags"] = [
            flagged_keywords[r["keyword"].lower()] for r in rows if r["keyword"].lower() in flagged_keywords
        ]
        c["detected_intents"] = {r["keyword"]: r.get("detected_intent") for r in rows}
