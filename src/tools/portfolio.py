"""Portfolio aggregator — combines holdings across all brokers."""

from __future__ import annotations

import yfinance as yf

from src.agent.utils.logger import get_logger
from src.tools.broker_accounts import BrokerAccountConfig
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


def _call_for_account(fn, account: BrokerAccountConfig | None):
    """Call legacy broker functions without changing their no-argument API."""
    return fn() if account is None else fn(account=account)


def _collect_broker(
    name: str,
    positions_fn,
    account_fn,
    result: dict,
    account: BrokerAccountConfig | None = None,
) -> None:
    """Fetch positions and account info for one broker, accumulating into result."""
    try:
        acc = _call_for_account(account_fn, account)
        if "error" not in acc:
            if account:
                acc["account_id"] = account.id
                acc["account_name"] = account.display_name
            result["accounts"].append(acc)
        positions = _call_for_account(positions_fn, account)
        for p in positions:
            if "error" not in p:
                p["broker"] = name
                if account:
                    p["account_id"] = account.id
                    p["account_name"] = account.display_name
                result["positions"].append(p)
                result["total_market_value_usd"] += float(p.get("market_value") or 0)
                result["total_unrealized_pnl_usd"] += float(
                    p.get("unrealized_pnl") or p.get("unrealized_pl") or 0
                )
    except Exception as exc:
        logger.warning("%s portfolio fetch failed: %s", name, exc)


def get_portfolio_summary(
    broker: str | None = None,
    accounts: list[BrokerAccountConfig] | None = None,
) -> dict:
    """Aggregate positions across all (or a specific) broker/account."""
    result: dict = {
        "positions": [],
        "accounts": [],
        "total_market_value_usd": 0.0,
        "total_unrealized_pnl_usd": 0.0,
    }
    if accounts is None:
        for name, pos_fn, acc_fn in _BROKER_FUNNELS:
            if broker is None or broker == name:
                _collect_broker(name, pos_fn, acc_fn, result)
    else:
        functions = {name: (pos_fn, acc_fn) for name, pos_fn, acc_fn in _BROKER_FUNNELS}
        for account in accounts:
            if broker is not None and broker != account.broker:
                continue
            pos_fn, acc_fn = functions[account.broker]
            _collect_broker(account.broker, pos_fn, acc_fn, result, account)
    result["total_market_value_usd"] = round(result["total_market_value_usd"], 2)
    result["total_unrealized_pnl_usd"] = round(result["total_unrealized_pnl_usd"], 2)
    return result


def get_account_info(broker: str, account: BrokerAccountConfig | None = None) -> dict:
    dispatch = {
        "alpaca": alpaca_tool.get_alpaca_account,
        "ibkr": ibkr_tool.get_ibkr_account,
        "coinbase": coinbase.get_coinbase_account,
        "binance": binance_tool.get_binance_account,
    }
    fn = dispatch.get(broker)
    if not fn:
        return {"error": f"Unknown broker: {broker}"}
    return _call_for_account(fn, account)


def get_trade_history(
    broker: str, days: int = 30, account: BrokerAccountConfig | None = None
) -> list[dict]:
    if broker == "alpaca":
        return (
            alpaca_tool.get_alpaca_orders(days, account=account)
            if account
            else alpaca_tool.get_alpaca_orders(days)
        )
    if broker == "ibkr":
        return (
            ibkr_tool.get_ibkr_orders(account=account)
            if account
            else ibkr_tool.get_ibkr_orders()
        )
    if broker == "coinbase":
        return (
            coinbase.get_coinbase_orders(account=account)
            if account
            else coinbase.get_coinbase_orders()
        )
    if broker == "binance":
        return (
            binance_tool.get_binance_orders(account=account)
            if account
            else binance_tool.get_binance_orders()
        )
    return [{"error": f"Unknown broker: {broker}"}]
