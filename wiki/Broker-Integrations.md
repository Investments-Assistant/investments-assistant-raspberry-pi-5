# Broker Integrations

The assistant integrates with four brokerages through their official Python SDKs.
Each broker is independently optional — configure only the ones you use.

---

## Design principles

1. **Lazy imports**: broker SDKs are imported inside the function that needs them, not
   at module load time. This means a missing or unconfigured SDK doesn't break startup.

2. **Defensive error handling**: every broker function wraps its SDK call in a try/except
   and returns `{"error": str(exc)}` on failure. The LLM receives this as a tool result
   and can explain the problem to the user rather than the app crashing.

3. **Lazy client creation**: `_get_client()` is called inside each function rather than
   at module level. This avoids connecting to the broker on startup and means credentials
   are only used when a tool is actually called.

4. **Consistent return shape**: all `get_*_account()` functions return a dict with a
   `"broker"` key so the portfolio aggregator can label results.

---

## Alpaca (`src/tools/brokers/alpaca.py`)

**SDK**: `alpaca-py` (official Alpaca Python SDK v2)

**What it supports**: US stocks, ETFs, fractional shares

**Why Alpaca was chosen**:
- Commission-free trading with a developer-friendly REST API
- Paper trading environment (set `ALPACA_PAPER=true` in `.env`) — lets you test the
  agent's autonomous mode without risking real money
- Fractional shares mean the agent can buy precise USD amounts rather than whole shares
- `alpaca-py` has a clean typed API with `TradingClient`, `MarketOrderRequest`, etc.

**Configuration**
```
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_PAPER=true   # set to false for live trading
```

**Functions**
| Function | What it does |
|---|---|
| `get_alpaca_account()` | Cash, buying power, portfolio value, equity, day-trade count |
| `get_alpaca_positions()` | All open positions: symbol, qty, avg cost, current price, unrealised P&L |
| `get_alpaca_orders(days)` | Order history for the last N days: side, qty, fill price, status |
| `submit_alpaca_order(...)` | Market, limit, or stop-limit orders; returns order ID and status |
| `cancel_alpaca_order(order_id)` | Cancel by order ID |

**Time-in-force**: all orders use `TimeInForce.DAY` — orders expire at market close if
unfilled. This is a conservative default; if the user wants GTC (Good Till Cancelled)
orders, the tool would need a `time_in_force` parameter added.

---

## Interactive Brokers (`src/tools/brokers/ibkr.py`)

