import io
import re

import pandas as pd
import pdfplumber

# Semrush export column names vary slightly by report/locale — match case-insensitively
# on a set of known aliases per field, so a real export loads without manual mapping.

BACKLINKS_COLUMN_ALIASES = {
    "source_url": ["source url", "page ascii url", "source", "source_url"],
    "target_url": ["target url", "target", "target_url"],
    "anchor": ["anchor", "anchor text"],
    # "Source title" was wrongly listed here in the past — that's the
    # linking page's article/title text, not a number, and mapping it into
    # domain_score silently produced garbage (str, never float) while the
    # export's real per-backlink score column ("Page ascore" in Semrush's
    # raw Backlinks CSV) went unmapped and got dropped. Confirmed against a
    # real 10,000-row export.
    "domain_score": ["page ascore", "page score", "domain score", "authority score", "page_score", "page_ascore"],
    "first_seen": ["first seen", "first_seen"],
    "last_seen": ["last seen", "last_seen"],
    "nofollow": ["nofollow"],
}

ORGANIC_COMPETITORS_COLUMN_ALIASES = {
    "domain": ["domain", "competitor"],
    "competitor_relevance": ["competitor relevance", "competitor_relevance"],
    "common_keywords": ["common keywords", "common_keywords"],
    "organic_keywords": ["organic keywords", "se keywords", "organic_keywords"],
    "organic_traffic": ["organic traffic", "se traffic", "organic_traffic"],
    "organic_cost": ["organic cost", "se traffic cost", "organic_cost"],
}

KEYWORD_GAP_COLUMN_ALIASES = {
    "keyword": ["keyword"],
    "cluster": ["cluster", "topic", "group"],
    "search_volume": ["search volume", "volume", "search_volume"],
    "keyword_difficulty": ["keyword difficulty", "kd", "kd%", "keyword_difficulty"],
    "cpc": ["cpc"],
    "intent": ["intents", "intent"],
}

# Semrush's real Keyword Gap tool export compares several domains (your own
# + up to a handful of competitors) side by side in ONE file — a column per
# domain holding its SERP position for that keyword (0 = not ranking), plus
# a same-named "<domain> (pages)" companion column holding the ranking URL.
# Column names are the actual domains compared, so they can't be a fixed
# alias like the fields above — confirmed against a real 8,044-keyword,
# 4-domain export. KEYWORD_GAP_COLUMN_ALIASES alone silently drops every
# one of these columns (not in its keep-list), losing the entire point of
# a Keyword Gap file (who ranks where) and keeping only a generic keyword
# list — _detect_keyword_gap_domain_columns recovers them.
_KEYWORD_GAP_NON_DOMAIN_COLS = {
    "keyword", "intents", "volume", "search volume", "keyword difficulty", "kd", "kd%",
    "cpc", "competition density", "results", "cluster", "topic", "group",
}
_DOMAIN_LIKE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+$", re.IGNORECASE)


def _detect_keyword_gap_domain_columns(df: pd.DataFrame) -> list[str]:
    domain_cols = []
    for c in df.columns:
        name = str(c).strip()
        if name.lower().endswith(" (pages)") or name.lower() in _KEYWORD_GAP_NON_DOMAIN_COLS:
            continue
        if _DOMAIN_LIKE.match(name):
            domain_cols.append(name)
    return domain_cols

# Semrush Organic Research > Positions export for a single domain — what that
# domain currently ranks for, and where. Distinct from Keyword Gap (which
# compares keyword coverage across multiple domains): this always carries a
# position/previous-position pair for one domain.
ORGANIC_POSITIONS_COLUMN_ALIASES = {
    "keyword": ["keyword"],
    "search_volume": ["search volume", "volume", "search_volume"],
    "keyword_difficulty": ["keyword difficulty", "kd", "kd%", "keyword_difficulty"],
    "position": ["position"],
    "previous_position": ["previous position", "previous_position"],
    "url": ["url"],
}

DOMAIN_OVERVIEW_COLUMN_ALIASES = {
    "domain": ["domain"],
    "rank": ["rank", "semrush rank", "semrush_rank"],
    "organic_keywords": ["organic keywords", "organic_keywords"],
    "organic_traffic": ["organic traffic", "organic_traffic"],
    "organic_cost": ["organic cost", "organic traffic cost", "organic_traffic_cost", "organic_cost"],
    "paid_keywords": ["adwords keywords", "paid keywords", "paid_keywords"],
    "paid_traffic": ["adwords traffic", "paid traffic", "paid_traffic"],
    "paid_cost": ["adwords cost", "paid traffic cost", "paid_traffic_cost", "paid_cost"],
    # Competitor Analysis comparison table columns (DR, Backlinks, Top
    # Countries, Branded/Non-Branded split) — present on Semrush's bulk
    # Domain Overview / Traffic Analytics export, not the single-domain one.
    "authority_score": ["authority score", "domain rank", "domain authority", "dr", "authority_score"],
    "backlinks_total": ["backlinks", "total backlinks", "backlinks_total"],
    "referring_domains": ["referring domains", "ref. domains", "referring_domains"],
    "top_countries": ["top countries", "top country", "geo distribution", "top_countries"],
    "branded_pct": ["branded traffic share", "branded traffic", "branded keywords share", "branded %", "branded_pct"],
    "nonbranded_pct": ["non-branded traffic share", "non-branded traffic", "non branded traffic share", "non-branded %", "nonbranded_pct"],
}

