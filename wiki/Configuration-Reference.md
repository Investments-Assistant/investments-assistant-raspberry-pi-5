# Configuration Reference

All settings are read from `.env` at startup via `pydantic-settings`.
Copy `.env.example` to `.env` and fill in the values.

`pydantic-settings` reads from `.env` with `case_sensitive=False` and `extra="ignore"`
(unknown variables are silently skipped). All variables have Python-typed defaults.

---

## Application

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `development` \| `production` | `production` | Controls docs visibility, IP check bypass, and SQL echo |
| `LOG_LEVEL` | string | `INFO` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `APP_HOST` | string | `0.0.0.0` | Host uvicorn binds to |
| `APP_PORT` | integer | `8000` | Port uvicorn binds to |

In `development` mode:

- FastAPI `/docs` and `/openapi.json` are enabled
- IP whitelist check is skipped
- SQLAlchemy echoes all SQL queries to the log
- uvicorn hot-reload is enabled via `make dev`

---

## Security

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ALLOWED_IPS` | comma-separated CIDRs | `10.8.0.0/24` | IP ranges allowed to access the app |
| `TRUST_PROXY_HEADERS` | bool | `true` | Trust the configured reverse proxy client-IP headers |
| `AUTH_USERNAME` | string | `admin` | Bootstrap local login ID; additional users use `scripts/create_user.py` |
| `AUTH_PASSWORD_HASH` | string | `""` | scrypt hash from `scripts/create_auth_hash.py` |
| `AUTH_SESSION_SECRET` | string | `""` | Random signing secret for browser sessions |
| `AUTH_SESSION_TTL_MINUTES` | integer | `480` | Signed session lifetime |
| `AUTH_REQUIRE_LOGIN` | bool | `true` | Must remain true on a production/Pi deploy |
| `AUTH_COOKIE_SECURE` | bool | `true` | Mark browser cookies Secure in production |

Examples:

```env
# VPN only (maximum security):
ALLOWED_IPS=10.8.0.0/24

# If you intentionally change this, also change the host firewall and Nginx rules.
# The supported Pi profile is VPN-only.
```

`settings.allowed_networks` parses this string into a list of `ipaddress.IPv4Network`
objects at startup. `settings.is_ip_allowed(ip)` checks membership.

---

## LLM Backend

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `LLM_MODEL_PATH` | string | `/app/models/qwen2.5-7b-instruct-q4_k_m.gguf` | Absolute path to GGUF file inside the container |
| `LLM_CONTEXT_SIZE` | integer | `4096` | Context window in tokens. Larger = more history, more RAM |
| `LLM_N_GPU_LAYERS` | integer | `0` | GPU layers to offload: `0` = CPU only, `-1` = all on GPU |
| `LLM_N_THREADS` | integer | `4` | llama.cpp CPU worker threads |
| `LLM_N_BATCH` | integer | `128` | Prompt-processing batch size |

**Pi 5 note**: `LLM_N_GPU_LAYERS=0` — the Pi has no GPU. Setting this to > 0 has no
effect without a CUDA/Metal/Vulkan device.

**Context size trade-off**: increasing `LLM_CONTEXT_SIZE` to 8192 allows more
conversation history and longer tool results, but increases RAM usage by ~500 MB for a
7B model. Monitor free RAM with `free -h` after startup.

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `AGENT_MAX_TOKENS` | integer | `2048` | Maximum tokens per LLM response |
| `AGENT_TEMPERATURE` | float | `0.1` | Sampling temperature. Low = deterministic, high = creative |
| `AGENT_MAX_CONTEXT_MESSAGES` | integer | `15` | Conversation turns kept in context before trimming |

`AGENT_MAX_CONTEXT_MESSAGES=15` is sized for the default 4096-token context window
(~400 tokens system prompt + 2048 response budget leaves ~1648 tokens for history ≈ 15
messages). Increase this when using a larger `LLM_CONTEXT_SIZE`.

`AGENT_TEMPERATURE=0.1` is intentionally low for a financial assistant — we want
deterministic, reproducible analysis, not creative variation. Increase to 0.3–0.5
if the agent feels too repetitive.

---

## Trading

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `TRADING_MODE` | `recommend` \| `auto` | `recommend` | Scheduler/bootstrap default; each local user's mode is stored in PostgreSQL |
| `AUTO_MAX_TRADE_USD` | float | `500.0` | Maximum USD per single trade in auto mode |
| `AUTO_DAILY_LOSS_LIMIT_USD` | float | `1000.0` | Maximum realized loss in a single day before auto-trading is halted |
| `AUTO_ALLOWED_SYMBOLS` | comma-separated | `""` (all) | Symbols the agent can trade autonomously |
| `AUTO_ALLOW_MARKET_ORDERS` | bool | `false` | Whether auto mode may submit orders without a limit/notional |
| `LIVE_TRADING_ENABLED` | bool | `false` | Explicit gate for live-money routes |
| `AUTONOMOUS_SCANS_ENABLED` | bool | `true` | Enable hourly local evidence reviews |
| `AUTONOMOUS_SCAN_INTERVAL_MINUTES` | integer | `60` | Autonomous review interval |

Examples:

```env
# Allow only blue-chip stocks and BTC in auto mode:
AUTO_ALLOWED_SYMBOLS=AAPL,MSFT,NVDA,SPY,QQQ,BTCUSDT,ETHUSDT

