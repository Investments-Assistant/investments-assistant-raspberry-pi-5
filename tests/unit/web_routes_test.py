"""Unit tests for src/web/routes.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

# ---------------------------------------------------------------------------
# Test application — router-only, no lifespan (no DB/scheduler at startup)
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    """Build a minimal FastAPI app with only the routes router."""
    from src.web.routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthEndpoint:
    def test_returns_200(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.trading_mode = "recommend"
            mock_cfg.llm_model_path = "/models/test.gguf"
            mock_cfg.is_development = True

            client = _make_client()
            response = client.get("/api/health")

        assert response.status_code == 200

    def test_response_contains_expected_keys(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.trading_mode = "recommend"
            mock_cfg.llm_model_path = "/models/test.gguf"
            mock_cfg.is_development = True

            client = _make_client()
            data = client.get("/api/health").json()

        assert "status" in data
        assert "timestamp" in data
        assert "trading_mode" in data
        assert "model" in data

    def test_llama_cpp_returns_model_path(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.trading_mode = "recommend"
            mock_cfg.llm_model_path = "/models/qwen.gguf"
            mock_cfg.is_development = True

            client = _make_client()
            data = client.get("/api/health").json()

        assert data["model"] == "/models/qwen.gguf"

    def test_trading_mode_reflected(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.trading_mode = "auto"
            mock_cfg.llm_model_path = "/models/test.gguf"
            mock_cfg.is_development = True

            client = _make_client()
            data = client.get("/api/health").json()

        assert data["trading_mode"] == "auto"


# ---------------------------------------------------------------------------
# /api/market/snapshot
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMarketSnapshotEndpoint:
    def test_returns_snapshot_when_available(self):
        snap = {"indices": {"SPY": 450.0}, "timestamp": "2024-01-01T00:00:00Z"}

        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.get_latest_snapshot", return_value=snap):
                client = _make_client()
                response = client.get("/api/market/snapshot")

        assert response.status_code == 200
        assert response.json()["indices"]["SPY"] == 450.0

    def test_returns_message_when_snapshot_not_available(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.get_latest_snapshot", return_value=None):
                client = _make_client()
                response = client.get("/api/market/snapshot")

        assert response.status_code == 200
        assert "message" in response.json()


# ---------------------------------------------------------------------------
# /api/reports
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListReportsEndpoint:
    def _make_report_mock(self):
        r = MagicMock()
        r.id = "r-1"
        r.title = "Monthly Report"
        r.period_start = MagicMock()
        r.period_start.isoformat.return_value = "2024-01-01"
        r.period_end = MagicMock()
        r.period_end.isoformat.return_value = "2024-01-31"
        r.pdf_path = "/app/reports/jan.pdf"
        r.created_at = MagicMock()
        r.created_at.isoformat.return_value = "2024-02-01T00:00:00"
        return r

    def test_returns_list_of_reports(self):
        report = self._make_report_mock()

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [report]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(return_value=execute_result)

        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.async_session", return_value=session):
                client = _make_client()
                response = client.get("/api/reports")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "r-1"
        assert data[0]["pdf_available"] is True

    def test_db_error_returns_500(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.async_session", return_value=session):
                client = _make_client()
                response = client.get("/api/reports")

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# /api/trades
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTradesEndpoint:
    def _make_trade_mock(self):
        t = MagicMock()
        t.id = "t-1"
        t.broker = "alpaca"
        t.symbol = "AAPL"
        t.side = "buy"
        t.quantity = 1.0
        t.price = None
        t.order_type = "market"
        t.status = "filled"
        t.mode = "auto"
        t.reason = "test"
        t.created_at = MagicMock()
        t.created_at.isoformat.return_value = "2024-01-01T10:00:00"
        return t

    def test_returns_list_of_trades(self):
        trade = self._make_trade_mock()

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [trade]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(return_value=execute_result)

        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.async_session", return_value=session):
                client = _make_client()
                response = client.get("/api/trades")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data[0]["symbol"] == "AAPL"
        assert data[0]["broker"] == "alpaca"

    def test_empty_trades_returns_empty_list(self):
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(return_value=execute_result)

        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.async_session", return_value=session):
                client = _make_client()
                response = client.get("/api/trades")

        assert response.status_code == 200
        assert response.json() == []

    def test_db_error_returns_500(self):
        session = AsyncMock()
        session.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
        session.__aexit__ = AsyncMock(return_value=False)

        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.async_session", return_value=session):
                client = _make_client()
                response = client.get("/api/trades")

        assert response.status_code == 500


# ---------------------------------------------------------------------------
# IP whitelist — require_allowed_ip dependency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequireAllowedIp:
    def test_allowed_ip_gets_through(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = False
            mock_cfg.is_ip_allowed = MagicMock(return_value=True)

            with patch("src.web.routes.get_latest_snapshot", return_value=None):
                client = _make_client()
                response = client.get(
                    "/api/market/snapshot", headers={"X-Forwarded-For": "192.168.1.5"}
                )

        assert response.status_code == 200

    def test_blocked_ip_returns_403(self):
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = False
            mock_cfg.is_ip_allowed = MagicMock(return_value=False)

            client = _make_client()
            response = client.get("/api/market/snapshot", headers={"X-Forwarded-For": "1.2.3.4"})

        assert response.status_code == 403

    def test_development_mode_bypasses_ip_check(self):
        """In development mode the IP check must never fire."""
        with patch("src.web.routes.settings") as mock_cfg:
            mock_cfg.is_development = True
            # is_ip_allowed is never consulted in development mode
            mock_cfg.is_ip_allowed = MagicMock(return_value=False)

            with patch("src.web.routes.get_latest_snapshot", return_value=None):
                client = _make_client()
                response = client.get("/api/market/snapshot")

        assert response.status_code == 200