# Semrush Site Audit "Crawled Pages" export — one row per crawled URL with
# on-page/technical findings. Distinct from our own crawler's Site Audit feature.
SITE_AUDIT_PAGES_COLUMN_ALIASES = {
    "page_url": ["page url", "page_url"],
    "http_status_code": ["http status code", "http_status_code"],
    "issues": ["issues"],
    "crawl_depth": ["crawl depth", "crawl_depth"],
    "in_sitemap": ["in sitemap", "in_sitemap"],
    "canonicalization": ["canonicalization"],
    "page_title": ["page title", "page_title"],
    "description": ["description"],
    "schema_jsonld": ["schema.org (json-ld)", "schema_jsonld"],
    "open_graph": ["open graph", "open_graph"],
    "twitter_cards": ["twitter cards", "twitter_cards"],
    "hreflang_issues": ["hreflang issues", "hreflang_issues"],
}

# Semrush Site Audit "Pages > Structured Data" export — one row per crawled
# URL, presence flags for the 4 markup formats plus a count column per rich-
# result type Google recognizes (FAQ, Product, Review, etc). Distinct from
# SITE_AUDIT_PAGES_COLUMN_ALIASES (that export's own schema columns are just
# presence flags, no per-rich-result-type breakdown).
STRUCTURED_DATA_COLUMN_ALIASES = {
    "page_url": ["page url", "page_url"],
    "schema_jsonld": ["schema.org (json-ld)", "schema_jsonld"],
    "schema_microdata": ["schema.org (microdata)", "schema_microdata"],
    "open_graph": ["open graph", "open_graph"],
    "twitter_cards": ["twitter cards", "twitter_cards"],
    "microformats": ["microformats"],
    "article_items": ["article items", "article_items"],
    "book_items": ["book items", "book_items"],
    "breadcrumb_items": ["breadcrumb items", "breadcrumb_items"],
    "carousel_items": ["carousel items", "carousel_items"],
    "course_items": ["course items", "course_items"],
    "dataset_items": ["dataset items", "dataset_items"],
    "employer_rating_items": ["employer aggregate rating items", "employer_rating_items"],
    "estimated_salary_items": ["estimated salary items", "estimated_salary_items"],
    "event_items": ["event items", "event_items"],
    "fact_check_items": ["fact check items", "fact_check_items"],
    "faq_items": ["faq items", "faq_items"],
    "guided_recipe_items": ["guided recipe items", "guided_recipe_items"],
    "howto_items": ["how-to items", "howto_items"],
    "job_posting_items": ["job posting items", "job_posting_items"],
    "local_business_items": ["local business items", "local_business_items"],
    "logo_items": ["logo items", "logo_items"],
    "merchant_listing_items": ["merchant listing items", "merchant_listing_items"],
    "movie_items": ["movie items", "movie_items"],
    "product_items": ["product snippet items", "product_items"],
    "product_group_items": ["product group items", "product_group_items"],
    "qa_items": ["q&a items", "qa_items"],
    "recipe_items": ["recipe on search items", "recipe_items"],
    "review_items": ["review snippet items", "review_items"],
    "sitelinks_searchbox_items": ["sitelinks search box items", "sitelinks_searchbox_items"],
    "site_names_items": ["site names items", "site_names_items"],
    "software_app_items": ["software app items", "software_app_items"],
    "vehicle_listing_items": ["vehicle listing items", "vehicle_listing_items"],
    "video_items": ["video items", "video_items"],
}

# Semrush Site Audit "Issues" export (the "Top Issues" overview table, every
# row not just the top 5 shown on the PDF) — one row per issue TYPE across the
# whole crawl, not per page. Distinct from SITE_AUDIT_PAGES_COLUMN_ALIASES,
# which is one row per URL.
SITE_AUDIT_ISSUES_COLUMN_ALIASES = {
    "issue_id": ["issue id", "issue_id"],
    "issue_type": ["issue type", "issue_type"],
    "issue": ["issue"],
    "failed_checks": ["failed checks", "failed_checks"],
    "total_checks": ["total checks", "total_checks"],
    "changed_from_last_audit": ["changed from last audit", "changed_from_last_audit"],
}


