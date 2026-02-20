"""
models.py
---------
Pydantic models for:
  - Application settings (loaded from .env)
  - Shivay API webhook payloads
  - Internal request/response schemas
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

    # Shivay API
    shivay_api_url: str = Field("http://shivay-api:8080")
    shivay_api_key: str = Field(...)
    shivay_instance_name: str = Field("my_whatsapp_bot")
    shivay_instance_token: str = Field(...)

    # Database
    database_url: str = Field(...)  # asyncpg URL for SQLAlchemy
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

    # Webhook
    webhook_secret: str = Field(...)
    webhook_url: str = Field("https://your-domain.example.com/webhook/shivay")

    # Rate limiting
    rate_limit_max: int = Field(10, ge=1)
    rate_limit_window: int = Field(60, ge=1)

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
# Shivay API Webhook Models
# =============================================================================


class ShivayMessageKey(BaseModel):
    """WhatsApp message key (identifies a specific message)."""

    remote_jid: str = Field(alias="remoteJid")
    from_me: bool = Field(alias="fromMe")
    id: str

    model_config = {"populate_by_name": True}


class ShivayMessageContent(BaseModel):
    """Inner message content — only the fields we care about."""

    conversation: str | None = None  # plain text
    extended_text_message: dict[str, Any] | None = Field(None, alias="extendedTextMessage")
    image_message: dict[str, Any] | None = Field(None, alias="imageMessage")
    video_message: dict[str, Any] | None = Field(None, alias="videoMessage")
    audio_message: dict[str, Any] | None = Field(None, alias="audioMessage")
    buttons_response_message: dict[str, Any] | None = Field(None, alias="buttonsResponseMessage")
    list_response_message: dict[str, Any] | None = Field(None, alias="listResponseMessage")

    model_config = {"populate_by_name": True}

    def extract_text(self) -> str | None:
        """Return the plain-text body regardless of message type."""
        if self.conversation:
            return self.conversation
        if self.extended_text_message:
            return self.extended_text_message.get("text")
        if self.buttons_response_message:
            return self.buttons_response_message.get("selectedDisplayText")
        if self.list_response_message:
            return self.list_response_message.get("title")
        return None


class ShivayMessageData(BaseModel):
    """The `data` object inside a MESSAGES_UPSERT event."""

    key: ShivayMessageKey
    message: ShivayMessageContent | None = None
    message_type: str = Field("", alias="messageType")
    # Unix timestamp (seconds)
    message_timestamp: int = Field(0, alias="messageTimestamp")
    push_name: str | None = Field(None, alias="pushName")
    instance: str | None = None

    model_config = {"populate_by_name": True}


class ShivayWebhookEvent(BaseModel):
    """Top-level webhook payload sent by Shivay API."""

    event: str  # e.g. MESSAGES_UPSERT, CONNECTION_UPDATE, QRCODE_UPDATED
    instance: str
    data: dict[str, Any] | list[Any]  # varies by event
    date_time: str | None = Field(None, alias="date_time")
    sender: str | None = None
    server_url: str | None = Field(None, alias="server_url")
    apikey: str | None = None

    model_config = {"populate_by_name": True}


# =============================================================================
# Internal / API Response Models
# =============================================================================


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str = "1.0.0"


class ConversationMessage(BaseModel):
    role: str  # "human" | "ai"
    content: str
    timestamp: str | None = None


class ConversationHistoryResponse(BaseModel):
    phone: str
    messages: list[ConversationMessage]
    total: int


class InstanceStatusResponse(BaseModel):
    instance_name: str
    state: str  # open | close | connecting
    qrcode: str | None = None


class WebhookSetupRequest(BaseModel):
    webhook_url: str
    events: list[str] = Field(
        default=[
            "MESSAGES_UPSERT",
            "CONNECTION_UPDATE",
            "QRCODE_UPDATED",
        ]
    )


class SendMessageRequest(BaseModel):
    """Used by admin endpoints for manual message sending."""

    to: str = Field(..., description="Phone number in international format, e.g. 15551234567")
    message: str


class MetricsResponse(BaseModel):
    messages_processed: int
    messages_failed: int
    active_conversations: int
    uptime_seconds: float


# =============================================================================
# Generic Simple Webhook Models
# =============================================================================

class GenericAgentRequest(BaseModel):
    """Simple request for external systems triggering the agent."""
    sender_id: str = Field(..., description="Unique ID for the user/session (used for conversation memory)")
    message: str = Field(..., description="The user's input text")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional extra data")


class GenericAgentResponse(BaseModel):
    """Simple response returned directly in the webhook reply."""
    reply: str
    status: str = "success"
