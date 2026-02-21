"""
models.py
---------
Pydantic models for:
  - Application settings (loaded from .env)
  - API request/response schemas
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# =============================================================================
# Application Settings
# =============================================================================


class Settings(BaseSettings):
    """Centralised configuration loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    groq_api_key: str = Field(..., description="Groq API key")

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


# ---------------------------------------------------------------------------
# Singleton – import `settings` everywhere rather than re-instantiating
# ---------------------------------------------------------------------------
settings = Settings()  # type: ignore[call-arg]


# =============================================================================
# API Models
# =============================================================================


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str = "1.0.0"


class GenericAgentRequest(BaseModel):
    """Request payload for the agent webhook."""
    sender_id: str = Field(..., description="Unique ID for the user/session (used for conversation memory)")
    message: str = Field(..., description="The user's input text")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional extra data")


class GenericAgentResponse(BaseModel):
    """Response returned by the agent webhook."""
    reply: str
    status: str = "success"
