"""Binance brokerage tool (crypto spot trading)."""

from __future__ import annotations

from src.agent.utils.logger import get_logger
from src.config import settings
from src.tools.broker_accounts import BrokerAccountConfig

logger = get_logger(__name__)


def _config(account: BrokerAccountConfig | None) -> dict:
    return account.config if account else {
        "api_key": settings.binance_api_key,
        "secret_key": settings.binance_secret_key,
        "testnet": settings.binance_testnet,
    }


def _configured(account: BrokerAccountConfig | None = None) -> bool:
    config = _config(account)
    return bool(config.get("api_key") and config.get("secret_key"))


def _not_configured(account: BrokerAccountConfig | None = None) -> dict:
    return {"broker": "binance", "error": "Binance credentials are not configured"}


def _get_client(account: BrokerAccountConfig | None = None):
    from binance.client import Client

    config = _config(account)
    client = Client(
        api_key=config["api_key"],
        api_secret=config["secret_key"],
        testnet=config.get("testnet", True),
    )
    return client


def get_binance_account(account: BrokerAccountConfig | None = None) -> dict:
    if not _configured(account):
        return _not_configured(account)
    try:
        client = _get_client(account)
        info = client.get_account()
        balances = [
            {
                "asset": b["asset"],
                "free": float(b["free"]),
                "locked": float(b["locked"]),
            }
            for b in info.get("balances", [])
            if float(b["free"]) > 0 or float(b["locked"]) > 0
        ]
        return {
            "broker": "binance",
            "testnet": _config(account).get("testnet", True),
            "can_trade": info.get("canTrade"),
            "maker_commission": info.get("makerCommission"),
            "taker_commission": info.get("takerCommission"),
            "balances": balances,
        }
    except Exception as exc:
        logger.error("Binance account fetch failed: %s", exc)
        return {"broker": "binance", "error": str(exc)}


def get_binance_positions(account: BrokerAccountConfig | None = None) -> list[dict]:
    """For spot Binance, return non-zero balances."""
    result = get_binance_account(account)
    if "error" in result:
        return [result]
    return result.get("balances", [])


def get_binance_orders(
    symbol: str | None = None, account: BrokerAccountConfig | None = None
) -> list[dict]:
    if not _configured(account):
        return [_not_configured(account)]
    try:
        client = _get_client(account)
        if symbol:
            orders = client.get_all_orders(symbol=symbol.upper(), limit=100)
        else:
            # Binance requires a symbol; fetch BTCUSDT, ETHUSDT as defaults
            orders = []
            for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
                try:
                    orders.extend(client.get_all_orders(symbol=sym, limit=20))
                except Exception:
                    pass
        return [
            {
                "order_id": str(o["orderId"]),
                "symbol": o["symbol"],
                "side": o["side"],
                "type": o["type"],
                "orig_qty": o["origQty"],
                "executed_qty": o["executedQty"],
                "price": o["price"],
                "status": o["status"],
                "time": o["time"],
            }
            for o in orders[:50]
        ]
    except Exception as exc:
        logger.error("Binance orders fetch failed: %s", exc)
        return [{"error": str(exc)}]


def submit_binance_order(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "market",
    limit_price: float | None = None,
    account: BrokerAccountConfig | None = None,
) -> dict:
    """
    symbol: Binance trading pair e.g. 'BTCUSDT'
    """
    if not _configured(account):
        return _not_configured(account)
    try:
        from binance.enums import (
            ORDER_TYPE_LIMIT,
            ORDER_TYPE_MARKET,
            SIDE_BUY,
            SIDE_SELL,
            TIME_IN_FORCE_GTC,
        )

        client = _get_client(account)
        _side = SIDE_BUY if side.lower() == "buy" else SIDE_SELL
        sym = symbol.upper()

        if order_type == "market":
            order = client.create_order(
                symbol=sym,
                side=_side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity,
            )
        elif order_type == "limit":
            if limit_price is None:
                return {"error": "limit_price required"}
            order = client.create_order(
                symbol=sym,
                side=_side,
                type=ORDER_TYPE_LIMIT,
                timeInForce=TIME_IN_FORCE_GTC,
                quantity=quantity,
                price=str(limit_price),
            )
        else:
            return {"error": f"Unsupported order type: {order_type}"}

        return {
            "success": True,
            "order_id": str(order["orderId"]),
            "symbol": sym,
            "side": side,
            "status": order["status"],
            "executed_qty": order.get("executedQty"),
            "price": order.get("price"),
        }
    except Exception as exc:
        logger.error("Binance order submission failed: %s", exc)
        return {"success": False, "error": str(exc)}


def cancel_binance_order(
    order_id: str,
    symbol: str = "BTCUSDT",
    account: BrokerAccountConfig | None = None,
) -> dict:
    if not _configured(account):
        return _not_configured(account)
    try:
        client = _get_client(account)
        result = client.cancel_order(symbol=symbol.upper(), orderId=int(order_id))
        return {"success": True, "order_id": order_id, "status": result.get("status")}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
