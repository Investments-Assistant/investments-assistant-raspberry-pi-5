# Development Guide

---

## Prerequisites

- Python 3.12 (matches `pyproject.toml` and `Dockerfile`)
- Poetry 2.x (`pip install poetry`)
- Docker + Docker Compose (for local PostgreSQL)
- `make` (GNU Make)

---

## Local setup

```bash
# 1. Clone and enter the directory
cd investments-assistant/

# 2. Install all dependencies (including dev deps: pytest, ruff, mypy)
make dev-install
# Equivalent: poetry install

# 3. Copy and configure .env
cp .env.example .env
# Edit .env:
#   ENVIRONMENT=development
#   POSTGRES_PASSWORD=localdev
#   PIHOLE_PASSWORD=localdev  (only needed if running pihole container)

# 4. Start PostgreSQL
docker compose up -d postgres

# 5. Run the app with hot reload
make dev
# Equivalent: poetry run uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload

# 6. Open http://localhost:8000 in your browser
```

In development mode:
- IP whitelist is disabled — any IP can access the app
- `/docs` (Swagger UI) and `/openapi.json` are enabled
- SQLAlchemy logs all SQL queries
- Hot reload restarts the server on source changes

---

## Project structure

```
src/
├── app.py            # FastAPI application, lifespan, static files
├── config.py         # pydantic-settings Settings class
│
├── agent/
│   ├── clients/
│   │   ├── __init__.py          # create_llm_client() factory
│   │   ├── base.py              # BaseLLMClient ABC
│   │   └── llama_cpp_client.py  # GGUF inference
│   ├── orchestrator.py          # Per-session chat loop
│   └── prompts.py               # System prompt and report prompt
│
├── db/
│   ├── database.py   # Engine, session factory, Base, create_all_tables()
│   └── models.py     # ORM models (ChatMessage, Trade, Report, etc.)
│
├── news/
│   ├── sources.py    # Fetch from RSS / Guardian / scraper
│   ├── ingestion.py  # Persist articles to DB
│   ├── search.py     # PostgreSQL FTS search
│   └── email_reader.py  # IMAP newsletter ingestion
│
├── scheduler/
│   ├── jobs.py       # APScheduler job definitions
│   └── reporter.py   # HTML/PDF report generator
│
├── tools/
│   ├── __init__.py          # dispatch_tool() export
│   ├── definitions.py       # 23 tool schemas
│   ├── dispatcher.py        # Routes tool calls to implementations
│   ├── market_data.py       # yfinance tools
│   ├── news.py              # Live news search
│   ├── news_memory.py       # Wrapper for persistent news search
│   ├── portfolio.py         # Cross-broker aggregator
│   ├── simulator.py         # Backtester
│   └── brokers/
│       ├── alpaca.py
│       ├── ibkr.py
│       ├── coinbase.py
│       └── binance.py
│
└── web/
    ├── routes.py           # HTTP + WebSocket endpoints
    └── static/
        ├── index.html
        ├── style.css
        └── app.js
```

---

## Running tests

```bash
# All tests
make test
# Equivalent: poetry run pytest

# Unit tests only (no DB required)
poetry run pytest -m unit -v

# Integration tests (requires postgres running)
poetry run pytest -m integration -v

# With coverage report
poetry run pytest --cov=src --cov-report=html
open htmlcov/index.html
```

**Test markers**:
- `@pytest.mark.unit` — pure Python, no external services. Mock everything.
- `@pytest.mark.integration` — requires a real PostgreSQL connection.

**`conftest.py`**: the root `tests/conftest.py` sets up `ENVIRONMENT=development` and
a test database URL. The integration `conftest.py` creates the schema before tests run
and tears it down after.

---

## Linting and formatting

```bash
# Check for lint issues
make lint
# Equivalent:
#   poetry run ruff check src/
#   poetry run mypy --follow-imports=skip src/

# Auto-fix + format
make format
# Equivalent: poetry run ruff format src/
```

