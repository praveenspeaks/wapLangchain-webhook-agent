# Customer Support AI API

A Python API that answers customer questions using Groq, LangGraph, and PostgreSQL.
It looks up orders, searches products, checks event ticket availability, creates
support tickets, and reports business hours. Conversations are saved by session ID.

Replies return directly over HTTP. A website, mobile app, or messaging gateway can
call this API. Scheduled birthday and anniversary greetings can be sent through
Shivay. Incoming WhatsApp webhook handling is not included.

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
    greetings/              # Occasion management, daily scheduler, Shivay sender
migrations/                 # Additive database migrations (safe for existing tables)
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

The graph uses the `GROQ_MODEL` setting (default: `openai/gpt-oss-120b`).
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
| GROQ_MODEL | Groq model ID; defaults to openai/gpt-oss-120b |
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

## Daily birthday and anniversary greetings

The scheduler runs inside the API process and sends personalized text via Shivay.
It does not depend on Groq for greetings. Keep at least one application instance
running; a hosting plan that sleeps cannot run the morning job while asleep.

1. Apply the additive migration to the business database once. Unlike schema.sql,
   it does not drop existing tables. Re-running it is safe for the same schema.

```sh
uv run python -m app.greetings.migrate
# Or, after building the image:
docker compose run --rm agent python -m app.greetings.migrate
```

2. Configure these values in the deployment environment (or .env locally):

```env
GREETINGS_ENABLED=true
GREETINGS_TIME=09:00
GREETINGS_ADMIN_API_KEY=your-long-random-administration-key
SHIVAY_API_URL=https://your-shivay-server
SHIVAY_API_KEY=your-shivay-api-key
SHIVAY_INSTANCE_NAME=your-connected-instance
```

GREETINGS_TIME is evaluated separately in each recipient's required timezone.
For example, Asia/Kolkata recipients receive greetings at 09:00 India time, while
America/New_York recipients receive them at 09:00 New York time, including DST.
Country is metadata; it is never used to guess a timezone. GREETINGS_TIMEZONE is
no longer used. Existing records without a timezone are skipped until updated. The sender uses the original project's
Shivay contract: POST /message/sendText/{instance}, apikey header, and a JSON body
containing number (international digits without +) and text. It expects key.id in
Shivay's success response. The instance must already be paired to WhatsApp.
TLS certificate verification is enabled. Restart/recreate the app after changing
configuration. The scheduler is disabled by default.

3. Add people through POST /greetings/occasions in /docs, or directly in PostgreSQL.
   Use the Authorize button in /docs with your GREETINGS_ADMIN_API_KEY. All
   /greetings endpoints require the X-Greetings-Key header and are disabled when
   no administration key is configured. Never put that key in a public frontend.

```json
{
  "name": "Alex",
  "occasion": "birthday",
  "month": 9,
  "day": 25,
  "year": null,
  "country": "GB",
  "timezone": "Europe/London",
  "phone_number": "+447700900123",
  "enabled": true
}
```

The year can be omitted, null, or the real year (for example, 1990). Each occasion
recurs by month/day. A person can have separate birthday and anniversary records.
Names personalize the greeting; ages and anniversary counts are not included.
Country uses a two-letter uppercase code. Phone numbers must include + and the
country calling code. The example phone number is illustrative; add your own
recipients. Only enable records for people you intend to receive these greetings.

```sql
INSERT INTO greeting_occasions
    (name, occasion, month, day, year, country, phone_number, timezone)
VALUES
    ('Alex', 'birthday', 9, 25, NULL, 'GB', '+447700900123', 'Europe/London');
```

| Endpoint | Purpose |
| --- | --- |
| POST /greetings/occasions | Add a person and occasion |
| GET /greetings/occasions | List records; supports limit and offset |
| PATCH /greetings/occasions/{id}/enabled | Pause/resume with {"enabled": false/true} |
| PATCH /greetings/occasions/{id}/timezone | Set an IANA timezone, e.g. {"timezone": "Asia/Kolkata"} |
| GET /greetings/deliveries | Inspect send history; supports limit and offset |

To change names, dates, or phone numbers, edit the record in PostgreSQL. Disable
records instead of deleting them so delivery history remains available.

The scheduler checks every minute and sends at or after the configured morning
time in each recipient's timezone. If the process restarts later in their local
day, it catches up on that day's occasions.
It does not send missed greetings from earlier dates. February 29 occasions run
only in leap years. Future original years do not match until that year arrives.

A database unique constraint and atomic claim prevent repeat attempts for the
same record/year across restarts, concurrent workers, and repeated clock hours.
A send interrupted by shutdown remains sending; a network timeout or ambiguous
provider result becomes unknown. Explicit provider rejections become failed.
There are no automatic retries for these states, because delivery may already
have happened. Check Shivay and reconcile such rows manually before retrying.
A sent status means Shivay accepted the message, not that the recipient read it.

## Current limitations

Authentication, customer-level order authorization, rate limiting, and conversation
history limits are not implemented. Session IDs are supplied by the caller.
Support ticket IDs use random five-digit numbers without collision retries, and
the database contact column is limited to 20 characters. Business hours use a
fixed UTC schedule without holiday-calendar checks.
