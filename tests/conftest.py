"""Pytest configuration — fixtures shared across all test modules."""

from __future__ import annotations

import os

# Ensure required env vars are set before any module import
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("POSTGRES_URL", "postgresql://agent:secret@localhost:5432/test")
os.environ.setdefault("TESTING_DB_URL", "postgresql://agent:secret@localhost:5432/test")