**SDK**: `ib_insync` (third-party async wrapper around IB's TWS API)

**What it supports**: stocks, options, futures, forex, bonds — the full IB universe

**Why IBKR was included**: IBKR is the most widely used platform for options trading.
If the user wants to trade options (which appear in `get_options_chain`), they need IBKR
or a similar platform. Alpaca only supports equities.

**Architecture difference vs other brokers**: IBKR does not have a REST API. Instead:
- You run **IB Gateway** or **Trader Workstation (TWS)** on a computer (or in a Docker
  container)
- `ib_insync` connects to it via a TCP socket (`ibkr_host:ibkr_port`)
- Each function connects, executes, then disconnects — a new connection per call

This "connect → do → disconnect" pattern avoids keeping a persistent connection alive,
which IB sometimes drops. The downside is connection latency per call (~1–2 seconds).

**Configuration**
```
IBKR_ENABLED=false          # must be explicitly set to true
IBKR_HOST=127.0.0.1         # or IP of the machine running IB Gateway
IBKR_PORT=4002              # IB Gateway paper port (4002); live is 4001; TWS paper is 7497
IBKR_CLIENT_ID=1
```

**Disabled by default**: `IBKR_ENABLED=false`. If disabled, every function returns
`{"error": "IBKR integration is disabled..."}` without attempting a connection.
This ensures startup doesn't fail if IB Gateway isn't running.

**Functions**
| Function | What it does |
|---|---|
| `get_ibkr_account()` | Net liquidation, cash, buying power, unrealised/realised P&L |
| `get_ibkr_positions()` | All positions with security type, currency, quantity, P&L |
| `get_ibkr_orders()` | Active and recently filled orders |
| `submit_ibkr_order(...)` | Market and limit orders for stocks |
| `cancel_ibkr_order(order_id)` | Cancel by order ID |

**Options trading via IBKR**: the `submit_ibkr_order` implementation only supports
`Stock` contracts. To trade options, you'd need to change `contract = Stock(...)` to
`contract = Option(symbol, expiry, strike, "C"/"P", "SMART")` and call
`ib.qualifyContracts(contract)`. This is a straightforward extension.

---

## Coinbase Advanced Trade (`src/tools/brokers/coinbase.py`)

**SDK**: `coinbase-advanced-py` (official Coinbase Advanced Trade SDK)

**What it supports**: all Coinbase-listed cryptocurrencies

**Why Coinbase**: Coinbase is the most regulated US crypto exchange, making it the default
choice for USD-denominated crypto trading. The Advanced Trade API supports limit and
market orders with proper USD settlement.

**Configuration**

```env
COINBASE_API_KEY=organizations/xxxxx/apiKeys/xxxxx
# The secret is the PEM private key from the CDP portal, all on one line with \n separators:
COINBASE_API_SECRET=<paste PEM key here, replacing newlines with \n>
```

**Important**: Coinbase API keys are now in the **CDP Key format** (not the legacy API
Key + Secret format). Generate them at https://portal.cdp.coinbase.com/.

**Order semantics**
- **Market buy**: uses `quote_size` (USD amount) — you buy "X USD worth of BTC"
- **Market sell**: uses `base_size` (crypto amount) — you sell "X BTC"
- **Limit orders**: always use `base_size` + `limit_price`

The asymmetry between market buy (quote) and market sell (base) is a Coinbase API
quirk. The implementation handles it in `submit_coinbase_order`.

**Functions**
| Function | What it does |
|---|---|
| `get_coinbase_account()` | All non-zero crypto balances (available + held) |
| `get_coinbase_positions()` | Alias for account balances (Coinbase has no "positions" concept) |
| `get_coinbase_orders()` | Last 50 filled orders |
| `submit_coinbase_order(...)` | Market and limit orders |
| `cancel_coinbase_order(order_id)` | Cancel by order ID |

---

## Binance (`src/tools/brokers/binance.py`)

**SDK**: `python-binance` (unofficial but widely-used Binance SDK)

**What it supports**: crypto spot trading (thousands of trading pairs)

**Why Binance**: Binance has the highest liquidity for most crypto pairs and supports many
altcoins not available on Coinbase. It's the go-to exchange for non-USD pairs (BTCUSDT,
ETHUSDT, SOLUSDT, etc.).

**Configuration**
```
BINANCE_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BINANCE_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BINANCE_TESTNET=true   # set to false for live trading
```

**Testnet**: Binance has a full testnet at https://testnet.binance.vision with fake funds.
Always start with `BINANCE_TESTNET=true`.

**Symbol format difference**: Binance uses `BTCUSDT` (no hyphen) while Coinbase and
Yahoo Finance use `BTC-USD`. The tool accepts either — it calls `symbol.upper()` and
passes it directly to Binance. If you provide `BTC-USD` to Binance, the API will return
an error; you must use `BTCUSDT`.

**Order history quirk**: Binance's REST API requires a `symbol` to fetch orders — there's
no "all recent orders" endpoint. The tool defaults to fetching orders for BTCUSDT,
ETHUSDT, SOLUSDT, and BNBUSDT. To get orders for other pairs, pass the symbol explicitly
(the `get_trade_history` tool doesn't expose this yet — it would need a `symbol` parameter).

**Functions**
| Function | What it does |
|---|---|
| `get_binance_account()` | All non-zero balances (free + locked), commission rates |
| `get_binance_positions()` | Alias for non-zero balances |
| `get_binance_orders(symbol)` | Order history (optionally for a specific symbol) |
| `submit_binance_order(...)` | Market and limit orders |
| `cancel_binance_order(order_id, symbol)` | Cancel by order ID and symbol |

---

## Portfolio aggregator (`src/tools/portfolio.py`)

`get_portfolio_summary()` iterates `_BROKER_FUNNELS` — a list of `(name, positions_fn, account_fn)` tuples for all four brokers — and accumulates:
- All positions tagged with their broker name
- Total market value in USD
- Total unrealised P&L in USD

Broker errors are silently skipped (logged at WARNING). This means a disconnected IB
Gateway or expired Coinbase key doesn't prevent the agent from showing Alpaca positions.

---

## Which broker for which asset class?

| Asset class | Recommended broker |
|---|---|
| US stocks and ETFs | Alpaca (commission-free, fractional shares) |
| US options | IBKR (most liquid, best options data) |
| Bitcoin, Ethereum | Coinbase (regulated, USD settlement) |
| Altcoins, DeFi-adjacent | Binance (broadest selection, lowest fees) |
