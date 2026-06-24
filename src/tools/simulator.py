"""Investment simulator and backtester.

Supported strategies:
- buy_and_hold: buy on day 1, hold to end
- sma_crossover: buy when fast SMA crosses above slow SMA, sell on crossunder
- rsi_mean_reversion: buy when RSI < rsi_buy, sell when RSI > rsi_sell
- momentum: buy top N performers over lookback window, rebalance monthly
"""

from __future__ import annotations

from datetime import UTC, datetime
import math

import pandas as pd
import yfinance as yf

from src.agent.utils.logger import get_logger

logger = get_logger(__name__)


def _download(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """Download adjusted close prices for symbols."""
    data = yf.download(symbols, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        return data["Close"].dropna(how="all")
    return data[["Close"]].rename(columns={"Close": symbols[0]}).dropna()


def _metrics(equity: pd.Series) -> dict:
    """Compute performance metrics from equity curve."""
    if equity.empty or len(equity) < 2:
        return {}
    total_ret = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    daily_ret = equity.pct_change().dropna()
    annual_factor = 252
    if daily_ret.std() == 0:
        sharpe = 0.0
    else:
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(annual_factor))
    # Max drawdown
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_dd = float(drawdown.min() * 100)
    return {
        "total_return_pct": round(float(total_ret), 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "annual_volatility_pct": round(float(daily_ret.std() * math.sqrt(annual_factor) * 100), 2),
    }


def _buy_and_hold(prices: pd.DataFrame, capital: float) -> tuple[pd.Series, list[dict]]:
    # Equal-weight portfolio, buy on first day, sell on last
    n = len(prices.columns)
    alloc = capital / n
    shares = {sym: alloc / prices[sym].iloc[0] for sym in prices.columns}
    equity = sum(shares[sym] * prices[sym] for sym in prices.columns)
    trades = [
        {
            "date": str(prices.index[0].date()),
            "action": "BUY",
            "symbol": sym,
            "shares": round(shares[sym], 4),
        }
        for sym in prices.columns
    ] + [
        {
            "date": str(prices.index[-1].date()),
            "action": "SELL",
            "symbol": sym,
            "shares": round(shares[sym], 4),
        }
        for sym in prices.columns
    ]
    return equity, trades


def _crossover_signal(sma_fast: pd.Series, sma_slow: pd.Series, i: int) -> str | None:
    """Return 'buy', 'sell', or None based on fast/slow SMA crossover at index i."""
    if sma_fast.iloc[i] > sma_slow.iloc[i] and sma_fast.iloc[i - 1] <= sma_slow.iloc[i - 1]:
        return "buy"
    if sma_fast.iloc[i] < sma_slow.iloc[i] and sma_fast.iloc[i - 1] >= sma_slow.iloc[i - 1]:
        return "sell"
    return None


def _sma_crossover(
    prices: pd.DataFrame,
    capital: float,
    fast: int = 20,
    slow: int = 50,
) -> tuple[pd.Series, list[dict]]:
    # Trade each symbol independently
    equity = pd.Series(0.0, index=prices.index)
    trades = []
    for sym in prices.columns:
        p = prices[sym].dropna()
        sma_fast = p.rolling(fast).mean()
        sma_slow = p.rolling(slow).mean()
        position = 0.0
        sym_cash = capital / len(prices.columns)
        sym_equity = pd.Series(sym_cash, index=prices.index)

        for i in range(1, len(p)):
            date = p.index[i]
            signal = _crossover_signal(sma_fast, sma_slow, i)
            if signal == "buy" and sym_cash > 0 and position == 0:
                position = sym_cash / p.iloc[i]
                sym_cash = 0.0
                trades.append(
                    {
                        "date": str(date.date()),
                        "action": "BUY",
                        "symbol": sym,
                        "price": round(p.iloc[i], 4),
                        "shares": round(position, 4),
                    }
                )
            elif signal == "sell" and position > 0:
                sym_cash = position * p.iloc[i]
                trades.append(
                    {
                        "date": str(date.date()),
                        "action": "SELL",
                        "symbol": sym,
                        "price": round(p.iloc[i], 4),
                        "proceeds": round(sym_cash, 2),
                    }
                )
                position = 0.0
            sym_equity.loc[date] = sym_cash + position * p.iloc[i]
        equity += sym_equity
    return equity, trades


def _rsi_mean_reversion(
    prices: pd.DataFrame,
    capital: float,
    rsi_buy: float = 30.0,
    rsi_sell: float = 70.0,
) -> tuple[pd.Series, list[dict]]:
    from ta.momentum import RSIIndicator

    equity = pd.Series(0.0, index=prices.index)
    trades = []
    for sym in prices.columns:
        p = prices[sym].dropna()
        rsi = RSIIndicator(close=p, window=14).rsi()
        sym_cash = capital / len(prices.columns)
        position = 0.0
        sym_equity = pd.Series(sym_cash, index=prices.index)

        for i in range(14, len(p)):
            date = p.index[i]
            r = rsi.iloc[i]
            if r < rsi_buy and position == 0 and sym_cash > 0:
                position = sym_cash / p.iloc[i]
                sym_cash = 0.0
                trades.append(
                    {
                        "date": str(date.date()),
                        "action": "BUY",
                        "symbol": sym,
                        "rsi": round(r, 1),
                        "price": round(p.iloc[i], 4),
                    }
                )
            elif r > rsi_sell and position > 0:
                sym_cash = position * p.iloc[i]
                trades.append(
                    {
                        "date": str(date.date()),
                        "action": "SELL",
                        "symbol": sym,
                        "rsi": round(r, 1),
                        "proceeds": round(sym_cash, 2),
                    }
                )
                position = 0.0
            sym_equity.loc[date] = sym_cash + position * p.iloc[i]
        equity += sym_equity
    return equity, trades


def run_simulation(
    name: str,
    symbols: list[str],
    strategy: dict,
    initial_capital: float = 10_000.0,
    period_start: str = "2023-01-01",
    period_end: str | None = None,
) -> dict:
    """Run a backtested simulation. Returns equity curve, metrics, and trades."""
    end = period_end or datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        prices = _download(symbols, period_start, end)
    except Exception as exc:
        return {"error": f"Failed to download price data: {exc}"}

    if prices.empty:
        return {"error": "No price data returned for the given symbols and period."}

    stype = strategy.get("type", "buy_and_hold")
    params = strategy.get("params", {})

    try:
        if stype == "buy_and_hold":
            equity, trades = _buy_and_hold(prices, initial_capital)
        elif stype == "sma_crossover":
            equity, trades = _sma_crossover(
                prices,
                initial_capital,
                fast=int(params.get("fast", 20)),
                slow=int(params.get("slow", 50)),
            )
        elif stype == "rsi_mean_reversion":
            equity, trades = _rsi_mean_reversion(
                prices,
                initial_capital,
                rsi_buy=float(params.get("rsi_buy", 30)),
                rsi_sell=float(params.get("rsi_sell", 70)),
            )
        else:
            return {
                "error": f"Unknown strategy type: {stype}. Use buy_and_hold, sma_crossover, \
                    or rsi_mean_reversion."
            }
    except Exception as exc:
        logger.exception("Simulation failed")
        return {"error": str(exc)}

    # Build equity curve (weekly resolution to keep response small)
    equity_weekly = equity.resample("W").last().dropna()
    equity_curve = [
        {"date": str(idx.date()), "value": round(float(val), 2)}
        for idx, val in equity_weekly.items()
    ]

    metrics = _metrics(equity)
    final_value = round(float(equity.iloc[-1]), 2)

    return {
        "name": name,
        "strategy": strategy,
        "symbols": symbols,
        "initial_capital": initial_capital,
        "final_value": final_value,
        "period_start": period_start,
        "period_end": end,
        "trades_count": len(trades),
        "trades_sample": trades[:20],  # first 20 trades
        "equity_curve": equity_curve,
        **metrics,
    }
