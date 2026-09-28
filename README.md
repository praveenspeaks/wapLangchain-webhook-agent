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

## WhatsApp archive, nightly summary, and owner data entry

### Connection Hub integration

For temporary payload diagnostics, set `WEBHOOK_LOG_PAYLOADS=true` and
`LOG_LEVEL=INFO` in the deployment and restart. `Webhook payload diagnostic` logs
include JSON bodies (including rejected requests), with credential fields, nested
headers/query objects and URLs redacted. Message text and phone numbers remain visible.
The fields `payload_apikey_present`, `payload_apikey_matches`,
`webhook_secret_header_present` and `webhook_secret_header_matches` diagnose
authentication without revealing secret values. The request ID links these entries
to outcome logs. Invalid JSON and bodies over 64 KiB are omitted from payload logs.
Return `WEBHOOK_LOG_PAYLOADS=false` after debugging. Logging is disabled by default.

Webhook diagnostics: `/version` reports `webhook_logging: v1` when arrival logging
is deployed. At `LOG_LEVEL=INFO`, every webhook request logs `Webhook request arrived`
and `Webhook request finished`, including status, outcome and a generated request ID.
These logs omit bodies, credentials and phone numbers. `401` means webhook authentication
failed, `403` can indicate an unexpected instance, and `422` means invalid input.
An ignored outgoing/non-text event returns an empty reply. A `reply_returned` outcome
means the hub received reply text, not that WhatsApp delivered it. POSTs to unknown
paths are logged as `unrecognized`. The response includes `X-Agent-Request-ID`.

Set the agent webhook URL in Connection Hub to `https://YOUR_AGENT_HOST/webhook`
(`/invoke` also works). Both accept the direct Connection Hub body, an n8n
`{"body": {...}}` wrapper, or a one-item array containing that wrapper. Send one
message per request. The legacy `{"sessionId":"...","message":"..."}` format
continues to work for Quick Test.

The agent reads `message` and `sessionId`, falling back to `data.message` and an
instance/chat-derived session when needed. It returns `{"response":"reply text"}`.
Configure the hub to forward this `response` to the originating WhatsApp chat.
Skip sending when `response` is empty: outgoing `fromMe` events, unsupported event
types and messages without text/captions do not trigger automatic chat replies.
The hub handles WhatsApp delivery; the agent does not call a URL from the payload
or use its `apikey` for outbound requests. With archiving enabled, the body `apikey`
can authenticate the event against the configured `SHIVAY_API_KEY`.
Provider credentials and transport metadata are not sent to
the model. JSON must contain actual URLs/JIDs, not Markdown links copied from chat.

For simple hub request/reply use, no `SHIVAY_*` values are required; keep
`WHATSAPP_ENABLED`, `WHATSAPP_DATA_ENTRY_ENABLED`, `WHATSAPP_SUMMARIES_ENABLED`,
and `GREETINGS_ENABLED` false. These switches control the archive/background
features, not the `/webhook` chat endpoint.

For archiving and scheduled summaries, enable/configure the features below and
authenticate with either a body `apikey` matching `SHIVAY_API_KEY` or the actual
HTTP header `X-Webhook-Secret` matching `SHIVAY_WEBHOOK_SECRET`. A supplied incorrect
header is rejected even if the body key matches. The JSON wrapper's
`headers` object does not authenticate a request. The event instance must match
`SHIVAY_INSTANCE_NAME`. Hub events are then archived before processing; duplicate
events return an empty response. Outgoing owner `/add` commands are archived for
the existing worker, which sends its replies privately, so the hub must not send
an additional reply. Do not forward the same event to both webhook endpoints.

Keep `SHIVAY_API_URL`, `SHIVAY_API_KEY`, and `SHIVAY_INSTANCE_NAME` for proactive
greetings, daily summaries, and private command feedback: these sends happen
outside the hub's request/response cycle. `GREETINGS_ADMIN_API_KEY` is only for
the greetings management API. The direct `/webhook/shivay` endpoint remains
available as a capture-only adapter; it does not return a chat answer.

The main chat endpoint (`/invoke`, including deployment Quick Test) also supports
natural-language birthday and anniversary entry. For example, "My friend Anjani
Kumar Singh has birthday on 16th October, can you add" collects the name and date,
then asks for the recipient's international phone number, country and timezone.
The original year is optional. Send follow-up answers with the **same sessionId**
so conversation memory retains the earlier details. Once all required fields are
valid, the agent saves to `greeting_occasions`; missing details do not create a
support ticket. It confirms saving only after the database tool succeeds and
reports if automatic greeting delivery is disabled. Restart/redeploy to load the
new tool and prompt. The owner-only WhatsApp `/add` → `/save` workflow below is
unchanged.

The Shivay webhook `POST /webhook/shivay` stores group and direct messages in
`whatsapp_messages`. It records text/captions, sender, chat, time and `fromMe`;
attachments are not downloaded or transcribed. Duplicate webhook deliveries are
ignored. This endpoint captures messages without automatically replying to other people.

To enable:

1. Run `python -m app.greetings.migrate` against the business database. This applies
   all additive migrations, including `003_whatsapp_capture_and_entries.sql`.
   Existing products/orders/event/support tables must already exist. Do not run
   the destructive sample `schema.sql` against a database containing real data.
