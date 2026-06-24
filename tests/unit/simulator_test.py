"""Unit tests for src/tools/simulator.py."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.tools.simulator import (
    _crossover_signal,
    _metrics,
    run_simulation,
)

# ---------------------------------------------------------------------------
# _crossover_signal
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossoverSignal:
    """Pure function — tests all three return branches."""

    def _make_series(self, values: list[float]) -> pd.Series:
        return pd.Series(values, dtype=float)

    def test_buy_when_fast_crosses_above_slow(self):
        # Arrange — fast was below slow (i-1), now above (i)
        fast = self._make_series([90.0, 101.0])  # index 0 → below, index 1 → above
        slow = self._make_series([100.0, 100.0])
        # Act
        result = _crossover_signal(fast, slow, i=1)
        # Assert
        assert result == "buy"

    def test_sell_when_fast_crosses_below_slow(self):
        # fast was above slow (i-1), now below (i)
        fast = self._make_series([110.0, 99.0])
        slow = self._make_series([100.0, 100.0])
        result = _crossover_signal(fast, slow, i=1)
        assert result == "sell"

    def test_none_when_no_crossover(self):
        # fast consistently above slow — no crossover
        fast = self._make_series([110.0, 111.0])
        slow = self._make_series([100.0, 100.0])
        result = _crossover_signal(fast, slow, i=1)
        assert result is None

    def test_none_when_values_equal(self):
        # Both equal — no strict crossover
        fast = self._make_series([100.0, 100.0])
        slow = self._make_series([100.0, 100.0])
        result = _crossover_signal(fast, slow, i=1)
        assert result is None

    def test_buy_at_exact_equality_boundary(self):
        # i-1: fast == slow (not strictly above), i: fast > slow → buy
        fast = self._make_series([100.0, 101.0])
        slow = self._make_series([100.0, 100.0])
        result = _crossover_signal(fast, slow, i=1)
        assert result == "buy"


# ---------------------------------------------------------------------------
# _metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetrics:
    def test_empty_series_returns_empty_dict(self):
        assert _metrics(pd.Series([], dtype=float)) == {}

    def test_single_element_returns_empty_dict(self):
        assert _metrics(pd.Series([100.0])) == {}

    def test_positive_return_computed(self):
        # Arrange — equity doubles
        equity = pd.Series([100.0, 200.0])
        # Act
        result = _metrics(equity)
        # Assert
        assert result["total_return_pct"] == pytest.approx(100.0, abs=0.1)

    def test_negative_return_computed(self):
        equity = pd.Series([100.0, 50.0])
        result = _metrics(equity)
        assert result["total_return_pct"] == pytest.approx(-50.0, abs=0.1)

    def test_zero_volatility_gives_zero_sharpe(self):
        # Flat equity → daily returns all zero → std = 0
        equity = pd.Series([100.0] * 10)
        result = _metrics(equity)
        assert result["sharpe_ratio"] == 0.0

    def test_max_drawdown_negative(self):
        # Peak at 200, drops to 100 → drawdown = -50%
        equity = pd.Series([100.0, 200.0, 100.0])
        result = _metrics(equity)
        assert result["max_drawdown_pct"] <= 0

    def test_keys_present(self):
        equity = pd.Series([100.0, 110.0, 105.0, 115.0])
        result = _metrics(equity)
        assert {
            "total_return_pct",
            "sharpe_ratio",
            "max_drawdown_pct",
            "annual_volatility_pct",
        } <= result.keys()


# ---------------------------------------------------------------------------
# run_simulation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunSimulation:
    def _make_prices(self) -> pd.DataFrame:
        dates = pd.date_range("2023-01-01", periods=100, freq="D")
        p = 100 + pd.Series(range(100), dtype=float) * 0.5
        return pd.DataFrame({"AAPL": p.values}, index=dates)

    def test_buy_and_hold_returns_result(self):
        prices = self._make_prices()
        with patch("src.tools.simulator._download", return_value=prices):
            result = run_simulation(
                name="test",
                symbols=["AAPL"],
                strategy={"type": "buy_and_hold"},
                initial_capital=10_000,
                period_start="2023-01-01",
            )
        assert "final_value" in result
        assert "equity_curve" in result
        assert result["initial_capital"] == 10_000

    def test_sma_crossover_strategy(self):
        prices = self._make_prices()
        with patch("src.tools.simulator._download", return_value=prices):
            result = run_simulation(
                name="sma",
                symbols=["AAPL"],
                strategy={"type": "sma_crossover", "params": {"fast": 5, "slow": 10}},
                initial_capital=10_000,
                period_start="2023-01-01",
            )
        assert "error" not in result

    def test_rsi_mean_reversion_strategy(self):
        prices = self._make_prices()
        with patch("src.tools.simulator._download", return_value=prices):
            result = run_simulation(
                name="rsi",
                symbols=["AAPL"],
                strategy={"type": "rsi_mean_reversion"},
                initial_capital=10_000,
                period_start="2023-01-01",
            )
        assert "error" not in result

    def test_unknown_strategy_returns_error(self):
        prices = self._make_prices()
        with patch("src.tools.simulator._download", return_value=prices):
            result = run_simulation(
                name="unknown",
                symbols=["AAPL"],
                strategy={"type": "magic_strategy"},
                initial_capital=10_000,
                period_start="2023-01-01",
            )
        assert "error" in result

    def test_empty_prices_returns_error(self):
        with patch("src.tools.simulator._download", return_value=pd.DataFrame()):
            result = run_simulation(
                name="empty",
                symbols=["FAKE"],
                strategy={"type": "buy_and_hold"},
                initial_capital=10_000,
                period_start="2023-01-01",
            )
        assert "error" in result

    def test_download_failure_returns_error(self):
        with patch("src.tools.simulator._download", side_effect=Exception("network")):
            result = run_simulation(
                name="fail",
                symbols=["AAPL"],
                strategy={"type": "buy_and_hold"},
                initial_capital=10_000,
                period_start="2023-01-01",
            )
        assert "error" in result
