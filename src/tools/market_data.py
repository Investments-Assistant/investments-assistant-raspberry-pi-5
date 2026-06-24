"""Market data tools: stocks, ETFs, crypto, options, technical indicators."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pandas as pd
import ta
import yfinance as yf

from src.agent.utils.logger import get_logger

logger = get_logger(__name__)

# ── OHLCV Data ─────────────────────────────────────────────────────────────────


def _df_to_records(df: pd.DataFrame, max_rows: int = 90) -> list[dict]:
    """Convert a OHLCV DataFrame to a JSON-serialisable list of dicts."""
    if df.empty:
        return []
    df = df.tail(max_rows).copy()
    df.index = df.index.strftime("%Y-%m-%d") if hasattr(df.index, "strftime") else df.index
    records = []
    for date, row in df.iterrows():
        records.append(
            {
                "date": str(date),
                "open": round(float(row.get("Open", 0)), 4),
                "high": round(float(row.get("High", 0)), 4),
                "low": round(float(row.get("Low", 0)), 4),
                "close": round(float(row.get("Close", 0)), 4),
                "volume": int(row.get("Volume", 0)),
            }
        )
    return records


def get_stock_data(
    symbols: list[str],
    period: str = "1mo",
    interval: str = "1d",
) -> dict:
    result: dict[str, object] = {}
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period=period, interval=interval)
            info = ticker.info or {}
            result[sym] = {
                "company_name": info.get("longName", sym),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "52w_high": info.get("fiftyTwoWeekHigh"),
                "52w_low": info.get("fiftyTwoWeekLow"),
                "candles": _df_to_records(df),
            }
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", sym, exc)
            result[sym] = {"error": str(exc)}
    return result


def get_crypto_data(
    symbols: list[str],
    period: str = "1mo",
    interval: str = "1d",
) -> dict:
    # Crypto uses same Yahoo Finance interface
    return get_stock_data(symbols, period=period, interval=interval)


def get_market_overview() -> dict:
    """Snapshot of major indices, VIX, bonds, commodities."""
    tickers = {
        "S&P 500": "^GSPC",
        "NASDAQ 100": "^NDX",
        "Dow Jones": "^DJI",
        "Russell 2000": "^RUT",
        "VIX (Fear Index)": "^VIX",
        "10Y Treasury Yield": "^TNX",
        "2Y Treasury Yield": "^IRX",
        "Gold": "GC=F",
        "Crude Oil (WTI)": "CL=F",
        "Bitcoin": "BTC-USD",
        "Ethereum": "ETH-USD",
        "Dollar Index": "DX-Y.NYB",
    }
    result: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "markets": {},
    }
    for name, sym in tickers.items():
        try:
            ticker = yf.Ticker(sym)
            info = ticker.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("regularMarketPreviousClose")
            change_pct = None
            if price and prev_close and prev_close != 0:
                change_pct = round((price - prev_close) / prev_close * 100, 2)
            result["markets"][name] = {
                "symbol": sym,
                "price": price,
                "change_pct": change_pct,
            }
        except Exception as exc:
            result["markets"][name] = {"error": str(exc)}
    return result


# ── Technical Indicators ───────────────────────────────────────────────────────


def _build_signals(
    rsi: float,
    macd_val: float,
    macd_signal: float,
    current_price: float,
    ema200: float | None,
    bb_upper: float,
    bb_lower: float,
) -> list[str]:
    signals: list[str] = []
    if rsi < 30:
        signals.append("RSI oversold (bullish signal)")
    elif rsi > 70:
        signals.append("RSI overbought (bearish signal)")
    if macd_val > macd_signal:
        signals.append("MACD bullish crossover")
    else:
        signals.append("MACD bearish crossover")
    if ema200 is not None and current_price > ema200:
        signals.append("Price above 200 EMA (uptrend)")
    else:
        signals.append("Price below 200 EMA (downtrend)")
    if current_price > bb_upper:
        signals.append("Price above upper Bollinger Band (overextended)")
    elif current_price < bb_lower:
        signals.append("Price below lower Bollinger Band (oversold)")
    return signals


def get_technical_indicators(symbol: str, period: str = "6mo") -> dict:
    """Calculate RSI, MACD, Bollinger Bands, EMA 20/50/200, ATR, OBV."""
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d")
        if df.empty or len(df) < 20:
            return {"error": f"Insufficient data for {symbol}"}

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        # RSI (14)
        rsi_indicator = ta.momentum.RSIIndicator(close=close, window=14)
        rsi = rsi_indicator.rsi().iloc[-1]

        # MACD
        macd_ind = ta.trend.MACD(close=close)
        macd_val = macd_ind.macd().iloc[-1]
        macd_signal = macd_ind.macd_signal().iloc[-1]
        macd_hist = macd_ind.macd_diff().iloc[-1]

        # Bollinger Bands (20, 2σ)
        bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        bb_upper = bb.bollinger_hband().iloc[-1]
        bb_middle = bb.bollinger_mavg().iloc[-1]
        bb_lower = bb.bollinger_lband().iloc[-1]

        # EMAs
        ema20 = ta.trend.EMAIndicator(close=close, window=20).ema_indicator().iloc[-1]
        ema50 = ta.trend.EMAIndicator(close=close, window=50).ema_indicator().iloc[-1]
        ema200 = (
            ta.trend.EMAIndicator(close=close, window=200).ema_indicator().iloc[-1]
            if len(df) >= 200
            else None
        )

        # ATR (14)
        atr = (
            ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14)
            .average_true_range()
            .iloc[-1]
        )

        # OBV
        obv = (
            ta.volume.OnBalanceVolumeIndicator(close=close, volume=volume)
            .on_balance_volume()
            .iloc[-1]
        )

        current_price = float(close.iloc[-1])

        def _r(v: float | None, digits: int = 4) -> float | None:
            return round(float(v), digits) if v is not None and v == v else None

        signals = _build_signals(
            rsi, macd_val, macd_signal, current_price, ema200, bb_upper, bb_lower
        )

        return {
            "symbol": symbol,
            "current_price": _r(current_price),
            "rsi_14": _r(rsi, 2),
            "macd": {
                "macd": _r(macd_val),
                "signal": _r(macd_signal),
                "histogram": _r(macd_hist),
            },
            "bollinger_bands": {
                "upper": _r(bb_upper),
                "middle": _r(bb_middle),
                "lower": _r(bb_lower),
            },
            "ema": {
                "ema_20": _r(ema20),
                "ema_50": _r(ema50),
                "ema_200": _r(ema200),
            },
            "atr_14": _r(atr),
            "obv": _r(obv, 0),
            "signals": signals,
        }
    except Exception as exc:
        logger.exception("Technical indicators failed for %s", symbol)
        return {"error": str(exc)}


# ── Options Chain ──────────────────────────────────────────────────────────────

_OPT_COLUMNS = [
    "strike",
    "bid",
    "ask",
    "lastPrice",
    "impliedVolatility",
    "openInterest",
    "delta",
    "gamma",
]


def _option_rows(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return df[_OPT_COLUMNS].head(20).to_dict("records")


def _clean_option_list(lst: list[dict]) -> list[dict]:
    """Replace NaN values and unwrap numpy scalars in an options row list."""
    for item in lst:
        for k, v in item.items():
            with contextlib.suppress(Exception):
                if pd.isna(v):
                    item[k] = None
                elif hasattr(v, "item"):
                    item[k] = v.item()
    return lst


def get_options_chain(symbol: str, expiry: str | None = None) -> dict:
    """Fetch options chain for a stock symbol."""
    try:
        ticker = yf.Ticker(symbol)
        exps = ticker.options  # available expiry dates
        if not exps:
            return {"error": f"No options available for {symbol}"}

        if expiry and expiry in exps:
            target_exps = [expiry]
        else:
            target_exps = list(exps[:3])  # next 3 expiries

        result: dict[str, object] = {"symbol": symbol, "expiries": {}}
        for exp in target_exps:
            opt = ticker.option_chain(exp)
            result["expiries"][exp] = {
                "calls": _clean_option_list(_option_rows(opt.calls)),
                "puts": _clean_option_list(_option_rows(opt.puts)),
            }
        return result
    except Exception as exc:
        logger.exception("Options chain failed for %s", symbol)
        return {"error": str(exc)}


# ── Ticker Search ──────────────────────────────────────────────────────────────


def search_ticker(query: str) -> dict:
    """Search Yahoo Finance for matching ticker symbols."""
    try:
        # yfinance doesn't have a search API; use a simple approach
        results = yf.Search(query, max_results=10)
        quotes = results.quotes if hasattr(results, "quotes") else []
        return {
            "query": query,
            "results": [
                {
                    "symbol": q.get("symbol"),
                    "name": q.get("longname") or q.get("shortname"),
                    "type": q.get("quoteType"),
                    "exchange": q.get("exchange"),
                }
                for q in quotes
            ],
        }
    except Exception as exc:
        return {"error": str(exc), "query": query}


# ── Earnings Calendar ──────────────────────────────────────────────────────────


def get_earnings_calendar(days_ahead: int = 7, symbols: list[str] | None = None) -> dict:
    """Return upcoming earnings via yfinance (best-effort)."""
    result: dict[str, object] = {"days_ahead": days_ahead, "earnings": []}
    targets = symbols if symbols else []
    if not targets:
        # Return a note — full calendar requires premium data
        result["note"] = (
            "Full earnings calendar requires a NewsAPI or premium data subscription. "
            "Provide specific symbols for individual earnings dates."
        )
    for sym in targets:
        try:
            cal = yf.Ticker(sym).calendar
            if cal is not None and not cal.empty:
                for col in cal.columns:
                    result["earnings"].append(
                        {
                            "symbol": sym,
                            "date": str(col),
                            "earnings_date": str(cal[col].get("Earnings Date", "")),
                            "eps_estimate": cal[col].get("EPS Estimate"),
                            "revenue_estimate": cal[col].get("Revenue Estimate"),
                        }
                    )
        except Exception as exc:
            result["earnings"].append({"symbol": sym, "error": str(exc)})  # type: ignore[attr-defined]
    return result
