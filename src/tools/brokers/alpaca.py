"""Alpaca Markets brokerage tool (stocks, ETFs, fractional shares)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.agent.utils.logger import get_logger
from src.config import settings
from src.tools.broker_accounts import BrokerAccountConfig

logger = get_logger(__name__)


def _config(account: BrokerAccountConfig | None) -> dict:
    return account.config if account else {
        "api_key": settings.alpaca_api_key,
        "secret_key": settings.alpaca_secret_key,
        "paper": settings.alpaca_paper,
    }


def _configured(account: BrokerAccountConfig | None = None) -> bool:
    config = _config(account)
    return bool(config.get("api_key") and config.get("secret_key"))


def _not_configured(account: BrokerAccountConfig | None = None) -> dict:
    return {"broker": "alpaca", "error": "Alpaca credentials are not configured"}


def _get_client(account: BrokerAccountConfig | None = None):
    """Lazily create Alpaca trading client."""
    from alpaca.trading.client import TradingClient

    config = _config(account)
    return TradingClient(
        api_key=config["api_key"],
        secret_key=config["secret_key"],
        paper=config.get("paper", True),
    )


def get_alpaca_account(account: BrokerAccountConfig | None = None) -> dict:
    if not _configured(account):
        return _not_configured(account)
    try:
        client = _get_client(account)
        acc = client.get_account()
        return {
            "broker": "alpaca",
            "paper": _config(account).get("paper", True),
            "status": acc.status,
            "cash": float(acc.cash),
            "buying_power": float(acc.buying_power),
            "portfolio_value": float(acc.portfolio_value),
            "equity": float(acc.equity),
            "daytrade_count": acc.daytrade_count,
            "pattern_day_trader": acc.pattern_day_trader,
        }
    except Exception as exc:
        logger.error("Alpaca account fetch failed: %s", exc)
        return {"broker": "alpaca", "error": str(exc)}


def get_alpaca_positions(account: BrokerAccountConfig | None = None) -> list[dict]:
    if not _configured(account):
        return [_not_configured(account)]
    try:
        client = _get_client(account)
        positions = client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price) if p.current_price else None,
                "market_value": float(p.market_value) if p.market_value else None,
                "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                "unrealized_plpc": float(p.unrealized_plpc) if p.unrealized_plpc else None,
                "side": p.side,
            }
            for p in positions
        ]
    except Exception as exc:
        logger.error("Alpaca positions fetch failed: %s", exc)
        return [{"error": str(exc)}]


def get_alpaca_orders(
    days: int = 30, account: BrokerAccountConfig | None = None
) -> list[dict]:
    if not _configured(account):
        return [_not_configured(account)]
    try:
        from alpaca.trading.requests import GetOrdersRequest

        client = _get_client(account)
        since = datetime.now(UTC) - timedelta(days=days)
        req = GetOrdersRequest(after=since)
        orders = client.get_orders(filter=req)
        return [
            {
                "id": str(o.id),
                "symbol": o.symbol,
                "side": o.side.value,
                "qty": float(o.qty) if o.qty else None,
                "filled_qty": float(o.filled_qty) if o.filled_qty else None,
                "order_type": o.order_type.value,
                "status": o.status.value,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "created_at": str(o.created_at),
                "filled_at": str(o.filled_at) if o.filled_at else None,
            }
            for o in orders
        ]
    except Exception as exc:
        logger.error("Alpaca orders fetch failed: %s", exc)
        return [{"error": str(exc)}]


def submit_alpaca_order(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_price: float | None = None,
    account: BrokerAccountConfig | None = None,
) -> dict:
    if not _configured(account):
        return _not_configured(account)
    try:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            MarketOrderRequest,
            StopLimitOrderRequest,
        )

        client = _get_client(account)
        _side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        if order_type == "market":
            req = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=_side,
                time_in_force=TimeInForce.DAY,
            )
        elif order_type == "limit":
            if limit_price is None:
                return {"error": "limit_price required for limit orders"}
            req = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=_side,
                limit_price=limit_price,
                time_in_force=TimeInForce.DAY,
            )
        elif order_type == "stop_limit":
            if limit_price is None or stop_price is None:
                return {"error": "limit_price and stop_price required for stop_limit orders"}
            req = StopLimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=_side,
                limit_price=limit_price,
                stop_price=stop_price,
                time_in_force=TimeInForce.DAY,
            )
        else:
            return {"error": f"Unsupported order type: {order_type}"}

        order = client.submit_order(req)
        return {
            "success": True,
            "order_id": str(order.id),
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": float(order.qty) if order.qty else None,
            "status": order.status.value,
        }
    except Exception as exc:
        logger.error("Alpaca order submission failed: %s", exc)
        return {"success": False, "error": str(exc)}


def cancel_alpaca_order(
    order_id: str, account: BrokerAccountConfig | None = None
) -> dict:
    if not _configured(account):
        return _not_configured(account)
    try:
        client = _get_client(account)
        client.cancel_order_by_id(order_id)
        return {"success": True, "order_id": order_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
