"""Assemble the API, logging, and resource lifecycle in one place."""

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.greetings.api import router as greetings_router
from app.lifespan import lifespan
from app.logging_config import configure_logging
from app.state import AppState
from app.whatsapp.api import router as whatsapp_router


def create_app() -> FastAPI:
    """Create an application with its own graph reference and metrics."""
    configure_logging(settings.log_level)
    application = FastAPI(title="AI Agent", version="1.0.0", lifespan=lifespan)
    application.state.runtime = AppState()
    application.include_router(router)
    application.include_router(greetings_router)
    application.include_router(whatsapp_router)
    return application
