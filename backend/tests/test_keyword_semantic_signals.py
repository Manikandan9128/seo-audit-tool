"""Zero-cost SERP-API alternative signals (SERP_API_Alternatives_for_SEO_
Keyword_Clustering.docx, 2026-09-24) — TF-IDF cosine similarity standing in
for embeddings, combined with the engine's existing intent/entity/user-need/
audience/modifier/page/architecture signals into one weighted score, plus a
dependency-free union-find "graph clustering" stand-in for HDBSCAN.

This module is wired ONLY into the review queue (a genuinely new zero-API
same-need pair gets flagged for a human to confirm), never into automatic
merging — an earlier attempt to fold combined_similarity into the merge
test itself (keyword_cluster_pipeline._merge_same_page_clusters) regressed
two existing guardrail tests (over-merged "sql index"/"sql backup" that
only share a site-wide head word) because current_url/intent_family are
already pre-equalized by that caller, making those two signal components
uninformative there and inflating the score. Kept out of that path."""

from app.services.keyword_semantic_signals import (
    SIGNAL_WEIGHTS, UnionFind, build_corpus_idf, combined_similarity, graph_cluster, semantic_similarity,
    tfidf_vector,
)


def test_signal_weights_sum_to_one():
    assert abs(sum(SIGNAL_WEIGHTS.values()) - 1.0) < 1e-9


def test_idf_penalizes_a_word_common_across_the_corpus():
    keywords = ["payroll software", "payroll pricing", "payroll for construction", "hr onboarding tool"]
    idf = build_corpus_idf(keywords)
    assert idf["payroll"] < idf["hr"]  # "payroll" appears in 3/4 keywords, "hr" in 1/4


def test_semantic_similarity_identical_keyword_is_high_unrelated_is_zero():
    idf = build_corpus_idf(["payroll software pricing", "payroll software demo", "unrelated topic entirely"])
    assert semantic_similarity("payroll software pricing", "payroll software pricing", idf) == 1.0
    assert semantic_similarity("payroll software pricing", "unrelated topic entirely", idf) == 0.0


def test_combined_similarity_is_exactly_neutral_with_zero_evidence_either_side():
    idf = build_corpus_idf(["widget a", "widget b"])
    a, b = {"keyword": "widget a"}, {"keyword": "gadget z"}
    # No shared tokens (semantic=0) and every other field absent — every
    # component with no real evidence on either side must be neutral (0.5),
    # never accidentally "matching" just because both sides are None.
    score = combined_similarity(a, b, idf)
    expected = SIGNAL_WEIGHTS["semantic"] * 0.0 + (1 - SIGNAL_WEIGHTS["semantic"]) * 0.5
    assert abs(score - round(expected, 4)) < 1e-6


def test_combined_similarity_rewards_real_agreement_and_penalizes_real_disagreement():
    idf = build_corpus_idf(["remote dba services", "database support services", "payroll for enterprise"])
    same_need_a = {"keyword": "remote dba services", "intent_family": "Commercial", "entity_type": "Service",
                   "user_need": "hire a database administrator", "audience": None, "current_url": None}
    same_need_b = {"keyword": "database support services", "intent_family": "Commercial", "entity_type": "Service",
                   "user_need": "hire a database administrator", "audience": None, "current_url": None}
    different = {"keyword": "payroll for enterprise", "intent_family": "Commercial", "entity_type": "Product",
                 "user_need": "buy payroll software", "audience": "Business / Enterprise", "current_url": None}
    high = combined_similarity(same_need_a, same_need_b, idf)
    low = combined_similarity(same_need_a, different, idf)
    # TF-IDF alone can't bridge "dba" vs "database" (zero literal overlap —
    # exactly item 17's stated limitation), so the ceiling here comes from
    # intent+entity+user_need agreeing, not semantics. Still clearly and
    # consistently above a real mismatch.
    assert high > low
    assert high >= 0.6


def test_combined_similarity_same_category_different_need_scores_lower_than_genuine_match():
    """Regression guard: this exact shape (same intent/entity/page, a
    real different need) over-merged in keyword_cluster_pipeline's
    _merge_same_page_clusters when combined_similarity was tried there
    directly — that integration was reverted; this module is only used
    for a review-queue signal now, at a threshold picked from these two
    numbers (see keyword_strategy_depth.deepen_topics)."""
    idf = build_corpus_idf(["sql index", "sql backup", "sql join"])
    index_row = {"keyword": "sql index", "intent_family": "Informational", "entity_type": "Concept",
                 "user_need": "understand database indexing", "audience": None, "current_url": "https://x.com/blog/sql"}
    backup_row = {"keyword": "sql backup", "intent_family": "Informational", "entity_type": "Concept",
                  "user_need": "learn how to back up a database", "audience": None, "current_url": "https://x.com/blog/sql"}
    same_topic_score = combined_similarity(index_row, backup_row, idf)

    idf2 = build_corpus_idf(["remote dba services", "database support services"])
    dba_a = {"keyword": "remote dba services", "intent_family": "Commercial", "entity_type": "Service",
             "user_need": "hire a database administrator", "audience": None, "current_url": None}
    dba_b = {"keyword": "database support services", "intent_family": "Commercial", "entity_type": "Service",
             "user_need": "hire a database administrator", "audience": None, "current_url": None}
    genuine_match_score = combined_similarity(dba_a, dba_b, idf2)

    assert same_topic_score < genuine_match_score


def test_union_find_groups_connected_items_and_leaves_isolates_alone():
    uf = UnionFind([0, 1, 2, 3])
    uf.union(0, 1)
    uf.union(1, 2)
    groups = {frozenset(g) for g in uf.groups()}
    assert frozenset({0, 1, 2}) in groups
    assert frozenset({3}) in groups


def test_graph_cluster_groups_same_need_rows_and_keeps_unrelated_ones_separate():
    rows = [
        {"keyword": "remote dba services", "intent_family": "Commercial", "entity_type": "Service",
         "user_need": "hire a dba", "audience": None, "current_url": None},
        {"keyword": "database support services", "intent_family": "Commercial", "entity_type": "Service",
         "user_need": "hire a dba", "audience": None, "current_url": None},
        {"keyword": "payroll for construction", "intent_family": "Commercial", "entity_type": "Product",
         "user_need": "buy payroll software", "audience": "Business / Enterprise", "current_url": None},
    ]
    idf = build_corpus_idf([r["keyword"] for r in rows])
    groups = graph_cluster(rows, idf, threshold=0.65)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]
    merged = next(g for g in groups if len(g) == 2)
    assert {r["keyword"] for r in merged} == {"remote dba services", "database support services"}


def test_graph_cluster_handles_empty_and_singleton_input():
    assert graph_cluster([], {}) == []
    row = {"keyword": "solo keyword"}
    assert graph_cluster([row], build_corpus_idf(["solo keyword"])) == [[row]]
