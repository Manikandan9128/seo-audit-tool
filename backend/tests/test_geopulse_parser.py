import json
from unittest.mock import MagicMock, patch

from app.services.geopulse_parser import MAX_PDF_PAGES, MAX_RAW_TEXT_CHARS, parse_geopulse_file


def test_parses_csv():
    content = b"query,mentions\nbest payroll software,12\n"
    result = parse_geopulse_file("export.csv", content)
    assert result["row_count"] == 1
    assert "best payroll software" in result["rows"][0]["raw_text"]


def test_parses_json():
    content = json.dumps({"query": "best payroll software", "mentions": 12}).encode()
    result = parse_geopulse_file("export.json", content)
    assert "best payroll software" in result["rows"][0]["raw_text"]


def test_falls_back_to_raw_text_for_unknown_format():
    content = b"some plain text export from geopulse"
    result = parse_geopulse_file("export.txt", content)
    assert result["rows"][0]["raw_text"] == "some plain text export from geopulse"


def test_truncates_very_long_text():
    content = b"a" * 100_000
    result = parse_geopulse_file("export.txt", content)
    assert len(result["rows"][0]["raw_text"]) == 40_000


def _fake_page(text=None, raises=False):
    page = MagicMock()
    if raises:
        page.extract_text.side_effect = RuntimeError("bad page")
    else:
        page.extract_text.return_value = text
    return page


def _patched_pdf(pages):
    ctx = MagicMock()
    ctx.__enter__.return_value = MagicMock(pages=pages)
    ctx.__exit__.return_value = False
    return patch("app.services.geopulse_parser.pdfplumber.open", return_value=ctx)


# Regression (2026-09-25): a real GeoPulse export PDF failed to upload
# with no error detail shown at all — the shape of a timeout/worker
# crash from unbounded page-by-page extraction, not a caught parsing
# exception (the route's own try/except already turns those into a
# clear message). PDF extraction must be bounded and resilient to a
# single bad page.
def test_pdf_extraction_stops_at_max_page_cap():
    pages = [_fake_page(f"page {i}") for i in range(MAX_PDF_PAGES + 50)]
    with _patched_pdf(pages):
        parse_geopulse_file("export.pdf", b"%PDF-1.4 fake")
    called = sum(1 for p in pages if p.extract_text.called)
    assert called == MAX_PDF_PAGES


def test_pdf_extraction_stops_once_enough_text_collected():
    pages = [_fake_page("x" * MAX_RAW_TEXT_CHARS)] + [_fake_page("should never run") for _ in range(10)]
    with _patched_pdf(pages):
        result = parse_geopulse_file("export.pdf", b"%PDF-1.4 fake")
    assert not pages[1].extract_text.called
    assert len(result["rows"][0]["raw_text"]) == MAX_RAW_TEXT_CHARS


def test_pdf_extraction_skips_a_page_that_throws_instead_of_failing_the_whole_file():
    pages = [_fake_page("first page text"), _fake_page(raises=True), _fake_page("third page text")]
    with _patched_pdf(pages):
        result = parse_geopulse_file("export.pdf", b"%PDF-1.4 fake")
    text = result["rows"][0]["raw_text"]
    assert "first page text" in text and "third page text" in text
