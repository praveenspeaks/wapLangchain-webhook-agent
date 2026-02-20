"""Pytest configuration — fixtures shared across all test modules."""

from __future__ import annotations

import os

# Ensure required env vars are set before any module import
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("SHIVAY_API_KEY", "test-shivay-key")
os.environ.setdefault("SHIVAY_INSTANCE_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://agent:secret@localhost:5432/test")
os.environ.setdefault("POSTGRES_URL", "postgresql://agent:secret@localhost:5432/test")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("TESTING_DB_URL", "postgresql://agent:secret@localhost:5432/test")
