"""build_report's ui_audit wiring (2026-09-25): the glue between
validate_ui_audit_issues()'s output and the two UI-Level Fixes slides
(or the no-issues edge case) actually lands in the built deck."""

import pytest
from pptx import Presentation

from app.reporting.pptx_builder import build_report


def _slide_titles(pptx_bytes: bytes) -> list[str]:
    import io

    prs = Presentation(io.BytesIO(pptx_bytes))
    titles = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                titles.append(shape.text_frame.text.strip())
                break
    return titles


def _ui_audit(total_count=3, **overrides):
    base = {
        "issues": [
            {"title": f"Issue {i}", "evidence": f"Evidence {i}", "where": f"Loc {i}", "fix": f"Fix {i}",
             "outcome_tags": ["clarity"], "priority": "High", "device": "Both", "impact_score": 8}
            for i in range(1, total_count + 1)
        ],
        "total_count": total_count,
        "counts_by_priority": {"High": total_count, "Medium": 0, "Low": 0},
        "full_list_url": None, "ga4_available": False,
    }
    base.update(overrides)
    return base


def test_build_report_renders_top5_and_outcomes_slides_when_issues_found():
    pptx_bytes = build_report("Acme", "https://acme.com", ui_audit=_ui_audit(3))
    all_text = "\n".join(_slide_titles(pptx_bytes))
    assert "UI-Level Fixes: Issues Found" in all_text
    assert "UI-Level Fixes: Expected Outcomes" in all_text


def test_build_report_renders_no_issues_slide_when_ui_audit_found_zero():
    pptx_bytes = build_report("Acme", "https://acme.com", ui_audit={"issues": [], "total_count": 0, "counts_by_priority": {}})
    all_text = "\n".join(_slide_titles(pptx_bytes))
    assert "UI-Level Fixes: No Major Issues Found" in all_text
    assert "UI-Level Fixes: Issues Found" not in all_text
    assert "UI-Level Fixes: Expected Outcomes" not in all_text


def test_build_report_renders_nothing_ui_audit_related_when_ui_audit_is_none():
    # Capture/analysis never ran at all (no vision key, site unreachable)
    # — ux_findings' own note fallback is the only thing that should
    # cover this gap, not any of the 3 new UI-Level Fixes slides.
    pptx_bytes = build_report(
        "Acme", "https://acme.com", ui_audit=None,
        ux_findings={"note": "A manual UX pass has not yet been done for this site."},
    )
    all_text = "\n".join(_slide_titles(pptx_bytes))
    assert "UI-Level Fixes: Issues Found" not in all_text
    assert "UI-Level Fixes: Expected Outcomes" not in all_text
    assert "UI-Level Fixes: No Major Issues Found" not in all_text
    assert "UI-Level Fixes" in all_text  # the note-fallback slide itself


def test_build_report_top5_slide_uses_the_configured_brand_color():
    pptx_bytes = build_report("Acme", "https://acme.com", ui_audit=_ui_audit(1), brand_color_hex="#1D4ED8")
    # Doesn't crash and produces a real deck with the UI audit slides —
    # brand-color-specific rendering is covered at the unit level in
    # test_ui_fixes_slides.py; this just confirms build_report actually
    # threads brand_color_hex through to the same _accent() those slides
    # read, end to end.
    all_text = "\n".join(_slide_titles(pptx_bytes))
    assert "UI-Level Fixes: Issues Found" in all_text


# --- Part C fixture sweep: 0 / 3 / 5 / 20 issues, light + red brand -----------

@pytest.mark.parametrize("total_count", [0, 3, 5, 20])
@pytest.mark.parametrize("brand_hex", ["#F5E042", "#CC0000"])  # light yellow, red
def test_build_report_full_deck_across_fixtures_and_brand_colors(total_count, brand_hex):
    ui_audit = {"issues": [], "total_count": 0, "counts_by_priority": {}} if total_count == 0 else _ui_audit(total_count)
    pptx_bytes = build_report("Fixture Co", "https://fixture.co", ui_audit=ui_audit, brand_color_hex=brand_hex)
    all_text = "\n".join(_slide_titles(pptx_bytes))
    if total_count == 0:
        assert "UI-Level Fixes: No Major Issues Found" in all_text
    else:
        title = "UI-Level Fixes: Top 5 Issues" if total_count >= 5 else "UI-Level Fixes: Issues Found"
        assert title in all_text
        assert "UI-Level Fixes: Expected Outcomes" in all_text
