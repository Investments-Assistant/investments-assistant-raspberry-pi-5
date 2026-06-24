"""Binance brokerage tool (crypto spot trading)."""

from __future__ import annotations

from src.agent.utils.logger import get_logger
from src.config import settings

logger = get_logger(__name__)


def _get_client():
    from binance.client import Client

    client = Client(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_secret_key,
        testnet=settings.binance_testnet,
    )
    return client


def get_binance_account() -> dict:
    try:
        client = _get_client()
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
            "testnet": settings.binance_testnet,
            "can_trade": info.get("canTrade"),
            "maker_commission": info.get("makerCommission"),
            "taker_commission": info.get("takerCommission"),
            "balances": balances,
        }
    except Exception as exc:
        logger.error("Binance account fetch failed: %s", exc)
        return {"broker": "binance", "error": str(exc)}


def get_binance_positions() -> list[dict]:
    """For spot Binance, return non-zero balances."""
    result = get_binance_account()
    if "error" in result:
        return [result]
    return result.get("balances", [])


def get_binance_orders(symbol: str | None = None) -> list[dict]:
    try:
        client = _get_client()
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
) -> dict:
    """
    symbol: Binance trading pair e.g. 'BTCUSDT'
    """
    try:
        from binance.enums import (
            ORDER_TYPE_LIMIT,
            ORDER_TYPE_MARKET,
            SIDE_BUY,
            SIDE_SELL,
            TIME_IN_FORCE_GTC,
        )

        client = _get_client()
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


def cancel_binance_order(order_id: str, symbol: str = "BTCUSDT") -> dict:
    try:
        client = _get_client()
        result = client.cancel_order(symbol=symbol.upper(), orderId=int(order_id))
        return {"success": True, "order_id": order_id, "status": result.get("status")}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
