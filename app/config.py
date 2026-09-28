"""Load application settings from environment variables and .env."""

import re
from datetime import time
from typing import Any, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

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
    webhook_log_payloads: bool = False

    # Scheduled WhatsApp greetings; opt in after loading the occasions table.
    greetings_enabled: bool = False
    greetings_time: str = "09:00"
    greetings_admin_api_key: SecretStr = SecretStr("")
    shivay_api_url: str = ""
    shivay_api_key: SecretStr = SecretStr("")
    shivay_instance_name: str = ""
    shivay_webhook_secret: SecretStr = SecretStr("")
    whatsapp_require_webhook_secret: bool = False
    whatsapp_enabled: bool = False
    whatsapp_owner_number: str = ""
    whatsapp_data_entry_enabled: bool = False
    whatsapp_summaries_enabled: bool = False
    whatsapp_summary_time: str = "21:00"
    whatsapp_summary_timezone: str = "Europe/London"
    whatsapp_summary_max_messages: int = Field(default=0, ge=0)

    @field_validator("whatsapp_summary_timezone")
    @classmethod
    def validate_summary_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Use an IANA timezone such as Europe/London") from exc
        return value

    @field_validator("greetings_time", "whatsapp_summary_time")
    @classmethod
    def validate_greeting_time(cls, value: str) -> str:
        if len(value) != 5 or value[2] != ":":
            raise ValueError("Scheduled time must be HH:MM in 24-hour format")
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_greeting_delivery(self) -> Self:
        outbound = self.whatsapp_data_entry_enabled or self.whatsapp_summaries_enabled
        if outbound and not self.whatsapp_enabled:
            raise ValueError("Enable WHATSAPP_ENABLED for data entry or summaries")
        if self.whatsapp_enabled and not self.shivay_instance_name.strip():
            raise ValueError("WhatsApp capture requires SHIVAY_INSTANCE_NAME")
        if (
            self.whatsapp_require_webhook_secret
            and not self.shivay_webhook_secret.get_secret_value()
        ):
            raise ValueError("SHIVAY_WEBHOOK_SECRET is required when webhook protection is enabled")
        if outbound and not re.fullmatch(r"\+[1-9][0-9]{7,14}", self.whatsapp_owner_number):
            raise ValueError("WHATSAPP_OWNER_NUMBER must include + and the country code")
        if self.greetings_enabled or outbound:
            if not self.shivay_api_key.get_secret_value() or not self.shivay_instance_name.strip():
                raise ValueError(
                    "WhatsApp delivery requires SHIVAY_API_KEY and SHIVAY_INSTANCE_NAME"
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
