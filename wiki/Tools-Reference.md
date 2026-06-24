# Tools Reference

All 19 agent tools, grouped by category. Tool names are the exact strings the LLM uses.

---

## Market Data

### `get_stock_data`
Fetch OHLCV (Open, High, Low, Close, Volume) candlestick data for one or more stocks or ETFs.

**Source**: Yahoo Finance via `yfinance`

**Parameters**
| Name | Type | Default | Description |
|---|---|---|---|
| `symbols` | `string[]` | required | Ticker symbols, e.g. `["AAPL", "SPY"]` |
| `period` | `string` | `"1mo"` | `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`, `ytd`, `max` |
| `interval` | `string` | `"1d"` | `1m`, `5m`, `15m`, `30m`, `60m`, `1d`, `1wk`, `1mo` |

**Returns**: for each symbol — company name, current price, market cap, P/E ratio, 52-week
high/low, and up to 90 OHLCV candles.

**Implementation note**: `_df_to_records()` caps rows at 90 to keep the LLM context small.
For 1-minute or 5-minute data, 90 candles covers only 1.5 hours — use `1d` or longer
intervals for meaningful analysis.

---

### `get_crypto_data`
Identical to `get_stock_data` but semantically scoped to crypto tickers.

**Source**: Yahoo Finance (crypto prices are available as `BTC-USD`, `ETH-USD`, etc.)

**Note**: Yahoo Finance's crypto data has occasional gaps and stale prices outside US
trading hours. For high-frequency crypto trading, consider a direct exchange API.

---

### `get_market_overview`
Snapshot of major market indicators. No parameters required.

**Returns**: price and day % change for:
- S&P 500, NASDAQ 100, Dow Jones, Russell 2000
- VIX (CBOE Volatility Index — the "fear index")
- 10-year and 2-year US Treasury yields
- Gold, WTI Crude Oil
- Bitcoin, Ethereum
- US Dollar Index (DXY)

**Use case**: the agent calls this first on almost every conversation to orient itself —
"is the market risk-on or risk-off today?"

---

### `get_technical_indicators`
Calculate technical analysis indicators for a single symbol.

**Source**: `ta` (technical-analysis) library, prices from Yahoo Finance

**Parameters**
| Name | Type | Default | Description |
|---|---|---|---|
| `symbol` | `string` | required | Any ticker, e.g. `"AAPL"` or `"BTC-USD"` |
| `period` | `string` | `"6mo"` | Lookback for calculations: `3mo`, `6mo`, `1y` |

**Returns**:
| Indicator | Config | Signal generated |
|---|---|---|
| RSI | 14-period | `RSI oversold (bullish signal)` if < 30; `RSI overbought (bearish signal)` if > 70 |
| MACD | default (12, 26, 9) | `MACD bullish crossover` if MACD > signal line |
| Bollinger Bands | 20-period, 2σ | `Price above upper BB (overextended)` or `below lower BB (oversold)` |
| EMA 20 / 50 / 200 | — | `Price above 200 EMA (uptrend)` |
| ATR | 14-period | Raw value in price units (used to size stop-losses) |
| OBV | — | Raw value (trend direction via cumulative volume) |

The `_build_signals()` helper converts raw numbers to plain-English signal strings, which
the LLM can include directly in its analysis without doing arithmetic.

**Why `ta` library?** `ta` (Technical Analysis Library in Python) is a thin wrapper
around pandas that provides clean APIs for all standard indicators. It was chosen over
`TA-Lib` (which requires a C binary) for simpler Docker builds.

---

### `get_options_chain`
Fetch calls and puts for a stock symbol.

**Source**: Yahoo Finance (`yfinance.Ticker.option_chain()`)

**Parameters**
| Name | Type | Default | Description |
|---|---|---|---|
| `symbol` | `string` | required | Underlying stock ticker |
| `expiry` | `string` | optional | `YYYY-MM-DD`. If omitted, returns next 3 expiries |

**Returns**: for each expiry — up to 20 nearest-the-money calls and puts with:
strike, bid, ask, last price, implied volatility, open interest, delta, gamma.

**Implementation note**: `_clean_option_list()` replaces NaN values (common in options
data for illiquid strikes) with `null` and unwraps numpy scalars to plain Python types
so JSON serialisation doesn't fail.

**Limitation**: Yahoo Finance's free options data has a ~15-minute delay. For live
options trading, use the IBKR integration which has real-time data (requires TWS).

---

### `search_ticker`
Resolve a company name or keyword to ticker symbols.

**Source**: `yfinance.Search()`

**Parameters**: `query` (string) — e.g. `"Apple"`, `"semiconductor ETF"`

**Returns**: up to 10 results with symbol, name, instrument type, and exchange.

---

## News and Sentiment

### `search_market_news`
Fetch recent articles and compute sentiment.

**Sources**: NewsAPI (if `NEWSAPI_KEY` is set) → RSS feeds fallback

