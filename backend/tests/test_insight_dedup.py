"""Key Insights dedup (2026-09-28): one version of each finding per slide,
and a finding stated on an earlier slide is not repeated on a later one.
Examples are the real repeats from the Geopits deck."""

import pytest

from app.reporting import pptx_builder as b

REFERRAL = [
    "Referral's 37.5% bounce rate is 35.2 points below the session-weighted average of 72.7% across all channels.",
    "Referral has a low 37.5% bounce rate (35.2 points below the 72.7% session-weighted average) but only 2% of "
    "sessions (26/month) — an underused, high-quality channel worth investing more into.",
    "Referral is small at 2% of sessions (26/month) but its 37.5% bounce rate is 35.2 points better than the 72.7% "
    "session-weighted average — an efficient channel at small scale — worth testing whether that quality holds if "
    "you invest more into it.",
]

EXEC_ACTIONS = [
    "304 keywords (97%) have competitors ranking with no page on Geopits's site yet — start with \"how to fix slow "
    "mysql queries\" (8,100/mo), where mydbops.com (#35) already rank.",
    "Of 11 keywords where both sides rank, \"aggregation pipeline in mongodb\" shows the widest gap — Geopits's site "
    "sits at #39 vs. #9 for the closest-ranking competitor.",
]


@pytest.fixture(autouse=True)
def _no_deck():
    yield
    b._reset_deck_insights(active=False)


def test_reworded_repeats_on_one_slide_collapse_to_the_richest():
    kept = b.client_facing_insights(REFERRAL)
    assert len(kept) == 1
    assert "2% of sessions" in kept[0] and "37.5%" in kept[0]


def test_distinct_findings_on_one_slide_all_stay():
    target_keywords = [
        "Awareness opportunity: 8 keyword(s) with 1,320 combined monthly searches, 5 of them low-difficulty (KD under 30).",
        "Strongest keyword: \"database management services\" — 590 searches/month, KD 28. Search intent: Informational.",
        "Existing page is a partial match; a dedicated page may be required.",
    ]
    assert b.client_facing_insights(target_keywords) == target_keywords


def test_finding_from_an_earlier_slide_is_not_repeated_later():
    b._reset_deck_insights(active=True)
    b._register_deck_insights(EXEC_ACTIONS)  # Executive Summary, slide 33
    later = [  # Key Insights, slide 36
        "Highest-volume Missing keyword: \"how to fix slow mysql queries\" (8,100/mo) — ranking competitors: mydbops.com (#35).",
        "Missing keywords carry 89% of the 139,180 combined monthly searches in the gap.",
        "Highest-volume Shared keyword: \"database managed services\" (320/mo) — you and mydbops.com (#9) rank.",
        "Weakest Shared ranking: \"aggregation pipeline in mongodb\" — you rank #39 while mydbops.com ranks #9.",
    ]
    assert b._drop_stated_earlier(later) == [later[1], later[2]]


def test_same_competitor_with_a_different_fact_is_not_a_repeat():
    b._reset_deck_insights(active=True)
    b._register_deck_insights(["mydbops.com ranks #9 for \"database managed services\"."])
    assert b._drop_stated_earlier(["mydbops.com publishes 120 blog posts a month."]) == [
        "mydbops.com publishes 120 blog posts a month."
    ]


def test_generic_per_cluster_wording_is_never_dropped_across_slides():
    b._reset_deck_insights(active=True)
    line = "Existing page is a partial match; a dedicated page may be required."
    b._register_deck_insights([line])
    assert b._drop_stated_earlier([line]) == [line]


def test_no_deck_pass_outside_a_report_build():
    b._register_deck_insights(EXEC_ACTIONS)
    assert b._drop_stated_earlier(EXEC_ACTIONS) == EXEC_ACTIONS


def test_rules_are_generic_not_tied_to_one_client():
    # A different site, channel and keyword: the same finding reworded
    # twice on one slide still collapses to one.
    kept = b.client_facing_insights([
        "Email drives a 21.4% bounce rate, 48.9 points under the 70.3% site average.",
        "Email is only 4.1% of sessions but bounces at 21.4% — 48.9 points better than the 70.3% average; scale it.",
        "Organic Search brings 61.2% of sessions.",
    ])
    assert len(kept) == 2
    assert kept[1] == "Organic Search brings 61.2% of sessions."


def test_slide_insights_drops_bullets_repeating_the_slides_own_headline():
    headline = "acme.io wins \"crm for startups\" at #2 with 12,400 monthly searches."
    bullets = [
        "Evidence: acme.io ranks #2 for \"crm for startups\" (12,400/mo).",
        "Its pricing page converts trial users with a free tier.",
    ]
    assert b.slide_insights(bullets, anchors=[headline]) == [bullets[1]]
