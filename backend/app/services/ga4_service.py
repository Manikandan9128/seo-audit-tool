import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


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


def _single_day_engagement_metrics(client, property_id: str, iso_date: str) -> dict[str, float]:
    """Direct single-day query for bounce rate + engagement rate, same
    proven pattern as _single_dimension_breakdown (used for the spike
    slide's country/channel/landing-page breakdowns): pulling this one
    day's value out of the 30-day get_traffic_overview batch sometimes came
    back empty for that specific row even though the aggregate (and every
    other day) was fine — confirmed real, Traffic Overview's own bounce-
    rate card renders a genuine non-zero number from the same underlying
    data. A dedicated request scoped to just this one day, mirroring how
    the other per-day breakdowns already work, is far more likely to
    return a real value than picking it out of the larger batch."""
    body = {
        "metrics": [{"name": "bounceRate"}, {"name": "engagementRate"}],
        "dateRanges": [{"startDate": iso_date, "endDate": iso_date}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    rows = response.get("rows", [])
    if not rows:
        return {}
    mv = rows[0]["metricValues"]
    return {"bounce_rate": float(mv[0]["value"]), "engagement_rate": float(mv[1]["value"])}


def _daily_metric_totals(client, property_id: str, start_date: str, end_date: str, metric_name: str) -> dict[str, float]:
    """date (YYYYMMDD) -> metric total, one query for the whole period —
    same shape as get_traffic_overview's per-day rows but for a metric
    that isn't pulled there (keyEvents), so the spike-day value and the
    period average can both be read off one call."""
    body = {
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": metric_name}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
    }
    response = client.properties().runReport(property=property_id, body=body).execute()
    return {row["dimensionValues"][0]["value"]: float(row["metricValues"][0]["value"]) for row in response.get("rows", [])}


def get_traffic_spike_breakdown(creds: Credentials, property_id: str, daily_rows: list[dict]) -> dict | None:
    """Finds the single biggest single-day traffic spike in the period (a day
    well above the period average — not just the highest day, since every
    period has *a* highest day even with near-zero real variance) and breaks
    down who drove it: age bracket, gender, country, acquisition channel,
    and (for the causal-hypothesis evidence chain below) landing page,
    engagement rate, and key events. Returns None when there's no real
    spike (flat traffic) or too few days to judge against.

    Age/gender need Google Signals / demographics enabled on the GA4
    property — on a property without it those two come back empty and are
    dropped, but country and channel (neither needs Signals) still show.

    channel -> landing_page -> engagement -> key_event evidence chain:
    teammate QA on the last report flagged this slide as a plain date/
    country/channel dump with no causal reasoning. by_landing_page (same
    per-day dimension query pattern as by_channel/by_country) plus spike-day
    vs period-average engagement rate and key events give pptx_builder's
    add_traffic_spike_slide real numbers to build a testable hypothesis
    from — genuine demand (engagement/key events rose with sessions) vs.
    low-intent/bot traffic (sessions rose but engagement/key events didn't)
    — instead of just listing who showed up."""
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

    # Period engagement-rate average and spike-day value both come straight
    # off daily_rows (get_traffic_overview already pulls engagementRate per
    # day) — no extra API call needed for that half of the evidence chain.
    engagement_by_date = {
        r["date"]: float(r["engagement_rate"]) for r in daily_rows if r.get("date") and r.get("engagement_rate") not in (None, "")
    }
    avg_engagement_rate = statistics.mean(engagement_by_date.values()) if engagement_by_date else None
    spike_engagement_rate = engagement_by_date.get(spike_date)

    # Bounce rate — same daily_rows, same zero-extra-call pattern as
    # engagement rate above. This is the one number the slide was missing
    # to answer "was this good traffic or noise" instead of just "traffic
    # went up": a spike with a normal bounce rate is a real volume event, a
    # spike with a much higher bounce rate is likely low-quality/bot
    # traffic even though sessions look great.
    bounce_by_date = {
        r["date"]: float(r["bounce_rate"]) for r in daily_rows if r.get("date") and r.get("bounce_rate") not in (None, "")
    }
    avg_bounce_rate = statistics.mean(bounce_by_date.values()) if bounce_by_date else None
    spike_bounce_rate = bounce_by_date.get(spike_date)

    # Spike-day-specific bounce/engagement can come back empty from the
    # batched 30-day pull above even when the period average (and every
    # other day) is fine — a dedicated single-day query, same pattern as
    # the country/channel/landing-page breakdowns below, recovers it.
    if spike_bounce_rate is None or spike_engagement_rate is None:
        try:
            single_day = _single_day_engagement_metrics(client, property_id, iso_date)
        except HttpError:
            single_day = {}
        if spike_bounce_rate is None and "bounce_rate" in single_day:
            spike_bounce_rate = single_day["bounce_rate"]
        if spike_engagement_rate is None and "engagement_rate" in single_day:
            spike_engagement_rate = single_day["engagement_rate"]

    # Average session duration (seconds) = engagement_duration / sessions
    # per day — GA4 doesn't expose "average session duration" as its own
    # per-day metric in get_traffic_overview, but it's derivable from the
    # two totals already pulled there, so no extra API call here either.
    duration_by_date: dict[str, float] = {}
    for r in daily_rows:
        d, dur, s = r.get("date"), r.get("engagement_duration"), r.get("sessions")
        if d and dur not in (None, "") and s not in (None, "") and float(s) > 0:
            duration_by_date[d] = float(dur) / float(s)
    avg_session_duration_sec = statistics.mean(duration_by_date.values()) if duration_by_date else None
    spike_session_duration_sec = duration_by_date.get(spike_date)

    all_dates = [d for d, _ in days]
    period_start = f"{min(all_dates)[0:4]}-{min(all_dates)[4:6]}-{min(all_dates)[6:8]}"
    period_end = f"{max(all_dates)[0:4]}-{max(all_dates)[4:6]}-{max(all_dates)[6:8]}"
    try:
        key_events_by_date = _daily_metric_totals(client, property_id, period_start, period_end, "keyEvents")
    except HttpError:
        # Properties with no key events configured reject the metric
        # outright rather than returning zeros — the rest of the spike
        # breakdown (channel/landing page/engagement) is still real and
        # worth keeping, so this piece degrades to absent, not a hard fail.
        key_events_by_date = {}
    avg_key_events = statistics.mean(key_events_by_date.values()) if key_events_by_date else None
    spike_key_events = key_events_by_date.get(spike_date)

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
        "by_landing_page": _single_dimension_breakdown(client, property_id, iso_date, "landingPage", 5),
        "avg_engagement_rate": avg_engagement_rate,
        "spike_engagement_rate": spike_engagement_rate,
        "avg_key_events": avg_key_events,
        "spike_key_events": spike_key_events,
        "avg_bounce_rate": avg_bounce_rate,
        "spike_bounce_rate": spike_bounce_rate,
        "avg_session_duration_sec": avg_session_duration_sec,
        "spike_session_duration_sec": spike_session_duration_sec,
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
    months = _months_in_range(start_date, end_date)

    def _channel_totals_query():
        # bounceRate added as the quality signal this breakdown needs to
        # pair with each channel's size — a 30-day-only report has no
        # trend to lean on for "what's interesting," so every insight has
        # to come from comparing segments within this one period instead
        # (see add_traffic_channel_breakdown_slide). Same call, no extra
        # API round-trip.
        client = _data_client(creds)
        body = {
            "dimensions": [{"name": "sessionDefaultChannelGroup"}],
            "metrics": [{"name": "sessions"}, {"name": "totalUsers"}, {"name": "bounceRate"}],
            "dateRanges": [{"startDate": start_date, "endDate": end_date}],
            "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        }
        response = client.properties().runReport(property=property_id, body=body).execute()
        return [
            (
                row["dimensionValues"][0]["value"],
                int(row["metricValues"][0]["value"]),
                int(row["metricValues"][1]["value"]),
                float(row["metricValues"][2]["value"]),
            )
            for row in response.get("rows", [])
        ]

    # 3 independent GA4 queries — each ran sequentially before (a fresh
    # client per thread, not a shared one, matching the established
    # concurrency pattern the caller in site_audit.py already uses for
    # independent GA4/GSC calls) so this job's wall-clock time is ~1 call
    # instead of 3 stacked calls.
    with ThreadPoolExecutor(max_workers=3) as pool:
        totals_future = pool.submit(_channel_totals_query)
        countries_future = pool.submit(_channel_crosstab, _data_client(creds), property_id, start_date, end_date, "country", top_n_secondary)
        devices_future = pool.submit(_channel_crosstab, _data_client(creds), property_id, start_date, end_date, "deviceCategory", top_n_secondary)
        channel_totals = totals_future.result()
        countries_by_channel = countries_future.result()
        devices_by_channel = devices_future.result()
    total_sessions = sum(s for _, s, _, _ in channel_totals)

    rows = [
        {
            "channel": channel,
            "avg_sessions_month": round(sessions / months),
            "avg_users_month": round(users / months),
            "pct_share": round(100 * sessions / total_sessions, 1) if total_sessions else 0,
            # bounceRate is a 0-1 fraction from the API, stored as a 0-100
            # percentage here so pptx_builder never has to remember which
            # convention this field uses (other GA4 rate fields in this
            # codebase are stored raw and *100'd at render time instead —
            # this one's converted here since it's brand new, not touching
            # an existing convention).
            "bounce_rate_pct": round(bounce_rate * 100, 1),
            "top_countries": countries_by_channel.get(channel, []),
            "top_devices": devices_by_channel.get(channel, []),
        }
        for channel, sessions, users, bounce_rate in channel_totals
    ]
    return {"rows": rows, "months": round(months, 1)}


def _traffic_sources_query(creds: Credentials, property_id: str, start_date: str, end_date: str) -> dict:
    client = _data_client(creds)
    body = {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "totalUsers"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    }
    return client.properties().runReport(property=property_id, body=body).execute()


def _traffic_sources_nvr_query(creds: Credentials, property_id: str, start_date: str, end_date: str) -> dict:
    client = _data_client(creds)
    nvr_body = {
        "dimensions": [{"name": "sessionDefaultChannelGroup"}, {"name": "newVsReturning"}],
        "metrics": [{"name": "totalUsers"}],
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "limit": 100000,
    }
    return client.properties().runReport(property=property_id, body=nvr_body).execute()


def get_traffic_sources(creds: Credentials, property_id: str, start_date: str, end_date: str) -> dict:
    # Both queries are independent — run concurrently (fresh client per
    # thread, same pattern as get_traffic_channel_breakdown) instead of
    # stacking two sequential GA4 round-trips into this one job's time.
    with ThreadPoolExecutor(max_workers=2) as pool:
        main_future = pool.submit(_traffic_sources_query, creds, property_id, start_date, end_date)
        nvr_future = pool.submit(_traffic_sources_nvr_query, creds, property_id, start_date, end_date)
        response = main_future.result()
        nvr_response = nvr_future.result()

    rows = []
    for row in response.get("rows", []):
        dv = row["dimensionValues"]
        mv = row["metricValues"]
        rows.append({"channel": dv[0]["value"], "sessions": mv[0]["value"], "users": mv[1]["value"]})

    # New vs. Returning is a separate 2-dimension query (channel +
    # newVsReturning) merged onto the rows above by channel, same reasoning
    # as get_traffic_channel_breakdown: keeping it out of the first query
    # avoids fragmenting sessions/totalUsers into thresholded sub-buckets.
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
