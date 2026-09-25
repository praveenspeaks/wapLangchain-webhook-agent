"""Compatibility imports. The connection pool is managed in app.database."""

from app.database import close_pool as close_pool
from app.database import get_pool as get_pool
from app.database import init_pool as init_pool
