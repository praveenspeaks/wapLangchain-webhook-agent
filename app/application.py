"""Assemble the API, logging, and resource lifecycle in one place."""

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.lifespan import lifespan
from app.logging_config import configure_logging
from app.state import AppState


def create_app() -> FastAPI:
    """Create an application with its own graph reference and metrics."""
    configure_logging(settings.log_level)
    application = FastAPI(title="AI Agent", version="1.0.0", lifespan=lifespan)
    application.state.runtime = AppState()
    application.include_router(router)
    return application
