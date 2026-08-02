"""Forex market data and carry-trade analysis tools using Yahoo Finance."""

from __future__ import annotations

from datetime import UTC, datetime

import yfinance as yf

from src.agent.utils.logger import get_logger

logger = get_logger(__name__)

# Central bank deposit/policy rates — static reference values only.  These are
# intentionally never treated as live trading data; current decisions must be
# corroborated with the central bank's official publication or market feed.
_CENTRAL_BANK_RATES: dict[str, dict] = {
    "USD": {"bank": "Federal Reserve", "rate_pct": 4.25, "currency": "US Dollar"},
    "EUR": {"bank": "ECB", "rate_pct": 3.25, "currency": "Euro"},
    "GBP": {"bank": "Bank of England", "rate_pct": 4.50, "currency": "British Pound"},
    "JPY": {"bank": "Bank of Japan", "rate_pct": 0.25, "currency": "Japanese Yen"},
    "CHF": {"bank": "Swiss National Bank", "rate_pct": 1.00, "currency": "Swiss Franc"},
    "AUD": {"bank": "Reserve Bank of Australia", "rate_pct": 4.10, "currency": "Australian Dollar"},
    "CAD": {"bank": "Bank of Canada", "rate_pct": 3.25, "currency": "Canadian Dollar"},
    "NZD": {
        "bank": "Reserve Bank of New Zealand",
        "rate_pct": 4.25,
        "currency": "New Zealand Dollar",
    },
    "BRL": {
        "bank": "Banco Central do Brasil (Selic)",
        "rate_pct": 13.75,
        "currency": "Brazilian Real",
    },
    "CNY": {"bank": "People's Bank of China", "rate_pct": 3.10, "currency": "Chinese Yuan"},
}

_DEFAULT_PAIRS = [
    "EURUSD=X",
    "GBPUSD=X",
    "USDJPY=X",
    "USDCHF=X",
    "AUDUSD=X",
    "USDCAD=X",
    "NZDUSD=X",
    "EURGBP=X",
    "EURJPY=X",
    "GBPJPY=X",
    "USDBRL=X",
    "EURBRL=X",
]


def _normalize_pair(pair: str) -> str:
    """Convert 'EUR/USD', 'EURUSD', 'eurusd' → 'EURUSD=X'."""
    p = pair.upper().replace("/", "").replace("-", "").replace(" ", "")
    if not p.endswith("=X"):
        p += "=X"
    return p


def _display_pair(yf_symbol: str) -> str:
    """'EURUSD=X' → 'EUR/USD'."""
    base = yf_symbol.replace("=X", "")
    return f"{base[:3]}/{base[3:]}"


def _pip_size(yf_symbol: str) -> float:
    """Return pip size: 0.01 for JPY pairs, 0.0001 for all others."""
    if "JPY" in yf_symbol.upper():
        return 0.01
    return 0.0001


def _df_to_candles(df, max_rows: int = 90) -> list[dict]:
    if df is None or df.empty:
        return []
    df = df.tail(max_rows).copy()
    df.index = df.index.strftime("%Y-%m-%d") if hasattr(df.index, "strftime") else df.index
    return [
        {
            "date": str(date),
            "open": round(float(row.get("Open", 0)), 5),
            "high": round(float(row.get("High", 0)), 5),
            "low": round(float(row.get("Low", 0)), 5),
            "close": round(float(row.get("Close", 0)), 5),
        }
        for date, row in df.iterrows()
    ]


def get_forex_data(
    pairs: list[str],
    period: str = "1mo",
    interval: str = "1d",
) -> dict:
    """Fetch OHLCV history for forex pairs via Yahoo Finance."""
    result: dict[str, object] = {}
    for pair in pairs:
        sym = _normalize_pair(pair)
        display = _display_pair(sym)
        try:
            ticker = yf.Ticker(sym)
            df = ticker.history(period=period, interval=interval)
            info = ticker.info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            result[display] = {
                "yfinance_symbol": sym,
                "current_rate": round(price, 5) if price else None,
                "pip_size": _pip_size(sym),
                "candles": _df_to_candles(df),
            }
        except Exception as exc:
            logger.warning("Failed to fetch forex data for %s: %s", pair, exc)
            result[display] = {"error": str(exc)}
    return result


def get_forex_rates(pairs: list[str] | None = None) -> dict:
    """Snapshot of current spot rates, daily change %, and pip size."""
    targets = [_normalize_pair(p) for p in pairs] if pairs else _DEFAULT_PAIRS
    result: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "rates": {},
    }
    for sym in targets:
        display = _display_pair(sym)
        try:
            info = yf.Ticker(sym).info or {}
            price = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("regularMarketPreviousClose")
            change_pct = None
            if price and prev_close and prev_close != 0:
                change_pct = round((price - prev_close) / prev_close * 100, 4)
            result["rates"][display] = {
                "symbol": sym,
                "rate": round(price, 5) if price else None,
                "change_pct_1d": change_pct,
                "pip_size": _pip_size(sym),
            }
        except Exception as exc:
            logger.warning("Failed to fetch rate for %s: %s", sym, exc)
            result["rates"][display] = {"error": str(exc)}
    return result


def get_central_bank_rates(currencies: list[str] | None = None) -> dict:
    """Return central bank policy rates and carry-trade differentials."""
    targets = [c.upper() for c in currencies] if currencies else list(_CENTRAL_BANK_RATES.keys())
    rates = {c: _CENTRAL_BANK_RATES[c] for c in targets if c in _CENTRAL_BANK_RATES}
    unknown = [c for c in targets if c not in _CENTRAL_BANK_RATES]

    # Top carry-trade pairs sorted by rate differential
    keys = list(rates.keys())
    differentials = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            c1, c2 = keys[i], keys[j]
            diff = rates[c1]["rate_pct"] - rates[c2]["rate_pct"]
            if diff == 0:
                continue
            long_ccy, short_ccy = (c1, c2) if diff > 0 else (c2, c1)
            differentials.append(
                {
                    "pair": f"{long_ccy}/{short_ccy}",
                    "long_currency": long_ccy,
                    "short_currency": short_ccy,
                    "carry_pct": round(abs(diff), 2),
                    "note": (
                        f"Long {long_ccy} ({rates[long_ccy]['rate_pct']}%) / "
                        f"short {short_ccy} ({rates[short_ccy]['rate_pct']}%) "
                        f"≈ {abs(diff):.2f}% annualised carry"
                    ),
                }
            )
    differentials.sort(key=lambda x: x["carry_pct"], reverse=True)

    return {
        "rates": rates,
        "top_carry_trades": differentials[:10],
        "unknown_currencies": unknown,
        "data_note": (
            "Static reference rates are included for educational carry arithmetic only. "
            "They may be stale and must not be used as a trade trigger. Verify each rate "
            "with the central bank's official publication before trading."
        ),
        "data_quality": "static_reference_only",
        "usable_for_auto_trading": False,
        "as_of": "not-live",
    }