2. Fill these settings in `.env` locally and in your deployment environment:

   ```dotenv
   WHATSAPP_ENABLED=true
   WHATSAPP_OWNER_NUMBER=+YOUR_COUNTRY_CODE_AND_NUMBER
   SHIVAY_WEBHOOK_SECRET=YOUR_RANDOM_SECRET
   WHATSAPP_DATA_ENTRY_ENABLED=true
   WHATSAPP_SUMMARIES_ENABLED=true
   WHATSAPP_SUMMARY_TIME=21:00
   WHATSAPP_SUMMARY_TIMEZONE=Europe/London
   WHATSAPP_SUMMARY_MAX_MESSAGES=0
   ```

   Also set `SHIVAY_API_URL`, `SHIVAY_API_KEY`, `SHIVAY_INSTANCE_NAME` and
   `GROQ_API_KEY`. Use your connected WhatsApp account's number for the owner.
   Features default to disabled until configured. Restart after configuration changes.
3. Configure Shivay to forward `messages.upsert` / `MESSAGES_UPSERT` events to
   `https://YOUR_HOST/webhook/shivay`, including incoming group/direct messages and
   outgoing `fromMe` messages. The payload's `apikey` must match the configured
   `SHIVAY_API_KEY`, or configure the custom header `X-Webhook-Secret` to match
   `SHIVAY_WEBHOOK_SECRET`. The latter can be empty when using body-key authentication.
   The instance name is checked in either case. Credentials are excluded from
   archived messages and model input. Verify its payload matches the example below.
4. Keep the application running continuously for timers and command processing.

Accepted event shape (the `data` field can also be a list):

```json
{
  "event": "messages.upsert",
  "instance": "YOUR_INSTANCE",
  "data": {
    "key": {"remoteJid": "123@g.us", "fromMe": false,
            "id": "UNIQUE_MESSAGE_ID", "participant": "447700900123@s.whatsapp.net"},
    "messageTimestamp": 1790351821,
    "pushName": "Alex",
    "message": {"conversation": "Meet tomorrow at 10 AM"}
  }
}
```

At 9 PM London time (including DST), one daily digest is prepared privately for
`WHATSAPP_OWNER_NUMBER`. It covers messages received by this app since the previous
9 PM cutoff, excluding your messages and the agent's outgoing messages. Processing
starts within a minute of the cutoff; generation and delivery take additional time.
Long digests arrive in multiple WhatsApp parts. There is no past-message import.
A restart after the cutoff catches up that same day, not previous missed days.

`WHATSAPP_SUMMARY_MAX_MESSAGES=0` includes all captured incoming messages; a positive
value caps the number and the digest explicitly labels itself partial. Summaries
use batches of 15 messages, paced 30 seconds apart, via `GROQ_MODEL`. Each message's
first 600 characters are summarized; the digest discloses when text was shortened.
Groq failures leave the summary uncommitted for a later attempt. Availability and
rate limits still depend on your Groq account. Message content is sent to Groq for
summarization; stored message bodies are not shortened.

### Add records through your own WhatsApp messages

Send commands from the connected owner account (the provider must report
`fromMe=true`), preferably in your self-chat. Ordinary messages do not create
records. The agent sends feedback privately to the configured owner number.
Only one active draft is kept per instance, persisted across restarts.

```text
/add restaurant {"name":"The Olive Tree"}
```

The agent asks for `location`. Supply it, inspect the returned draft, then save:

```text
/set {"location":"Richmond, London","cuisine":"Mediterranean"}
/save
```

Natural language also works: `/add service Sam, plumber, +447700900123, Richmond`.
`/set` accepts additional details or JSON corrections. `/draft` shows the draft,
`/cancel` discards it, and `/help` lists commands. JSON entry bypasses the LLM.
Missing or invalid fields prevent saving. Corrections must be reviewed again.
A successful `/save` returns the created record ID. Database constraint failures
retain the draft, allowing correction. Repeated delivery of the same command
message cannot create a second record; a new `/add` after saving is a new entry.

| Type | Required fields |
| --- | --- |
| restaurant | name, location |
| service | name, category (e.g. plumber), phone_number, location |
| place | name, location, category |
| event | event_name, event_date (YYYY-MM-DD), venue, total_tickets, price, category |
| support_ticket | id (TKT-...), customer_phone, issue |
| product | name, price, stock, category |
| order | id (ORD-...), customer_phone |
| order_item | order_id, product_id, quantity, unit_price |
| occasion | name, occasion (birthday/anniversary), month, day, country, timezone, phone_number |

Phone numbers include `+` and country code. Prices cannot be negative; quantities
must be positive; stock cannot be negative; event sales cannot exceed capacity.
Orders/products must exist before adding order items. Adding an item recalculates
the order total from its items; it does not reserve stock or take payment. Events
use the existing ticketed-event table. Occasion year is optional and timezone uses
an IANA name. Optional/default fields are shown in the review. This workflow creates
records; it does not edit already saved records or expose arbitrary SQL.

Modules under `app/whatsapp/`: `messages.py` normalizes provider events, `api.py`
authenticates and archives, `entries.py` defines field validation, `llm.py` extracts
fields and summarizes, `store.py` handles drafts and inserts, and `worker.py`
processes commands, schedules summaries, and sends the durable outbox.

`whatsapp_entry_drafts`, `whatsapp_summary_runs`, and `whatsapp_outbox` track work.
Outbox `sent` means provider acceptance. Failed, unknown or interrupted (`sending`)
deliveries are not automatically retried, to avoid duplicate sends; inspect Shivay
before manually reconciling them. Archive records remain until you remove them.

Run offline tests with `python -m pytest -q`. The additional PostgreSQL workflow test
runs only when `WHATSAPP_TEST_DATABASE_URL` is set; it creates and removes a uniquely
named test schema and mocks all model calls and outbound sends.
