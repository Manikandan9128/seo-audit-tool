from app.integrations.pagespeed_client import (
    _combined_projection,
    _extract_issues,
    _lighthouse_metric_score,
    _overall_score,
    _quick_wins,
    _score_breakdown,
)


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


def test_lighthouse_metric_score_at_p10_scores_90():
    # By construction the p10 control point must land at score 90.
    assert _lighthouse_metric_score(2500, median=4000, p10=2500) == 90


def test_lighthouse_metric_score_at_median_scores_50():
    assert _lighthouse_metric_score(4000, median=4000, p10=2500) == 50


def test_lighthouse_metric_score_worse_than_median_scores_below_50():
    assert _lighthouse_metric_score(6000, median=4000, p10=2500) < 50


def test_lighthouse_metric_score_better_than_p10_scores_above_90():
    assert _lighthouse_metric_score(1500, median=4000, p10=2500) > 90


def test_score_breakdown_uses_numeric_value_not_display_value():
    audits = {
        "largest-contentful-paint": {"numericValue": 4800, "displayValue": "4.8 s"},
        "total-blocking-time": {"numericValue": 720, "displayValue": "720 ms"},
        "cumulative-layout-shift": {"numericValue": 0.28, "displayValue": "0.28"},
        "first-contentful-paint": {"numericValue": 2600, "displayValue": "2.6 s"},
        "speed-index": {"numericValue": 6100, "displayValue": "6.1 s"},
    }
    breakdown = _score_breakdown(audits)
    assert {m["id"] for m in breakdown} == set(audits.keys())
    lcp = next(m for m in breakdown if m["id"] == "largest-contentful-paint")
    assert lcp["value"] == 4800
    assert lcp["weight"] == 0.25


def test_score_breakdown_skips_missing_metrics():
    breakdown = _score_breakdown({"largest-contentful-paint": {"numericValue": 4000}})
    assert len(breakdown) == 1


def test_overall_score_matches_weighted_sum_of_perfect_metrics():
    breakdown = [
        {"id": "a", "score": 100, "weight": 0.6, "median": 4000, "p10": 2500},
        {"id": "b", "score": 100, "weight": 0.4, "median": 600, "p10": 200},
    ]
    assert _overall_score(breakdown) == 100


def test_overall_score_override_improves_total():
    breakdown = [
        {"id": "lcp", "score": 30, "weight": 0.5, "median": 4000, "p10": 2500},
        {"id": "tbt", "score": 90, "weight": 0.5, "median": 600, "p10": 200},
    ]
    baseline = _overall_score(breakdown)
    improved = _overall_score(breakdown, {"lcp": 2500})  # LCP fixed to its own p10 -> scores 90
    assert improved > baseline


def test_quick_wins_ranks_worst_metric_highest_delta():
    breakdown = _score_breakdown({
        "largest-contentful-paint": {"numericValue": 4800},
        "total-blocking-time": {"numericValue": 720},
        "cumulative-layout-shift": {"numericValue": 0.28},
        "first-contentful-paint": {"numericValue": 2600},
        "speed-index": {"numericValue": 6100},
    })
    current = _overall_score(breakdown)
    wins = _quick_wins(breakdown, current)
    assert all(wins[i]["score_delta"] >= wins[i + 1]["score_delta"] for i in range(len(wins) - 1))
    assert all(w["score_delta"] >= 0 for w in wins)  # fixing to 'good' never makes the score worse


def test_combined_projection_never_exceeds_100_and_beats_baseline():
    breakdown = _score_breakdown({
        "largest-contentful-paint": {"numericValue": 4800},
        "total-blocking-time": {"numericValue": 720},
        "cumulative-layout-shift": {"numericValue": 0.28},
    })
    current = _overall_score(breakdown)
    wins = _quick_wins(breakdown, current)
    projection = _combined_projection(breakdown, wins, current)
    assert projection["score_after"] <= 100
    assert projection["score_after"] >= projection["score_before"]
