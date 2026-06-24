# Architecture

## Why a Raspberry Pi 5?

The core design requirement was **full data sovereignty**: brokerage API keys, portfolio
data, chat history, and trade reasoning must never leave the home network. Running on a
Raspberry Pi 5 achieves this at minimal cost (~$80) with low idle power (~5 W).

The Pi 5 uses a quad-core Cortex-A76 ARM64 CPU. This is important because
`llama-cpp-python` ships hand-optimised GGML kernels with **ARM NEON SIMD** acceleration
— the same CPU instruction set the Pi 5 uses. A 7B parameter model quantised to 4-bit
(Q4_K_M, ~4.7 GB) achieves acceptable token throughput (~5–8 tokens/sec) without any GPU.

---

## Why Docker Compose?

All services (app, Postgres, Pi-hole, Nginx) run as Docker containers orchestrated
by a single `docker-compose.yml`. The reasons:

- **Isolation**: each service has its own file system, user, and network namespace
- **Reproducibility**: `make pi-deploy` on a fresh Pi always produces the same result
- **Pi-hole compatibility**: Pi-hole needs `NET_ADMIN` capability to bind to port 53;
  Docker Compose makes it easy to grant only that capability to the one container that
  needs it without running everything as root
- **Network segmentation**: the `internal` bridge network means app dependencies are
  never directly reachable from outside Docker

Cluster orchestration is overkill for a single-node home server; the added
control-plane complexity has no benefit at this scale.

---

## Why Python?

- **Ecosystem**: every data science and brokerage library we need (`yfinance`, `ta`,
  `alpaca-py`, `ib_insync`, `binance`) has a Python SDK. Writing the same integrations in
  Go or Rust would require maintaining custom HTTP clients for each broker.
- **AsyncIO**: Python 3.12's `asyncio` is mature. `asyncpg` and SQLAlchemy's async layer
  give genuinely non-blocking database access, which matters when the LLM is running in a
  thread pool and we need to serve WebSocket heartbeats concurrently.
- **llama-cpp-python**: the primary LLM runtime (`llama-cpp-python`) is a C/C++ library
  with Python bindings. Using Python keeps everything in one process — no IPC overhead.

---

## Service map

```
┌── nginx (container, :443) ──────────────────────────────────┐
│  TLS termination, IP whitelist, rate limiting                │
│  proxy_pass → app:8000                                       │
└───────────────────────────────────────────────────────────────┘
         ▼
┌── app (container, :8000) ───────────────────────────────────┐
│                                                              │
│  FastAPI application                                         │
│   ├─ GET /               → serves index.html                 │
│   ├─ GET /static/*       → CSS / JS                          │
│   ├─ WS  /ws/chat/{id}   → streaming chat                    │
│   ├─ GET /api/health     → liveness probe                    │
│   ├─ GET /api/market/snapshot → cached market data          │
│   ├─ GET /api/reports    → list reports                      │
│   ├─ GET /api/reports/{id}/pdf → download PDF                │
│   ├─ GET /api/trades     → trade history                     │
│   └─ POST /api/tools/invoke → MCP server bridge             │
│                                                              │
│  Orchestrator (one instance per session_id)                  │
│   └─ LLM Client (singleton, one model loaded on first use)  │
│       └─ LlamaCppClient  (llama-cpp-python, GGUF in-proc)   │
│                                                              │
│  Tool Dispatcher                                             │
│   ├─ market_data.py      yfinance                           │
│   ├─ news.py             RSS + NewsAPI                      │
│   ├─ news_memory.py      PostgreSQL FTS                     │
│   ├─ portfolio.py        cross-broker aggregator            │
│   ├─ brokers/alpaca.py   alpaca-py SDK                      │
│   ├─ brokers/ibkr.py     ib_insync                          │
│   ├─ brokers/coinbase.py coinbase-advanced-py               │
│   ├─ brokers/binance.py  python-binance                     │
│   └─ simulator.py        pandas + ta                        │
│                                                              │
│  APScheduler (in-process async scheduler)                    │
│   ├─ every 5 min   → market snapshot                        │
│   ├─ every 30 min  → news ingestion                         │
│   ├─ Mon–Fri 9–5   → autonomous scan (auto mode)            │
│   ├─ Sunday 18:00  → weekly report                          │
│   └─ Saturday 9am  → newsletter email ingestion             │
└───────────────────────────────────────────────────────────────┘
         │
         ▼
┌── postgres ─────┐
│  Tables:        │
│  chat_messages  │
│  trades         │
│  analyses       │
│  reports        │
│  daily_pnl      │
│  news_articles  │
│  simulation_    │
│  results        │
└─────────────────┘
         │
┌── pihole ───────┐
│  DNS :53        │
│  Web UI :8080   │
└─────────────────┘
```

