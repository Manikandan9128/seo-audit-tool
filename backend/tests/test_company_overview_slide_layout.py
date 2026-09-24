"""Company Overview slide (add_company_overview_extracted_slide) layout
regression: the footer (registration/contact) line must never land on top
of the left column's real content."""

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.reporting.pptx_builder import SLIDE_H, SLIDE_W, add_company_overview_extracted_slide


def _prs():
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H
    return prs


def test_footer_never_overlaps_wrapped_industry_chips():
    # Regression (confirmed real, Geopits report, 2026-09-24): the left
    # card's height was a fixed constant, not measured from real content.
    # A long description + 6 KPIs + industries wrapping onto a second chip
    # row pushed real content past that fixed height, but the footer
    # (registration/contact) still drew at the fixed top+height offset —
    # landing directly on top of the wrapped "E-Learning / EdTech" /
    # "Retail" chips instead of below them.
    overview = {
        "company_name": "Geopits",
        "description": (
            "Geopits is a database management and data engineering services firm specializing in "
            "24/7 managed DBA support, database modernization, and cloud migration. The agency "
            "delivers end-to-end data platform engineering, performance tuning, high availability "
            "architecture, and generative AI readiness solutions across hybrid and cloud environments."
        ),
        "kpis": [
            "200+ Clients Globally", "2000+ Databases Managed", "140+ Successful Migrations Completed",
            "25% cloud cost reduction", "99.99% average pipeline uptime", "15-minute response for P1 critical outages",
        ],
        "industries": ["Automotive", "Non-Banking Financial Companies (NBFC)", "E-Learning / EdTech", "Retail"],
        "target_country": "Global",
        "target_market": "Mid-Market and Enterprise",
        "primary_buyers": ["CTO", "CIO", "VP of Engineering", "Head of Data", "Director of Infrastructure"],
        "daily_users": ["Database Administrators", "Data Engineers", "DevOps Engineers", "Software Developers"],
        "beneficiaries": ["In-house Engineering Teams", "Data Analytics Teams", "Executives", "Business Operations"],
        "registration_info": "ISO 27001 Certified, ISO 9001 Certified",
    }
    prs = _prs()
    slide = add_company_overview_extracted_slide(prs, "Geopits", overview)

    chip_bottoms = [
        sh.top + sh.height for sh in slide.shapes
        if sh.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE and sh.has_text_frame
        and sh.text_frame.text.strip() in overview["industries"]
    ]
    assert chip_bottoms, "expected industry chips to be found on the slide"

    footer = next(
        sh for sh in slide.shapes
        if sh.has_text_frame and "ISO 27001 Certified" in sh.text_frame.text
    )
    assert footer.top >= max(chip_bottoms), (
        f"footer (top={footer.top}) overlaps the industry chips (bottom={max(chip_bottoms)})"
    )
