"""Load application settings from environment variables and .env."""

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    groq_api_key: str = Field(..., description="Groq API key")
    groq_model: str = Field("llama-3.1-8b-instant", description="Groq model ID")

    # Database
    postgres_url: str = Field(...)  # psycopg URL for LangGraph checkpointer
    testing_db_url: str = Field(
        "", description="psycopg URL for the business-data DB used by tools"
    )

    @field_validator("testing_db_url", mode="before")
    @classmethod
    def default_testing_db_url(cls, v: str, info: Any) -> str:  # noqa: ANN401
        """Fall back to postgres_url when TESTING_DB_URL is not set."""
        if v:
            return v
        return info.data.get("postgres_url", "")

    # Application
    log_level: str = Field("INFO")
    environment: str = Field("development")

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        return v.upper()


settings = Settings()  # type: ignore[call-arg]
