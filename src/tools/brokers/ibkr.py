"""Interactive Brokers brokerage tool via ib_insync.

Requires IB Gateway or TWS to be running and configured.
Set IBKR_ENABLED=false in .env to disable without errors.
"""

from __future__ import annotations

from src.agent.utils.logger import get_logger
from src.config import settings

logger = get_logger(__name__)


def _get_ib():
    """Connect to IB Gateway and return an IB instance."""
    from ib_insync import IB

    ib = IB()
    ib.connect(
        host=settings.ibkr_host,
        port=settings.ibkr_port,
        clientId=settings.ibkr_client_id,
        timeout=10,
        readonly=False,
    )
    return ib


def _disabled() -> dict:
    return {
        "error": "IBKR integration is disabled. \
            Set IBKR_ENABLED=true and ensure IB Gateway is running.",
        "help": "See docs/ibkr-gateway.md for setup instructions.",
    }


def get_ibkr_account() -> dict:
    if not settings.ibkr_enabled:
        return _disabled()
    try:
        ib = _get_ib()
        summary = ib.accountSummary()
        result: dict[str, object] = {"broker": "ibkr", "account": {}}
        for item in summary:
            result["account"][item.tag] = item.value
        ib.disconnect()
        # Extract key fields
        acc = result["account"]
        return {
            "broker": "ibkr",
            "net_liquidation": acc.get("NetLiquidation"),
            "cash_balance": acc.get("CashBalance"),
            "buying_power": acc.get("BuyingPower"),
            "available_funds": acc.get("AvailableFunds"),
            "unrealized_pnl": acc.get("UnrealizedPnL"),
            "realized_pnl": acc.get("RealizedPnL"),
        }
    except Exception as exc:
        logger.error("IBKR account fetch failed: %s", exc)
        return {"broker": "ibkr", "error": str(exc)}


def get_ibkr_positions() -> list[dict]:
    if not settings.ibkr_enabled:
        return [_disabled()]
    try:
        ib = _get_ib()
        portfolio = ib.portfolio()
        ib.disconnect()
        return [
            {
                "symbol": item.contract.symbol,
                "security_type": item.contract.secType,
                "currency": item.contract.currency,
                "qty": item.position,
                "avg_cost": item.averageCost,
                "market_price": item.marketPrice,
                "market_value": item.marketValue,
                "unrealized_pnl": item.unrealizedPNL,
                "realized_pnl": item.realizedPNL,
            }
            for item in portfolio
        ]
    except Exception as exc:
        logger.error("IBKR positions fetch failed: %s", exc)
        return [{"error": str(exc)}]


def get_ibkr_orders() -> list[dict]:
    if not settings.ibkr_enabled:
        return [_disabled()]
    try:
        ib = _get_ib()
        trades = ib.trades()
        ib.disconnect()
        return [
            {
                "order_id": trade.order.orderId,
                "symbol": trade.contract.symbol,
                "action": trade.order.action,
                "qty": trade.order.totalQuantity,
                "order_type": trade.order.orderType,
                "limit_price": trade.order.lmtPrice,
                "status": trade.orderStatus.status,
                "filled": trade.orderStatus.filled,
                "avg_fill_price": trade.orderStatus.avgFillPrice,
            }
            for trade in trades
        ]
    except Exception as exc:
        logger.error("IBKR orders fetch failed: %s", exc)
        return [{"error": str(exc)}]


def _is_forex_pair(symbol: str) -> bool:
    """Return True for symbols like 'EURUSD', 'EUR/USD', 'EUR-USD'."""
    clean = symbol.upper().replace("/", "").replace("-", "")
    return len(clean) == 6 and clean.isalpha()


def submit_ibkr_order(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_price: float | None = None,
    asset_type: str | None = None,
    option_expiry: str | None = None,
    option_strike: float | None = None,
    option_right: str | None = None,
) -> dict:
    if not settings.ibkr_enabled:
        return _disabled()
    try:
        from ib_insync import Forex, LimitOrder, MarketOrder, Option, Stock, StopLimitOrder

        ib = _get_ib()
        normalized_asset_type = (asset_type or "").lower().strip()
        if normalized_asset_type == "option":
            if not option_expiry or option_strike is None or option_right not in {"C", "P"}:
                return {
                    "success": False,
                    "error": "Option orders require expiry, strike, and right (C or P).",
                }
            expiry = option_expiry.replace("-", "")
            contract = Option(
                symbol,
                expiry,
                float(option_strike),
                option_right,
                "SMART",
                multiplier="100",
                currency="USD",
            )
        elif normalized_asset_type == "forex" or (
            not normalized_asset_type and _is_forex_pair(symbol)
        ):
            clean = symbol.upper().replace("/", "").replace("-", "")
            contract = Forex(clean)
        else:
            contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)

        action = "BUY" if side.lower() == "buy" else "SELL"
        if order_type == "market":
            order = MarketOrder(action, quantity)
        elif order_type == "limit":
            if limit_price is None:
                return {"error": "limit_price required"}
            order = LimitOrder(action, quantity, limit_price)
        elif order_type == "stop_limit":
            if limit_price is None or stop_price is None:
                return {"error": "limit_price and stop_price required"}
            order = StopLimitOrder(action, quantity, limit_price, stop_price)
        else:
            return {"error": f"Unsupported order type: {order_type}"}

        trade = ib.placeOrder(contract, order)
        ib.sleep(1)
        ib.disconnect()
        return {
            "success": True,
            "order_id": trade.order.orderId,
            "symbol": symbol,
            "asset_type": normalized_asset_type or ("forex" if _is_forex_pair(symbol) else "stock"),
            "status": trade.orderStatus.status,
        }
    except Exception as exc:
        logger.error("IBKR order submission failed: %s", exc)
        return {"success": False, "error": str(exc)}


def cancel_ibkr_order(order_id: str) -> dict:
    if not settings.ibkr_enabled:
        return _disabled()
    try:
        ib = _get_ib()
        open_trades = ib.openTrades()
        target = next((t for t in open_trades if str(t.order.orderId) == str(order_id)), None)
        if not target:
            ib.disconnect()
            return {"error": f"Order {order_id} not found"}
        ib.cancelOrder(target.order)
        ib.sleep(1)
        ib.disconnect()
        return {"success": True, "order_id": order_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
