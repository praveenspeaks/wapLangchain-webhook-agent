"""Public HTTP request and response models."""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class InvokeRequest(BaseModel):
    """Request payload for the agent webhook."""

    sessionId: str = Field(
        ..., description="Unique ID for the user/session (used for conversation memory)"
    )
    message: str = Field(..., description="The user's input text")


class InvokeResponse(BaseModel):
    """Response returned by the agent webhook."""

    response: str
