"""Portfolio aggregator — combines holdings across all brokers."""

from __future__ import annotations

import yfinance as yf

from src.agent.utils.logger import get_logger
from src.tools.brokers import (
    alpaca as alpaca_tool,
    binance as binance_tool,
    coinbase,
    ibkr as ibkr_tool,
)

logger = get_logger(__name__)


def _enrich_position(pos: dict, symbol_key: str = "symbol") -> dict:
    """Add current market price to a position if not already present."""
    sym = pos.get(symbol_key)
    if not sym or pos.get("current_price"):
        return pos
    try:
        ticker = yf.Ticker(sym)
        info = ticker.info or {}
        pos["current_price"] = info.get("regularMarketPrice") or info.get("currentPrice")
    except Exception:
        pass
    return pos


_BROKER_FUNNELS = [
    ("alpaca", alpaca_tool.get_alpaca_positions, alpaca_tool.get_alpaca_account),
    ("ibkr", ibkr_tool.get_ibkr_positions, ibkr_tool.get_ibkr_account),
    ("coinbase", coinbase.get_coinbase_positions, coinbase.get_coinbase_account),
    ("binance", binance_tool.get_binance_positions, binance_tool.get_binance_account),
]


def _collect_broker(name: str, positions_fn, account_fn, result: dict) -> None:
    """Fetch positions and account info for one broker, accumulating into result."""
    try:
        acc = account_fn()
        if "error" not in acc:
            result["accounts"].append(acc)
        positions = positions_fn()
        for p in positions:
            if "error" not in p:
                p["broker"] = name
                result["positions"].append(p)
                result["total_market_value_usd"] += float(p.get("market_value") or 0)
                result["total_unrealized_pnl_usd"] += float(
                    p.get("unrealized_pnl") or p.get("unrealized_pl") or 0
                )
    except Exception as exc:
        logger.warning("%s portfolio fetch failed: %s", name, exc)


def get_portfolio_summary(broker: str | None = None) -> dict:
    """Aggregate positions across all (or a specific) broker."""
    result: dict = {
        "positions": [],
        "accounts": [],
        "total_market_value_usd": 0.0,
        "total_unrealized_pnl_usd": 0.0,
    }
    for name, pos_fn, acc_fn in _BROKER_FUNNELS:
        if broker is None or broker == name:
            _collect_broker(name, pos_fn, acc_fn, result)
    result["total_market_value_usd"] = round(result["total_market_value_usd"], 2)
    result["total_unrealized_pnl_usd"] = round(result["total_unrealized_pnl_usd"], 2)
    return result


def get_account_info(broker: str) -> dict:
    dispatch = {
        "alpaca": alpaca_tool.get_alpaca_account,
        "ibkr": ibkr_tool.get_ibkr_account,
        "coinbase": coinbase.get_coinbase_account,
        "binance": binance_tool.get_binance_account,
    }
    fn = dispatch.get(broker)
    if not fn:
        return {"error": f"Unknown broker: {broker}"}
    return fn()


def get_trade_history(broker: str, days: int = 30) -> list[dict]:
    dispatch = {
        "alpaca": lambda: alpaca_tool.get_alpaca_orders(days),
        "ibkr": lambda: ibkr_tool.get_ibkr_orders(),
        "coinbase": lambda: coinbase.get_coinbase_orders(),
        "binance": lambda: binance_tool.get_binance_orders(),
    }
    fn = dispatch.get(broker)
    if not fn:
        return [{"error": f"Unknown broker: {broker}"}]
    return fn()
