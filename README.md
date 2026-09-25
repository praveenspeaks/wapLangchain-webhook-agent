# Customer Support AI API

A Python API that answers customer questions using Groq, LangGraph, and PostgreSQL.
It looks up orders, searches products, checks event ticket availability, creates
support tickets, and reports business hours. Conversations are saved by session ID.

Replies return directly over HTTP. A website, mobile app, or messaging gateway can
call this API. WhatsApp/Shivay integration is not included.

## Start reading here

1. `main.py` creates the application and is the Uvicorn entry point.
2. `app/application.py` assembles routes, logging, state, and the resource lifecycle.
3. `app/api/routes.py` receives messages and calls the agent service.
4. `app/agent/service.py` runs a conversation turn using the session ID.
5. `app/agent/graph.py` lets the model call tools until it produces an answer.
6. `app/tools/` contains business operations, grouped by domain.

## Project structure

```text
main.py                     # Entry point: main:app
app/
    application.py          # FastAPI application factory
    config.py               # Environment settings
    logging_config.py       # JSON console logging
    lifespan.py             # Startup and shutdown of resources
    state.py                # Graph reference and per-process metrics
    database.py             # Shared business-data connection pool
    api/
        routes.py           # /invoke, /health, /metrics
        schemas.py          # HTTP request and response models
    agent/
        graph.py            # Model setup, graph nodes, routing
        prompts.py          # Assistant instructions
        service.py          # Session handling and answer extraction
    tools/
        __init__.py         # Registry of tools exposed to the model
        orders.py           # Order details and status filtering
        products.py         # Product search and details
        events.py           # Event availability
        support.py          # Support ticket creation
        business.py         # Business hours (no database)
tests/                      # Offline tests with mocked external services
schema.sql                  # Demo schema and sample data
Dockerfile                  # Container image
docker-compose.yml          # API and optional ngrok tunnel
```

The root `agent.py`, `db.py`, `models.py`, and `tools.py` files are compatibility
imports for existing Python callers. New code should import from `app`.
The legacy `agent.process_message(phone=...)` call still works; the new service
uses the clearer name `session_id`.

## How a message travels

```text
POST /invoke {sessionId, message}
    -> API validates the request
    -> Agent service uses sessionId as the conversation thread ID
    -> LangGraph loads persisted conversation state
    -> Groq receives the conversation and system instructions
    -> Model optionally requests a tool
    -> Tool queries PostgreSQL (or creates a support ticket)
    -> Tool result returns to the model
    -> Final answer returns as {"response": "..."}
```

The graph uses the `GROQ_MODEL` setting (default: `llama-3.1-8b-instant`).
Choose a tool-calling model available to your Groq account in `.env`, then restart
the application. For Docker, recreate the container to reload its environment.
Tool execution can repeat within a
turn, with a graph recursion limit of 10. Requests await the answer; there is no
background delivery or streaming endpoint.

Conversation checkpoints and business queries use separate connection pools.
`POSTGRES_URL` stores conversation checkpoints. `TESTING_DB_URL` selects the
business database, despite its historical name; when omitted, it falls back to
`POSTGRES_URL`. Tools share one business pool per process. Run one application
lifecycle per process.

## Run locally

Requires Python 3.12+, uv, PostgreSQL, and a Groq API key.

```sh
uv sync --extra dev
```

Copy `.env.example` to `.env` if you do not already have one, then configure:

| Variable | Purpose |
| --- | --- |
| GROQ_API_KEY | Required Groq API key |
| GROQ_MODEL | Groq model ID; defaults to llama-3.1-8b-instant |
| POSTGRES_URL | Required PostgreSQL URL for conversation memory |
| TESTING_DB_URL | Optional separate business database URL |
| LOG_LEVEL | Logging level; defaults to INFO |
| ENVIRONMENT | Environment label; defaults to development |
| NGROK_AUTHTOKEN | Only needed for the optional development tunnel |

For a new disposable demo database, run `schema.sql` against the business database.
**This script drops and recreates business tables and inserts sample data.**
It is not a production migration. Startup creates the LangGraph checkpoint tables,
but does not create business tables.

```sh
psql "YOUR_BUSINESS_DATABASE_URL" -f schema.sql
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open http://localhost:8000/docs to explore and try the API.

## API

`POST /invoke` accepts:

```json
{
  "sessionId": "user123",
  "message": "What is the status of ORD-10001?"
}
```

It returns a `response` string. Reuse the same session ID for follow-up questions.

- `GET /health`: process liveness; does not check database or model availability.
- `GET /metrics`: processed/failed counters and uptime, reset on restart.
  Handled agent errors return fallback text and currently count as processed.

## Docker

Configure `.env` with database URLs reachable from inside the container.

```sh
docker compose up --build -d
```

PostgreSQL is external; Compose does not create it. To also run the development
tunnel, use `docker compose --profile dev up --build -d`.

## Make changes

| What you want to change | Where to work |
| --- | --- |
| Add/change an HTTP endpoint | app/api/routes.py and app/api/schemas.py |
| Change assistant tone or tool-selection rules | app/agent/prompts.py |
| Change model parameters or graph routing | app/agent/graph.py |
| Change conversation handling | app/agent/service.py |
| Change a query or business rule | Relevant module in app/tools/ |
| Add an environment setting | app/config.py and .env.example |
| Change startup or cleanup | app/lifespan.py |
| Change connection pool behavior | app/database.py |

To add a tool, define an async function with the LangChain `@tool` decorator
and a clear docstring in the appropriate domain module. Register it in
`app/tools/__init__.py`, describe its intended use in the system prompt, and add
a test. Keep HTTP handling in the API layer and SQL/business operations in tools.

## Verify changes

Dependencies are resolved in `uv.lock`; Docker uses that lockfile too.
After changing dependencies, sync the environment and regenerate the pip export
from the same lockfile so both installation methods use matching versions:

```sh
uv sync --locked --extra dev
uv export --frozen --no-dev --no-emit-project --no-hashes --output-file requirements.txt
```

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests mock database and model calls. They cover tool behavior, HTTP responses,
conversation memory, tool routing, and resource cleanup without external access.
They do not prove live Groq, PostgreSQL, or Docker connectivity.

## Current limitations

Authentication, customer-level order authorization, rate limiting, and conversation
history limits are not implemented. Session IDs are supplied by the caller.
Support ticket IDs use random five-digit numbers without collision retries, and
the database contact column is limited to 20 characters. Business hours use a
fixed UTC schedule without holiday-calendar checks.
