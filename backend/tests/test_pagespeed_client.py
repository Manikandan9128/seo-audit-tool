from app.integrations.pagespeed_client import _extract_issues


def test_extract_issues_includes_failing_opportunity():
    audits = {
        "render-blocking-resources": {
            "score": 0.2,
            "scoreDisplayMode": "numeric",
            "title": "Eliminate render-blocking resources",
            "displayValue": "Potential savings of 1,200 ms",
            "details": {"overallSavingsMs": 1200},
        }
    }
    issues = _extract_issues(audits)
    assert len(issues) == 1
    assert issues[0]["title"] == "Eliminate render-blocking resources"
    assert issues[0]["savings_ms"] == 1200


def test_extract_issues_excludes_passing_audit():
    audits = {"uses-passive-event-listeners": {"score": 1.0, "scoreDisplayMode": "binary", "title": "Uses passive listeners"}}
    assert _extract_issues(audits) == []


def test_extract_issues_excludes_informative_audit_with_no_score():
    audits = {"final-screenshot": {"score": None, "scoreDisplayMode": "informative", "title": "Final screenshot"}}
    assert _extract_issues(audits) == []


def test_extract_issues_excludes_core_web_vital_metrics():
    # These are already surfaced separately as core_web_vitals — including
    # them here would duplicate the same finding under a different name.
    audits = {
        "largest-contentful-paint": {"score": 0.3, "scoreDisplayMode": "numeric", "title": "LCP", "displayValue": "4.2 s"},
        "cumulative-layout-shift": {"score": 0.4, "scoreDisplayMode": "numeric", "title": "CLS"},
    }
    assert _extract_issues(audits) == []


def test_extract_issues_sorted_by_savings_descending():
    audits = {
        "small-savings": {"score": 0.5, "scoreDisplayMode": "numeric", "title": "Small", "details": {"overallSavingsMs": 100}},
        "big-savings": {"score": 0.5, "scoreDisplayMode": "numeric", "title": "Big", "details": {"overallSavingsMs": 900}},
    }
    issues = _extract_issues(audits)
    assert [i["title"] for i in issues] == ["Big", "Small"]


def test_extract_issues_respects_limit():
    audits = {
        f"issue-{i}": {"score": 0.1, "scoreDisplayMode": "binary", "title": f"Issue {i}"}
        for i in range(10)
    }
    assert len(_extract_issues(audits, limit=3)) == 3
