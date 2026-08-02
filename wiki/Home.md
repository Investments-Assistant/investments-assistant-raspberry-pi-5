# Investment Assistant — Wiki

A private, home-hosted investment assistant that runs entirely on a **Raspberry Pi 5 (8 GB)**.
No cloud AI or remote reasoning. Outbound traffic is limited to configured market/news
sources and any explicitly enabled broker connectivity.

---

## Pages

| Page | What it covers |
|---|---|
| [Architecture](Architecture) | Full system diagram, design rationale, and request-flow walkthrough |
| [LLM Backend](LLM-Backends) | llama.cpp, GGUF format, model selection for Pi 5 |
| [Agent and Tool Use](Agent-and-Tool-Use) | ReAct loop, orchestrator, conversation history, system prompt |
| [Tools Reference](Tools-Reference) | All 23 tools — inputs, outputs, implementation notes |
| [Broker Integrations](Broker-Integrations) | Alpaca, Interactive Brokers, Coinbase, Binance — why each, how each works |
| [News Pipeline](News-Pipeline) | RSS, optional API adapters, web scraping, email ingestion, PostgreSQL FTS |
| [Database Schema](Database-Schema) | All ORM models, column types, indexes, design decisions |
| [Scheduler and Jobs](Scheduler-and-Jobs) | APScheduler, every job, timing, the autonomous scan |
| [Security Model](Security-Model) | WireGuard-only access, iptables, Nginx, FastAPI, ID/password auth |
| [Configuration Reference](Configuration-Reference) | Every `.env` variable explained |
| [CI/CD](CI-CD) | GitHub Actions workflows, SonarCloud quality gate |
| [MCP Integration](MCP-Integration) | Using the assistant from Claude Desktop / Claude Code |
| [Development Guide](Development-Guide) | Local setup, testing, contributing |

---

## 30-second orientation

```
Your browser / Claude Desktop
    │  WireGuard VPN (no public internet exposure)
    ▼
Nginx (TLS, IP whitelist, rate limit)
    ▼
FastAPI  →  WebSocket /ws/chat/{session_id}
    ▼
Orchestrator  →  LLM Backend (local GGUF model)
    ▼
Tool Dispatcher  →  yfinance / brokers / news
    ▼
PostgreSQL (history, trades, reports, news articles)
```

The LLM never calls home. It runs entirely in-process via `llama-cpp-python`.
