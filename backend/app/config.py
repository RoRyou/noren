"""Settings loaded from environment (.env locally, env vars on Cloud Run)."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    google_api_key: str = Field("", validation_alias=AliasChoices("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    google_genai_use_vertexai: bool = False
    google_cloud_project: str = ""
    google_cloud_location: str = "asia-northeast1"
    google_application_credentials: str = ""

    live_model: str = "gemini-3.8-live"
    # gemini-3.8-live on Agent Platform is only served from us / eu (no Tokyo)
    live_location: str = "us-central1"
    live_voice: str = "Kore"

    # Cost guards: idle live sessions are closed, concurrent sessions are capped
    live_idle_timeout_s: int = 120
    live_max_concurrent_sessions: int = 20


settings = Settings()
