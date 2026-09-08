from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, v: str) -> str:
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v
    jwt_secret: str
    token_encryption_key: str
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    google_psi_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    claude_api_key: str = ""
    # Raw JSON content of a Google Cloud service account key (Sheets API +
    # Drive API enabled) — separate from google_client_id/secret above,
    # which is the per-client GA4/GSC OAuth flow. This one is app-owned, not
    # tied to any client, and creates+shares one Google Sheet per competitor
    # per report so the full (uncapped) keyword list can be linked from a
    # slide instead of rendered as a table capped at ~14 rows.
    google_service_account_json: str = ""
    # ID of a Google Drive folder owned by a real human, shared with that
    # service account as Editor — see google_sheets_service.py's
    # _create_spreadsheet_file docstring for why this is required (a bare
    # service account has no Drive storage of its own under a Workspace
    # org). The folder ID is the part of its URL after "folders/".
    google_drive_folder_id: str = ""
    # A separate, dedicated Web-application OAuth client for the Sheets
    # connection above — deliberately NOT reusing google_client_id/secret
    # (the per-client GA4/GSC OAuth client). That client's actual
    # configuration was uncertain/unverified at the time this was built, so
    # this avoids any risk of disrupting already-working GA4/GSC
    # connections while wiring up something new.
    google_sheets_oauth_client_id: str = ""
    google_sheets_oauth_client_secret: str = ""


settings = Settings()
