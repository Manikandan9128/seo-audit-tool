from app.reporting.pptx_builder import _cross_competitor_keyword_insights


def test_returns_empty_for_fewer_than_two_competitors():
    assert _cross_competitor_keyword_insights({}) == []
    assert _cross_competitor_keyword_insights({"a.com": [{"keyword": "x", "position": 1}]}) == []


def test_identifies_footprint_leader_and_shared_keywords():
    positions = {
        "a.com": [
            {"keyword": "payroll software", "position": 3},
            {"keyword": "certified payroll", "position": 5},
        ],
        "b.com": [
            {"keyword": "payroll software", "position": 15},
        ],
    }
    insights = _cross_competitor_keyword_insights(positions)
    assert any("a.com" in i and "largest page-1 footprint" in i for i in insights)
    assert any("total" in i.lower() and "3" in i for i in insights)
