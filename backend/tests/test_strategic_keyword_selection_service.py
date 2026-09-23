from app.services.strategic_keyword_selection_service import select_strategic_clusters


def _row(keyword, cluster, sub_category=None, volume=None, kd=None, intent=None):
    r = {"keyword": keyword, "cluster": cluster}
    if sub_category is not None:
        r["sub_category"] = sub_category
    if volume is not None:
        r["search_volume"] = volume
    if kd is not None:
        r["keyword_difficulty"] = kd
    if intent is not None:
        r["intent"] = intent
    return r


def test_empty_input_returns_empty_list():
    assert select_strategic_clusters([]) == []
    assert select_strategic_clusters(None) == []


def test_cluster_below_minimum_keyword_floor_excluded():
    rows = [_row("a", "Tiny Cluster"), _row("b", "Tiny Cluster")]  # only 2, floor is 3
    assert select_strategic_clusters(rows) == []


def test_caps_at_six_clusters_never_manufactures_more():
    topics = [
        "Heavy Trucks", "Fleet Management", "Truck Financing", "Spare Parts", "Driver Training",
        "Roadside Assistance", "Vehicle Leasing", "Cargo Insurance", "Route Planning", "Fuel Cards",
    ]
    rows = []
    for t_i, topic in enumerate(topics):
        for k in range(4):
            rows.append(_row(f"{topic.lower()} option {k}", topic, volume=1000 - t_i * 10 - k, kd=30 + k))
    result = select_strategic_clusters(rows)
    assert len(result) == 6


def test_caps_at_eight_keywords_per_cluster():
    rows = [_row(f"widget topic {i} distinct", "Widgets", volume=1000 - i, kd=30) for i in range(12)]
    result = select_strategic_clusters(rows)
    assert len(result) == 1
    assert len(result[0]["keywords"]) <= 8


def test_original_values_preserved_exactly_not_estimated():
    rows = [
        _row("widget pricing", "Widgets", sub_category="Pricing", volume=1234, kd=42, intent="Commercial"),
        _row("widget install", "Widgets", sub_category="Install", volume=500, kd=20, intent="Informational"),
        _row("widget repair", "Widgets", sub_category="Repair", volume=300, kd=15, intent="Informational"),
    ]
    result = select_strategic_clusters(rows)
    by_kw = {k["keyword"]: k for k in result[0]["keywords"]}
    assert by_kw["widget pricing"]["search_volume"] == 1234
    assert by_kw["widget pricing"]["keyword_difficulty"] == 42
    assert by_kw["widget pricing"]["intent"] == "Commercial"


def test_diversity_and_opportunity_let_a_smaller_cluster_outrank_a_bigger_low_quality_one():
    # A huge, high-volume cluster that's all one sub-category/intent and
    # very hard (high KD) vs. a smaller, diverse, low-KD cluster — the ban
    # on "highest count/volume alone" means the diverse one must still
    # make a top-6 cut even though it has far fewer keywords/less volume.
    rows = []
    for i in range(30):
        rows.append(_row(f"bulk keyword {i}", "Bulk Cluster", sub_category="Only", volume=5000, kd=95, intent="Navigational"))
    for i, (sub, intent) in enumerate([("A", "Informational"), ("B", "Commercial"), ("C", "Transactional")]):
        rows.append(_row(f"diverse keyword {i}", "Diverse Cluster", sub_category=sub, volume=200, kd=10, intent=intent))
    result = select_strategic_clusters(rows)
    clusters_shown = {c["cluster"] for c in result}
    assert "Diverse Cluster" in clusters_shown


def test_overlapping_cluster_labels_deduped_to_the_stronger_one():
    rows = []
    for i in range(5):
        rows.append(_row(f"seo service keyword {i}", "SEO Services", volume=1000, kd=30))
    for i in range(5):
        rows.append(_row(f"seo service item {i}", "SEO Service Solutions", volume=100, kd=60))
    result = select_strategic_clusters(rows)
    clusters_shown = [c["cluster"] for c in result]
    assert "SEO Services" in clusters_shown
    assert "SEO Service Solutions" not in clusters_shown


def test_near_duplicate_keywords_consolidated_keeping_higher_scorer():
    rows = [
        _row("widget pricing calculator", "Widgets", volume=1000, kd=20),
        _row("widget pricing calculator tool", "Widgets", volume=50, kd=80),  # near-dup, lower value
        _row("widget install guide", "Widgets", volume=500, kd=25),
        _row("widget repair steps", "Widgets", volume=300, kd=30),
    ]
    result = select_strategic_clusters(rows)
    keywords = [k["keyword"] for k in result[0]["keywords"]]
    assert "widget pricing calculator" in keywords
    assert "widget pricing calculator tool" not in keywords


def test_sub_category_coverage_round_robin_not_dominated_by_one_subcategory():
    rows = []
    # Pricing sub-category has 6 high-scoring keywords; two other
    # sub-categories have 1 each — round-robin must still surface both of
    # the smaller sub-categories' keywords, not let Pricing fill everything.
    for i in range(6):
        rows.append(_row(f"pricing keyword {i}", "Widgets", sub_category="Pricing", volume=900 - i, kd=20))
    rows.append(_row("install keyword", "Widgets", sub_category="Install", volume=100, kd=20))
    rows.append(_row("repair keyword", "Widgets", sub_category="Repair", volume=100, kd=20))
    result = select_strategic_clusters(rows)
    keywords = [k["keyword"] for k in result[0]["keywords"]]
    assert "install keyword" in keywords
    assert "repair keyword" in keywords


def test_no_keyword_repeated_across_selected_clusters():
    rows = [
        _row("shared term", "Cluster A", volume=900, kd=20),
        _row("a filler one", "Cluster A", volume=800, kd=20),
        _row("a filler two", "Cluster A", volume=700, kd=20),
        _row("shared term", "Cluster B", volume=900, kd=20),
        _row("b filler one", "Cluster B", volume=800, kd=20),
        _row("b filler two", "Cluster B", volume=700, kd=20),
    ]
    result = select_strategic_clusters(rows)
    all_keywords = [k["keyword"] for c in result for k in c["keywords"]]
    assert all_keywords.count("shared term") <= 1


def test_sub_topic_clusters_sharing_one_word_are_not_treated_as_duplicates():
    # BharatBenz manual sheet, 2026-09-23: "Trucks" was selected first and
    # every "Truck X" cluster was then dropped as its "duplicate".
    rows = []
    for label in ("Trucks", "Truck Types & Applications", "Truck Price & Buying", "Truck Parts & Components"):
        for i in range(4):
            rows.append(_row(f"{label.lower()} keyword {i}", label, volume=1000, kd=30))
    clusters_shown = {c["cluster"] for c in select_strategic_clusters(rows)}
    assert clusters_shown == {"Trucks", "Truck Types & Applications", "Truck Price & Buying", "Truck Parts & Components"}
