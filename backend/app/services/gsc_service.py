from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


def list_sites(creds: Credentials) -> list[dict]:
    webmasters = build("searchconsole", "v1", credentials=creds)
    sites = webmasters.sites().list().execute()
    return [
        {"site_url": s["siteUrl"], "permission_level": s["permissionLevel"]}
        for s in sites.get("siteEntry", [])
    ]


def get_search_analytics(creds: Credentials, site_url: str, start_date: str, end_date: str, row_limit: int = 100) -> dict:
    webmasters = build("searchconsole", "v1", credentials=creds)
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query"],
        "rowLimit": row_limit,
    }
    response = webmasters.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = []
    for row in response.get("rows", []):
        rows.append(
            {
                "query": row["keys"][0],
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": row["ctr"],
                "position": row["position"],
            }
        )
    return {"rows": rows}


def get_page_clicks(creds: Credentials, site_url: str, start_date: str, end_date: str, row_limit: int = 1000) -> dict:
    """Same Search Analytics API as get_search_analytics, dimensioned by page
    instead of query — clicks/impressions per URL, for cross-referencing
    against per-page SEO issues (which URLs are actually losing search
    traffic, not just which have the most issues by count)."""
    webmasters = build("searchconsole", "v1", credentials=creds)
    body = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["page"],
        "rowLimit": row_limit,
    }
    response = webmasters.searchanalytics().query(siteUrl=site_url, body=body).execute()
    rows = []
    for row in response.get("rows", []):
        rows.append(
            {
                "page": row["keys"][0],
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": row["ctr"],
                "position": row["position"],
            }
        )
    return {"rows": rows}
