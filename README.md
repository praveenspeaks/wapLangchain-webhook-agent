# WhatsApp AI Agent

Production-ready WhatsApp chatbot built with **LangGraph**, **Shivay API**, and **FastAPI**.

```
WhatsApp ──► Shivay API ──► FastAPI Webhook ──► LangGraph Agent ──► Groq LLM
     ▲                                        │               │
     │                                    │      PostgreSQL (history + tools)
     └────────────────────────────────────┘
                   reply via API
```

## Features

| Feature | Implementation |
|---|---|
| WhatsApp gateway | Shivay API v2 |
| AI agent | LangGraph `StateGraph` + Groq `llama-3.3-70b-versatile` |
| Conversation persistence | `AsyncPostgresSaver` (per phone-number thread) |
| 7 DB-backed tools | Orders, products, events, support tickets (real PostgreSQL) |
| Rate limiting | In-memory sliding window (10 msg / 60 s per user) |
| Async processing | FastAPI `BackgroundTasks` — webhook returns in < 15 ms |
| LID support | Handles WhatsApp Linked ID (`@lid`) addresses |
| Containerisation | Docker Compose (agent + ngrok for dev) |

---

## Project Structure

```
.
├── main.py              # FastAPI app, webhook endpoint, lifespan
├── agent.py               # LangGraph graph definition + process_message()
├── tools.py               # 7 LangChain tools backed by PostgreSQL
├── db.py                  # Async connection pool for tool queries
├── shivay_client.py       # HTTP client for Shivay API (send/receive)
├── models.py              # Pydantic models + app settings
├── schema.sql             # Database schema + sample data
├── Dockerfile             # Production image
├── docker-compose.yml     # Agent + ngrok (dev tunnel)
├── pyproject.toml         # Dependencies (uv/hatch)
├── .env.example           # Config template
├── tests/
│   ├── conftest.py        # Shared fixtures + env defaults
│   ├── test_webhook.py    # Webhook + model tests
│   └── test_tools.py      # Tool unit tests (mocked DB)
└── .vscode/
    ├── settings.json
    ├── extensions.json
    ├── tasks.json
    ├── launch.json
    └── rest-client.http   # Sample API requests
```

---

## Message Flow (end-to-end)

### Step-by-step: Receiving a message to sending a reply

```
WhatsApp User
     │
     ▼
Shivay API
     │  POST /webhook/shivay  (event: messages.upsert)
     ▼
┌─ main.py ─────────────────────────────────────────────────┐
│                                                              │
│  webhook_shivay()             ← FastAPI route handler        │
│    ├─ Normalize event name    (messages.upsert → MESSAGES_UPSERT)
│    ├─ Parse ShivayMessageData (Pydantic validation)          │
│    ├─ Skip if fromMe=true                                    │
│    ├─ Extract text via msg.message.extract_text()            │
│    ├─ Resolve phone (JID or LID)                             │
│    ├─ _check_rate_limit(phone) → reject if exceeded          │
│    └─ background_tasks.add_task(_handle_message, ...)        │
│                                                              │
│  _handle_message()            ← runs in background           │
│    ├─ shivay_client.send_typing(phone)                        │
│    ├─ process_message(graph, phone, text, push_name)  ──────┼──►
│    └─ shivay_client.send_text(phone, response)               │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ agent.py ──────────────────────────────────────────────────┐
│                                                              │
│  process_message()                                           │
│    ├─ Prepend "[User: pushName]" to text                     │
│    ├─ config = { thread_id: phone }                          │
│    └─ graph.ainvoke({ messages: [HumanMessage] }, config)    │
│                                                              │
│  LangGraph StateGraph:                                       │
│    START → agent_node → (tool calls?) → tool_node → agent    │
│                └──────── no ──────────► END                  │
│                                                              │
│  _agent_node()                                               │
│    ├─ Prepend SystemMessage (SYSTEM_PROMPT)                  │
│    └─ llm_with_tools.ainvoke(messages)  → AIMessage          │
│                                                              │
│  _should_continue()                                          │
│    └─ If AIMessage has tool_calls → "tools", else → END      │
│                                                              │
│  ToolNode(TOOLS)           ← auto-executes tool calls        │
│    └─ Calls matching tool from tools.py                      │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ tools.py ──────────────────────────────────────────────────┐
│                                                              │
│  Each tool calls get_pool() from db.py, runs SQL,            │
│  returns JSON string for the LLM to humanise.                │
│                                                              │
│  get_order_status(order_id)      → orders + order_items      │
│  get_orders_by_status(status)    → orders filtered           │
│  search_product(query)           → products ILIKE search     │
│  get_product_info(product_id)    → single product details    │
│  get_event_tickets(event_name)   → event ticket availability │
│  create_support_ticket(issue, contact) → INSERT ticket       │
│  get_business_hours()            → schedule (no DB)          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ db.py ─────────────────────────────────────────────────────┐
│  AsyncConnectionPool (psycopg3)                              │
│  init_pool() / close_pool() / get_pool()                     │
│  Connected to external PostgreSQL (TESTING_DB_URL)           │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ shivay_client.py ──────────────────────────────────────────┐
│                                                              │
│  send_text(to, message)                                      │
│    POST /message/sendText/{instance}                         │
│    payload: { "number": to, "text": message }                │
│                                                              │
│  send_typing(to)    ← best-effort, silently skipped if 404  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                         │
                         ▼
              Shivay API → WhatsApp → User sees reply
```