# No restriction:
AUTO_ALLOWED_SYMBOLS=
```

**`AUTO_DAILY_LOSS_LIMIT_USD` is fail-closed**: a recorded halt blocks subsequent auto trades,
and a database outage also blocks them. The limit is updated only from an explicit realized-P&L
value returned by a broker adapter; order submission alone is not treated as a fill or as P&L.

**Start with `TRADING_MODE=recommend`** until you've verified the agent's behaviour
through several conversations. Switch to `auto` only after you're confident in the
agent's reasoning quality and safety limits.

---

## Database

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `POSTGRES_HOST` | string | `postgres` | Hostname — `postgres` inside Docker, `localhost` for local dev |
| `POSTGRES_PORT` | integer | `5432` | PostgreSQL port |
| `POSTGRES_DB` | string | `investment_assistant` | Database name |
| `POSTGRES_USER` | string | `ia_user` | Database user |
| `POSTGRES_PASSWORD` | string | `change_me` | **Must be changed** — required by docker-compose.yml |

`settings.database_url` is computed:

```text
postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}
```

---

## Alpaca

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ALPACA_API_KEY` | string | `""` | API key from <https://alpaca.markets> |
| `ALPACA_SECRET_KEY` | string | `""` | Secret key |
| `ALPACA_PAPER` | bool | `true` | `true` = paper trading; `false` = live money |

**Always start with `ALPACA_PAPER=true`**. Paper trading uses real market data but
simulated money. Switch to `false` only after extensive testing.

---

## Interactive Brokers

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `IBKR_ENABLED` | bool | `false` | Must be `true` to enable IBKR tools |
| `IBKR_HOST` | string | `127.0.0.1` | Host running IB Gateway / TWS |
| `IBKR_PORT` | integer | `4002` | IB Gateway paper port. Live: `4001`. TWS paper: `7497`. TWS live: `7496` |
| `IBKR_CLIENT_ID` | integer | `1` | Must be unique per concurrent connection |

---

## Coinbase

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `COINBASE_API_KEY` | string | `""` | CDP API key |
| `COINBASE_API_SECRET` | string | `""` | CDP private key (PEM format) |

---

## Binance

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `BINANCE_API_KEY` | string | `""` | Binance API key |
| `BINANCE_SECRET_KEY` | string | `""` | Binance secret key |
| `BINANCE_TESTNET` | bool | `true` | `true` = testnet; `false` = live |

---

## News

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `NEWSAPI_KEY` | string | `""` | NewsAPI key from <https://newsapi.org> |
| `GUARDIAN_API_KEY` | string | `""` | Guardian Content API key — free at <https://open-platform.theguardian.com> |
| `NEWS_API_ADAPTERS_ENABLED` | bool | `false` | Opt in to NewsAPI/Guardian; RSS/HTML are the default |

Both keys are optional and ignored unless the adapter flag is enabled. The default profile
uses RSS and polite HTML scraping without news APIs.

---

## Newsletter Email (IMAP)

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `NEWSLETTER_IMAP_SERVER` | string | `imap.gmail.com` | IMAP hostname |
| `NEWSLETTER_IMAP_PORT` | integer | `993` | IMAP over SSL port |
| `NEWSLETTER_EMAIL_USER` | string | `""` | Your email address |
| `NEWSLETTER_EMAIL_PASSWORD` | string | `""` | App password (not your main password) |
| `NEWSLETTER_SENDER_FILTER` | string | `""` | Only ingest from this sender. Empty = all senders |

---

## Scheduler

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `MARKET_DATA_REFRESH_MINUTES` | integer | `5` | How often to refresh the market snapshot |
| `NEWS_INGESTION_MINUTES` | integer | `60` | How often to ingest RSS/HTML news |
| `WEEKLY_REPORT_DAY` | integer | `6` | Weekday for weekly report: 0=Monday, 6=Sunday |
| `WEEKLY_REPORT_HOUR` | integer | `18` | UTC hour |
| `WEEKLY_REPORT_MINUTE` | integer | `0` | UTC minute |

---

## Reports

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `REPORTS_DIR` | string | `/app/reports` | Directory for PDF reports. Mounted as a Docker volume |

---

## Pi-hole

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `PIHOLE_PASSWORD` | string | — | Pi-hole admin UI password. Required |
| `TZ` | string | `Europe/Lisbon` | Timezone for Pi-hole (affects log timestamps) |

---

## Settings singleton

Settings are loaded once at import time via `@lru_cache(maxsize=1)`:

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

This means `.env` is read once at startup. If you change `.env`, you must restart the
container. The `trading_mode` is an exception — it can be mutated at runtime via the
`set_trading_mode` tool (the mutation is on the cached singleton, not the `.env` file).

`settings.is_development` and `settings.is_production` are `@property` helpers derived
from `environment`, used throughout the codebase.
