import json
from unittest.mock import patch

from app.services.ui_audit_service import generate_ui_audit_issues, validate_ui_audit_issues

_MOD = "app.services.ui_audit_service"


def _images():
    return [(b"d1", "image/png"), (b"d2", "image/png"), (b"m1", "image/png"), (b"m2", "image/png")]


# --- generate_ui_audit_issues -------------------------------------------------

def test_generate_issues_parses_valid_response():
    response = json.dumps({"issues": [{"title": "No clear CTA", "evidence": "0 CTAs above the fold", "where": "Hero"}]})
    with patch(f"{_MOD}.generate_text_with_images", return_value=(response, "claude")):
        result = generate_ui_audit_issues("Acme", "acme.com", None, {}, _images())
    assert result["issues"][0]["title"] == "No clear CTA"


def test_generate_issues_strips_markdown_fences():
    response = "```json\n" + json.dumps({"issues": []}) + "\n```"
    with patch(f"{_MOD}.generate_text_with_images", return_value=(response, "groq")):
        result = generate_ui_audit_issues("Acme", "acme.com", None, {}, _images())
    assert result["issues"] == []


def test_generate_issues_requires_images():
    result = generate_ui_audit_issues("Acme", "acme.com", None, {}, [])
    assert "error" in result


def test_generate_issues_returns_error_on_malformed_json():
    with patch(f"{_MOD}.generate_text_with_images", return_value=("not json at all", "groq")):
        result = generate_ui_audit_issues("Acme", "acme.com", None, {}, _images())
    assert "error" in result


def test_generate_issues_returns_error_when_issues_key_missing():
    with patch(f"{_MOD}.generate_text_with_images", return_value=(json.dumps({"other": []}), "groq")):
        result = generate_ui_audit_issues("Acme", "acme.com", None, {}, _images())
    assert "error" in result


def test_generate_issues_returns_error_when_no_provider_configured():
    from app.integrations.text_ai_client import NoAIProviderConfigured
    with patch(f"{_MOD}.generate_text_with_images", side_effect=NoAIProviderConfigured("no key")):
        result = generate_ui_audit_issues("Acme", "acme.com", None, {}, _images())
    assert result["error"] == "no key"


# --- validate_ui_audit_issues -------------------------------------------------

def _issue(**overrides):
    base = {
        "title": "Issue", "evidence": "measured fact", "where": "Hero", "fix": "Do the fix",
        "outcome_tags": ["clarity"], "priority": "Medium", "device": "Both", "impact_score": 5,
    }
    base.update(overrides)
    return base


def test_validate_drops_issues_with_empty_evidence():
    result = validate_ui_audit_issues([_issue(evidence=""), _issue()], {})
    assert result["total_count"] == 1


def test_validate_drops_unsupported_overlap_claims():
    issue = _issue(title="Chat widget covers the CTA", outcome_tags=["friction"])
    result = validate_ui_audit_issues([issue], {"overlaps": []})
    assert result["total_count"] == 0


def test_validate_keeps_overlap_claim_when_page_facts_confirms_it():
    issue = _issue(title="Chat widget covers the CTA", outcome_tags=["friction"])
    page_facts = {"overlaps": [{"overlay_kind": "chat_widget", "cta_text": "Contact"}]}
    result = validate_ui_audit_issues([issue], page_facts)
    assert result["total_count"] == 1


def test_validate_keeps_overlap_claim_when_per_device_page_facts_confirms_it():
    issue = _issue(title="Banner overlaps CTA", outcome_tags=["friction"])
    page_facts = {"desktop": {"overlaps": [{"overlay_kind": "announcement_bar"}]}, "mobile": {"overlaps": []}}
    result = validate_ui_audit_issues([issue], page_facts)
    assert result["total_count"] == 1


def test_validate_dedupes_same_element_described_twice():
    a = _issue(title="Hero CTA is not visible", where="Hero section")
    b = _issue(title="Hero CTA not visible enough", where="Hero section")  # same element, reworded
    result = validate_ui_audit_issues([a, b], {})
    assert result["total_count"] == 1


def test_validate_keeps_distinct_issues_at_the_same_location():
    a = _issue(title="No clear CTA", where="Hero section")
    b = _issue(title="Low contrast text", where="Hero section")  # different problem, same location
    result = validate_ui_audit_issues([a, b], {})
    assert result["total_count"] == 2


def test_validate_sorts_by_priority_then_impact_score():
    low = _issue(title="Low one", priority="Low", impact_score=9)
    high_weak = _issue(title="High weak", where="A", priority="High", impact_score=3)
    high_strong = _issue(title="High strong", where="B", priority="High", impact_score=8)
    medium = _issue(title="Medium one", where="C", priority="Medium", impact_score=5)
    result = validate_ui_audit_issues([low, high_weak, high_strong, medium], {})
    assert [i["title"] for i in result["issues"]] == ["High strong", "High weak", "Medium one", "Low one"]


def test_validate_counts_by_priority():
    issues = [
        _issue(where="A", priority="High"), _issue(where="B", priority="High"),
        _issue(where="C", priority="Medium"), _issue(where="D", priority="Low"),
    ]
    result = validate_ui_audit_issues(issues, {})
    assert result["counts_by_priority"] == {"High": 2, "Medium": 1, "Low": 1}


def test_validate_defaults_invalid_priority_device_and_impact_score():
    issue = _issue(priority="Critical", device="Tablet", impact_score="not a number")
    result = validate_ui_audit_issues([issue], {})
    out = result["issues"][0]
    assert out["priority"] == "Medium"
    assert out["device"] == "Both"
    assert out["impact_score"] == 5


def test_validate_clamps_impact_score_to_1_10_range():
    result = validate_ui_audit_issues([_issue(impact_score=99), _issue(where="B", impact_score=-5)], {})
    scores = sorted(i["impact_score"] for i in result["issues"])
    assert scores == [1, 10]


def test_validate_truncates_oversized_fields():
    issue = _issue(title="X" * 100, evidence="Y" * 200, where="Z" * 100, fix="W" * 300)
    out = validate_ui_audit_issues([issue], {})["issues"][0]
    assert len(out["title"]) <= 45
    assert len(out["evidence"]) <= 130
    assert len(out["where"]) <= 55
    assert len(out["fix"]) <= 160
    assert out["title"].endswith("…")


def test_validate_drops_unknown_outcome_tags():
    issue = _issue(outcome_tags=["clarity", "made_up_tag", "trust"])
    out = validate_ui_audit_issues([issue], {})["issues"][0]
    assert out["outcome_tags"] == ["clarity", "trust"]


def test_validate_ignores_non_dict_entries():
    result = validate_ui_audit_issues(["not a dict", None, _issue()], {})
    assert result["total_count"] == 1


def test_validate_caps_at_25_issues():
    issues = [_issue(where=f"Loc {i}", priority="High", impact_score=5) for i in range(30)]
    result = validate_ui_audit_issues(issues, {})
    assert result["total_count"] == 25


def test_validate_empty_input_returns_zero_counts():
    result = validate_ui_audit_issues([], {})
    assert result == {"issues": [], "total_count": 0, "counts_by_priority": {"High": 0, "Medium": 0, "Low": 0}}
