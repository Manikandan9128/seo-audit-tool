from datetime import datetime

from app.services.semrush_analysis_service import analyze


def _rec(import_type, own, label, rows):
    return {"import_type": import_type, "is_own_site": own, "domain_label": label,
            "created_at": datetime(2026, 10, 1), "parsed_data": {"rows": rows, "row_count": len(rows)}}


def _sample(n):
    return [{"source_url": f"https://d{i}.example/"} for i in range(n)]


def test_gap_uses_overview_totals_not_backlink_sample():
    records = [
        _rec("domain_overview", True, "own", [{"domain": "lumberfi.com", "referring_domains": 781}]),
        _rec("domain_overview", False, "rippling.com", [{"domain": "rippling.com", "referring_domains": 18000}]),
        _rec("backlinks", True, "own", _sample(112)),
        _rec("backlinks", False, "rippling.com", _sample(152)),
    ]
    issues = analyze(records, own_domain="lumberfi.com")["issues"]
    gap = [i for i in issues if "referring domains" in i["summary"]]
    assert len(gap) == 1
    assert "17,219 more referring domains" in gap[0]["summary"]
    assert "18,000" in gap[0]["detail"] and "781" in gap[0]["detail"]


def test_no_totals_means_no_gap_claim_from_sample():
    records = [
        _rec("backlinks", True, "own", _sample(112)),
        _rec("backlinks", False, "rippling.com", _sample(152)),
    ]
    issues = analyze(records, own_domain="lumberfi.com")["issues"]
    assert not [i for i in issues if "referring domains" in i["summary"]]
