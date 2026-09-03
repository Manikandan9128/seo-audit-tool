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


def _single_dimension_breakdown(client, property_id: str, iso_date: str, dimension: str, n: int) -> list[dict]:
    """One day's sessions broken down by a single GA4 dimension, top n by
    share. Queried alone (not cross-tabbed with other dimensions) — GA4
    applies data-thresholding to protect privacy, suppressing any row whose
    segment is too small. Cross-tabbing age+gender+country in one query was
    tried first and confirmed to fragment a single day's sessions into
    combinations nearly all below that threshold — even country, which
    needs no Google Signals and should almost always report something on
    its own, came back empty when bundled with the others. One dimension
    per query keeps each bucket coarse enough to usually clear it."""
    body = {
        "dimensions": [{"name": dimension}],
        "metrics": [{"name": "sessions"}],
        "dateRanges": [{"startDate": iso_date, "endDate": iso_date}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    totals: dict[str, int] = {}
    for row in response.get("rows", []):
        value = row["dimensionValues"][0]["value"]
        if value in _DEMOGRAPHIC_IGNORE:
            continue
        sessions = int(row["metricValues"][0]["value"])
        totals[value] = totals.get(value, 0) + sessions
    total_all = sum(totals.values())
    top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [
        {"label": label, "sessions": s, "pct": round(100 * s / total_all, 1) if total_all else 0}
        for label, s in top
    ]


def get_traffic_spike_breakdown(creds: Credentials, property_id: str, daily_rows: list[dict]) -> dict | None:
    """Finds the single biggest single-day traffic spike in the period (a day
    well above the period average — not just the highest day, since every
    period has *a* highest day even with near-zero real variance) and breaks
    down who drove it: age bracket, gender, country, and acquisition
    channel. Returns None when there's no real spike (flat traffic) or too
    few days to judge against.

    Age/gender need Google Signals / demographics enabled on the GA4
    property — on a property without it those two come back empty and are
    dropped, but country and channel (neither needs Signals) still show."""
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

    return {
        "date": iso_date,
        "day_of_week": _date.fromisoformat(iso_date).strftime("%A"),
        "sessions": spike_sessions,
        "avg_sessions": round(mean),
        "pct_above_avg": round(100 * (spike_sessions - mean) / mean, 1) if mean else 0,
        "by_age": _single_dimension_breakdown(client, property_id, iso_date, "userAgeBracket", 5),
        "by_gender": _single_dimension_breakdown(client, property_id, iso_date, "userGender", 3),
        "by_country": _single_dimension_breakdown(client, property_id, iso_date, "country", 5),
        "by_channel": _single_dimension_breakdown(client, property_id, iso_date, "sessionDefaultChannelGroup", 5),
    }


def _months_in_range(start_date: str, end_date: str) -> float:
    days = (_date.fromisoformat(end_date) - _date.fromisoformat(start_date)).days + 1
    return max(days / 30.44, 1.0)


def _channel_crosstab(client, property_id: str, start_date: str, end_date: str, dimension: str, top_n: int) -> dict[str, list[dict]]:
    """Sessions for (channel, dimension) pairs, grouped by channel with each
    channel's own top-n dimension values by share — e.g. per-channel top
    countries, not a single global top-n across all channels."""
    body = {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}, {"name": dimension}],
        "metrics": [{"name": "sessions"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "limit": 100000,
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    by_channel: dict[str, dict[str, int]] = {}
    for row in response.get("rows", []):
        channel = row["dimensionValues"][0]["value"]
        value = row["dimensionValues"][1]["value"]
        if value in _DEMOGRAPHIC_IGNORE:
            continue
        sessions = int(row["metricValues"][0]["value"])
        by_channel.setdefault(channel, {})
        by_channel[channel][value] = by_channel[channel].get(value, 0) + sessions

    result: dict[str, list[dict]] = {}
    for channel, totals in by_channel.items():
        channel_total = sum(totals.values())
        top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        result[channel] = [
            {"label": label, "pct": round(100 * s / channel_total, 1) if channel_total else 0}
            for label, s in top
        ]
    return result


def get_traffic_channel_breakdown(
    creds: Credentials, property_id: str, start_date: str, end_date: str, top_n_secondary: int = 3
) -> dict:
    """Channel is the primary key for this breakdown (per report spec) —
    one row per channel with its own monthly-average sessions/users and,
    folded into the same row rather than separate country/device sections,
    that channel's own top countries and device split. Two 2-dimension
    queries (channel+country, channel+device) instead of one 3-dimension
    cross-tab — GA4's data-thresholding fragments a 3-way cross-tab too
    much to reliably return rows (see get_traffic_spike_breakdown)."""
    client = _data_client(creds)
    months = _months_in_range(start_date, end_date)

    body = {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    channel_totals = []
    for row in response.get("rows", []):
        dv, mv = row["dimensionValues"], row["metricValues"]
        channel_totals.append((dv[0]["value"], int(mv[0]["value"]), int(mv[1]["value"])))
    total_sessions = sum(s for _, s, _ in channel_totals)

    countries_by_channel = _channel_crosstab(client, property_id, start_date, end_date, "country", top_n_secondary)
    devices_by_channel = _channel_crosstab(client, property_id, start_date, end_date, "deviceCategory", top_n_secondary)

    rows = [
        {
            "channel": channel,
            "avg_sessions_month": round(sessions / months),
            "avg_users_month": round(users / months),
            "pct_share": round(100 * sessions / total_sessions, 1) if total_sessions else 0,
            "top_countries": countries_by_channel.get(channel, []),
            "top_devices": devices_by_channel.get(channel, []),
        }
        for channel, sessions, users in channel_totals
    ]
    return {"rows": rows, "months": round(months, 1)}


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

    # New vs. Returning is a separate 2-dimension query (channel +
    # newVsReturning) merged onto the rows above by channel, same reasoning
    # as get_traffic_channel_breakdown: keeping it out of the first query
    # avoids fragmenting sessions/totalUsers into thresholded sub-buckets.
    nvr_body = {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}, {"name": "newVsReturning"}],
        "metrics": [{"name": "totalUsers"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "limit": 100000,
    }
    nvr_response = client.properties().runReport(property=property_id, body=nvr_body).execute()
    nvr_by_channel: dict[str, dict[str, int]] = {}
    for row in nvr_response.get("rows", []):
        channel = row["dimensionValues"][0]["value"]
        segment = row["dimensionValues"][1]["value"]
        if segment in _DEMOGRAPHIC_IGNORE:
            continue
        users = int(row["metricValues"][0]["value"])
        nvr_by_channel.setdefault(channel, {})[segment] = users

    for r in rows:
        segment_users = nvr_by_channel.get(r["channel"], {})
        new_users = segment_users.get("new", 0)
        returning_users = segment_users.get("returning", 0)
        r["new_users"] = new_users
        r["returning_users"] = returning_users
        total = new_users + returning_users
        r["return_rate_pct"] = round(100 * returning_users / total, 1) if total else None

    return {"rows": rows}
