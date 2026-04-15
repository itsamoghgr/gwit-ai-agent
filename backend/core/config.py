"""
core/config.py — Single source of truth for all environment variables.
Reads from app_gw-it/.env automatically.
"""
from functools import lru_cache
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # PostgreSQL
    POSTGRES_HOST:     str = "localhost"
    POSTGRES_PORT:     int = 5432
    POSTGRES_DB:       str = "gw_it_ticket_db"
    POSTGRES_USER:     str = "postgres"
    POSTGRES_PASSWORD: str = ""

    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT:    str = ""
    AZURE_OPENAI_API_KEY:     str = ""
    AZURE_OPENAI_API_VERSION: str = "2025-01-01-preview"
    AZURE_OPENAI_EMBED_MODEL: str = "text-embedding-ada-002"
    AZURE_OPENAI_DEPLOYMENT:  str = "gpt-4.1"

    # Hidden run IDs (comma-separated)
    HIDDEN_RUN_IDS: str = (
        "1f05b932-4059-4985-9d93-8f858ae2b4da,"
        "1828d71d-0823-434d-b785-eccb34a3a00f,"
        "d16e869b-c87c-4f64-8505-fa0c390bdc33"
    )

    model_config = SettingsConfigDict(
        # Look for .env in app_gw-it/ (two levels up from this file)
        env_file=str(Path(__file__).resolve().parents[2].parent / "app_gw-it" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def hidden_run_list(self) -> List[str]:
        return [x.strip() for x in self.HIDDEN_RUN_IDS.split(",") if x.strip()]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
