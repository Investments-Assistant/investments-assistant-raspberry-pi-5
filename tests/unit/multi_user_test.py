"""Focused isolation tests for concurrent local users."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
def test_sessions_are_isolated_by_user_and_conversation_id():
    import src.agent.orchestrator as orchestrator

    previous = orchestrator._sessions.copy()
    orchestrator._sessions.clear()
    try:
        with patch.object(orchestrator, "create_llm_client", return_value=MagicMock()):
            first = orchestrator.get_or_create_session("shared-tab-id", "user-a")
            second = orchestrator.get_or_create_session("shared-tab-id", "user-b")
            first_again = orchestrator.get_or_create_session("shared-tab-id", "user-a")

        assert first is first_again
        assert first is not second
        assert first.user_id == "user-a"
        assert second.user_id == "user-b"
    finally:
        orchestrator._sessions.clear()
        orchestrator._sessions.update(previous)


@pytest.mark.unit
def test_signed_session_preserves_user_identity():
    from src.web.auth import create_session, verify_session

    with patch("src.web.auth.config.settings") as cfg:
        cfg.auth_session_secret = "test-session-secret"
        cfg.auth_username = "bootstrap"
        cfg.auth_password_hash = "configured"
        cfg.auth_session_ttl_minutes = 10
        cfg.authentication_ready = True

        principal = verify_session(create_session("alice", "user-a"))

    assert principal is not None
    assert principal.username == "alice"
    assert principal.user_id == "user-a"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trade_confirmation_cannot_cross_user_boundary():
    from src.tools import dispatcher

    trade = {
        "broker": "alpaca",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1.0,
        "order_type": "limit",
        "limit_price": 100.0,
        "reason": "test",
    }
    with dispatcher.tool_context("conversation", "user-a"):
        confirmation_id = dispatcher._remember_proposal(trade)

    with dispatcher.tool_context("conversation", "user-b"):
        result = await dispatcher._confirm_trade({"confirmation_id": confirmation_id})

    assert result["blocked"] is True
    assert "another chat session" in result["reason"]
    dispatcher._pending_trade_proposals.pop(confirmation_id, None)