**Parameters**
| Name | Type | Default | Description |
|---|---|---|---|
| `query` | `string` | required | Search terms, e.g. `"Apple earnings"` |
| `max_articles` | `integer` | `10` | 1–20 |
| `sources` | `string[]` | optional | Filter by source name, e.g. `["reuters"]` |

**Sentiment algorithm**: keyword intersection against two word sets:
- Positive: surge, rally, gain, rise, soar, boom, bull, beat, record, growth, profit, upgrade, outperform, recovery...
- Negative: fall, drop, crash, plunge, decline, loss, bear, miss, recession, downgrade, risk, fear, inflation, bankruptcy...

Score = (positive_words - negative_words) / total_sentiment_words.
`bullish` if score > 0.15; `bearish` if score < -0.15; `neutral` otherwise.

**Why this over a proper NLP model?** Running a sentiment model (e.g. FinBERT) on the Pi
would use extra RAM and add significant latency. The keyword approach is fast (microseconds)
and good enough for "is this article broadly positive or negative about the market?" The
LLM itself provides nuanced sentiment analysis in its response.

**Fallback chain**: if NewsAPI returns no results (no key, rate limit, query too narrow),
the function falls back to parsing RSS feeds from Reuters, CNBC, MarketWatch, Seeking
Alpha, Yahoo Finance, Coindesk, and CryptoNews.

---

### `get_earnings_calendar`
Upcoming earnings announcements.

**Source**: Yahoo Finance (`Ticker.calendar`)

**Parameters**
| Name | Type | Default | Description |
|---|---|---|---|
| `days_ahead` | `integer` | `7` | 1–30 |
| `symbols` | `string[]` | optional | Specific tickers to check |

**Limitation**: Yahoo Finance doesn't provide a full market-wide earnings calendar on
the free tier. If `symbols` is empty, the tool returns a note explaining this. Providing
specific symbols (e.g. `["AAPL", "MSFT", "NVDA"]`) works reliably.

---

### `search_stored_news`
Full-text search over the persistent news memory database.

**Source**: PostgreSQL FTS (`plainto_tsquery`)

**Parameters**
| Name | Type | Default | Description |
|---|---|---|---|
| `query` | `string` | required | Natural language, e.g. `"ECB rate decision"` |
| `days_back` | `integer` | `30` | 0 = all history |
| `sources` | `string[]` | optional | Filter by source name |
| `sentiment` | `string` | optional | `bullish`, `bearish`, or `neutral` |
| `limit` | `integer` | `20` | 1–100 |

**How FTS works**: the query goes through `plainto_tsquery('english', query)` which:
1. Tokenises the query into lexemes
2. Applies English stemming (investing → invest, rates → rate)
3. Removes stop words (the, a, of...)
4. Adds implicit AND between tokens

Results are ranked by `ts_rank(ts_vector, ts_query)` — articles where the query terms
appear more frequently and in important positions (title > summary > content) rank higher.

**Use case**: "search for news about ECB from last week" or "find all articles that
mention NVDA and are bearish" — enables historical context that `search_market_news`
(live fetch) cannot provide.

---

### `get_latest_news`
Most recently ingested headlines from all sources.

**Parameters**: `limit` (integer, default 20, max 50)

**Returns**: title, source, URL, published_at, sentiment for each article.

**Use case**: quick "what's happening right now?" scan before a deeper search.

---

## Portfolio

### `get_portfolio_summary`
Aggregated positions and account info across all connected brokers.

**Parameters**: `broker` (optional) — filter to one broker: `alpaca`, `ibkr`, `coinbase`,
`binance`

**Returns**: all positions (symbol, qty, avg cost, current price, unrealised P&L),
all account balances, and total portfolio market value + unrealised P&L in USD.

**Market price enrichment**: `_enrich_position()` calls `yf.Ticker(symbol).info` for any
position missing a `current_price`. This ensures positions from brokers that don't return
live prices (e.g. IBKR with a stale connection) still show current values.

---

### `get_account_info`
Cash balance, buying power, and equity for a single broker.

**Parameters**: `broker` (required) — `alpaca`, `ibkr`, `coinbase`, or `binance`

---

### `get_trade_history`
Recent orders/fills from a broker.

**Parameters**: `broker` (required), `days` (default 30)

