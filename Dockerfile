# =============================================================================
# Multi-stage Dockerfile using uv for fast, reproducible builds
# =============================================================================

# ── Stage 1: dependency resolver ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv (fast Python package installer)
COPY --from=ghcr.io/astral-sh/uv:0.5.20 /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests only (better layer caching)
COPY pyproject.toml uv.lock* ./

# Install dependencies into an isolated virtual environment
RUN uv sync --frozen --no-dev --no-install-project


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install only the C-libs needed at runtime (psycopg uses libpq)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

# Copy the pre-built virtual environment from the builder stage
COPY --from=builder /app/.venv /app/.venv

# Copy application source
COPY agent.py db.py models.py main.py tools.py ./

# Make sure the venv's bin is in PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER appuser

# Expose the FastAPI port
EXPOSE 8000

# Health check — ensures Docker marks the container as unhealthy if /health fails
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/health',timeout=5); sys.exit(0)" || exit 1

# Start the FastAPI app via uvicorn
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log"]
