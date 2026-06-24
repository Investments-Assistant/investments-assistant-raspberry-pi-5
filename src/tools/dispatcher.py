"""Tool dispatcher — maps tool names to their Python implementations."""

from __future__ import annotations

from datetime import UTC, datetime
import json

from sqlalchemy import select

from src.agent.utils.logger import get_logger
from src.config import settings
from src.db.database import async_session
from src.db.models import DailyPnL, SimulationResult, Trade
from src.tools.brokers import (
    alpaca as alpaca_tool,
    binance as binance_tool,
    coinbase,
    ibkr as ibkr_tool,
)
from src.tools.forex import get_central_bank_rates, get_forex_data, get_forex_rates
from src.tools.market_data import (
    get_crypto_data,
    get_earnings_calendar,
    get_market_overview,
    get_options_chain,
    get_stock_data,
    get_technical_indicators,
    search_ticker,
)
from src.tools.news import search_market_news
from src.tools.news_memory import get_latest_news, search_stored_news
from src.tools.portfolio import get_account_info, get_portfolio_summary, get_trade_history
from src.tools.simulator import run_simulation

logger = get_logger(__name__)


async def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Call the appropriate tool and return a JSON string result."""
    logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])
    try:
        result = await _dispatch(tool_name, tool_input)
    except Exception as exc:
        logger.exception("Tool %s raised an exception", tool_name)
        result = {"error": str(exc), "tool": tool_name}
    return json.dumps(result, default=str, ensure_ascii=False)


# Synchronous tools mapped by name to a callable that receives the raw input dict.
_SYNC_DISPATCH: dict[str, object] = {
    "get_stock_data": lambda inp: get_stock_data(**inp),
    "get_crypto_data": lambda inp: get_crypto_data(**inp),
    "get_forex_data": lambda inp: get_forex_data(**inp),
    "get_forex_rates": lambda inp: get_forex_rates(pairs=inp.get("pairs")),
    "get_central_bank_rates": lambda inp: get_central_bank_rates(currencies=inp.get("currencies")),
    "get_market_overview": lambda _: get_market_overview(),
    "get_technical_indicators": lambda inp: get_technical_indicators(**inp),
    "get_options_chain": lambda inp: get_options_chain(**inp),
    "search_ticker": lambda inp: search_ticker(**inp),
    "get_earnings_calendar": lambda inp: get_earnings_calendar(**inp),
    "search_market_news": lambda inp: search_market_news(**inp),
    "get_portfolio_summary": lambda inp: get_portfolio_summary(broker=inp.get("broker")),
    "get_account_info": lambda inp: get_account_info(broker=inp["broker"]),
    "get_trade_history": lambda inp: get_trade_history(
        broker=inp["broker"], days=inp.get("days", 30)
    ),
    "set_trading_mode": lambda inp: _set_trading_mode(inp["mode"]),
}

# Async tools that can't live in _SYNC_DISPATCH (they are awaited in _dispatch)
_ASYNC_DISPATCH: dict[str, object] = {
    "search_stored_news": lambda inp: search_stored_news(**inp),
    "get_latest_news": lambda inp: get_latest_news(limit=inp.get("limit", 20)),
    # run_simulation is CPU-bound but needs async DB persistence afterward
    "run_simulation": lambda inp: _run_simulation_and_persist(inp),
}


async def _dispatch(name: str, inp: dict) -> object:
    if name in _SYNC_DISPATCH:
        return _SYNC_DISPATCH[name](inp)  # type: ignore[operator]
    if name in _ASYNC_DISPATCH:
        return await _ASYNC_DISPATCH[name](inp)  # type: ignore[operator]
    if name == "execute_trade":
        return await _execute_trade(inp)
    if name == "confirm_trade":
        return await _confirm_trade(inp)
    if name == "cancel_order":
        return _cancel_order(inp)
    if name == "generate_report":
        return await _generate_report(inp)
    return {"error": f"Unknown tool: {name}"}


# ── Daily loss-limit helpers ─────────────────────────────────────────────────


async def _is_daily_halted() -> bool:
    """Return True if auto-trading has been halted for today."""
    try:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        async with async_session() as session:
            result = await session.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            return bool(record and record.auto_trading_halted)
    except Exception as exc:
        # Fail open: don't block trading on a DB read error
        logger.warning("Failed to check daily halt flag: %s", exc)
        return False


async def _check_and_enforce_daily_limit(realized_delta_usd: float) -> None:
    """Update today's realized P&L and set halt flag if limit is breached."""
    try:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        async with async_session() as session:
            result = await session.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            if record is None:
                record = DailyPnL(date=today, realized_usd=0.0)
                session.add(record)

            record.realized_usd = (record.realized_usd or 0.0) + realized_delta_usd

            if record.realized_usd < -abs(settings.auto_daily_loss_limit_usd):
                record.auto_trading_halted = True
                logger.warning(
                    "Daily loss limit breached (%.2f USD). Auto-trading halted for %s.",
                    record.realized_usd,
                    today,
                )

            await session.commit()
    except Exception as exc:
        logger.warning("Failed to update daily P&L: %s", exc)


# ── Trade execution ──────────────────────────────────────────────────────────


