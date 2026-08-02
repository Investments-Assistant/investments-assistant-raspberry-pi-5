"""Claude tool definitions (JSON schemas) for all agent tools."""

TOOL_DEFINITIONS = [
    # ── Market Data ────────────────────────────────────────────────────────────
    {
        "name": "get_stock_data",
        "description": (
            "Fetch OHLCV price data for one or more stocks or ETFs from Yahoo Finance. "
            "Returns open, high, low, close, volume for each candle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of ticker symbols, e.g. ['AAPL', 'SPY', 'QQQ']",
                },
                "period": {
                    "type": "string",
                    "description": "Data period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max",
                    "default": "1mo",
                },
                "interval": {
                    "type": "string",
                    "description": "Candle interval: 1m, 5m, 15m, 30m, 60m, 1d, 1wk, 1mo",
                    "default": "1d",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "get_crypto_data",
        "description": (
            "Fetch OHLCV price data for one or more cryptocurrencies from Yahoo Finance. "
            "Use symbols like BTC-USD, ETH-USD, SOL-USD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Crypto ticker symbols, e.g. ['BTC-USD', 'ETH-USD']",
                },
                "period": {
                    "type": "string",
                    "description": "Data period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y",
                    "default": "1mo",
                },
                "interval": {
                    "type": "string",
                    "description": "Candle interval: 1h, 1d, 1wk",
                    "default": "1d",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "assess_nft_risk",
        "description": (
            "Assess NFT collection liquidity, concentration, and volatility risks from "
            "caller-supplied metrics. This is evidence-only: it does not fetch marketplace "
            "data, verify authenticity, value an NFT, or place NFT orders."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "collection": {"type": "string", "description": "Collection name"},
                "floor_price_usd": {"type": "number", "minimum": 0},
                "floor_change_7d_pct": {"type": "number"},
                "volume_7d_usd": {"type": "number", "minimum": 0},
                "sales_7d": {"type": "integer", "minimum": 0},
                "holders": {"type": "integer", "minimum": 0},
                "top_holder_pct": {"type": "number", "minimum": 0},
                "bid_ask_spread_pct": {"type": "number", "minimum": 0},
            },
            "required": ["collection"],
        },
    },
    # ── Forex ──────────────────────────────────────────────────────────────────
    {
        "name": "get_forex_data",
        "description": (
            "Fetch OHLCV price history for forex (FX) currency pairs from Yahoo Finance. "
            "Accepts pairs in any format: 'EUR/USD', 'EURUSD', 'eurusd'. "
            "Returns candles and current spot rate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pairs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Currency pairs, e.g. ['EUR/USD', 'GBP/USD', 'USD/JPY']",
                },
                "period": {
                    "type": "string",
                    "description": "Data period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y",
                    "default": "1mo",
                },
                "interval": {
                    "type": "string",
                    "description": "Candle interval: 1h, 1d, 1wk, 1mo",
                    "default": "1d",
                },
            },
            "required": ["pairs"],
        },
    },
    {
        "name": "get_forex_rates",
        "description": (
            "Get current spot rates, daily % change, and pip size for forex pairs. "
            "If no pairs are supplied, returns a snapshot of all major pairs including "
            "EUR/USD, GBP/USD, USD/JPY, USD/BRL, EUR/BRL and more."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pairs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Currency pairs to query. If empty, returns all major pairs.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_central_bank_rates",
        "description": (
            "Return static reference central bank rates and compute indicative carry "
            "differentials. The result is educational only, may be stale, and must never "
            "be used as an automatic trade trigger without live official confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "currencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ISO currency codes, e.g. ['USD', 'EUR', 'JPY']. "
                    "If empty, returns all supported currencies.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_market_overview",
        "description": (
            "Get a snapshot of major market indices, VIX (fear index), "
            "treasury yields, and commodity prices."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_technical_indicators",
        "description": (
            "Calculate technical analysis indicators for a symbol. "
            "Returns RSI, MACD, Bollinger Bands, EMA(20/50/200), ATR, OBV."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Ticker symbol, e.g. 'AAPL' or 'BTC-USD'",
                },
                "period": {
                    "type": "string",
                    "description": "Lookback period for the calculation: 3mo, 6mo, 1y",
                    "default": "6mo",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_options_chain",
        "description": (
            "Fetch the options chain for a stock symbol (calls and puts). "
            "Returns strikes, expiries, bid/ask, implied volatility, open interest, delta, gamma."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Underlying stock ticker, e.g. 'AAPL'",
                },
                "expiry": {
                    "type": "string",
                    "description": "Expiry date in YYYY-MM-DD format. If omitted, \
                        returns next 3 expiries.",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "search_ticker",
        "description": "Search for a ticker symbol by company name or keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Company name or keyword, e.g. 'Apple' or 'semiconductor ETF'",
                },
            },
            "required": ["query"],
        },
    },
    # ── News & Sentiment ───────────────────────────────────────────────────────
    {
        "name": "search_market_news",
        "description": (
            "Fetch recent financial news articles for a topic or symbol and analyse sentiment. "
            "Returns article titles, summaries, sources, publication dates, and sentiment scores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'Apple earnings', 'Bitcoin ETF', \
                        'Fed rate decision'",
                },
                "max_articles": {
                    "type": "integer",
                    "description": "Maximum number of articles to return (1–20)",
                    "default": 10,
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of news sources to filter by, \
                        e.g. ['reuters', 'bloomberg']",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_earnings_calendar",
        "description": "Get upcoming earnings announcements for the next N days.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days ahead to look (1–30)",
                    "default": 7,
                },
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: filter by specific symbols. If empty, return all.",
                },
            },
            "required": [],
        },
    },
    # ── Portfolio ──────────────────────────────────────────────────────────────
    {
        "name": "get_portfolio_summary",
        "description": (
            "Get the current portfolio summary across all connected brokers: "
            "positions, quantities, average cost, current value, unrealised P&L."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "broker": {
                    "type": "string",
                    "description": "Filter by broker: alpaca, ibkr, coinbase, binance. If omitted, \
                        returns all.",
                },
                "account_id": {
                    "type": "string",
                    "description": (
                        "Optional account ID from the authenticated user's Brokerage Accounts. "
                        "Required when multiple accounts match."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_account_info",
        "description": "Get account balance, buying power, and cash available for a broker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "broker": {
                    "type": "string",
                    "enum": ["alpaca", "ibkr", "coinbase", "binance"],
                    "description": "Which broker account to query",
                },
                "account_id": {
                    "type": "string",
                    "description": (
                        "Optional account ID; required when the user has multiple "
                        "matching accounts."
                    ),
                },
            },
            "required": ["broker"],
        },
    },
    {
        "name": "get_trade_history",
        "description": "Retrieve recent trade history from a broker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "broker": {
                    "type": "string",
                    "enum": ["alpaca", "ibkr", "coinbase", "binance"],
                },
                "account_id": {
                    "type": "string",
                    "description": (
                        "Optional account ID; required when the user has multiple "
                        "matching accounts."
                    ),
                },
                "days": {
                    "type": "integer",
                    "description": "How many days back to fetch",
                    "default": 30,
                },
            },
            "required": ["broker"],
        },
    },
    # ── Trade Execution ────────────────────────────────────────────────────────
    {
        "name": "execute_trade",
        "description": (
            "Execute a buy or sell order via a brokerage. "
            "In RECOMMEND mode this creates a pending recommendation requiring user confirmation. "
            "In AUTO mode this submits the order directly. "
            "For forex pairs (e.g. 'EUR/USD', 'GBPUSD') and options use broker='ibkr'. "
            "Option orders require asset_type='option', option_expiry, option_strike, "
            "and option_right."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "broker": {
                    "type": "string",
                    "enum": ["alpaca", "ibkr", "coinbase", "binance"],
                    "description": (
                        "Which broker to route the order through. "
                        "Use ibkr for stocks, options, and forex pairs."
                    ),
                },
                "account_id": {
                    "type": "string",
                    "description": (
                        "Optional account ID from Brokerage Accounts. Required when multiple "
                        "accounts exist for the selected broker."
                    ),
                },
                "symbol": {
                    "type": "string",
                    "description": (
                        "Ticker or trading pair, e.g. 'AAPL', 'BTC-USD', 'BTCUSDT', "
                        "'EUR/USD', 'GBPUSD'"
                    ),
                },
                "asset_type": {
                    "type": "string",
                    "enum": ["stock", "etf", "option", "forex", "crypto"],
                    "description": (
                        "Asset class. Defaults to stock; forex is inferred for six-letter "
                        "IBKR pairs. "
                        "Use option for an IBKR option contract."
                    ),
                },
                "option_expiry": {
                    "type": "string",
                    "description": "Option expiration in YYYY-MM-DD format (IBKR only).",
                },
                "option_strike": {
                    "type": "number",
                    "description": "Option strike price (IBKR only).",
                },
                "option_right": {
                    "type": "string",
                    "enum": ["C", "P"],
                    "description": "Option right: C for call or P for put (IBKR only).",
                },
                "side": {
                    "type": "string",
                    "enum": ["buy", "sell"],
                },
                "quantity": {
                    "type": "number",
                    "description": "Number of shares / coins. For fractional shares use decimals.",
                },
                "order_type": {
                    "type": "string",
                    "enum": ["market", "limit", "stop_limit"],
                    "default": "market",
                },
                "limit_price": {
                    "type": "number",
                    "description": "Limit price (required for limit / stop_limit orders)",
                },
                "stop_price": {
                    "type": "number",
                    "description": "Stop trigger price (required for stop_limit orders)",
                },
                "reason": {
                    "type": "string",
                    "description": "Mandatory: explain WHY this trade is being placed",
                },
                "estimated_notional_usd": {
                    "type": "number",
                    "description": (
                        "Estimated USD value of the order. Required for bounded auto mode "
                        "when a limit price is not available."
                    ),
                },
            },
            "required": ["broker", "symbol", "side", "quantity", "reason"],
        },
    },
    {
        "name": "cancel_order",
        "description": "Cancel an open order at a broker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "broker": {
                    "type": "string",
                    "enum": ["alpaca", "ibkr", "coinbase", "binance"],
                },
                "account_id": {
                    "type": "string",
                    "description": "Optional account ID; required when multiple accounts match.",
                },
                "order_id": {
                    "type": "string",
                    "description": "The broker order ID to cancel",
                },
            },
            "required": ["broker", "order_id"],
        },
    },
    {
        "name": "confirm_trade",
        "description": (
            "Execute a trade that was previously recommended and is pending user confirmation. "
            "Call this ONLY after the user explicitly approves the recommendation and provides "
            "the confirmation_id returned by execute_trade. The server ignores arbitrary trade "
            "parameters and uses the short-lived proposal it created."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmation_id": {
                    "type": "string",
                    "description": "Short-lived server-generated ID from execute_trade",
                },
            },
            "required": ["confirmation_id"],
        },
    },
    # ── Simulation ─────────────────────────────────────────────────────────────
    {
        "name": "run_simulation",
        "description": (
            "Run a backtested investment simulation using historical data. "
            "Returns equity curve, total return, Sharpe ratio, max drawdown, and trade list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "A descriptive name for this simulation",
                },
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Symbols to include in the simulation",
                },
                "strategy": {
                    "type": "object",
                    "description": (
                        "Strategy parameters. Supported types: "
                        "'buy_and_hold', 'sma_crossover' (params: fast, slow), "
                        "'rsi_mean_reversion' (params: rsi_buy, rsi_sell), or "
                        "'momentum' (params: lookback_days, top_n)."
                    ),
                    "properties": {
                        "type": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["type"],
                },
                "initial_capital": {
                    "type": "number",
                    "description": "Starting capital in USD",
                    "default": 10000,
                },
                "period_start": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD",
                },
                "period_end": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (defaults to today)",
                },
            },
            "required": ["name", "symbols", "strategy", "period_start"],
        },
    },
    # ── Agent Control ──────────────────────────────────────────────────────────
    {
        "name": "set_trading_mode",
        "description": "Switch the agent between recommend and auto trading modes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["recommend", "auto"],
                    "description": "'recommend' = agent proposes, user confirms; \
                        'auto' = agent executes automatically",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "generate_report",
        "description": (
            "Generate a comprehensive investment report for a given time period. "
            "Returns a structured HTML report and saves a PDF."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_start": {
                    "type": "string",
                    "description": "Start date YYYY-MM-DD",
                },
                "period_end": {
                    "type": "string",
                    "description": "End date YYYY-MM-DD (defaults to today)",
                },
            },
            "required": ["period_start"],
        },
    },
    # ── News Memory ────────────────────────────────────────────────────────────
    {
        "name": "search_stored_news",
        "description": (
            "Search the persistent news memory for articles matching a query. "
            "The memory is continuously updated from Reuters, The Guardian, CNBC, "
            "Financial Times, The Economist, ECB, Portuguese sources (Jornal de Negócios, "
            "Dinheiro Vivo, ECO), crypto news, and your email newsletters. "
            "Use this to recall past events, track a story over time, or find coverage "
            "of a specific company, macro theme, or geopolitical event."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search, \
                        e.g. 'ECB rate decision Portugal economy'",
                },
                "days_back": {
                    "type": "integer",
                    "description": "How many days back to search (0 = all history)",
                    "default": 30,
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these sources, e.g. ['The Guardian', 'Reuters']",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["bullish", "bearish", "neutral"],
                    "description": "Filter by article sentiment",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max articles to return (1–100)",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_latest_news",
        "description": (
            "Return the most recently ingested news headlines from all sources. "
            "Use this for a quick 'what happened today' overview before a deeper search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of headlines to return (1–50)",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
]


def to_openai_tools(definitions: list[dict]) -> list[dict]:
    """Convert tool definitions from Claude input_schema format to OpenAI function calling format.

    Claude:  {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:  {"type": "function", "function": {"name": ..., "description": ..., \
        "parameters": {...}}}
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in definitions
    ]
