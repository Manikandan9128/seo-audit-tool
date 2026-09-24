"""Zero-cost SERP-API alternative signals for keyword clustering
(D:\\SNIP\\SERP_API_Alternatives_for_SEO_Keyword_Clustering.docx, 2026-09-24
— given after the user ruled out a paid SERP API: "USE THIS INSTEAD").

TF-IDF cosine similarity stands in for "semantic embeddings" (item 1) — no
sentence-transformer model, no new heavy dependency, computed fresh from
THIS report's own keyword corpus (item 11: keyword co-occurrence/corpus
analysis), never a pretrained external one. Combined with the
intent/entity/user-need/audience/modifier/keyword-to-page/architecture
signals the engine already computes elsewhere (items 2-9) into one
configurable weighted score (item 15) — used where a bare word-overlap
check misses a same-need pair that only the AI grouping step used to catch
(see keyword_cluster_pipeline._same_topic's known limit).

Item 17's limitation stays true here too: this is not real SERP overlap.
Nothing in this module claims to observe Google's actual search results —
it only approximates "does this look like the same underlying need," the
same honesty rule every other Blocked-on-SERP item in
docs/keyword_engine_spec_coverage.md already follows.
"""

import math
import re
from collections import Counter

from app.services.keyword_intelligence_service import _STOP_WORDS, modifier_types, normalize_keyword

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# §15 — starting weights from the spec doc, configurable (not a fixed
# clustering rule this file's callers must accept unmodified).
SIGNAL_WEIGHTS = {
    "semantic": 0.25, "intent": 0.20, "entity": 0.15, "user_need": 0.15,
    "keyword_page": 0.10, "audience": 0.05, "modifier": 0.05, "architecture": 0.05,
}


def _tfidf_tokens(keyword: str) -> list[str]:
    norm = normalize_keyword(keyword or "")
    tokens = sorted(norm["core_tokens"])
    return tokens or [t for t in _TOKEN_RE.findall((keyword or "").lower()) if t not in _STOP_WORDS]


def build_corpus_idf(keywords: list[str]) -> dict[str, float]:
    """§11 — document frequency across every keyword in THIS report's own
    corpus, not a static/pretrained one, so IDF reflects this client's
    actual keyword set."""
    n = len(keywords) or 1
    doc_freq: Counter = Counter()
    for kw in keywords:
        for t in set(_tfidf_tokens(kw)):
            doc_freq[t] += 1
    return {t: math.log((1 + n) / (1 + df)) + 1 for t, df in doc_freq.items()}


def tfidf_vector(keyword: str, idf: dict[str, float]) -> dict[str, float]:
    tokens = _tfidf_tokens(keyword)
    if not tokens:
        return {}
    tf = Counter(tokens)
    return {t: (count / len(tokens)) * idf.get(t, 1.0) for t, count in tf.items()}


def cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return round(dot / (na * nb), 4) if na and nb else 0.0


def semantic_similarity(keyword_a: str, keyword_b: str, idf: dict[str, float]) -> float:
    """§1 — TF-IDF cosine similarity between two raw keyword strings."""
    return cosine_similarity(tfidf_vector(keyword_a, idf), tfidf_vector(keyword_b, idf))


# Each component below defaults to 0.5 (neutral) when its evidence is
# missing on either side, so an absent field never drags the combined
# score toward "different" just because the data isn't there — the same
# discipline keyword_opportunity() already uses for missing factors
# (keyword_strategy_service.py).
def _intent_score(a: dict, b: dict) -> float:
    fa, fb = a.get("intent_family"), b.get("intent_family")
    if not fa or not fb:
        return 0.5
    return 1.0 if fa == fb else 0.0


def _entity_score(a: dict, b: dict) -> float:
    ea, eb = a.get("entity_type"), b.get("entity_type")
    if not ea or not eb:
        return 0.5
    return 1.0 if ea == eb else 0.0


def _user_need_score(a: dict, b: dict) -> float:
    na, nb = (a.get("user_need") or "").lower(), (b.get("user_need") or "").lower()
    if not na or not nb:
        return 0.5
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.5


def _keyword_page_score(a: dict, b: dict) -> float:
    ua, ub = a.get("current_url"), b.get("current_url")
    if not ua or not ub:
        return 0.5
    return 1.0 if ua.rstrip("/").lower() == ub.rstrip("/").lower() else 0.0


def _audience_score(a: dict, b: dict) -> float:
    aa, ab = a.get("audience"), b.get("audience")
    if not aa or not ab:
        return 0.5
    return 1.0 if aa == ab else 0.0


def _modifier_score(a: dict, b: dict) -> float:
    ma, mb = set(modifier_types(a.get("keyword") or "")), set(modifier_types(b.get("keyword") or ""))
    if not ma or not mb:
        return 0.5
    return len(ma & mb) / len(ma | mb) if ma | mb else 0.5


def _architecture_score(a: dict, b: dict, site_model: dict | None) -> float:
    pages = {p["url"].rstrip("/").lower(): p for p in (site_model or {}).get("pages") or []}
    pa = pages.get((a.get("current_url") or "").rstrip("/").lower())
    pb = pages.get((b.get("current_url") or "").rstrip("/").lower())
    if not pa or not pb:
        return 0.5
    return 1.0 if pa.get("page_type") == pb.get("page_type") else 0.0


def combined_similarity(row_a: dict, row_b: dict, idf: dict[str, float], site_model: dict | None = None) -> float:
    """§15 — the weighted combination of every zero-API replacement signal.
    `row_a`/`row_b` are keyword rows (annotate_keyword_rows output — keyword/
    intent_family/entity_type/user_need/audience/current_url already on
    them when available)."""
    scores = {
        "semantic": semantic_similarity(row_a.get("keyword") or "", row_b.get("keyword") or "", idf),
        "intent": _intent_score(row_a, row_b),
        "entity": _entity_score(row_a, row_b),
        "user_need": _user_need_score(row_a, row_b),
        "keyword_page": _keyword_page_score(row_a, row_b),
        "audience": _audience_score(row_a, row_b),
        "modifier": _modifier_score(row_a, row_b),
        "architecture": _architecture_score(row_a, row_b, site_model),
    }
    return round(sum(SIGNAL_WEIGHTS[k] * v for k, v in scores.items()), 4)


class UnionFind:
    """§12/§13 — plain union-find, the deterministic dependency-free stand-in
    for graph-based/density-based clustering (no networkx, no HDBSCAN):
    connected components of a similarity graph ARE the candidate clusters,
    and a node nothing connects to stays its own singleton — exactly
    HDBSCAN's "keep low-confidence points unclassified" behavior, achieved
    by simply never merging it."""

    def __init__(self, items: list):
        self._parent = {i: i for i in items}

    def find(self, x):
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self) -> list[list]:
        by_root: dict = {}
        for item in self._parent:
            by_root.setdefault(self.find(item), []).append(item)
        return list(by_root.values())


def graph_cluster(rows: list[dict], idf: dict[str, float], threshold: float = 0.6,
                  site_model: dict | None = None) -> list[list[dict]]:
    """§12/§13 candidate clustering over an already-homogeneous bucket
    (same business theme/intent/page category — the caller's hard boundary,
    never crossed here): union two rows whenever their combined_similarity
    clears `threshold`. O(n^2) — only meant for one bucket's rows, not a
    whole report's keyword list."""
    if len(rows) <= 1:
        return [rows] if rows else []
    uf = UnionFind(list(range(len(rows))))
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if combined_similarity(rows[i], rows[j], idf, site_model) >= threshold:
                uf.union(i, j)
    return [[rows[i] for i in group] for group in uf.groups()]