def _parse_abbrev_number(text: str) -> float | None:
    """'12.7K' -> 12700.0, '599.7k' -> 599700.0, '$60.4K' -> 60400.0. Semrush's
    Domain Overview PDF only ever shows abbreviated figures, never raw counts."""
    match = re.search(r"([\d,]+(?:\.\d+)?)\s*([KMB])?", text.replace("$", ""), re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((match.group(2) or "").upper(), 1)
    return value * multiplier


def _pdf_section(text: str, start_marker: str, end_markers: list[str]) -> str:
    """Slice out the text between one Domain Overview PDF section heading and
    whichever of the given following headings comes first. Markers are
    matched with flexible whitespace (\\s+ instead of a literal space) since
    pdfplumber's text extraction doesn't always preserve exact spacing
    between words in bold/tight-kerned headings."""
    start_re = re.compile(re.escape(start_marker).replace(r"\ ", r"\s+"), re.IGNORECASE)
    start_match = start_re.search(text)
    if not start_match:
        return ""
    start = start_match.end()
    end = len(text)
    for marker in end_markers:
        end_re = re.compile(re.escape(marker).replace(r"\ ", r"\s+"), re.IGNORECASE)
        end_match = end_re.search(text, start)
        if end_match:
            end = min(end, end_match.start())
    return text[start:end]


def _first_number(section: str, pattern: str) -> float | None:
    m = re.search(pattern, section)
    return _parse_abbrev_number(m.group(1)) if m else None


def _parse_traffic_summary_column(text: str) -> dict:
    """Pulls Traffic/Keywords/Traffic Cost out of one "___ Search: Summary"
    card's isolated text (already sliced to just that column — see the
    left/right page crop in _parse_domain_overview_text, which exists
    because Organic and Paid summary cards sit side by side and a plain
    top-to-bottom text join can interleave their lines)."""
    return {
        # The gap between the traffic number and "TRAFFIC" holds a %-change
        # badge whose minus sign (for a negative change) extracts as a NUL
        # byte rather than "-", so the gap can't be a plain [\d.\-]* class —
        # but it must stay restricted to badge-only characters (digit, dot,
        # dash, NUL, whitespace, percent). A permissive "any character" gap
        # was tried and is NOT safe: on non-cropped (interleaved) text it
        # bridged clean over an entire neighboring card's own number+K/M/B
        # suffix to latch onto that OTHER card's "TRAFFIC" label, silently
        # reporting one domain's organic traffic as its paid traffic. This
        # class can't cross a letter, so it can never skip over another
        # card's number (which always ends in a K/M/B letter) this way.
        "traffic": _first_number(text, r"(\d[\d.,]*[KMB]?)[\d.\-\x00\s%]{0,15}TRAFFIC"),
        "keywords": _first_number(text, r"Keywords\s+(\d[\d.,]*[KMB]?)"),
        "cost": _first_number(text, r"Traffic Cost\s+\$?\s*(\d[\d.,]*[KMB]?)"),
    }


def _detriple(word_text: str) -> str | None:
    """Semrush's donut-chart legend labels (country codes, Branded/
    Non-Branded) render each character 3x in a row — e.g. "uuusss" for
    "us", "888...777777" for "8.77" — a bold-synthesis artifact, not an
    extraction bug. Strip stray NUL bytes then collapse triples; returns
    None (not just the stripped text) when the input isn't actually
    triple-encoded, so normal single-rendered words (headings, "US |
    Domain | ...") are never mistaken for chart-label fragments."""
    cleaned = word_text.replace("\x00", "")
    if not cleaned or len(cleaned) % 3 != 0:
        return None
    chunks = [cleaned[i : i + 3] for i in range(0, len(cleaned), 3)]
    if all(len(set(chunk)) == 1 for chunk in chunks):
        return "".join(chunk[0] for chunk in chunks)
    return None


def _extract_top_countries(pdf: "pdfplumber.PDF") -> str:
    """Semrush's Domain Overview PDF has no clean "top countries" table —
    only the "Organic Search: Keywords By Country" donut, whose slice
    labels/values render as separate triple-encoded word fragments (see
    _detriple) positioned around the chart. Pairs each country-code label
    with the percentage directly below it at the same x-position (label
    order around the donut isn't reliable — confirmed two countries can
    render label-before-label, value-after-value on real exports — so
    pairing must go by column position, not text order), keeps only the
    Organic-side donut (left of the Paid donut's column boundary), and
    returns the top 3 by share as "US, PH, IN" to match the manual report's
    column format."""
    for page in pdf.pages:
        page_text = page.extract_text() or ""
        if "Keywords By Country" not in page_text:
            continue
        words = page.extract_words()
        # Two "Country" words sit on this page's heading row — the Organic
        # side ("Keywords By Country", capital B) and the Paid side
        # ("Ad Keywords by Country", lowercase b). Anchor on the one with a
        # same-row "By" so the boundary/cutoff below are computed from the
        # Organic heading specifically.
        by_tops = {w["top"] for w in words if w["text"] == "By"}
        heading = next(
            (w for w in words if w["text"] == "Country" and any(abs(w["top"] - t) < 1 for t in by_tops)), None
        )
        if not heading:
            continue
        paid_word = next((w for w in words if w["text"] == "Paid" and abs(w["top"] - heading["top"]) < 1), None)
        if not paid_word:
            continue
        boundary_x = paid_word["x0"]
        cutoff = next(
            (w["top"] for w in words if w["text"] == "Traffic:" and w["top"] > heading["top"]),
            page.height,
        )

        labels: list[tuple[float, float, str]] = []
        values: list[tuple[float, float, float]] = []
        for w in words:
            if not (heading["top"] + 25 < w["top"] < cutoff):
                continue
            decoded = _detriple(w["text"])
            if decoded is None:
                continue
            if re.fullmatch(r"[a-z-]+", decoded) and 2 <= len(decoded) <= 12:
                labels.append((w["top"], w["x0"], decoded))
            elif re.fullmatch(r"[\d.]+", decoded):
                try:
                    values.append((w["top"], w["x0"], float(decoded)))
                except ValueError:
                    pass

        organic: list[tuple[str, float]] = []
        for label_top, label_x, code in labels:
            if label_x >= boundary_x or code == "others":
                continue
            candidates = [
                (v_top - label_top, pct)
                for v_top, v_x, pct in values
                if abs(v_x - label_x) < 3 and v_top > label_top
            ]
            if candidates:
                candidates.sort(key=lambda c: c[0])
                organic.append((code, candidates[0][1]))

        if organic:
            organic.sort(key=lambda c: c[1], reverse=True)
            return ", ".join(code.upper() for code, _ in organic[:3])
    return ""


def _parse_domain_overview_text(
    text: str, page2_left: str = "", page2_right: str = "", branded_col: str = "", top_countries: str = ""
) -> dict | None:
    if "Domain Overview" not in text:
        return None

    domain_match = re.search(r"\|\s*Domain\s*\|\s*([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
    if not domain_match:
        return None
    domain = domain_match.group(1)

    # Organic and Paid Summary cards render side by side on the page — a
    # plain top-to-bottom text join can interleave their lines, so prefer
    # the already-isolated left/right column text when given (real PDF
    # path). Only trust the crop when it's unambiguous — exactly one side
    # has "Organic" and the other has "Paid" — otherwise a boundary that
    # bled into the wrong column could silently swap the two, which is
    # worse than not cropping at all; fall back to whole-page section
    # slicing (also what plain sequential-layout tests exercise).
    #
    # Limited to the first ~300 chars of each column (roughly the Summary
    # card itself) rather than the whole column — further down the page a
    # shared, centered chart caption ("Traffic: Organic vs Paid") spans
    # both columns and would otherwise poison this check on every real PDF,
    # forcing the unreliable fallback path every time.
    left_head, right_head = page2_left[:300], page2_right[:300]
    left_organic, right_organic = "Organic" in left_head, "Organic" in right_head
    left_paid, right_paid = "Paid" in left_head, "Paid" in right_head
    if left_organic and right_paid and not (left_paid or right_organic):
        organic_col, paid_col = page2_left, page2_right
    elif right_organic and left_paid and not (right_paid or left_organic):
        organic_col, paid_col = page2_right, page2_left
    else:
        organic_col = paid_col = ""
    organic = organic_col or _pdf_section(text, "Organic Search: Summary", ["Paid Search: Summary"])
    paid = paid_col or _pdf_section(text, "Paid Search: Summary", ["Backlinks: Summary"])
    backlinks = _pdf_section(text, "Backlinks: Summary", ["Organic Search: Keywords By Country", "Paid Search: Ad Keywords"])
    # "Organic Branded Search" (left) and "Branded vs Non-Branded" (right)
    # sit side by side too, same as the Organic/Paid Summary cards — prefer
    # the isolated right-column text when given, else fall back to
    # whole-page slicing (works for non-interleaved/synthetic text, where
    # there's nothing to disambiguate).
    branded = branded_col or _pdf_section(
        text, "Branded vs Non-Branded", ["Organic Search: Branded Traffic Trend", "Paid search traffic", "Backlinks"]
    )

    organic_fields = _parse_traffic_summary_column(organic)
    paid_fields = _parse_traffic_summary_column(paid)

    row = {
        "domain": domain,
        "organic_traffic": organic_fields["traffic"],
        "organic_keywords": organic_fields["keywords"],
        "organic_cost": organic_fields["cost"],
        "paid_traffic": paid_fields["traffic"],
        "paid_keywords": paid_fields["keywords"],
        "paid_cost": paid_fields["cost"],
        "backlinks_total": _first_number(backlinks, r"(\d[\d.,]*[KMB]?)\s*TOTAL\s*BACKLINKS"),
        "referring_domains": _first_number(backlinks, r"Referring\s*Domains\s+(\d[\d.,]*[KMB]?)"),
        "top_countries": top_countries or None,
        "export_date": _extract_export_date(text),
    }
    # The two percentages sit on their own line, followed by their labels
    # on the next line — "14.57% 85.43%\nBranded Traffic Non-Branded
    # Traffic" — positionally paired (1st number <-> 1st label), not each
    # number immediately preceding its own label. A lookback like
    # "<number>% Branded Traffic" grabs whichever number sits directly
    # before that text, which is the *second* (Non-Branded) value, and
    # leaves Non-Branded's own lookback with nothing but a label word
    # ("Traffic") in front of it — always blank.
    paired_pct = re.search(
        r"([\d.]+)\s*%\s+([\d.]+)\s*%\s*\n?\s*Branded\s*Traffic\s+Non-?Branded\s*Traffic", branded
    )
    if paired_pct:
        row["branded_pct"] = f"{paired_pct.group(1)}%"
        row["nonbranded_pct"] = f"{paired_pct.group(2)}%"
    else:
        branded_pct = re.search(r"([\d.]+)\s*%\s*Branded\s*Traffic", branded)
        nonbranded_pct = re.search(r"([\d.]+)\s*%\s*Non-?Branded\s*Traffic", branded)
        if branded_pct:
            row["branded_pct"] = f"{branded_pct.group(1)}%"
        if nonbranded_pct:
            row["nonbranded_pct"] = f"{nonbranded_pct.group(1)}%"
    return {k: v for k, v in row.items() if v is not None}


_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
    "|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
# Matches "Aug 25, 2026", "August 25 2026", or ISO "2026-08-25" — Semrush PDF
# exports print a generation/data-as-of date somewhere on the page (exact
# wording/position varies by report type), so this scans the whole page text
# rather than anchoring to a label. UNVERIFIED against a real export as of
# 2026-08-28 (no sample file on hand) — first match wins, so if a report ever
# prints an unrelated date earlier in the text than the real one, this will
# need a label-anchored rewrite once a real sample surfaces the actual format.
_DATE_PATTERN = re.compile(
    rf"\b(?:({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})|(\d{{4}})-(\d{{2}})-(\d{{2}}))\b"
)
_MONTH_ABBREV = {
    "january": "Jan", "jan": "Jan", "february": "Feb", "feb": "Feb", "march": "Mar", "mar": "Mar",
    "april": "Apr", "apr": "Apr", "may": "May", "june": "Jun", "jun": "Jun", "july": "Jul", "jul": "Jul",
    "august": "Aug", "aug": "Aug", "september": "Sep", "sep": "Sep", "sept": "Sep",
    "october": "Oct", "oct": "Oct", "november": "Nov", "nov": "Nov", "december": "Dec", "dec": "Dec",
}


def _extract_export_date(text: str) -> str | None:
    """Best-effort scan for a report-generation / data-as-of date printed
    somewhere in a Semrush PDF export's text layer. Returns 'Aug 25, 2026'
    style, or None if nothing date-shaped is found. See _DATE_PATTERN note —
    unverified against a real export."""
    match = _DATE_PATTERN.search(text)
    if not match:
        return None
    month_name, day, year, iso_year, iso_month, iso_day = match.groups()
    if month_name:
        try:
            day_i, year_i = int(day), int(year)
        except ValueError:
            return None
        if not (1 <= day_i <= 31 and 2015 <= year_i <= 2035):
            return None
        return f"{_MONTH_ABBREV.get(month_name.lower(), month_name[:3].title())} {day_i}, {year_i}"
    try:
        from datetime import date as _date

        return _date(int(iso_year), int(iso_month), int(iso_day)).strftime("%b %d, %Y")
    except ValueError:
        return None


_CRAWLED_PAGE_CATEGORIES = ["Blocked", "Redirect", "Have issues", "Broken", "Healthy"]


def parse_site_audit_overview_pdf(content: bytes) -> dict | None:
    """Parses Semrush's "Site Audit: Overview" PDF export — a real full-site
    crawl's Site Health %, AI Search Health %, and the Blocked/Redirect/Have
    issues/Broken/Healthy page breakdown. Distinct from the Domain Overview
    PDF (traffic/backlinks/competitors) and from the Site Audit issues.csv
    (per-issue-type rollup) — this is the crawl-health summary only. Returns
    None if this doesn't look like a Site Audit Overview PDF.

    "Site Health" and "AI Search Health" render as two side-by-side stat
    cards — the same layout that caused the Domain Overview PDF's Organic/
    Paid summary interleaving bug (see parse_domain_overview_pdf). A plain
    joined-text regex assuming a fixed line order broke in production even
    though it matched locally, so this splits the page at the x-position
    between the two "Health" words (proven technique, same as the "Paid"
    word split below) instead of trusting text-extraction row order."""
    site_health_pct = ai_search_health_pct = None
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if "Site Health" not in text or "Crawled Pages" not in text:
            return None

        for page in pdf.pages:
            health_words = sorted((w for w in page.extract_words() if w["text"] == "Health"), key=lambda w: w["top"])
            if len(health_words) < 2:
                continue
            row_top = health_words[0]["top"]
            row_words = sorted((w for w in health_words if abs(w["top"] - row_top) < 3), key=lambda w: w["x0"])
            if len(row_words) < 2:
                continue
            boundary = (row_words[0]["x1"] + row_words[1]["x0"]) / 2
            left_text = page.within_bbox((0, 0, boundary, page.height)).extract_text() or ""
            right_text = page.within_bbox((boundary, 0, page.width, page.height)).extract_text() or ""
            left_pct = re.search(r"(\d+)%", left_text)
            right_pct = re.search(r"(\d+)%", right_text)
            if left_pct and right_pct:
                site_health_pct = int(left_pct.group(1))
                ai_search_health_pct = int(right_pct.group(1))
            break

    if site_health_pct is None:
        return None

    result = {
        "site_health_pct": site_health_pct,
        "ai_search_health_pct": ai_search_health_pct,
        "export_date": _extract_export_date(text),
    }
    for category in _CRAWLED_PAGE_CATEGORIES:
        key = category.lower().replace(" ", "_")
        match = re.search(rf"{re.escape(category)}\s+([\d.]+)%\s+(\d+)", text)
        if match:
            result[f"{key}_pct"] = float(match.group(1))
            result[f"{key}_count"] = int(match.group(2))

    total_match = re.search(r"Total\s+(\d+)", text)
    if not total_match:
        return None
    result["pages_total"] = int(total_match.group(1))
    return result


_LINK_ATTRIBUTE_LABELS = ["Follow", "Nofollow", "Sponsored", "UGC"]


def parse_backlink_list_pdf(content: bytes) -> dict | None:
    """Parses Semrush's "Backlink List" PDF export — the summary stat row
    (Authority Score, Referring Domains, Total Backlinks, Referring IPs) and
    the Link Attributes breakdown (Follow/Nofollow/Sponsored/UGC). Does NOT
    parse the per-backlink table itself (page 3+) — that page's text layer
    is a garbled mess of embedded template source, not real link data; the
    summary stats are the only clean, reliably-parseable numbers in this
    export. Returns None if this doesn't look like a Backlink List PDF.

    The 4 summary stats render as side-by-side cards — same layout risk
    that broke the Site Health PDF's line-order regex in production — so
    this locates them by word position (the value row's x-position, sorted
    left to right) instead of trusting extract_text()'s line order."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if "Backlink List" not in text or "Referring Domains" not in text:
            return None

        result = {}
        for page in pdf.pages:
            words = page.extract_words()
            authority_word = next((w for w in words if w["text"] == "Authority"), None)
            if not authority_word:
                continue
            label_top = authority_word["top"]
            value_words = sorted(
                (w for w in words if re.match(r"^[\d.,]+[KMB]?$", w["text"]) and label_top < w["top"] < label_top + 40),
                key=lambda w: w["x0"],
            )
            if len(value_words) >= 4:
                result["authority_score"] = _parse_abbrev_number(value_words[0]["text"])
                result["referring_domains"] = _parse_abbrev_number(value_words[1]["text"])
                result["backlinks_total"] = _parse_abbrev_number(value_words[2]["text"])
                result["referring_ips"] = _parse_abbrev_number(value_words[3]["text"])
            break

        if "backlinks_total" not in result:
            return None

        result["export_date"] = _extract_export_date(text)

        # Link Attributes is a plain single-column list (Follow/Nofollow/
        # Sponsored/UGC each its own line) — no side-by-side ambiguity here,
        # a joined-text regex is safe.
        for label in _LINK_ATTRIBUTE_LABELS:
            key = label.lower()
            match = re.search(rf"\b{label}\s+([\d.]+)%\s+([\d.,]+[KMB]?)", text)
            if match:
                result[f"{key}_pct"] = float(match.group(1))
                result[f"{key}_count"] = _parse_abbrev_number(match.group(2))

    return result


def parse_domain_overview_pdf(content: bytes) -> dict | None:
    """Parses Semrush's "Domain Overview (Desktop)" PDF export — the
    single-domain report available on every plan (no Bulk Analysis needed).
    Pulls Organic/Paid Traffic, Keywords, Backlinks, Referring Domains, and
    Branded/Non-Branded split — the same fields DOMAIN_OVERVIEW_COLUMN_ALIASES
    expects from a CSV. Authority Score/DR is NOT included in this PDF
    export at all (confirmed missing even when visible on the live page) —
    callers must fill that in manually. Returns None if this doesn't look
    like a Domain Overview PDF."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        page2_left = page2_right = ""
        if len(pdf.pages) > 1:
            page2 = pdf.pages[1]
            # Split at the actual x-position of the "Paid" word rather than
            # an assumed page-width midpoint — page margins mean the true
            # column boundary rarely sits at width/2, and a fixed guess
            # either bleeds into the right column or clips its first
            # characters (confirmed both ways while testing this).
            paid_word = next((w for w in page2.extract_words() if w["text"] == "Paid"), None)
            if paid_word:
                boundary = paid_word["x0"]
                page2_left = page2.within_bbox((0, 0, boundary, page2.height)).extract_text() or ""
                page2_right = page2.within_bbox((boundary, 0, page2.width, page2.height)).extract_text() or ""

        # "Organic Branded Search" (left) and "Branded vs Non-Branded"
        # (right) sit side by side on their own page, same interleaving
        # risk as the Organic/Paid Summary cards. Find whichever page has
        # both "Branded" occurrences (the heading row) and split at the
        # second one's x-position — the first "Branded" belongs to the left
        # card's own heading, so it can't be used as the boundary itself.
        branded_col = ""
        for page in pdf.pages:
            branded_words = sorted(
                (w for w in page.extract_words() if w["text"] == "Branded"), key=lambda w: (w["top"], w["x0"])
            )
            if len(branded_words) >= 2 and abs(branded_words[0]["top"] - branded_words[1]["top"]) < 2:
                boundary = branded_words[1]["x0"]
                branded_col = page.within_bbox((boundary, 0, page.width, page.height)).extract_text() or ""
                break

        top_countries = _extract_top_countries(pdf)
    return _parse_domain_overview_text(text, page2_left, page2_right, branded_col, top_countries)


def _read_table(filename: str, content: bytes) -> pd.DataFrame:
    buffer = io.BytesIO(content)
    name = filename.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(buffer)
    if name.endswith(".json"):
        return pd.read_json(buffer)
    if name.endswith(".xml"):
        return pd.read_xml(buffer, parser="etree")
    if name.endswith(".tsv"):
        return pd.read_csv(buffer, sep="\t", index_col=False)
    # Semrush CSV exports are typically semicolon or comma separated.
    # index_col=False is required: some real Semrush exports (confirmed on
    # a Structured Data export) have one more field per data row than the
    # header has names (a trailing unlabeled column) — pandas' default
    # behavior in that case silently treats the first column as a row
    # index instead of real data, which shifts every OTHER column's values
    # one slot away from their header, corrupting the whole row rather
    # than just dropping the one extra trailing field.
    try:
        return pd.read_csv(buffer, sep=None, engine="python", index_col=False)
    except Exception:
        buffer.seek(0)
        return pd.read_csv(buffer, index_col=False)


def _map_columns(df: pd.DataFrame, aliases: dict[str, list[str]]) -> pd.DataFrame:
    lower_cols = {c.lower().strip(): c for c in df.columns}
    rename_map = {}
    for field, candidates in aliases.items():
        for candidate in candidates:
            if candidate in lower_cols:
                rename_map[lower_cols[candidate]] = field
                break
    mapped = df.rename(columns=rename_map)
    keep_cols = [c for c in aliases.keys() if c in mapped.columns]
    return mapped[keep_cols]


_OVERVIEW_TREND_METRICS = {
    "organic traffic": "organic_traffic",
    "organic keywords": "organic_keywords",
    "paid traffic": "paid_traffic",
    "paid keywords": "paid_keywords",
}


def _parse_overview_trend_df(df: pd.DataFrame) -> dict | None:
    """Semrush's "Overview Trend" CSV export — same core metrics as Domain
    Overview, but with an explicit Database column per metric row (e.g.
    "Worldwide", "US") plus a daily trend, one column per date. Only
    Worldwide-database exports are used for now (the Worldwide gap this was
    built for — Domain Overview PDF is always a single country's database,
    never Worldwide).

    Uses the most recent day's value per metric as the "current" figure —
    NOT the Summary column, which is the SUM across every day in the trend
    window and isn't a comparable snapshot (confirmed against a real
    export: Summary ran ~30x a single day's value, while manual-report
    reference numbers for other domains were snapshot-scale, matching a
    single day's figure, not a monthly sum)."""
    cols = {c.lower().strip(): c for c in df.columns}
    if not {"target", "metric", "database"} <= set(cols):
        return None
    date_cols = [c for c in df.columns if re.match(r"^\d{4}-\d{2}-\d{2}$", str(c).strip())]
    if not date_cols:
        return None
    latest_col = sorted(date_cols)[-1]

    target_col, metric_col, database_col = cols["target"], cols["metric"], cols["database"]
    if df.empty:
        return None
    domain = str(df[target_col].iloc[0]).strip()
    database = str(df[database_col].iloc[0]).strip()

    result = {"domain": domain, "database": database, "trend_date": str(latest_col).strip()}
    for _, row in df.iterrows():
        field = _OVERVIEW_TREND_METRICS.get(str(row[metric_col]).strip().lower())
        if not field:
            continue
        try:
            result[field] = float(row[latest_col])
        except (TypeError, ValueError):
            continue
    return result if "organic_traffic" in result else None


def detect_import_type(df: pd.DataFrame) -> str:
    cols = {c.lower().strip() for c in df.columns}
    if {"source url", "target url", "source_url", "target_url"} & cols or "anchor" in cols:
        return "backlinks"
    if {"common keywords", "competitor relevance", "common_keywords", "competitor_relevance"} & cols:
        return "organic_competitors"
    if "keyword" in cols and "position" in cols:
        return "organic_positions"
    if "keyword" in cols and ({"search volume", "volume", "search_volume"} & cols):
        return "keyword_gap"
    if "domain" in cols and ({"rank", "semrush rank", "semrush_rank"} & cols):
        return "domain_overview"
    if {"page url", "page_url"} & cols and ({"http status code", "http_status_code"} & cols):
        return "site_audit_pages"
    if "issue" in cols and ({"failed checks", "failed_checks"} & cols) and ({"total checks", "total_checks"} & cols):
        return "site_audit_issues"
    # Structured Data export has no "http status code" column (unlike
    # site_audit_pages above) — instead it's "page url" plus at least 2 of
    # the ~29 "<type> items" rich-result-count columns.
    if {"page url", "page_url"} & cols and sum(1 for c in cols if c.endswith(" items")) >= 2:
        return "structured_data"
    return "unknown"


def parse_semrush_file(filename: str, content: bytes) -> tuple[str, dict]:
    if filename.lower().endswith(".pdf"):
        overview_row = parse_site_audit_overview_pdf(content)
        if overview_row is not None:
            return "site_audit_overview", {"row_count": 1, "rows": [overview_row]}
        backlink_row = parse_backlink_list_pdf(content)
        if backlink_row is not None:
            return "backlink_summary", {"row_count": 1, "rows": [backlink_row]}
        row = parse_domain_overview_pdf(content)
        if row is None:
            return "unknown", {"row_count": 0, "rows": []}
        return "domain_overview", {"row_count": 1, "rows": [row]}

    df = _read_table(filename, content)

    trend_row = _parse_overview_trend_df(df)
    if trend_row is not None:
        return "overview_trend", {"row_count": 1, "rows": [trend_row]}

    import_type = detect_import_type(df)

    if import_type == "backlinks":
        mapped = _map_columns(df, BACKLINKS_COLUMN_ALIASES)
    elif import_type == "organic_competitors":
        mapped = _map_columns(df, ORGANIC_COMPETITORS_COLUMN_ALIASES)
    elif import_type == "keyword_gap":
        mapped = _map_columns(df, KEYWORD_GAP_COLUMN_ALIASES)
    elif import_type == "organic_positions":
        mapped = _map_columns(df, ORGANIC_POSITIONS_COLUMN_ALIASES)
    elif import_type == "domain_overview":
        mapped = _map_columns(df, DOMAIN_OVERVIEW_COLUMN_ALIASES)
    elif import_type == "site_audit_pages":
        mapped = _map_columns(df, SITE_AUDIT_PAGES_COLUMN_ALIASES)
    elif import_type == "site_audit_issues":
        mapped = _map_columns(df, SITE_AUDIT_ISSUES_COLUMN_ALIASES)
    elif import_type == "structured_data":
        mapped = _map_columns(df, STRUCTURED_DATA_COLUMN_ALIASES)
    else:
        mapped = df

    mapped = mapped.head(500)  # cap rows stored to keep JSONB payload reasonable

    if import_type == "keyword_gap":
        domain_cols = _detect_keyword_gap_domain_columns(df)
        if domain_cols:
            # .item() converts numpy scalars (int64/float64) to native
            # Python types — without it these values fail to JSON-encode
            # when the parsed row gets stored to the JSONB column (pandas'
            # own .to_dict() does this conversion automatically for the
            # aliased columns above; this per-cell lookup does not).
            def _cell(v):
                if pd.isna(v):
                    return None
                return v.item() if hasattr(v, "item") else v

            mapped = mapped.copy()
            pages_cols = {d: f"{d} (pages)" for d in domain_cols if f"{d} (pages)" in df.columns}
            positions, urls = [], []
            for idx in mapped.index:
                row = df.loc[idx]
                positions.append({d: _cell(row[d]) for d in domain_cols})
                urls.append({d: _cell(row[c]) for d, c in pages_cols.items()})
            mapped["domain_positions"] = positions
            if pages_cols:
                mapped["domain_ranking_urls"] = urls

    parsed_data = {
        "row_count": len(df),
        "rows": mapped.fillna("").to_dict(orient="records"),
    }
    return import_type, parsed_data
