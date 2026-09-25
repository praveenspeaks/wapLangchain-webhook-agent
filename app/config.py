"""Load application settings from environment variables and .env."""

from datetime import time
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    groq_api_key: str = Field(..., description="Groq API key")
    groq_model: str = Field("openai/gpt-oss-120b", description="Groq model ID")

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

    # Scheduled WhatsApp greetings; opt in after loading the occasions table.
    greetings_enabled: bool = False
    greetings_time: str = "09:00"
    greetings_admin_api_key: SecretStr = SecretStr("")
    shivay_api_url: str = ""
    shivay_api_key: SecretStr = SecretStr("")
    shivay_instance_name: str = ""

    @field_validator("greetings_time")
    @classmethod
    def validate_greeting_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("GREETINGS_TIME must be HH:MM in 24-hour format")
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_greeting_delivery(self) -> Self:
        if self.greetings_enabled:
            if not self.shivay_api_key.get_secret_value() or not self.shivay_instance_name.strip():
                raise ValueError(
                    "Greeting delivery requires SHIVAY_API_KEY and SHIVAY_INSTANCE_NAME"
                )
            url = urlsplit(self.shivay_api_url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError(
                    "SHIVAY_API_URL must be an HTTP(S) URL without embedded credentials"
                )
            if url.query or url.fragment:
                raise ValueError("SHIVAY_API_URL must not contain a query or fragment")
        return self

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        return v.upper()


settings = Settings()  # type: ignore[call-arg]
