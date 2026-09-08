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
    # A separate, dedicated Web-application OAuth client for the Sheets
    # connection above — deliberately NOT reusing google_client_id/secret
    # (the per-client GA4/GSC OAuth client). That client's actual
    # configuration was uncertain/unverified at the time this was built, so
    # this avoids any risk of disrupting already-working GA4/GSC
    # connections while wiring up something new.
    google_sheets_oauth_client_id: str = ""
    google_sheets_oauth_client_secret: str = ""


settings = Settings()