async def _execute_trade(inp: dict) -> dict:
    broker = inp["broker"]
    symbol = inp["symbol"]
    side = inp["side"]
    quantity = float(inp["quantity"])
    order_type = inp.get("order_type", "market")
    limit_price = inp.get("limit_price")
    stop_price = inp.get("stop_price")
    reason = inp.get("reason", "")

    if settings.trading_mode == "recommend":
        return {
            "status": "pending_confirmation",
            "message": (
                f"RECOMMENDATION: {side.upper()} {quantity} {symbol} via {broker} "
                f"({order_type} order{f' @ {limit_price}' if limit_price else ''}). "
                f"Reason: {reason}. "
                "Reply 'confirm trade' to execute, or 'cancel trade' to discard."
            ),
            "trade_details": {
                "broker": broker,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "order_type": order_type,
                "limit_price": limit_price,
                "stop_price": stop_price,
                "reason": reason,
            },
        }

    # AUTO mode — check safety guards before executing
    if (
        settings.auto_allowed_symbols_set
        and symbol.upper() not in settings.auto_allowed_symbols_set
    ):
        return {
            "blocked": True,
            "reason": f"{symbol} is not in the auto-trading allowed symbols list.",
        }

    if await _is_daily_halted():
        return {
            "blocked": True,
            "reason": (
                f"Auto-trading is halted for today: daily loss limit of "
                f"{settings.auto_daily_loss_limit_usd} USD has been reached."
            ),
        }

    result = _route_order(broker, symbol, side, quantity, order_type, limit_price, stop_price)
    result["reason"] = reason

    # Persist trade to DB
    try:
        async with async_session() as session:
            trade = Trade(
                broker=broker,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=limit_price,
                order_type=order_type,
                status=result.get("status", "submitted"),
                broker_order_id=result.get("order_id"),
                mode="auto",
                reason=reason,
            )
            session.add(trade)
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist trade to DB: %s", exc)

    # Negative delta for sells (potential loss), zero for buys (cost, not yet realised)
    if side == "sell" and limit_price:
        await _check_and_enforce_daily_limit(-(quantity * limit_price))

    return result


async def _confirm_trade(inp: dict) -> dict:
    """Execute a user-confirmed recommendation. Bypasses recommend-mode guard."""
    broker = inp["broker"]
    symbol = inp["symbol"]
    side = inp["side"]
    quantity = float(inp["quantity"])
    order_type = inp.get("order_type", "market")
    limit_price = inp.get("limit_price")
    stop_price = inp.get("stop_price")
    reason = inp.get("reason", "User confirmed recommendation")

    result = _route_order(broker, symbol, side, quantity, order_type, limit_price, stop_price)
    result["reason"] = reason

    try:
        async with async_session() as session:
            trade = Trade(
                broker=broker,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=limit_price,
                order_type=order_type,
                status=result.get("status", "submitted"),
                broker_order_id=result.get("order_id"),
                mode="manual",
                reason=reason,
            )
            session.add(trade)
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist confirmed trade to DB: %s", exc)

    return result


def _route_order(
    broker: str,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str,
    limit_price: float | None,
    stop_price: float | None,
) -> dict:
    if broker == "alpaca":
        return alpaca_tool.submit_alpaca_order(
            symbol, side, quantity, order_type, limit_price, stop_price
        )
    if broker == "ibkr":
        return ibkr_tool.submit_ibkr_order(symbol, side, quantity, order_type, limit_price)
    if broker == "coinbase":
        return coinbase.submit_coinbase_order(symbol, side, quantity, order_type, limit_price)
    if broker == "binance":
        return binance_tool.submit_binance_order(symbol, side, quantity, order_type, limit_price)
    return {"error": f"Unknown broker: {broker}"}


def _cancel_order(inp: dict) -> dict:
    broker = inp["broker"]
    order_id = inp["order_id"]
    if broker == "alpaca":
        return alpaca_tool.cancel_alpaca_order(order_id)
    if broker == "ibkr":
        return ibkr_tool.cancel_ibkr_order(order_id)
    if broker == "coinbase":
        return coinbase.cancel_coinbase_order(order_id)
    if broker == "binance":
        return binance_tool.cancel_binance_order(order_id)
    return {"error": f"Unknown broker: {broker}"}


def _set_trading_mode(mode: str) -> dict:
    if mode not in ("recommend", "auto"):
        return {"error": "mode must be 'recommend' or 'auto'"}
    settings.trading_mode = mode  # type: ignore[misc]
    return {
        "success": True,
        "trading_mode": mode,
        "message": f"Trading mode switched to '{mode}'.",
    }


async def _generate_report(inp: dict) -> dict:
    from src.scheduler.reporter import generate_report

    return await generate_report(
        period_start=inp["period_start"],
        period_end=inp.get("period_end"),
    )


# ── Simulation with DB persistence ──────────────────────────────────────────


async def _run_simulation_and_persist(inp: dict) -> dict:
    """Run a backtest simulation and persist the result to the DB."""
    result = run_simulation(**inp)
    if "error" in result:
        return result

    try:
        async with async_session() as session:
            sim = SimulationResult(
                name=result["name"],
                strategy=result["strategy"],
                initial_capital=result["initial_capital"],
                final_value=result["final_value"],
                total_return_pct=result.get("total_return_pct", 0.0),
                sharpe_ratio=result.get("sharpe_ratio"),
                max_drawdown_pct=result.get("max_drawdown_pct"),
                trades_count=result["trades_count"],
                period_start=result["period_start"],
                period_end=result["period_end"],
                equity_curve=result["equity_curve"],
            )
            session.add(sim)
            await session.commit()
            result["simulation_id"] = sim.id
            logger.info("Simulation '%s' persisted (id=%s)", sim.name, sim.id)
    except Exception as exc:
        logger.warning("Failed to persist simulation result: %s", exc)

    return result
