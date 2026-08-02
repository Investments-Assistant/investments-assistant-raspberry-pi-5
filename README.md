# Investment Assistant

A private, home-hosted investment assistant that runs entirely on a **Raspberry Pi 5**.
It monitors stock, ETF, crypto, and options markets in real time, executes or recommends
trades through four optional broker integrations, and is accessible from anywhere in the world
through a private WireGuard VPN. The local model and reasoning stay on the Pi; only explicitly
configured market, news, newsletter, or broker connections leave the network.

LLM inference is **fully local**: GGUF models are loaded directly into process memory
via `llama-cpp-python`. No external AI API or model sidecar is used.

---

## Features

- **23 agent tools** — market data, technical indicators, options chains, NFT risk, news sentiment,
  portfolio management, trade execution, backtesting, report generation
- **4 broker integrations** — Alpaca (stocks/ETFs), Interactive Brokers (stocks/options),
  Coinbase (crypto), Binance (crypto)
- **Two trading modes** — `recommend` (agent proposes, you confirm) or `auto` (agent
  executes within configurable safety limits)
- **Authenticated chat UI** — FastAPI + WebSocket with ID/password login, signed sessions,
  CSRF protection, origin checks, and VPN-only access
- **Scheduled evidence reviews** — market data refreshed every 5 min, news ingested hourly,
  and an hourly local review that records findings; execution remains server-guarded
- **Weekly reports** — HTML + PDF with auditable data, assumptions, risks, and decisions
- **Pi-hole** — optional DNS ad blocker for VPN clients (admin via SSH tunnel)
- **WireGuard VPN** — private, cryptographically authenticated remote access from anywhere
- **No cloud AI dependency** — the LLM runs on-device; outbound traffic is limited to the
  configured market/news/broker data paths

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CLIENT DEVICES  (phone, laptop, tablet)                                    │
│                                                                             │
│  ┌──────────────────────┐       ┌──────────────────────────────────────┐   │
│  │  Browser / Web UI    │       │  Claude Desktop / Claude Code        │   │
│  │  https://10.8.0.1    │       │  (optional — via MCP server          │   │
│  └──────────┬───────────┘       │   on your laptop)                    │   │
│             │                   └──────────────┬───────────────────────┘   │
└─────────────┼──────────────────────────────────┼───────────────────────────┘
              │  WireGuard VPN (UDP 51820)        │  WireGuard VPN
              │  Only authenticated peers         │  POST /api/tools/invoke
              ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  RASPBERRY PI 5  (8 GB RAM)                                                 │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Nginx  (Docker container, port 443)                                │   │