### Function call chain (compact)

```
main.webhook_shivay()
  → main._handle_message()           (BackgroundTask)
    → shivay_client.send_typing()       (best-effort)
    → agent.process_message()
      → graph.ainvoke()
        → agent._agent_node()          (LLM call with tools)
        → agent._should_continue()     (route to tools or END)
        → ToolNode → tools.*()         (DB queries via db.get_pool())
        → agent._agent_node()          (final response)
    → shivay_client.send_text()         (reply to WhatsApp)
```

---

## Startup / Lifespan

When the main starts (`main.py:lifespan()`):

1. **PostgreSQL pool** — `AsyncConnectionPool` for LangGraph checkpointer (`POSTGRES_URL`)
2. **Checkpointer** — `AsyncPostgresSaver.setup()` creates checkpoint tables
3. **Agent graph** — `build_graph(checkpointer)` compiles the `StateGraph`
4. **Tool DB pool** — `db.init_pool()` opens a separate pool for business data (`TESTING_DB_URL`)

On shutdown: closes tool pool, checkpointer pool, and HTTP client.

---

## Agent Tools

| Tool | Description | DB Table |
|---|---|---|
| `get_order_status(order_id)` | Order details + line items | `orders`, `order_items` |
| `get_orders_by_status(status)` | List orders by status | `orders` |
| `search_product(query)` | ILIKE search on name/category | `products` |
| `get_product_info(product_id)` | Full product details by ID | `products` |
| `get_event_tickets(event_name)` | Tickets sold + remaining | `event_tickets` |
| `create_support_ticket(issue, contact)` | Insert support ticket | `support_tickets` |
| `get_business_hours()` | Operating hours + open/closed | (none) |

### Adding a new tool

1. Add a `@tool` async function in `tools.py`
2. Append it to the `TOOLS` list at the bottom
3. Update `SYSTEM_PROMPT` in `agent.py` to describe the new tool
4. No other changes needed — ToolNode auto-discovers tools from the list

---

## Quick Start

### Prerequisites
- Docker Desktop >= 24.0
- Python 3.12+ (for local dev without Docker)
- `uv` package manager
- Groq API key — [console.groq.com](https://console.groq.com)
- Shivay API instance
- External PostgreSQL database with `schema.sql` applied

### 1. Clone & configure

```bash
git clone <your-repo>
cd whatsapp-agent
cp .env.example .env
# Edit .env with your keys
```

### 2. Set up database

```bash
psql -h <host> -U <user> -d <dbname> -f schema.sql
```

### 3. Run with Docker

```bash
# Production (agent only)
docker compose up -d

# Development (agent + ngrok tunnel)
docker compose --profile dev up -d
```

### 4. Get ngrok URL (dev only)

```bash
curl -s http://localhost:4040/api/tunnels | python -m json.tool
```

Copy the `public_url` and set it as the webhook URL in your Shivay API dashboard:
```
https://<ngrok-id>.ngrok-free.dev/webhook/shivay
```

### 5. Configure Shivay API webhook

In your Shivay API dashboard, set:
- **Webhook URL**: `https://<your-domain>/webhook/shivay`
- **Events**: `messages.upsert`

### 6. Test it

Send a WhatsApp message to your connected number and watch logs:
```bash
docker compose logs -f agent
```

---

## Local Development (without Docker)

```bash
uv sync                    # Install dependencies
cp .env.example .env       # Configure
uv run python main.py    # Start with hot reload on :8000
```

Or use VS Code task: **Run: FastAPI (hot-reload)**

---

## Running Tests

```bash
uv run pytest tests/ -v
```

Tests mock `db.get_pool()` so no real database connection is needed.

---

## Configuration

All settings are read from environment variables (or `.env`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | Yes | — | Groq LLM API key |
| `SHIVAY_API_URL` | Yes | — | Shivay API base URL |
| `SHIVAY_API_KEY` | Yes | — | Shivay API key |
| `SHIVAY_INSTANCE_NAME` | Yes | — | WhatsApp instance name |
| `POSTGRES_URL` | Yes | — | psycopg PostgreSQL URL (checkpointer) |
| `TESTING_DB_URL` | No | `POSTGRES_URL` | psycopg URL for business data DB |
| `RATE_LIMIT_MAX` | No | `10` | Max messages per window |
| `RATE_LIMIT_WINDOW` | No | `60` | Window size in seconds |
| `LOG_LEVEL` | No | `INFO` | Python log level |
| `ENVIRONMENT` | No | `development` | `development` or `production` |
| `NGROK_AUTHTOKEN` | No | — | ngrok auth token (dev only) |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/shivay` | Shivay API webhook (main entry point) |
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Messages processed/failed + uptime |

Interactive docs: `http://localhost:8000/docs`

---

## Tech Stack

| Component | Technology |
|---|---|
| AI Framework | LangGraph 0.3+ (StateGraph) |
| LLM | Groq — Llama 3.3 70B Versatile |
| Web Framework | FastAPI + Uvicorn |
| WhatsApp Gateway | Shivay API v2 |
| Database | PostgreSQL (psycopg3 async) |
| Checkpointer | `langgraph-checkpoint-postgres` |
| HTTP Client | httpx (async) |
| Retry Logic | tenacity |
| Validation | Pydantic v2 + pydantic-settings |
| Logging | python-json-logger (structured JSON) |
| Package Manager | uv + hatch |

---

## License

MIT
