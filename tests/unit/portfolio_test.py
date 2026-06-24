"""Unit tests for src/tools/portfolio.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tools.portfolio import (
    _collect_broker,
    get_account_info,
    get_portfolio_summary,
    get_trade_history,
)

# ---------------------------------------------------------------------------
# _collect_broker
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCollectBroker:
    def _make_result(self) -> dict:
        return {
            "positions": [],
            "accounts": [],
            "total_market_value_usd": 0.0,
            "total_unrealized_pnl_usd": 0.0,
        }

    def test_appends_account_and_position(self):
        # Arrange
        account_fn = MagicMock(return_value={"broker": "alpaca", "equity": 5000.0})
        positions_fn = MagicMock(
            return_value=[{"symbol": "AAPL", "market_value": 1000.0, "unrealized_pl": 50.0}]
        )
        result = self._make_result()

        # Act
        _collect_broker("alpaca", positions_fn, account_fn, result)

        # Assert
        assert len(result["accounts"]) == 1
        assert len(result["positions"]) == 1
        assert result["positions"][0]["broker"] == "alpaca"
        assert result["total_market_value_usd"] == pytest.approx(1000.0)
        assert result["total_unrealized_pnl_usd"] == pytest.approx(50.0)

    def test_account_error_not_appended(self):
        # Accounts with "error" key are skipped
        account_fn = MagicMock(return_value={"error": "auth failed"})
        positions_fn = MagicMock(return_value=[])
        result = self._make_result()

        _collect_broker("alpaca", positions_fn, account_fn, result)

        assert result["accounts"] == []

    def test_position_error_not_appended(self):
        account_fn = MagicMock(return_value={"broker": "alpaca"})
        positions_fn = MagicMock(return_value=[{"error": "no data"}])
        result = self._make_result()

        _collect_broker("alpaca", positions_fn, account_fn, result)

        assert result["positions"] == []

    def test_exception_logged_and_skipped(self):
        account_fn = MagicMock(side_effect=RuntimeError("broker down"))
        positions_fn = MagicMock(return_value=[])
        result = self._make_result()

        # Act — should not raise
        _collect_broker("alpaca", positions_fn, account_fn, result)

        assert result["positions"] == []

    def test_missing_market_value_treated_as_zero(self):
        account_fn = MagicMock(return_value={"broker": "ibkr"})
        positions_fn = MagicMock(return_value=[{"symbol": "SPY"}])  # no market_value
        result = self._make_result()

        _collect_broker("ibkr", positions_fn, account_fn, result)

        assert result["total_market_value_usd"] == pytest.approx(0.0)

    def test_unrealized_pnl_from_unrealized_pl_key(self):
        """Some brokers use 'unrealized_pl', others 'unrealized_pnl'."""
        account_fn = MagicMock(return_value={"broker": "ibkr"})
        positions_fn = MagicMock(
            return_value=[{"symbol": "SPY", "market_value": 500.0, "unrealized_pl": 25.0}]
        )
        result = self._make_result()

        _collect_broker("ibkr", positions_fn, account_fn, result)

        assert result["total_unrealized_pnl_usd"] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# get_portfolio_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPortfolioSummary:
    def test_all_brokers_queried_when_no_filter(self):
        called = []

        def fake_collect(name, pos_fn, acc_fn, result):
            called.append(name)

        with patch("src.tools.portfolio._collect_broker", side_effect=fake_collect):
            get_portfolio_summary()

        assert "alpaca" in called
        assert "ibkr" in called
        assert "coinbase" in called
        assert "binance" in called

    def test_single_broker_filter(self):
        called = []

        def fake_collect(name, pos_fn, acc_fn, result):
            called.append(name)

        with patch("src.tools.portfolio._collect_broker", side_effect=fake_collect):
            get_portfolio_summary(broker="alpaca")

        assert called == ["alpaca"]

    def test_totals_rounded_to_two_decimals(self):
        def fake_collect(name, pos_fn, acc_fn, result):
            result["total_market_value_usd"] += 1000.123456
            result["total_unrealized_pnl_usd"] += 12.987654

        with patch("src.tools.portfolio._collect_broker", side_effect=fake_collect):
            result = get_portfolio_summary()

        # After 4 brokers: 4 * 1000.123456 = 4000.493824 → rounded to 4000.49
        assert result["total_market_value_usd"] == round(result["total_market_value_usd"], 2)

    def test_returns_expected_structure(self):
        with patch("src.tools.portfolio._collect_broker"):
            result = get_portfolio_summary()

        assert "positions" in result
        assert "accounts" in result
        assert "total_market_value_usd" in result
        assert "total_unrealized_pnl_usd" in result


# ---------------------------------------------------------------------------
# get_account_info
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAccountInfo:
    def test_known_broker_dispatched(self):
        mock_fn = MagicMock(return_value={"equity": 5000})
        with patch("src.tools.portfolio.alpaca_tool") as mock_alpaca:
            mock_alpaca.get_alpaca_account = mock_fn
            result = get_account_info("alpaca")
        assert result == {"equity": 5000}

    def test_unknown_broker_returns_error(self):
        result = get_account_info("unknown_broker")
        assert "error" in result


# ---------------------------------------------------------------------------
# get_trade_history
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTradeHistory:
    def test_unknown_broker_returns_error_list(self):
        result = get_trade_history("unknown")
        assert isinstance(result, list)
        assert "error" in result[0]

    def test_known_broker_dispatched(self):
        mock_orders = [{"id": "1", "symbol": "AAPL"}]
        with patch("src.tools.portfolio.alpaca_tool") as mock_alpaca:
            mock_alpaca.get_alpaca_orders = MagicMock(return_value=mock_orders)
            result = get_trade_history("alpaca", days=10)
        assert result == mock_orders
