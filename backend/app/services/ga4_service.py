import statistics
from datetime import date as _date

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def list_properties(creds: Credentials) -> list[dict]:
    admin = build("analyticsadmin", "v1beta", credentials=creds)
    results = []
    accounts = admin.accounts().list().execute()
    for acc in accounts.get("accounts", []):
        props = admin.properties().list(filter=f"parent:{acc['name']}").execute()
        for p in props.get("properties", []):
            results.append({"name": p["name"], "display_name": p["displayName"]})
    return results


def _data_client(creds: Credentials):
    return build("analyticsdata", "v1beta", credentials=creds)


def get_traffic_overview(creds: Credentials, property_id: str, start_date: str, end_date: str) -> dict:
    client = _data_client(creds)
    body = {
        "dimensions": [{"name": "date"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "screenPageViews"},
            {"name": "engagementRate"},
            {"name": "userEngagementDuration"},
            {"name": "activeUsers"},
            {"name": "bounceRate"},
        ],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "orderBys": [{"dimension": {"dimensionName": "date"}}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    rows = []
    for row in response.get("rows", []):
        dv = row["dimensionValues"]
        mv = row["metricValues"]
        rows.append(
            {
                "date": dv[0]["value"],
                "sessions": mv[0]["value"],
                "total_users": mv[1]["value"],
                "page_views": mv[2]["value"],
                "engagement_rate": mv[3]["value"],
                "engagement_duration": mv[4]["value"],
                "active_users": mv[5]["value"],
                "bounce_rate": mv[6]["value"],
            }
        )
    return {"rows": rows}


def get_top_pages(creds: Credentials, property_id: str, start_date: str, end_date: str, limit: int = 20) -> dict:
    client = _data_client(creds)
    body = {
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}, {"name": "userEngagementDuration"}, {"name": "activeUsers"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "limit": limit,
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    rows = []
    for row in response.get("rows", []):
        dv = row["dimensionValues"]
        mv = row["metricValues"]
        rows.append(
            {
                "path": dv[0]["value"],
                "page_views": mv[0]["value"],
                "engagement_duration": mv[1]["value"],
                "active_users": mv[2]["value"],
            }
        )
    return {"rows": rows}


# Legal/utility pages (careers, privacy policy, cookie policy, security
# page) almost always have low pageviews for reasons unrelated to SEO
# performance — nobody visits them on purpose — so they crowd out the
# "poor performing pages" list with a non-signal. Excluded before the
# top/bottom split (not just at display time) so a real weak page isn't
# pushed out of the bottom-N slots by one of these.
_EXCLUDED_PAGE_PATH_PATTERNS = ["career", "privacy", "cookie", "security"]


def _is_excluded_page_path(path: str) -> bool:
    path_lower = (path or "").lower()
    return any(pattern in path_lower for pattern in _EXCLUDED_PAGE_PATH_PATTERNS)


def get_page_performance(
    creds: Credentials,
    property_id: str,
    start_date: str,
    end_date: str,
    top_n: int = 10,
    bottom_n: int = 10,
    max_rows: int = 1000,
) -> dict:
    """Every page's pageviews for the period, then split into top/bottom
    performers with each page's % share of total pageviews, plus the total
    page count that contributed traffic in the window."""
    client = _data_client(creds)
    body = {
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}, {"name": "userEngagementDuration"}, {"name": "bounceRate"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "limit": max_rows,
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()

    rows = []
    for row in response.get("rows", []):
        dv = row["dimensionValues"]
        mv = row["metricValues"]
        rows.append(
            {
                "path": dv[0]["value"],
                "page_views": int(mv[0]["value"]),
                "engagement_duration": mv[1]["value"],
                "bounce_rate": mv[2]["value"],
            }
        )

    total_page_views = sum(r["page_views"] for r in rows)
    total_pages = len(rows)

    def _with_pct(r):
        pct = round(100 * r["page_views"] / total_page_views, 2) if total_page_views else 0
        return {**r, "pct_of_total": pct}

    # total_page_views/total_pages above stay based on every page (the site
    # really did get that traffic) — only the top/bottom picks exclude
    # legal/utility pages, since those aren't a meaningful performance signal.
    eligible_rows = [r for r in rows if not _is_excluded_page_path(r["path"])]
    top_pages = [_with_pct(r) for r in eligible_rows[:top_n]]
    bottom_pages = [_with_pct(r) for r in list(reversed(eligible_rows))[:bottom_n]]

    return {
        "total_pages": total_pages,
        "total_page_views": total_page_views,
        "truncated": len(response.get("rows", [])) >= max_rows,
        "top_pages": top_pages,
        "bottom_pages": bottom_pages,
    }


_DEMOGRAPHIC_IGNORE = {"(not set)", "unknown", ""}


def get_traffic_spike_breakdown(creds: Credentials, property_id: str, daily_rows: list[dict]) -> dict | None:
    """Finds the single biggest single-day traffic spike in the period (a day
    well above the period average — not just the highest day, since every
    period has *a* highest day even with near-zero real variance) and breaks
    down who drove it: age bracket, gender, country. Returns None when
    there's no real spike (flat traffic) or too few days to judge against.

    Age/gender need Google Signals / demographics enabled on the GA4
    property — on a property without it those two come back empty and are
    dropped, but country (always available) still shows."""
    days = [
        (r["date"], int(float(r["sessions"])))
        for r in daily_rows
        if r.get("date") and r.get("sessions") not in (None, "")
    ]
    if len(days) < 5:
        return None

    sessions_values = [s for _, s in days]
    mean = statistics.mean(sessions_values)
    stdev = statistics.pstdev(sessions_values)
    spike_date, spike_sessions = max(days, key=lambda d: d[1])

    # Both a relative jump (30%+ above average) and a statistical outlier
    # (1.5+ standard deviations above average) must hold — relative-only
    # would flag noise on a low-traffic site, stdev-only would flag a
    # trivial bump on a site with naturally flat/spiky daily counts.
    if stdev == 0 or spike_sessions < mean * 1.3 or (spike_sessions - mean) / stdev < 1.5:
        return None

    iso_date = f"{spike_date[0:4]}-{spike_date[4:6]}-{spike_date[6:8]}"

    client = _data_client(creds)
    body = {
        "dimensions": [{"name": "userAgeBracket"}, {"name": "userGender"}, {"name": "country"}],
        "metrics": [{"name": "sessions"}],
        "dateRanges": [{"startDate": iso_date, "endDate": iso_date}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()

    age_totals, gender_totals, country_totals = {}, {}, {}
    for row in response.get("rows", []):
        dv = row["dimensionValues"]
        row_sessions = int(row["metricValues"][0]["value"])
        age, gender, country = dv[0]["value"], dv[1]["value"], dv[2]["value"]
        if age not in _DEMOGRAPHIC_IGNORE:
            age_totals[age] = age_totals.get(age, 0) + row_sessions
        if gender not in _DEMOGRAPHIC_IGNORE:
            gender_totals[gender] = gender_totals.get(gender, 0) + row_sessions
        if country not in _DEMOGRAPHIC_IGNORE:
            country_totals[country] = country_totals.get(country, 0) + row_sessions

    def _ranked(totals: dict, n: int) -> list[dict]:
        total_all = sum(totals.values())
        top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:n]
        return [
            {"label": label, "sessions": s, "pct": round(100 * s / total_all, 1) if total_all else 0}
            for label, s in top
        ]

    return {
        "date": iso_date,
        "day_of_week": _date.fromisoformat(iso_date).strftime("%A"),
        "sessions": spike_sessions,
        "avg_sessions": round(mean),
        "pct_above_avg": round(100 * (spike_sessions - mean) / mean, 1) if mean else 0,
        "by_age": _ranked(age_totals, 5),
        "by_gender": _ranked(gender_totals, 3),
        "by_country": _ranked(country_totals, 5),
    }


def get_traffic_sources(creds: Credentials, property_id: str, start_date: str, end_date: str) -> dict:
    client = _data_client(creds)
    body = {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    rows = []
    for row in response.get("rows", []):
        dv = row["dimensionValues"]
        mv = row["metricValues"]
        rows.append({"channel": dv[0]["value"], "sessions": mv[0]["value"], "users": mv[1]["value"]})
    return {"rows": rows}
