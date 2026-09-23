"""SEO Cluster & Keyword Selection for Presentation (2026-09-21 spec).

Selects the 5-6 most strategically meaningful clusters — and, within each,
5-8 representative keywords — from a client's own manually-prepared
keyword-cluster sheet (manual_keyword_cluster_parser.py), for a dedicated
client-facing Target Keywords section. When a manual sheet exists its
output REPLACES the Semrush/GSC + AI-clustered Target Keywords slides
(2026-09-23 user instruction); with no manual sheet this returns nothing
and the AI Phase 2/3 pipeline's Target Keywords slides render instead.

Deliberately deterministic, no AI call: the sheet is already the client's
own curated source of truth, so this is a scoring/ranking/dedup problem
over real data already present, not a judgment call needing an LLM.

Every Keyword/Search Volume/KD/Intent value in the output is copied
verbatim from the uploaded sheet — never estimated, modified, or
fabricated (spec section 5). Rows the sheet didn't give a value for simply
don't carry that field, same discipline manual_keyword_cluster_parser
already applies at parse time.
"""

import re
from difflib import SequenceMatcher

# A cluster needs at least this many keywords to be a "strategically
# meaningful" candidate at all — same floor reasoning as Programmatic SEO's
# _PROGRAMMATIC_MIN_SUBPAGES (pptx_builder.py): below this it's a stray
# grouping, not a real topic worth its own presentation section.
_MIN_CLUSTER_KEYWORDS = 3
# Aim for 5-6 clusters, 5-8 keywords each (spec sections 1/3) — both are
# ceilings only. Nothing here ever pads a short list to reach them; a
# dataset that genuinely supports fewer shows fewer (spec sections 3/7).
_MAX_SELECTED_CLUSTERS = 6
_MAX_KEYWORDS_PER_CLUSTER = 8

# Two cluster labels overlapping this much (by distinctive-word Jaccard) are
# the same topic/intent wearing different phrasing — selecting both would
# violate section 1's "avoid selecting clusters that substantially overlap."
_CLUSTER_OVERLAP_JACCARD = 0.6
# Same threshold Programmatic SEO uses for "this keyword isn't a distinct
# intent from one already kept" — reused here for within-cluster near-
# duplicate keyword consolidation (section 4).
_KEYWORD_OVERLAP_JACCARD = 0.6
_TYPO_RATIO = 0.82

_COMMERCIAL_INTENT_MARKERS = ("commercial", "transactional", "buy", "purchase")


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def _fuzzy_match(token: str, other_tokens: set[str]) -> bool:
    return any(SequenceMatcher(None, token, t).ratio() >= _TYPO_RATIO for t in other_tokens)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize(values: list[float]) -> dict[int, float]:
    """index -> value scaled to [0, 1] against this set's own min/max — a
    self-relative scale (this dataset's own spread), never an absolute
    external benchmark, consistent with every other normalized score in
    this codebase (e.g. build_high_potential_countries' own dataset-
    relative CTR benchmark)."""
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi == lo:
        return {i: 0.5 for i in range(len(values))}
    return {i: (v - lo) / (hi - lo) for i, v in enumerate(values)}


def _commercial_share(rows: list[dict]) -> float | None:
    with_intent = [r for r in rows if r.get("intent")]
    if not with_intent:
        return None
    hits = sum(1 for r in with_intent if any(m in r["intent"].lower() for m in _COMMERCIAL_INTENT_MARKERS))
    return hits / len(with_intent)


def _cluster_signals(cluster: str, rows: list[dict]) -> dict:
    volumes = [_num(r.get("search_volume")) for r in rows if _num(r.get("search_volume")) is not None]
    kds = [_num(r.get("keyword_difficulty")) for r in rows if _num(r.get("keyword_difficulty")) is not None]
    sub_categories = {r["sub_category"] for r in rows if r.get("sub_category")}
    intents = {r["intent"].strip().lower() for r in rows if r.get("intent")}
    return {
        "cluster": cluster,
        "rows": rows,
        "keyword_count": len(rows),
        "total_volume": sum(volumes) if volumes else 0.0,
        "avg_kd": (sum(kds) / len(kds)) if kds else None,
        "sub_category_count": len(sub_categories),
        "intent_count": len(intents),
        "commercial_share": _commercial_share(rows),
    }