**Note**: Binance requires a symbol to fetch orders — if no symbol is provided, it queries
`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, and `BNBUSDT` as defaults.

---

## Trade Execution

### `execute_trade`
Place a buy or sell order.

**Parameters**
| Name | Type | Required | Description |
|---|---|---|---|
| `broker` | `string` | yes | `alpaca`, `ibkr`, `coinbase`, `binance` |
| `symbol` | `string` | yes | Ticker, e.g. `"AAPL"`, `"BTC-USD"`, `"BTCUSDT"` |
| `side` | `string` | yes | `buy` or `sell` |
| `quantity` | `number` | yes | Shares or coins. Fractional supported on Alpaca |
| `order_type` | `string` | no | `market` (default), `limit`, `stop_limit` |
| `limit_price` | `number` | if limit | — |
| `stop_price` | `number` | if stop_limit | — |
| `reason` | `string` | yes | Mandatory: why this trade is being placed |

**Behaviour**:

- `RECOMMEND` mode: returns `{"status": "pending_confirmation", "message": "...", "trade_details": {...}}`.
  No broker is contacted. The user reads the recommendation and says "confirm trade" or
  "cancel trade". The LLM then calls `confirm_trade` with the `trade_details` object.
- `AUTO` mode: checks the symbol allowlist, then checks the daily loss-limit halt flag,
  then routes to the correct broker SDK, then persists the trade with `mode="auto"`.

The `reason` field is mandatory. This is intentional: it forces the LLM to document its
thesis before executing, creating an audit trail in the `trades` table.

---

### `confirm_trade`
Execute a trade that was previously recommended and is awaiting user confirmation.

**Parameters** — identical to `execute_trade` (pass `trade_details` from the
`pending_confirmation` response directly):

| Name | Type | Required | Description |
|---|---|---|---|
| `broker` | `string` | yes | `alpaca`, `ibkr`, `coinbase`, `binance` |
| `symbol` | `string` | yes | Ticker |
| `side` | `string` | yes | `buy` or `sell` |
| `quantity` | `number` | yes | Shares or coins |
| `order_type` | `string` | no | `market` (default), `limit`, `stop_limit` |
| `limit_price` | `number` | if limit | — |
| `stop_price` | `number` | if stop_limit | — |
| `reason` | `string` | no | Original reasoning (carried forward from the recommendation) |

**Behaviour**: routes directly to `_route_order()` regardless of `trading_mode`. No mode
guard and no symbol-allowlist check — the user has explicitly approved the trade, so
allowlist restrictions are bypassed. The trade is persisted with `mode="manual"`, which
distinguishes user-confirmed trades from autonomous auto-mode trades in the `trades` table.

The daily loss-limit halt flag is **not** checked for user-confirmed trades — the halt
is intended to stop autonomous action, not informed user decisions.

---

### `cancel_order`
Cancel an open order.

**Parameters**: `broker` (required), `order_id` (required — the broker's order ID string)

---

## Analysis and Control

### `run_simulation`
Backtest a trading strategy on historical data.

**Parameters**
| Name | Type | Description |
|---|---|---|
| `name` | `string` | Descriptive name for the simulation |
| `symbols` | `string[]` | Tickers to trade |
| `strategy` | `object` | `{"type": "...", "params": {...}}` |
| `initial_capital` | `number` | Starting USD (default 10,000) |
| `period_start` | `string` | `YYYY-MM-DD` |
| `period_end` | `string` | `YYYY-MM-DD` (default: today) |

**Supported strategies**

| Type | Params | Description |
|---|---|---|
| `buy_and_hold` | — | Equal-weight buy on day 1, hold to end |
| `sma_crossover` | `fast` (default 20), `slow` (default 50) | Buy on fast > slow crossover, sell on crossunder |
| `rsi_mean_reversion` | `rsi_buy` (default 30), `rsi_sell` (default 70) | Buy when RSI oversold, sell when overbought |

**Metrics returned**:

- `total_return_pct`
- `sharpe_ratio` — annualised (252-day factor), not risk-free-rate adjusted
- `max_drawdown_pct`
- `annual_volatility_pct`
- `equity_curve` — weekly resampled to keep context small
- `trades_sample` — first 20 individual trades
- `simulation_id` — UUID of the persisted `simulation_results` row

**Persistence**: every successful simulation is written to the `simulation_results` table
via `_run_simulation_and_persist()` in the dispatcher. The sync `run_simulation()` function
in `simulator.py` is kept pure (no DB calls); the dispatcher async wrapper handles the
`async with async_session()` write after the backtest completes.

---

### `set_trading_mode`
Switch between `recommend` and `auto` modes at runtime.

**Parameters**: `mode` — `"recommend"` or `"auto"`

**Implementation**: mutates `settings.trading_mode` on the in-process singleton.
The change is not persisted across restarts — to change the default permanently, update
`TRADING_MODE` in `.env`.

---

### `generate_report`
Generate a comprehensive investment report for a time period.

**Parameters**: `period_start` (`YYYY-MM-DD`), `period_end` (optional, defaults to today)

**What it does**:
1. Creates a fresh LLM client call with `WEEKLY_REPORT_PROMPT`
2. The LLM uses available tools to fetch portfolio data, trades, market performance, news
3. The resulting markdown is converted to HTML via `_markdown_to_html()`
4. `weasyprint` renders the HTML to a PDF saved in `/app/reports/`
5. The report is persisted to the `reports` table

**PDF dependencies**: `weasyprint` requires `pango`, `cairo`, and `gdk-pixbuf` system
libraries, which are included in the `Dockerfile`. PDF generation is best-effort — if
`weasyprint` fails (e.g. missing fonts), the HTML report is still saved to the database.
