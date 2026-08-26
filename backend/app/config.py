from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    token_encryption_key: str
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    google_psi_api_key: str = ""
    gemini_api_key: str = ""
    claude_api_key: str = ""


settings = Settings()