def _score_clusters(cluster_signals: list[dict]) -> None:
    """Composite, multi-signal score written back onto each entry as
    "_score" — depth, demand, SEO opportunity (inverse KD), and topic/
    intent diversity each contribute roughly equally, so a cluster can't
    win purely by keyword count or purely by raw volume (spec section 1's
    explicit ban on selecting "only because they contain the highest
    number of keywords or highest total search volume")."""
    depth = _normalize([c["keyword_count"] for c in cluster_signals])
    demand = _normalize([c["total_volume"] for c in cluster_signals])
    diversity = _normalize([c["sub_category_count"] + c["intent_count"] for c in cluster_signals])

    # Lower KD = more SEO opportunity; a cluster with no KD data at all
    # gets a neutral 0.5 rather than being penalized for a data gap —
    # normalize only over the clusters that actually have a KD, keyed by
    # each cluster dict's own identity so a missing value never shifts
    # another cluster's index alignment.
    known_kd_clusters = [c for c in cluster_signals if c["avg_kd"] is not None]
    kd_norm_by_id = {}
    if known_kd_clusters:
        kd_norm = _normalize([c["avg_kd"] for c in known_kd_clusters])
        kd_norm_by_id = {id(c): kd_norm[i] for i, c in enumerate(known_kd_clusters)}

    commercial_known = [c["commercial_share"] for c in cluster_signals if c["commercial_share"] is not None]
    commercial_fallback = (sum(commercial_known) / len(commercial_known)) if commercial_known else 0.5

    for i, c in enumerate(cluster_signals):
        opportunity = 1.0 - kd_norm_by_id[id(c)] if id(c) in kd_norm_by_id else 0.5
        commercial = c["commercial_share"] if c["commercial_share"] is not None else commercial_fallback
        c["_score"] = (depth.get(i, 0.5) + demand.get(i, 0.5) + opportunity + diversity.get(i, 0.5) + commercial) / 5.0


def _cluster_labels_overlap(a: set[str], b: set[str]) -> bool:
    """Same topic only when the shorter label is (fuzzily) contained in the
    longer one AND that shared part is most of the longer label too.
    Containment alone was too loose (2026-09-23, BharatBenz manual sheet):
    "Trucks" sat inside "Truck Types & Applications", "Truck Price &
    Buying", "Truck Parts & Components"... so every Truck sub-cluster was
    dropped as a "duplicate" of Trucks and only 2 of the sheet's clusters
    reached the deck. Fuzzy per-token match still catches "Service" vs
    "Services" phrasing variants."""
    if not a or not b:
        return False
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    if not all(_fuzzy_match(t, large) for t in small):
        return False
    shared_in_large = sum(1 for t in large if _fuzzy_match(t, small))
    return shared_in_large / len(large) >= _CLUSTER_OVERLAP_JACCARD


def _select_non_overlapping_clusters(ranked: list[dict], max_n: int) -> list[dict]:
    selected: list[dict] = []
    selected_tokens: list[set[str]] = []
    for c in ranked:
        if len(selected) >= max_n:
            break
        tokens = _tokens(c["cluster"])
        if any(_cluster_labels_overlap(tokens, seen) for seen in selected_tokens):
            continue  # substantially overlaps a higher-ranked cluster already selected
        selected.append(c)
        selected_tokens.append(tokens)
    return selected


def _dedupe_and_rank_keywords(cluster_label: str, rows: list[dict], used_keywords: set[str]) -> list[dict]:
    """Within one cluster: drops keywords already used by an earlier
    selected cluster (global no-repeat, section 4), then consolidates
    near-duplicate phrasings of the same query into whichever one scores
    higher (section 4's "select the keyword with greater strategic
    value"), same token-overlap/typo-fuzzy approach Programmatic SEO uses
    for its own sub-page consolidation."""
    hub_tokens = _tokens(cluster_label)

    def _kw_score(r: dict) -> float:
        vol = _num(r.get("search_volume")) or 0.0
        kd = _num(r.get("keyword_difficulty"))
        opportunity = (100.0 - kd) if kd is not None else 50.0
        commercial = 20.0 if r.get("intent") and any(m in r["intent"].lower() for m in _COMMERCIAL_INTENT_MARKERS) else 0.0
        sub_bonus = 5.0 if r.get("sub_category") else 0.0
        return vol * 0.01 + opportunity + commercial + sub_bonus

    candidates = [r for r in rows if (r.get("keyword") or "").strip().lower() not in used_keywords]
    candidates.sort(key=_kw_score, reverse=True)

    kept: list[dict] = []
    kept_token_sets: list[set[str]] = []
    for r in candidates:
        keyword = r["keyword"]
        tokens = _tokens(keyword) - hub_tokens
        dup_of = None
        for seen_tokens in kept_token_sets:
            fuzzy_all = tokens and seen_tokens and all(_fuzzy_match(t, seen_tokens) for t in tokens)
            if _jaccard(tokens, seen_tokens) >= _KEYWORD_OVERLAP_JACCARD or fuzzy_all:
                dup_of = seen_tokens
                break
        if dup_of is not None:
            continue  # a lower-scored near-duplicate of a keyword already kept — the higher scorer wins (section 4)
        kept.append(r)
        kept_token_sets.append(tokens)
    return kept


