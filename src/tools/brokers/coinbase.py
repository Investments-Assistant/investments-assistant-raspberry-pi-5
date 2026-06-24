"""Coinbase Advanced Trade brokerage tool."""

from __future__ import annotations

from src.agent.utils.logger import get_logger
from src.config import settings

logger = get_logger(__name__)


def _get_client():
    from coinbase.rest import RESTClient

    return RESTClient(
        api_key=settings.coinbase_api_key,
        api_secret=settings.coinbase_api_secret,
    )


def get_coinbase_account() -> dict:
    try:
        client = _get_client()
        accounts = client.get_accounts()
        balances = []
        for acc in accounts.get("accounts", []):
            avail = acc.get("available_balance", {})
            hold = acc.get("hold", {})
            val = float(avail.get("value", 0))
            if val > 0.0001:
                balances.append(
                    {
                        "currency": avail.get("currency"),
                        "available": val,
                        "hold": float(hold.get("value", 0)),
                    }
                )
        return {"broker": "coinbase", "balances": balances}
    except Exception as exc:
        logger.error("Coinbase account fetch failed: %s", exc)
        return {"broker": "coinbase", "error": str(exc)}


def get_coinbase_positions() -> list[dict]:
    """For Coinbase, positions = non-zero crypto holdings."""
    result = get_coinbase_account()
    if "error" in result:
        return [result]
    return result.get("balances", [])


def get_coinbase_orders() -> list[dict]:
    try:
        client = _get_client()
        resp = client.list_orders(order_status="FILLED")
        orders = resp.get("orders", [])
        return [
            {
                "order_id": o.get("order_id"),
                "product_id": o.get("product_id"),
                "side": o.get("side"),
                "order_type": o.get("order_type"),
                "filled_size": o.get("filled_size"),
                "average_filled_price": o.get("average_filled_price"),
                "status": o.get("status"),
                "created_time": o.get("created_time"),
            }
            for o in orders[:50]
        ]
    except Exception as exc:
        logger.error("Coinbase orders fetch failed: %s", exc)
        return [{"error": str(exc)}]


def submit_coinbase_order(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "market",
    limit_price: float | None = None,
) -> dict:
    """
    symbol: Coinbase product ID, e.g. 'BTC-USD'
    side: 'buy' or 'sell'
    """
    try:
        import uuid

        client = _get_client()
        client_order_id = str(uuid.uuid4())
        product_id = symbol.upper()

        if order_type == "market":
            if side.lower() == "buy":
                config = {"market_market_ioc": {"quote_size": str(quantity)}}
            else:
                config = {"market_market_ioc": {"base_size": str(quantity)}}
        elif order_type == "limit":
            if limit_price is None:
                return {"error": "limit_price required for limit orders"}
            config = {
                "limit_limit_gtc": {
                    "base_size": str(quantity),
                    "limit_price": str(limit_price),
                    "post_only": False,
                }
            }
        else:
            return {"error": f"Unsupported order type: {order_type}"}

        resp = client.create_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side.upper(),
            order_configuration=config,
        )
        success = resp.get("success", False)
        return {
            "success": success,
            "order_id": resp.get("order_id") or resp.get("success_response", {}).get("order_id"),
            "product_id": product_id,
            "side": side,
            "error": resp.get("error_response", {}).get("message") if not success else None,
        }
    except Exception as exc:
        logger.error("Coinbase order submission failed: %s", exc)
        return {"success": False, "error": str(exc)}


def cancel_coinbase_order(order_id: str) -> dict:
    try:
        client = _get_client()
        resp = client.cancel_orders(order_ids=[order_id])
        results = resp.get("results", [])
        if results:
            r = results[0]
            return {"success": r.get("success", False), "order_id": order_id}
        return {"success": False, "error": "No result returned"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
