import json

from app.reporting.pptx_builder import build_schema_report_parts
from app.services.technical_seo_service import (
    _extract_schema_entities, _schema_field_issues, _schema_types_from_entities, aggregate_schema_validation,
)


def _html(*blocks) -> str:
    return "".join(f'<script type="application/ld+json">{json.dumps(b)}</script>' for b in blocks)


def test_nested_image_object_is_detected():
    html = _html({
        "@type": "Article", "headline": "h", "datePublished": "2026-01-01",
        "image": {"@type": "ImageObject", "url": "https://x.com/a.jpg"},
    })
    assert "ImageObject" in _schema_types_from_entities(_extract_schema_entities(html))


def test_image_object_inside_graph_and_lists_is_detected_once_per_node():
    html = _html({"@graph": [
        {"@type": "Organization", "name": "n", "url": "u",
         "logo": [{"@type": "ImageObject", "contentUrl": "https://x.com/l.png"}]},
    ]})
    entities = _extract_schema_entities(html)
    assert [e for e in entities if e.get("@type") == "ImageObject"] == [
        {"@type": "ImageObject", "contentUrl": "https://x.com/l.png"}
    ]


def test_only_image_object_is_pulled_up_from_nesting():
    html = _html({"@type": "Article", "publisher": {"@type": "Organization", "name": "n"}})
    assert "Organization" not in _schema_types_from_entities(_extract_schema_entities(html))


def test_image_object_gaps_are_recommended_never_required():
    issues = _schema_field_issues([{"@type": "ImageObject", "url": "https://x.com/a.jpg"}])
    assert issues and all("recommended" in i for i in issues)
    assert "ImageObject schema missing recommended field: license" in issues


def test_detected_image_object_gets_a_schema_slide_row():
    pages = [{
        "url": f"https://x.com/p{i}",
        "meta": {"schema_types_found": ["ImageObject"],
                 "schema_field_issues": ["ImageObject schema missing recommended field: license"]},
    } for i in range(3)]
    rows = build_schema_report_parts(aggregate_schema_validation(pages))["part2"]
    row = next(r for r in rows if r["schema_type"] == "ImageObject (detected)")
    assert (row["present"], row["valid"], row["invalid"]) == (3, 3, 0)
