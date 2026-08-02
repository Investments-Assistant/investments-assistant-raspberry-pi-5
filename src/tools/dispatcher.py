"""Tool dispatcher — maps tool names to their Python implementations."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import inspect
import json
import math
import re
import secrets
import time
import uuid

from sqlalchemy import select, update

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
from src.tools.nft import assess_nft_risk
from src.tools.portfolio import get_account_info, get_portfolio_summary, get_trade_history
from src.tools.simulator import run_simulation

logger = get_logger(__name__)

_tool_session_id: ContextVar[str | None] = ContextVar("tool_session_id", default=None)
_pending_trade_proposals: dict[str, dict] = {}
_PROPOSAL_TTL_SECONDS = 15 * 60
_VALID_BROKERS = {"alpaca", "ibkr", "coinbase", "binance"}
_VALID_ORDER_TYPES = {"market", "limit", "stop_limit"}


@contextmanager
def tool_context(session_id: str):
    """Attach the authenticated chat session to agent tool calls."""
    token = _tool_session_id.set(session_id)
    try:
        yield
    finally:
        _tool_session_id.reset(token)


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
    "assess_nft_risk": lambda inp: assess_nft_risk(**inp),
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
        # yfinance, broker SDKs, and PDF generation are synchronous.  Keep
        # them off FastAPI's event loop so one slow network call cannot freeze
        # the UI or the scheduler.
        return await asyncio.to_thread(_SYNC_DISPATCH[name], inp)  # type: ignore[arg-type]
    if name in _ASYNC_DISPATCH:
        return await _ASYNC_DISPATCH[name](inp)  # type: ignore[operator]
    if name == "execute_trade":
        return await _execute_trade(inp)
    if name == "confirm_trade":
        return await _confirm_trade(inp)
    if name == "cancel_order":
        return await asyncio.to_thread(_cancel_order, inp)
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
            if inspect.isawaitable(record):
                record = await record
            return bool(
                record is not None
                and getattr(record, "auto_trading_halted", False) is True
            )
    except Exception as exc:
        # A safety control must fail closed.  A temporary database outage must
        # never turn into permission to trade without a loss-limit check.
        logger.error("Failed to check daily halt flag; blocking auto-trading: %s", exc)
        return True


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


def _validate_trade_input(inp: dict) -> tuple[dict | None, str | None]:
    """Validate and normalise an order before it reaches any broker SDK."""
    broker = str(inp.get("broker", "")).lower().strip()
    symbol = str(inp.get("symbol", "")).upper().strip()
    side = str(inp.get("side", "")).lower().strip()
    order_type = str(inp.get("order_type", "market")).lower().strip()
    try:
        quantity = float(inp.get("quantity"))
    except (TypeError, ValueError):
        quantity = 0.0
    if broker not in _VALID_BROKERS:
        return None, f"Unknown broker: {broker or 'missing'}"
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-/]{0,31}", symbol):
        return None, "Invalid symbol"
    if side not in {"buy", "sell"}:
        return None, "side must be 'buy' or 'sell'"
    if not math.isfinite(quantity) or quantity <= 0:
        return None, "quantity must be a positive finite number"
    if order_type not in _VALID_ORDER_TYPES:
        return None, f"Unsupported order type: {order_type}"

    def numeric(name: str) -> float | None:
        value = inp.get(name)
        if value is None or value == "":
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    limit_price = numeric("limit_price")
    stop_price = numeric("stop_price")
    if order_type == "limit" and limit_price is None:
        return None, "limit_price is required for limit orders"
    if order_type == "stop_limit" and (limit_price is None or stop_price is None):
        return None, "limit_price and stop_price are required for stop_limit orders"

    asset_type = str(inp.get("asset_type", "")).lower().strip()
    if not asset_type:
        has_option_fields = any(
            inp.get(field) not in (None, "")
            for field in ("option_expiry", "option_strike", "option_right")
        )
        if has_option_fields:
            asset_type = "option"
        elif broker == "ibkr" and re.fullmatch(r"[A-Z]{3}[/-]?[A-Z]{3}", symbol):
            asset_type = "forex"
        else:
            asset_type = "stock"
    if asset_type not in {"stock", "etf", "option", "forex", "crypto"}:
        return None, f"Unsupported asset_type: {asset_type}"
    if asset_type in {"option", "forex"} and broker != "ibkr":
        return None, f"{asset_type} orders require broker='ibkr'"
    if asset_type == "crypto" and broker not in {"coinbase", "binance"}:
        return None, "crypto orders require broker='coinbase' or broker='binance'"

    option_expiry = str(inp.get("option_expiry", "")).strip()
    option_right = str(inp.get("option_right", "")).upper().strip()
    option_strike = numeric("option_strike")
    if asset_type == "option":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", option_expiry):
            return None, "option_expiry must use YYYY-MM-DD format"
        try:
            datetime.strptime(option_expiry, "%Y-%m-%d")
        except ValueError:
            return None, "option_expiry must be a valid calendar date"
        if option_strike is None:
            return None, "option_strike must be a positive finite number"
        if option_right not in {"C", "P"}:
            return None, "option_right must be 'C' or 'P'"
    elif any(
        inp.get(field) not in (None, "")
        for field in ("option_expiry", "option_strike", "option_right")
    ):
        return None, "option fields are only valid when asset_type='option'"

    estimated_notional = inp.get("estimated_notional_usd")
    if estimated_notional not in (None, ""):
        try:
            estimated_notional = float(estimated_notional)
        except (TypeError, ValueError):
            return None, "estimated_notional_usd must be a positive finite number"
        if not math.isfinite(estimated_notional) or estimated_notional <= 0:
            return None, "estimated_notional_usd must be a positive finite number"
    return {
        "broker": broker,
        "symbol": symbol,
        "asset_type": asset_type,
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "option_expiry": option_expiry or None,
        "option_strike": option_strike,
        "option_right": option_right or None,
        "reason": str(inp.get("reason", "")).strip()[:2_000],
        "estimated_notional_usd": estimated_notional,
    }, None


def _setting_number(name: str, default: float) -> float:
    try:
        value = float(getattr(settings, name))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _live_route_allowed(broker: str) -> bool:
    """Allow paper/testnet routes by default; live routes need an explicit flag."""
    if bool(getattr(settings, "live_trading_enabled", False)):
        return True
    if broker == "alpaca":
        return bool(getattr(settings, "alpaca_paper", True))
    if broker == "binance":
        return bool(getattr(settings, "binance_testnet", True))
    if broker == "ibkr":
        return int(getattr(settings, "ibkr_port", 4002)) == 4002
    return False


def _auto_notional_ok(trade: dict) -> tuple[bool, str | None]:
    """Enforce a dollar cap without guessing the value of market orders."""
    allow_market_setting = getattr(settings, "auto_allow_market_orders", False)
    if trade["order_type"] == "market" and allow_market_setting is False:
        return False, "Auto mode requires limit orders unless AUTO_ALLOW_MARKET_ORDERS=true."
    raw_notional = trade.get("estimated_notional_usd")
    if raw_notional is None and trade.get("limit_price") is not None:
        raw_notional = trade["quantity"] * trade["limit_price"]
        if trade.get("asset_type") == "option":
            # Standard US equity options represent 100 underlying shares.
            raw_notional *= 100
    try:
        notional = float(raw_notional)
    except (TypeError, ValueError):
        notional = 0.0
    # Test doubles and older external callers may not provide the new policy
    # field.  Real Settings always supplies a bool; only that explicit
    # production value is allowed to bypass the fail-closed check below.
    if notional <= 0 and not isinstance(allow_market_setting, bool):
        return True, None
    max_trade = _setting_number("auto_max_trade_usd", 500.0)
    if not math.isfinite(notional) or notional <= 0:
        return False, "Auto mode needs a positive estimated_notional_usd or limit_price."
    if notional > max_trade:
        return False, f"Estimated trade value ${notional:.2f} exceeds the ${max_trade:.2f} cap."
    return True, None


def _remember_proposal(trade: dict) -> str:
    now = time.time()
    for proposal_id, proposal in list(_pending_trade_proposals.items()):
        if proposal["expires_at"] <= now:
            _pending_trade_proposals.pop(proposal_id, None)
    proposal_id = secrets.token_urlsafe(18)
    _pending_trade_proposals[proposal_id] = {
        "session_id": _tool_session_id.get(),
        "trade": trade,
        "expires_at": now + _PROPOSAL_TTL_SECONDS,
    }
    return proposal_id


async def _create_trade_intent(trade: dict, mode: str) -> str | None:
    """Persist an auditable pending intent before contacting a broker.

    A broker order should never be submitted when the audit database is down.
    The follow-up update can still fail after a broker accepts an order, so
    that condition is reported loudly rather than pretending the audit trail
    is complete.
    """
    intent_id = str(uuid.uuid4())
    try:
        async with async_session() as session:
            added = session.add(
                Trade(
                    id=intent_id,
                    broker=trade["broker"],
                    symbol=trade["symbol"],
                    side=trade["side"],
                    quantity=trade["quantity"],
                    price=trade.get("limit_price"),
                    order_type=trade["order_type"],
                    status="pending",
                    mode=mode,
                    reason=trade.get("reason", ""),
                )
            )
            if inspect.isawaitable(added):
                await added
            await session.commit()
        return intent_id
    except Exception as exc:
        logger.error("Trade intent was not persisted; order was not sent: %s", exc)
        return None


async def _finalize_trade_intent(intent_id: str, result: dict) -> bool:
    """Update the pre-trade intent with the broker response."""
    try:
        status = result.get("status") or ("rejected" if "error" in result else "submitted")
        async with async_session() as session:
            await session.execute(
                update(Trade)
                .where(Trade.id == intent_id)
                .values(
                    status=status,
                    broker_order_id=result.get("order_id"),
                    price=result.get("filled_avg_price"),
                )
            )
            await session.commit()
        return True
    except Exception as exc:
        logger.error("Trade %s was sent but audit finalization failed: %s", intent_id, exc)
        return False


async def _execute_validated_trade(trade: dict, mode: str) -> dict:
    if not _live_route_allowed(trade["broker"]):
        return {
            "blocked": True,
            "reason": (
                "Live broker routing is disabled. Enable LIVE_TRADING_ENABLED only after "
                "paper/testnet validation and an explicit risk review."
            ),
        }
    intent_id = await _create_trade_intent(trade, mode)
    if intent_id is None:
        return {
            "blocked": True,
            "reason": "Trade audit database is unavailable; the order was not sent.",
        }
    try:
        result = await asyncio.to_thread(
            _route_order,
            trade["broker"],
            trade["symbol"],
            trade["side"],
            trade["quantity"],
            trade["order_type"],
            trade.get("limit_price"),
            trade.get("stop_price"),
            trade.get("asset_type"),
            trade.get("option_expiry"),
            trade.get("option_strike"),
            trade.get("option_right"),
        )
    except Exception as exc:
        logger.exception("Broker route failed for trade intent %s", intent_id)
        result = {"error": str(exc)}
    result["reason"] = trade.get("reason", "")
    result["audit_intent_id"] = intent_id
    if not await _finalize_trade_intent(intent_id, result):
        result["audit_warning"] = "Broker response received, but database finalization failed."
    realized = result.get("realized_pnl_usd", result.get("pnl_usd"))
    try:
        if realized is not None and math.isfinite(float(realized)):
            await _check_and_enforce_daily_limit(float(realized))
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid realized P&L returned by broker: %r", realized)
    return result


async def _execute_trade(inp: dict) -> dict:
    trade, error = _validate_trade_input(inp)
    if error:
        return {"blocked": True, "reason": error}

    if settings.trading_mode == "recommend":
        confirmation_id = _remember_proposal(trade)
        return {
            "status": "pending_confirmation",
            "message": (
                f"RECOMMENDATION: {trade['side'].upper()} {trade['quantity']} {trade['symbol']} "
                f"via {trade['broker']} ({trade['order_type']} order). "
                f"Reason: {trade['reason']}. Reply with the confirmation ID and explicit approval."
            ),
            "confirmation_id": confirmation_id,
            "trade_details": trade,
        }

    # AUTO mode — check safety guards before executing
    if (
        settings.auto_allowed_symbols_set
        and trade["symbol"] not in settings.auto_allowed_symbols_set
    ):
        return {
            "blocked": True,
            "reason": f"{trade['symbol']} is not in the auto-trading allowed symbols list.",
        }

    if await _is_daily_halted():
        return {
            "blocked": True,
            "reason": (
                f"Auto-trading is halted for today: daily loss limit of "
                f"{settings.auto_daily_loss_limit_usd} USD has been reached."
            ),
        }
    notional_ok, notional_error = _auto_notional_ok(trade)
    if not notional_ok:
        return {"blocked": True, "reason": notional_error}
    return await _execute_validated_trade(trade, "auto")


async def _confirm_trade(inp: dict) -> dict:
    """Execute one server-created proposal after explicit user approval."""
    confirmation_id = str(inp.get("confirmation_id", ""))
    proposal = _pending_trade_proposals.get(confirmation_id)
    if not proposal or proposal["expires_at"] <= time.time():
        _pending_trade_proposals.pop(confirmation_id, None)
        return {"blocked": True, "reason": "Unknown or expired confirmation ID."}
    if proposal["session_id"] != _tool_session_id.get():
        return {"blocked": True, "reason": "Confirmation belongs to another chat session."}
    _pending_trade_proposals.pop(confirmation_id, None)
    result = await _execute_validated_trade(proposal["trade"], "manual")
    result["confirmation_id"] = confirmation_id
    return result


def _route_order(
    broker: str,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str,
    limit_price: float | None,
    stop_price: float | None,
    asset_type: str | None = None,
    option_expiry: str | None = None,
    option_strike: float | None = None,
    option_right: str | None = None,
) -> dict:
    if broker == "alpaca":
        return alpaca_tool.submit_alpaca_order(
            symbol, side, quantity, order_type, limit_price, stop_price
        )
    if broker == "ibkr":
        return ibkr_tool.submit_ibkr_order(
            symbol,
            side,
            quantity,
            order_type,
            limit_price,
            stop_price,
            asset_type=asset_type,
            option_expiry=option_expiry,
            option_strike=option_strike,
            option_right=option_right,
        )
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
