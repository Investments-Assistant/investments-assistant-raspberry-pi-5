"""Unit tests for src/tools/market_data.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.tools.market_data import (
    _build_signals,
    _clean_option_list,
    _df_to_records,
    _option_rows,
    get_options_chain,
    get_stock_data,
    get_technical_indicators,
)

# ---------------------------------------------------------------------------
# _build_signals
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSignals:
    """_build_signals — pure function covering all branch combinations."""

    def test_rsi_oversold_produces_bullish_signal(self):
        # Arrange
        signals = _build_signals(
            rsi=25.0,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=100,
            ema200=90,
            bb_upper=110,
            bb_lower=90,
        )
        # Assert
        assert any("oversold" in s for s in signals)
        assert any("bullish" in s.lower() for s in signals)

    def test_rsi_overbought_produces_bearish_signal(self):
        signals = _build_signals(
            rsi=75.0,
            macd_val=0.5,
            macd_signal=1.0,
            current_price=100,
            ema200=90,
            bb_upper=110,
            bb_lower=90,
        )
        assert any("overbought" in s for s in signals)

    def test_rsi_neutral_range_produces_no_rsi_signal(self):
        signals = _build_signals(
            rsi=50.0,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=100,
            ema200=90,
            bb_upper=110,
            bb_lower=90,
        )
        assert not any("RSI" in s for s in signals)

    def test_macd_bullish_crossover(self):
        signals = _build_signals(
            rsi=50,
            macd_val=2.0,
            macd_signal=1.0,
            current_price=100,
            ema200=90,
            bb_upper=110,
            bb_lower=90,
        )
        assert any("MACD bullish" in s for s in signals)

    def test_macd_bearish_crossover(self):
        signals = _build_signals(
            rsi=50,
            macd_val=0.5,
            macd_signal=1.5,
            current_price=100,
            ema200=90,
            bb_upper=110,
            bb_lower=90,
        )
        assert any("MACD bearish" in s for s in signals)

    def test_price_above_ema200_uptrend(self):
        signals = _build_signals(
            rsi=50,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=150,
            ema200=100,
            bb_upper=160,
            bb_lower=130,
        )
        assert any("uptrend" in s for s in signals)

    def test_price_below_ema200_downtrend(self):
        signals = _build_signals(
            rsi=50,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=80,
            ema200=100,
            bb_upper=90,
            bb_lower=70,
        )
        assert any("downtrend" in s for s in signals)

    def test_ema200_none_produces_downtrend_signal(self):
        # When ema200 is None the else branch fires → downtrend
        signals = _build_signals(
            rsi=50,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=100,
            ema200=None,
            bb_upper=110,
            bb_lower=90,
        )
        assert any("downtrend" in s for s in signals)

    def test_price_above_upper_bb(self):
        signals = _build_signals(
            rsi=50,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=120,
            ema200=100,
            bb_upper=110,
            bb_lower=90,
        )
        assert any("overextended" in s for s in signals)

    def test_price_below_lower_bb(self):
        signals = _build_signals(
            rsi=50,
            macd_val=1.0,
            macd_signal=0.5,
            current_price=80,
            ema200=None,
            bb_upper=110,
            bb_lower=90,
        )
        assert any("oversold" in s.lower() for s in signals)

    def test_returns_list(self):
        result = _build_signals(50, 1, 0.5, 100, 90, 110, 90)
        assert isinstance(result, list)
        assert len(result) >= 2  # always has MACD + EMA signal


# ---------------------------------------------------------------------------
# _df_to_records
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDfToRecords:
    def _make_ohlcv(self, rows: int = 5) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=rows, freq="D")
        return pd.DataFrame(
            {
                "Open": [100.0] * rows,
                "High": [105.0] * rows,
                "Low": [98.0] * rows,
                "Close": [102.0] * rows,
                "Volume": [1_000_000] * rows,
            },
            index=dates,
        )

    def test_empty_df_returns_empty_list(self):
        assert _df_to_records(pd.DataFrame()) == []

    def test_returns_correct_number_of_records(self):
        df = self._make_ohlcv(10)
        records = _df_to_records(df, max_rows=10)
        assert len(records) == 10

    def test_max_rows_limits_output(self):
        df = self._make_ohlcv(100)
        records = _df_to_records(df, max_rows=30)
        assert len(records) == 30

    def test_record_has_expected_keys(self):
        df = self._make_ohlcv(1)
        record = _df_to_records(df)[0]
        assert {"date", "open", "high", "low", "close", "volume"} <= record.keys()

    def test_values_are_rounded(self):
        df = self._make_ohlcv(1)
        record = _df_to_records(df)[0]
        # Round to 4 decimal places — no more
        assert record["close"] == round(record["close"], 4)


# ---------------------------------------------------------------------------
# _option_rows / _clean_option_list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOptionRows:
    def _make_options_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "strike": [100.0, 105.0],
                "bid": [2.5, 1.5],
                "ask": [2.7, 1.7],
                "lastPrice": [2.6, 1.6],
                "impliedVolatility": [0.25, 0.30],
                "openInterest": [100, 200],
                "delta": [0.5, 0.4],
                "gamma": [0.05, 0.04],
            }
        )

    def test_empty_df_returns_empty_list(self):
        assert _option_rows(pd.DataFrame()) == []

    def test_returns_up_to_20_rows(self):
        big_df = pd.concat([self._make_options_df()] * 15, ignore_index=True)
        result = _option_rows(big_df)
        assert len(result) == 20

    def test_returns_dicts_with_correct_keys(self):
        result = _option_rows(self._make_options_df())
        assert all("strike" in r and "bid" in r for r in result)


@pytest.mark.unit
class TestCleanOptionList:
    def test_nan_replaced_with_none(self):
        items = [{"strike": float("nan"), "bid": 1.0}]
        result = _clean_option_list(items)
        assert result[0]["strike"] is None

    def test_numpy_scalar_unwrapped(self):
        import numpy as np

        items = [{"strike": np.float64(150.0), "bid": np.float64(2.5)}]
        result = _clean_option_list(items)
        assert isinstance(result[0]["strike"], float)
        assert not hasattr(result[0]["strike"], "item")  # unwrapped from numpy


# ---------------------------------------------------------------------------
# get_stock_data (mocked yfinance)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStockData:
    def _mock_ticker(self):
        mock = MagicMock()
        mock.history.return_value = pd.DataFrame(
            {"Open": [100.0], "High": [105.0], "Low": [98.0], "Close": [102.0], "Volume": [1000]},
            index=pd.date_range("2024-01-01", periods=1),
        )
        mock.info = {"longName": "Apple Inc.", "currentPrice": 102.0, "marketCap": 2e12}
        return mock

    def test_returns_data_for_symbol(self):
        with patch("src.tools.market_data.yf.Ticker", return_value=self._mock_ticker()):
            result = get_stock_data(["AAPL"])
        assert "AAPL" in result
        assert result["AAPL"]["company_name"] == "Apple Inc."

    def test_exception_stored_as_error(self):
        mock = MagicMock()
        mock.history.side_effect = Exception("network error")
        mock.info = {}
        with patch("src.tools.market_data.yf.Ticker", return_value=mock):
            result = get_stock_data(["AAPL"])
        assert "error" in result["AAPL"]


# ---------------------------------------------------------------------------
# get_technical_indicators (mocked yfinance)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTechnicalIndicators:
    def _make_price_df(self, n: int = 60) -> pd.DataFrame:
        """Create a synthetic OHLCV DataFrame with n rows."""
        import numpy as np

        prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame(
            {
                "Open": prices,
                "High": prices + 1,
                "Low": prices - 1,
                "Close": prices,
                "Volume": [1_000_000] * n,
            },
            index=pd.date_range("2023-01-01", periods=n, freq="D"),
        )

    def test_returns_expected_keys(self):
        df = self._make_price_df(60)
        mock = MagicMock()
        mock.history.return_value = df
        with patch("src.tools.market_data.yf.Ticker", return_value=mock):
            result = get_technical_indicators("AAPL")
        assert "rsi_14" in result
        assert "macd" in result
        assert "bollinger_bands" in result
        assert "signals" in result

    def test_insufficient_data_returns_error(self):
        mock = MagicMock()
        mock.history.return_value = pd.DataFrame()  # empty
        with patch("src.tools.market_data.yf.Ticker", return_value=mock):
            result = get_technical_indicators("AAPL")
        assert "error" in result

    def test_yfinance_exception_returns_error(self):
        mock = MagicMock()
        mock.history.side_effect = RuntimeError("timeout")
        with patch("src.tools.market_data.yf.Ticker", return_value=mock):
            result = get_technical_indicators("AAPL")
        assert "error" in result


# ---------------------------------------------------------------------------
# get_options_chain (mocked yfinance)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOptionsChain:
    def test_no_options_returns_error(self):
        mock = MagicMock()
        mock.options = []
        with patch("src.tools.market_data.yf.Ticker", return_value=mock):
            result = get_options_chain("AAPL")
        assert "error" in result

    def test_returns_expiries_dict(self):
        mock = MagicMock()
        mock.options = ("2024-06-21",)
        chain = MagicMock()
        chain.calls = pd.DataFrame(
            {
                "strike": [100.0],
                "bid": [2.0],
                "ask": [2.5],
                "lastPrice": [2.2],
                "impliedVolatility": [0.25],
                "openInterest": [100],
                "delta": [0.5],
                "gamma": [0.05],
            }
        )
        chain.puts = pd.DataFrame(
            {
                "strike": [100.0],
                "bid": [1.5],
                "ask": [2.0],
                "lastPrice": [1.7],
                "impliedVolatility": [0.25],
                "openInterest": [80],
                "delta": [-0.5],
                "gamma": [0.05],
            }
        )
        mock.option_chain.return_value = chain

        with patch("src.tools.market_data.yf.Ticker", return_value=mock):
            result = get_options_chain("AAPL")

        assert "expiries" in result
        assert "2024-06-21" in result["expiries"]