Ruff is configured in `pyproject.toml`:
```toml
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]  # errors, pyflakes, isort, pyupgrade, bugbear
```

The `I` rule enforces import sorting (replaces isort). The `UP` rule enforces modern
Python syntax (replaces pyupgrade). The `B` rule catches common bugs (bugbear).

---

## Adding a new tool

1. **Define the schema** in `src/tools/definitions.py` — add a new dict to `TOOL_DEFINITIONS`
2. **Implement** the tool function in the appropriate module (or create a new file in `src/tools/`)
3. **Register** it in `src/tools/dispatcher.py` — add to `_SYNC_DISPATCH` or `_ASYNC_DISPATCH`
4. **Test** it:
   ```python
   # tests/unit/test_new_tool.py
   @pytest.mark.unit
   def test_new_tool_returns_expected_shape():
       result = new_tool(param="value")
       assert "key" in result
   ```

The tool is automatically available to the LLM after the next server restart, and
automatically exposed via the MCP server.

---

## Adding a new LLM backend

1. Create `src/agent/clients/<name>_client.py` implementing `BaseLLMClient`
2. Implement `async def stream_response(messages, system) -> AsyncGenerator[dict, None]`
3. Add `get_<name>_client()` singleton getter in that file
4. Add a branch in `create_llm_client()` in `src/agent/clients/__init__.py`:
   ```python
   if backend == "my_backend":
       from src.agent.clients.my_backend_client import get_my_backend_client
       return get_my_backend_client()
   ```
5. Add the new backend's package to `pyproject.toml` as an optional dependency
6. Document the install instructions in [LLM-Backends](LLM-Backends)

---

## Docker workflow

```bash
# Validate .env/model, build the app image, and start all services
make pi-deploy

# Stream logs
make docker-logs

# Restart just the app (after code changes)
make docker-restart

# Stop everything
make docker-down
```

Use `make docker-build` and `make docker-up` directly only when you intentionally want
to bypass the Raspberry Pi deployment validation checks.

**The `models/` directory**: GGUF models are bind-mounted read-only:
```yaml
volumes:
  - ./models:/app/models:ro
```
Download models to `investments-assistant/models/` on the host before starting.
The `LLM_MODEL_PATH` in `.env` must use the container path (`/app/models/...`).

---

## Generating TLS certificates

```bash
make gen-certs
```

This creates `config/nginx/certs/selfsigned.key` and `selfsigned.crt` — a self-signed
4096-bit RSA certificate valid for 10 years. The certificate is mounted into the Nginx
container read-only.

Your browser will show a certificate warning on first visit. To suppress it permanently,
either:
- Add an exception in your browser
- Import the certificate as a trusted CA on your devices
- Use a proper CA-signed certificate (Let's Encrypt via DNS challenge)

---

## Common pitfalls

### The model takes 60 seconds to respond on the first message

Normal. The GGUF model (~4.7 GB) loads from disk into RAM on the first inference call.
Subsequent messages are fast. Watch the logs: `INFO  Loading GGUF model: ...` → `INFO  GGUF model loaded`.

### `pg_isready` fails on startup

The app waits for PostgreSQL via Docker Compose's `depends_on.condition: service_healthy`
and the healthcheck `pg_isready -U ia_user`. If the container takes > 50s to become
healthy (rare), the app might start before DB is ready. The `pool_pre_ping=True` engine
setting handles brief DB unavailability after startup.

### Poetry version mismatch

The `unit-tests.yml` workflow uses Poetry 2.3.4; `integration-tests.yml` uses 1.8.3.
Use Poetry 2.x locally to match the unit test environment.

### Ruff import-order errors

`ruff check` with the `I` rule enforces isort-style imports. Run `make format` to
auto-fix. The config uses `force-sort-within-sections = true` and `combine-as-imports = true`.
