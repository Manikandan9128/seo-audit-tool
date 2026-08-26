"""Quick OAuth credential test — confirms Client ID/Secret work and lists
the GA4 properties + Search Console sites visible to the logged-in Google account.

Run: python test_oauth.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

CLIENT_SECRET_FILE = "client_secret.json"


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, scopes=SCOPES)
    creds = flow.run_local_server(port=8000)

    print("\n✅ OAuth login success. Access token obtained.\n")

    # List GA4 accounts/properties visible to this Google account
    print("--- Google Analytics (GA4) accounts/properties ---")
    try:
        analytics_admin = build("analyticsadmin", "v1beta", credentials=creds)
        accounts = analytics_admin.accounts().list().execute()
        for acc in accounts.get("accounts", []):
            print(f"Account: {acc['displayName']} ({acc['name']})")
            props = analytics_admin.properties().list(
                filter=f"parent:{acc['name']}"
            ).execute()
            for p in props.get("properties", []):
                print(f"   Property: {p['displayName']} ({p['name']})")
        if not accounts.get("accounts"):
            print("No GA4 accounts found for this Google login.")
    except Exception as e:
        print(f"GA4 fetch failed: {e}")

    # List Search Console sites visible to this Google account
    print("\n--- Search Console sites ---")
    try:
        webmasters = build("searchconsole", "v1", credentials=creds)
        sites = webmasters.sites().list().execute()
        for s in sites.get("siteEntry", []):
            print(f"Site: {s['siteUrl']} (permission: {s['permissionLevel']})")
        if not sites.get("siteEntry"):
            print("No Search Console sites found for this Google login.")
    except Exception as e:
        print(f"Search Console fetch failed: {e}")


if __name__ == "__main__":
    main()