│  │  • TLS termination (self-signed cert)                               │   │
│  │  • IP whitelist: WireGuard 10.8.0.0/24 only                         │   │
│  │  • Rate limiting: 60 req/min general, 10 WebSocket connections      │   │
│  └───────────────────────────────┬─────────────────────────────────────┘   │
│                                  │  proxy_pass → :8000                     │
│  ┌───────────────────────────────▼─────────────────────────────────────┐   │
│  │  FastAPI  (Docker container, port 8000)                             │   │
│  │                                                                     │   │
│  │  ┌──────────────────┐   ┌───────────────────┐   ┌───────────────┐  │   │
│  │  │ GET /            │   │ WS /ws/chat/{id}  │   │ REST API      │  │   │
│  │  │ Chat UI (HTML)   │   │ Streaming chat    │   │ /api/health   │  │   │
│  │  └──────────────────┘   └────────┬──────────┘   │ /api/reports  │  │   │
│  │                                  │               │ /api/trades   │  │   │
│  │                          ┌───────▼──────────┐   │ /api/tools/   │  │   │
│  │                          │  Orchestrator    │   │   invoke      │  │   │
│  │                          │  (per session)   │   └───────────────┘  │   │
│  │                          │  • history[40]   │                      │   │
│  │                          │  • system prompt │                      │   │
│  │                          └───────┬──────────┘                      │   │
│  │                                  │                                  │   │
│  │           ┌──────────────────────┴─────────────────────┐           │   │
│  │           ▼                                            ▼           │   │
│  │  ┌─────────────────────────┐            ┌──────────────────────┐   │   │
│  │  │  LLM Backend            │            │  Tool Dispatcher     │   │   │
│  │  │                         │  tool call │                      │   │   │
│  │  │  ┌─────────────────┐   │◄──────────►│  ┌────────────────┐  │   │   │
│  │  │  │ LlamaCppClient  │   │  result    │  │ Market Data    │  │   │   │
│  │  │  │ (GGUF in-proc)  │   │            │  │ get_stock_data │  │   │   │
│  │  │  │ Qwen2.5-7B      │   │            │  │ get_crypto     │  │   │   │
│  │  │  │ ~4.7 GB RAM     │   │            │  │ get_technical  │  │   │   │
│  │  │  └─────────────────┘   │            │  │ get_options    │  │   │   │
│  │  │                         │            │  │ search_ticker  │  │   │   │
│  │  │                         │            │  └────────────────┘  │   │   │
│  │  │                         │            │  ┌────────────────┐  │   │   │
│  │  │                         │            │  │ News & Sentiment│  │   │   │
│  │  │                         │            │  │ search_news    │  │   │   │
│  │  │                         │            │  │ earnings_cal.  │  │   │   │
│  │  │  Tool-use loop (ReAct): │            │  │ stored_news    │  │   │   │
│  │  │  while finish_reason    │            │  │ latest_news    │  │   │   │
│  │  │  == "tool_calls":       │            │  └────────────────┘  │   │   │
│  │  │    dispatch → feed back │            │  ┌────────────────┐  │   │   │
│  │  └─────────────────────────┘            │  │ Portfolio      │  │   │   │
│  │                                         │  │ get_portfolio  │  │   │   │
│  └─────────────────────────────────────────┤  │ get_account    │  │   │   │
│                                            │  │ get_trades     │  │   │   │
│  ┌─────────────────────────────────────┐   │  └────────────────┘  │   │   │
│  │  APScheduler  (in-process)          │   │  ┌────────────────┐  │   │   │
│  │                                     │   │  │ Trade Execution│  │   │   │
│  │  every 5 min  → market snapshot     │   │  │ execute_trade  │  │   │   │
│  │  every 60 min → news ingestion       │   │  │ cancel_order   │  │   │   │
│  │  every 60 min → autonomous review    │   │  └────────────────┘  │   │   │
│  │  Sunday 18:00 → weekly PDF report   │   │  ┌────────────────┐  │   │   │
│  │  Saturday 9am → newsletter email    │   │  │ Analysis       │  │   │   │
│  └─────────────────────────────────────┘   │  │ run_simulation │  │   │   │
│                                            │  │ gen_report     │  │   │   │
│  ┌────────────────┐                         │  │ set_mode       │  │   │   │
│  │  PostgreSQL    │                         │  └────────────────┘  │   │   │
│  │  chat history  │                         └──────────────────────┘   │   │
│  │  trades        │                                                    │   │
│  │  reports       │                                                    │   │
│  └────────────────┘                                                    │   │
│                                                                         │   │
│  ┌─────────────────────────────────────────────────────────────────┐   │   │
│  │  Pi-hole  (Docker port 53; admin via SSH tunnel)                 │   │   │
│  │  Optional DNS for VPN clients only                               │   │   │
│  └─────────────────────────────────────────────────────────────────┘   │   │
│                                                                         │   │
└─────────────────────────────────────────────────────────────────────────────┘
              │ configured egress only; public inbound is WireGuard UDP 51820
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL DATA SOURCES  (only explicitly configured connections)             │
│                                                                             │
│  Yahoo Finance (yfinance)    RSS feeds / HTML scraping                      │
│  Alpaca Markets API          Coinbase Advanced API     Binance API          │
│  Interactive Brokers TWS     IMAP (newsletter emails)                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Request flow — user sends a chat message

