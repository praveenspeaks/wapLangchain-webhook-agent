"""Compatibility imports. Settings and HTTP schemas now have separate modules."""

from app.api.schemas import HealthResponse as HealthResponse
from app.api.schemas import InvokeRequest as InvokeRequest
from app.api.schemas import InvokeResponse as InvokeResponse
from app.config import Settings as Settings
from app.config import settings as settings
