"""Unit tests for src/tools/dispatcher.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.dispatcher import (
    _cancel_order,
    _execute_trade,
    _route_order,
    _set_trading_mode,
    dispatch_tool,
)

# ---------------------------------------------------------------------------
# dispatch_tool  (public entry-point)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDispatchTool:
    async def test_returns_json_string(self):
        with patch("src.tools.dispatcher._dispatch", new=AsyncMock(return_value={"ok": True})):
            result = await dispatch_tool("get_stock_data", {"symbol": "AAPL"})

        assert isinstance(result, str)
        assert json.loads(result) == {"ok": True}

    async def test_exception_returns_error_json(self):
        with patch(
            "src.tools.dispatcher._dispatch", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            result = await dispatch_tool("bad_tool", {})

        payload = json.loads(result)
        assert "error" in payload
        assert payload["tool"] == "bad_tool"

    async def test_non_serialisable_values_coerced(self):
        """Non-serialisable objects (e.g. datetime) must not raise."""
        from datetime import date

        with patch(
            "src.tools.dispatcher._dispatch", new=AsyncMock(return_value={"d": date(2024, 1, 1)})
        ):
            result = await dispatch_tool("anything", {})

        assert "2024-01-01" in result


# ---------------------------------------------------------------------------
# _set_trading_mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetTradingMode:
    def test_recommend_mode_accepted(self):
        with patch("src.tools.dispatcher.settings"):
            result = _set_trading_mode("recommend")

        assert result["success"] is True
        assert result["trading_mode"] == "recommend"

    def test_auto_mode_accepted(self):
        with patch("src.tools.dispatcher.settings"):
            result = _set_trading_mode("auto")

        assert result["success"] is True
        assert result["trading_mode"] == "auto"

    def test_invalid_mode_returns_error(self):
        result = _set_trading_mode("yolo")
        assert "error" in result


# ---------------------------------------------------------------------------
# _route_order
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRouteOrder:
    def test_alpaca_dispatched(self):
        mock_submit = MagicMock(return_value={"order_id": "abc"})
        with patch("src.tools.dispatcher.alpaca_tool") as mock_alpaca:
            mock_alpaca.submit_alpaca_order = mock_submit
            result = _route_order("alpaca", "AAPL", "buy", 1.0, "market", None, None)

        assert result == {"order_id": "abc"}
        mock_submit.assert_called_once_with("AAPL", "buy", 1.0, "market", None, None)

    def test_ibkr_dispatched(self):
        mock_submit = MagicMock(return_value={"order_id": "ibkr-1"})
        with patch("src.tools.dispatcher.ibkr_tool") as mock_ibkr:
            mock_ibkr.submit_ibkr_order = mock_submit
            result = _route_order("ibkr", "SPY", "sell", 2.0, "limit", 450.0, None)

        assert result == {"order_id": "ibkr-1"}

    def test_coinbase_dispatched(self):
        mock_submit = MagicMock(return_value={"order_id": "cb-1"})
        with patch("src.tools.dispatcher.coinbase") as mock_cb:
            mock_cb.submit_coinbase_order = mock_submit
            result = _route_order("coinbase", "BTC-USD", "buy", 0.1, "market", None, None)

        assert result == {"order_id": "cb-1"}

    def test_binance_dispatched(self):
        mock_submit = MagicMock(return_value={"order_id": "bn-1"})
        with patch("src.tools.dispatcher.binance_tool") as mock_bn:
            mock_bn.submit_binance_order = mock_submit
            result = _route_order("binance", "ETHUSDT", "buy", 1.0, "market", None, None)

        assert result == {"order_id": "bn-1"}

    def test_unknown_broker_returns_error(self):
        result = _route_order("robinhood", "AAPL", "buy", 1.0, "market", None, None)
        assert "error" in result


# ---------------------------------------------------------------------------
# _cancel_order
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelOrder:
    def test_alpaca_cancel_dispatched(self):
        mock_cancel = MagicMock(return_value={"cancelled": True})
        with patch("src.tools.dispatcher.alpaca_tool") as mock_alpaca:
            mock_alpaca.cancel_alpaca_order = mock_cancel
            result = _cancel_order({"broker": "alpaca", "order_id": "order-1"})

        assert result == {"cancelled": True}
        mock_cancel.assert_called_once_with("order-1")

    def test_ibkr_cancel_dispatched(self):
        mock_cancel = MagicMock(return_value={"cancelled": True})
        with patch("src.tools.dispatcher.ibkr_tool") as mock_ibkr:
            mock_ibkr.cancel_ibkr_order = mock_cancel
            result = _cancel_order({"broker": "ibkr", "order_id": "ibkr-order-1"})

        assert result == {"cancelled": True}

    def test_unknown_broker_returns_error(self):
        result = _cancel_order({"broker": "unknown", "order_id": "x"})
        assert "error" in result


# ---------------------------------------------------------------------------
# _execute_trade
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteTrade:
    def _trade_input(self, **overrides) -> dict:
        base = {
            "broker": "alpaca",
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 1,
            "order_type": "market",
            "reason": "test",
        }
        base.update(overrides)
        return base

    async def test_recommend_mode_returns_pending_confirmation(self):
        with patch("src.tools.dispatcher.settings") as mock_cfg:
            mock_cfg.trading_mode = "recommend"
            mock_cfg.auto_allowed_symbols_set = set()

            result = await _execute_trade(self._trade_input())

        assert result["status"] == "pending_confirmation"
        assert "trade_details" in result

    async def test_auto_mode_executes_and_persists(self):
        mock_order = {"order_id": "auto-1", "status": "submitted"}
        with patch("src.tools.dispatcher.settings") as mock_cfg:
            mock_cfg.trading_mode = "auto"
            mock_cfg.auto_allowed_symbols_set = set()

            with patch("src.tools.dispatcher._route_order", return_value=mock_order):
                with patch("src.tools.dispatcher.async_session") as mock_session_cls:
                    session = AsyncMock()
                    session.__aenter__ = AsyncMock(return_value=session)
                    session.__aexit__ = AsyncMock(return_value=False)
                    mock_session_cls.return_value = session

                    result = await _execute_trade(self._trade_input())

        assert result["order_id"] == "auto-1"

    async def test_auto_mode_symbol_blocked_when_not_in_allowlist(self):
        with patch("src.tools.dispatcher.settings") as mock_cfg:
            mock_cfg.trading_mode = "auto"
            mock_cfg.auto_allowed_symbols_set = {"SPY", "QQQ"}

            result = await _execute_trade(self._trade_input(symbol="AAPL"))

        assert result["blocked"] is True

    async def test_auto_mode_symbol_allowed_when_in_allowlist(self):
        mock_order = {"order_id": "1", "status": "submitted"}
        with patch("src.tools.dispatcher.settings") as mock_cfg:
            mock_cfg.trading_mode = "auto"
            mock_cfg.auto_allowed_symbols_set = {"SPY", "AAPL"}

            with patch("src.tools.dispatcher._route_order", return_value=mock_order):
                with patch("src.tools.dispatcher.async_session") as mock_session_cls:
                    session = AsyncMock()
                    session.__aenter__ = AsyncMock(return_value=session)
                    session.__aexit__ = AsyncMock(return_value=False)
                    mock_session_cls.return_value = session

                    result = await _execute_trade(self._trade_input(symbol="AAPL"))

        assert "blocked" not in result

    async def test_db_persist_failure_does_not_raise(self):
        """A DB failure during persist should be swallowed — trade result is still returned."""
        mock_order = {"order_id": "x", "status": "submitted"}
        with patch("src.tools.dispatcher.settings") as mock_cfg:
            mock_cfg.trading_mode = "auto"
            mock_cfg.auto_allowed_symbols_set = set()

            with patch("src.tools.dispatcher._route_order", return_value=mock_order):
                with patch(
                    "src.tools.dispatcher.async_session", side_effect=RuntimeError("db down")
                ):
                    result = await _execute_trade(self._trade_input())

        # Trade result should still be returned despite DB error
        assert result["order_id"] == "x"