---

## Request flow — WebSocket chat

```
1. User opens https://10.8.0.1 in browser
2. app.js opens WebSocket to /ws/chat/{uuid}
3. User types a message and presses Enter
4. Browser sends JSON: {"message": "What's AAPL doing?"}

5. routes.py: ip check → get_or_create_session(session_id)
6. orchestrator.load_history_from_db()   # restore prior turns
7. orchestrator.chat(user_message)
   a. appends {"role": "user", "content": ...} to history
   b. calls llm_client.stream_response(messages, system_prompt)

8. LlamaCppClient.stream_response() — agentic loop:
   a. builds [system, ...history] message list
   b. calls llm.create_chat_completion(messages, tools=_TOOLS, tool_choice="auto")
      runs in asyncio.run_in_executor() — thread pool, keeps event loop free
   c. if finish_reason == "tool_calls":
      - parse tool_calls from response
      - for each call: dispatch_tool(name, input) → result_str
      - yield {"type": "tool_call", ...} and {"type": "tool_result", ...}
      - append tool call + results to messages
      - loop back to (b)
   d. if finish_reason == "stop":
      - yield {"type": "text_delta", "text": "..."}  (full text, not streamed tokens)
      - yield {"type": "done"}

9. orchestrator streams all events back through WebSocket
10. app.js renders text_delta events to the chat bubble in real time
11. After stream ends, orchestrator persists user+assistant turn to Postgres
```

**Note on "streaming"**: `llama-cpp-python`'s `create_chat_completion` is synchronous and
returns the full response at once. True token-by-token streaming would require the
`stream=True` parameter — it was omitted to keep tool-call parsing simpler. The text
appears to "arrive" when the model finishes the full turn. This is a known trade-off.

---

## Why APScheduler (in-process) instead of Celery?

Celery would add a separate worker process, a task serialisation layer,
and operational complexity (supervisor, flower, etc.). For five scheduled jobs that don't
compete for resources, an in-process `AsyncIOScheduler` is simpler and has no extra
infra cost. The downside is that scheduled jobs share the same process as request serving —
if the LLM is mid-inference and a scheduled job fires, the job waits. This is acceptable
because scheduled jobs are fire-and-forget and have generous `misfire_grace_time` settings.

---

## Why Nginx in front of FastAPI?

FastAPI (uvicorn) can serve HTTPS directly, but Nginx adds:
- **TLS session caching** (`ssl_session_cache shared:SSL:10m`) — reduces handshake cost
- **Rate limiting** — the `limit_req` zones protect against a rogue VPN peer flooding the app
- **WebSocket connection limiting** — `limit_conn ws_conn 3` caps concurrent WS connections per IP
- **Static file caching** — `Cache-Control: public, max-age=3600` for CSS/JS
- **HTTP → HTTPS redirect** — clean user experience

Caddy was considered as a lighter alternative but Nginx was chosen for its battle-tested
`limit_req` / `limit_conn` modules and wider familiarity.

---

## Why PostgreSQL instead of SQLite?

- **Full-text search (FTS)**: PostgreSQL's `plainto_tsquery` / `ts_rank` / GIN indexes
  give the news memory tool fast ranked search over hundreds of thousands of articles.
  SQLite's FTS5 exists but lacks `ts_rank` and is harder to use from SQLAlchemy async.
- **Async driver**: `asyncpg` is a native asyncio PostgreSQL driver with no thread pool
  overhead. SQLite's aiosqlite wraps the sync driver in a thread pool.
- **JSON column type**: PostgreSQL's native `JSONB` (used for `tags`, `recommendations`,
  `equity_curve`) gives indexed, queryable JSON without a schema migration every time the
  shape changes. We use `JSON` (not `JSONB`) because the data is write-once and we never
  query inside it via SQL — we read the whole column in Python.
- **Concurrent writes**: APScheduler background jobs, the WebSocket handler, and REST
  endpoints all write concurrently. PostgreSQL handles this natively; SQLite's write-lock
  would serialize everything.

---

## Two Docker networks: `internal` and `external`

```yaml
networks:
  internal:   # app, postgres, pihole — never directly routable from outside
  external:   # nginx only — nginx bridges external traffic into the internal net
```

Only Nginx is on the `external` network. The app container has no publicly routable
network interface. Even if an attacker bypassed Nginx, they couldn't reach `app:8000`
because it only listens on the internal Docker bridge.