```
User types message
       │
       ▼
  WebSocket /ws/chat/{session_id}
       │
       ▼
  Orchestrator.chat()
  • appends user message to history
  • passes history + system prompt to LLM
       │
       ▼
  LLM Backend  ←─── system prompt + conversation history + 23 tool schemas
  (ReAct loop)
       │
       ├─ finish_reason == "tool_calls" ──► Tool Dispatcher ──► broker / yfinance / news
       │       ▲                                                        │
       │       └────────────── tool result fed back ───────────────────┘
       │
       └─ finish_reason == "stop" ──► response events streamed to browser
                                   ──► persisted to PostgreSQL
```

### Scheduled jobs

```
APScheduler (runs inside FastAPI process)
│
├── every 5 min     → get_market_overview() + top news   → _latest_snapshot cache
├── every 60 min     → RSS / HTML ingestion                → PostgreSQL news store
├── every 60 min     → autonomous local evidence review    → PostgreSQL analysis store
├── Sunday 18:00    → weekly report (LLM + tools + PDF)  → PostgreSQL + /app/reports
└── Saturday 09:00  → IMAP email reader (newsletters)    → PostgreSQL news store
```

---

## Project Structure

```
investments-assistant/
├── Dockerfile                        # ARM64-optimised, compiles llama-cpp-python
├── docker-compose.yml                # app, postgres, pihole, nginx
├── Makefile                          # common commands
├── pyproject.toml                    # Poetry dependencies
├── .env.example                      # environment template
│
├── config/
│   ├── nginx/
│   │   ├── investment-assistant.conf # IP whitelist, HTTPS, WebSocket proxy
│   │   └── rate-limit.conf           # Nginx rate-limiting zones
│   ├── wireguard/
│   │   ├── wg0.conf.template         # server config (fill in keys)
│   │   ├── client.conf.template      # per-device client config
│   │   └── setup.md                  # WireGuard setup guide
│   └── pihole/
│       └── setup.md                  # Pi-hole setup guide
│
├── models/                           # GGUF model files go here (bind-mounted)
│
├── scripts/
│   ├── setup.sh                      # automated Pi 5 setup (Docker, WireGuard, firewall)
│   └── download_model.py             # download GGUF models from HuggingFace
│
└── src/
    ├── app.py                        # FastAPI application entry point
    ├── config.py                     # pydantic-settings configuration
    │
    ├── agent/
    │   ├── clients/
    │   │   ├── base.py               # BaseLLMClient ABC
    │   │   └── llama_cpp_client.py   # GGUF inference for Pi 5
    │   ├── orchestrator.py           # stateful per-session agent loop
    │   └── prompts.py                # system prompt + weekly report prompt
    │
    ├── tools/
    │   ├── definitions.py            # 23 tool schemas + OpenAI format converter
    │   ├── dispatcher.py             # routes tool calls to implementations
    │   ├── nft.py                    # local evidence-only NFT risk screen
    │   ├── market_data.py            # yfinance: OHLCV, indicators, options, earnings
    │   ├── news.py                   # RSS + optional adapters with sentiment analysis
    │   ├── portfolio.py              # cross-broker portfolio aggregation
    │   ├── alpaca.py                 # Alpaca Markets integration
    │   ├── ibkr.py                   # Interactive Brokers integration
    │   ├── coinbase.py               # Coinbase Advanced Trade integration
    │   ├── binance_tool.py           # Binance integration
    │   └── simulator.py             # backtesting engine
    │
    ├── db/
    │   ├── database.py               # SQLAlchemy async engine
    │   └── models.py                 # ChatMessage, Trade, Analysis, Report, etc.
    │
    ├── scheduler/
    │   ├── jobs.py                   # APScheduler jobs
    │   └── reporter.py               # weekly HTML+PDF report generator
    │
    └── web/
        ├── routes.py                 # HTTP + WebSocket endpoints
        └── static/                   # index.html, app.js, style.css
```

---

## LLM Backend

Uses `llama-cpp-python` to load **GGUF quantised models** directly into memory.
ARM NEON-optimised; designed for CPU inference.

```bash
# Download a model
python3 scripts/download_model.py --list
python3 scripts/download_model.py qwen2.5-7b --output-dir ./models

# .env
LLM_MODEL_PATH=/app/models/qwen2.5-7b-instruct-q4_k_m.gguf
LLM_N_THREADS=4
LLM_N_BATCH=128
```