def _select_representative_keywords(cluster_label: str, rows: list[dict], used_keywords: set[str]) -> list[dict]:
    """Picks up to _MAX_KEYWORDS_PER_CLUSTER keywords, spreading selection
    across sub-categories where the sheet provides them (section 2/6 —
    "select keywords across relevant sub-categories to provide broader
    topic coverage") rather than letting one sub-category's keywords crowd
    out the others just because they happen to score highest individually."""
    deduped = _dedupe_and_rank_keywords(cluster_label, rows, used_keywords)
    if not deduped:
        return []

    by_sub: dict[str, list[dict]] = {}
    no_sub: list[dict] = []
    for r in deduped:
        sub = r.get("sub_category")
        if sub:
            by_sub.setdefault(sub, []).append(r)
        else:
            no_sub.append(r)

    selected: list[dict] = []
    if by_sub:
        # Round-robin: one keyword from each sub-category per pass, each
        # sub-category's own list already ranked by _dedupe_and_rank_keywords'
        # score order, so pass 1 takes every sub-category's single best
        # keyword before any sub-category gets a second.
        sub_lists = list(by_sub.values())
        idx = 0
        while len(selected) < _MAX_KEYWORDS_PER_CLUSTER:
            progressed = False
            for lst in sub_lists:
                if idx < len(lst) and len(selected) < _MAX_KEYWORDS_PER_CLUSTER:
                    selected.append(lst[idx])
                    progressed = True
            idx += 1
            if not progressed:
                break
        # Any remaining room after every sub-category is exhausted goes to
        # the next-best no-sub-category keywords, if there's still room.
        for r in no_sub:
            if len(selected) >= _MAX_KEYWORDS_PER_CLUSTER:
                break
            selected.append(r)
    else:
        selected = deduped[:_MAX_KEYWORDS_PER_CLUSTER]

    return selected


def select_strategic_clusters(manual_rows: list[dict]) -> list[dict]:
    """Entry point. `manual_rows` is manual_keyword_cluster_parser's own
    row shape (keyword/cluster + optional sub_category/search_volume/
    keyword_difficulty/intent). Returns up to _MAX_SELECTED_CLUSTERS
    entries: [{"cluster": str, "keywords": [{"keyword", "search_volume"?,
    "keyword_difficulty"?, "intent"?, "sub_category"?}, ...]}], ranked by
    strategic score, highest first. Empty list when there's no manual data
    or nothing clears the minimum-cluster-size floor — never a manufactured
    fallback."""
    if not manual_rows:
        return []

    by_cluster: dict[str, list[dict]] = {}
    for r in manual_rows:
        cluster = (r.get("cluster") or "").strip()
        keyword = (r.get("keyword") or "").strip()
        if not cluster or not keyword:
            continue
        by_cluster.setdefault(cluster, []).append(r)

    candidates = [
        _cluster_signals(cluster, rows) for cluster, rows in by_cluster.items()
        if len(rows) >= _MIN_CLUSTER_KEYWORDS
    ]
    if not candidates:
        return []

    _score_clusters(candidates)
    candidates.sort(key=lambda c: -c["_score"])
    selected_clusters = _select_non_overlapping_clusters(candidates, _MAX_SELECTED_CLUSTERS)

    used_keywords: set[str] = set()
    output = []
    for c in selected_clusters:
        keywords = _select_representative_keywords(c["cluster"], c["rows"], used_keywords)
        if not keywords:
            continue
        for r in keywords:
            used_keywords.add((r.get("keyword") or "").strip().lower())
        output.append({
            "cluster": c["cluster"],
            "keywords": [
                {
                    k: r[k] for k in ("keyword", "search_volume", "keyword_difficulty", "intent", "sub_category")
                    if k in r
                }
                for r in keywords
            ],
        })
    return output
