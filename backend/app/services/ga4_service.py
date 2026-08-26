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

    top_pages = [_with_pct(r) for r in rows[:top_n]]
    bottom_pages = [_with_pct(r) for r in list(reversed(rows))[:bottom_n]]

    return {
        "total_pages": total_pages,
        "total_page_views": total_page_views,
        "truncated": len(response.get("rows", [])) >= max_rows,
        "top_pages": top_pages,
        "bottom_pages": bottom_pages,
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
