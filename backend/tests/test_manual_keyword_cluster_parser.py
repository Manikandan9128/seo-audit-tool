import pytest

from app.services.manual_keyword_cluster_parser import parse_manual_keyword_cluster_file


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


def test_parses_keyword_and_cluster_columns():
    content = _csv("Keyword,Cluster\n6x4 truck,6x4 Truck Configuration\n6x4 truck price,6x4 Truck Configuration\n")
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    assert result["row_count"] == 2
    keywords = {r["keyword"] for r in result["rows"]}
    assert keywords == {"6x4 truck", "6x4 truck price"}
    assert all(r["cluster"] == "6x4 Truck Configuration" for r in result["rows"])


def test_accepts_topic_and_group_as_cluster_column_aliases():
    content = _csv("Keyword,Topic\nheavy truck,Heavy Trucks\n")
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    assert result["rows"][0]["cluster"] == "Heavy Trucks"


def test_missing_required_columns_raises_clear_error():
    content = _csv("Term,Bucket\nheavy truck,Heavy Trucks\n")
    with pytest.raises(ValueError, match="Keyword"):
        parse_manual_keyword_cluster_file("clusters.csv", content)


def test_explicit_primary_secondary_column_is_respected():
    content = _csv(
        "Keyword,Cluster,Primary/Secondary\n"
        "6x4 truck,6x4 Truck Configuration,Primary\n"
        "6x4 truck price,6x4 Truck Configuration,Secondary\n"
    )
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    by_kw = {r["keyword"]: r for r in result["rows"]}
    assert by_kw["6x4 truck"]["primary_or_secondary"] == "Primary"
    assert by_kw["6x4 truck price"]["primary_or_secondary"] == "Secondary"


def test_missing_primary_secondary_column_defaults_first_row_per_cluster_to_primary():
    content = _csv(
        "Keyword,Cluster\n"
        "6x4 truck,6x4 Truck Configuration\n"
        "6x4 truck price,6x4 Truck Configuration\n"
        "heavy truck,Heavy Trucks\n"
    )
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    by_kw = {r["keyword"]: r for r in result["rows"]}
    assert by_kw["6x4 truck"]["primary_or_secondary"] == "Primary"
    assert by_kw["6x4 truck price"]["primary_or_secondary"] == "Secondary"
    assert by_kw["heavy truck"]["primary_or_secondary"] == "Primary"


def test_blank_keyword_or_cluster_rows_are_dropped():
    content = _csv("Keyword,Cluster\n,Heavy Trucks\nheavy truck,\nreal keyword,Real Cluster\n")
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    assert result["row_count"] == 1
    assert result["rows"][0]["keyword"] == "real keyword"


def test_reads_sub_category_search_volume_kd_and_intent_when_present():
    content = _csv(
        "Keyword,Cluster,Sub-Category,Search Volume,KD,Intent\n"
        "6x4 truck,6x4 Truck Configuration,Configuration,1200,45,Commercial\n"
    )
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    row = result["rows"][0]
    assert row["sub_category"] == "Configuration"
    assert row["search_volume"] == 1200
    assert row["keyword_difficulty"] == 45
    assert row["intent"] == "Commercial"


def test_optional_metric_columns_absent_when_sheet_has_no_such_column():
    content = _csv("Keyword,Cluster\n6x4 truck,6x4 Truck Configuration\n")
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    row = result["rows"][0]
    for field in ("sub_category", "search_volume", "keyword_difficulty", "intent"):
        assert field not in row


def test_blank_metric_cell_is_dropped_not_defaulted_to_zero():
    # 2026-09-21 spec section 5: never fabricate a value the sheet didn't
    # actually provide for this specific row.
    content = _csv("Keyword,Cluster,Search Volume,KD\nkw one,Cluster A,1000,40\nkw two,Cluster A,,\n")
    result = parse_manual_keyword_cluster_file("clusters.csv", content)
    by_kw = {r["keyword"]: r for r in result["rows"]}
    assert by_kw["kw one"]["search_volume"] == 1000
    assert "search_volume" not in by_kw["kw two"]
    assert "keyword_difficulty" not in by_kw["kw two"]