Tested models on Pi 5 (8 GB RAM):

| Model | Size | Notes |
| --- | --- | --- |
| `qwen2.5-7b` | ~4.7 GB | Best quality/speed — recommended |
| `llama3.2-3b` | ~3.4 GB | Faster, lower RAM |
| `mistral-7b` | ~4.4 GB | Solid all-rounder |
| `llama3.1-8b` | ~4.9 GB | Strong tool use |

### Local-only boundary and fine-tuning

There is no AI API, remote model server, embedding service, or cloud reasoning path in the
default deployment. RSS/HTML feeds are used for news; market quotes and any account/order
execution require the explicitly configured external data or brokerage connection. If a
strictly air-gapped installation is required, leave broker credentials empty, keep
`LIVE_TRADING_ENABLED=false`, and use analysis/backtesting only.

Fine-tuning is not part of runtime. On a Pi 5, a verified tool-aware base model plus grounded
local data is safer and easier to update than a fine-tuned model that may memorize stale prices
or learn unsafe order behavior. Fine-tuning can be evaluated off-device later, but only with a
versioned dataset, held-out risk tests, and a model that still emits structured tool calls.

## Security Model

The app is **not publicly reachable**. The only port forwarded on your router should be
**UDP 51820** (WireGuard). A separate ID/password login is still required after the VPN
connection; a WireGuard private key is not a substitute for the application password.
Everything else is blocked at four layers:

1. **WireGuard** — silently drops all packets from unknown peers (no response to attackers)
2. **DOCKER-USER iptables** — kernel-level drop of 80/443 from non-VPN IPs, even if Docker binds to `0.0.0.0`
3. **Nginx IP whitelist** — application-layer check; rate limiting (60 req/min)
4. **FastAPI `is_ip_allowed()` + signed session** — in-process VPN and identity checks;
   state-changing browser requests also require CSRF validation

Generate the password hash and session secret with `scripts/create_auth_hash.py`.
See [config/wireguard/setup.md](config/wireguard/setup.md) for the VPN setup.

---

## Quick Start

See [QUICKSTART.md](QUICKSTART.md) for the full step-by-step deployment guide.

```bash
# 1. Run the automated Pi setup (Docker, WireGuard, certs, firewall)
bash scripts/setup.sh

# 2. Download a model
python3 scripts/download_model.py qwen2.5-7b --output-dir ./models

# 3. Configure .env
cp .env.example .env && nano .env

# 4. Start everything
bash scripts/pi_deploy.sh

# 5. Access via WireGuard VPN
# https://10.8.0.1
```

---

## Configuration

All settings are read from `.env`. Copy `.env.example` as a starting point.

Key groups:

| Group | Variables |
| --- | --- |
| LLM | `LLM_MODEL_PATH`, `LLM_CONTEXT_SIZE`, `LLM_N_THREADS`, `AGENT_MAX_TOKENS` |
| Trading | `TRADING_MODE`, `AUTO_MAX_TRADE_USD`, `AUTO_DAILY_LOSS_LIMIT_USD`, `LIVE_TRADING_ENABLED` |
| Security | `ALLOWED_IPS`, `AUTH_USERNAME`, `AUTH_PASSWORD_HASH`, `AUTH_SESSION_SECRET` |
| Database | `POSTGRES_PASSWORD` |
| Brokers | `ALPACA_API_KEY`, `COINBASE_API_KEY`, `BINANCE_API_KEY`, `IBKR_ENABLED` |
| News | RSS/HTML by default; optional `NEWS_API_ADAPTERS_ENABLED`, `NEWSAPI_KEY`, `GUARDIAN_API_KEY` |
| Pi-hole | `PIHOLE_PASSWORD`, `TZ` |

---

## Development

```bash
make install       # install dependencies (Poetry)
make dev           # run with hot reload
make lint          # ruff check
make format        # ruff format
make test          # pytest
```

---

## Disclaimer

This assistant provides investment analysis and execution assistance for informational
purposes only. It does not constitute financial advice. Past performance does not
guarantee future results. Always consult a qualified financial advisor before making
investment decisions.
