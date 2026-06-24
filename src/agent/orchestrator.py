"""Investment agent orchestrator.

Manages conversation history, builds system prompt, and streams responses
from the configured LLM client through to the caller (WebSocket handler).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from src.agent.clients import BaseLLMClient, create_llm_client
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.utils.logger import get_logger
from src.config import settings

logger = get_logger(__name__)


class InvestmentsAssistantOrchestrator:
    """Stateful orchestrator for one chat session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.history: list[dict[str, Any]] = []
        self._client: BaseLLMClient = create_llm_client()

    def _build_system(self) -> str:
        return SYSTEM_PROMPT.format(
            trading_mode=settings.trading_mode,
            auto_max_trade_usd=settings.auto_max_trade_usd,
            auto_daily_loss_limit_usd=settings.auto_daily_loss_limit_usd,
        )

    def _trimmed_history(self) -> list[dict]:
        """Keep the last settings.agent_max_context_messages to stay within context limits."""
        return self.history[-settings.agent_max_context_messages :]

    async def chat(
        self,
        user_message: str,
    ) -> AsyncGenerator[dict, None]:
        """
        Add the user message to history, call the agent, and stream events back.

        Yields dicts:
          {"type": "text_delta", "text": "..."}
          {"type": "tool_call", "name": "...", "input": {...}}
          {"type": "tool_result", "name": "...", "result": "..."}
          {"type": "done"}
        """
        self.history.append({"role": "user", "content": user_message})

        full_response_text = ""
        async for event in self._client.stream_response(
            messages=self._trimmed_history(),
            system=self._build_system(),
        ):
            if event["type"] == "text_delta":
                full_response_text += event["text"]
            yield event

        # Append assistant response to history
        if full_response_text:
            self.history.append({"role": "assistant", "content": full_response_text})

        # Persist messages to DB (best-effort)
        await self._persist_messages(user_message, full_response_text)

    async def _persist_messages(self, user_msg: str, assistant_msg: str) -> None:
        try:
            from src.db.database import async_session
            from src.db.models import ChatMessage

            async with async_session() as session:
                session.add(
                    ChatMessage(
                        session_id=self.session_id,
                        role="user",
                        content=user_msg,
                    )
                )
                if assistant_msg:
                    session.add(
                        ChatMessage(
                            session_id=self.session_id,
                            role="assistant",
                            content=assistant_msg,
                        )
                    )
                await session.commit()
        except Exception as exc:
            logger.warning("Failed to persist chat messages: %s", exc)

    async def load_history_from_db(self) -> None:
        """Restore conversation history from DB for a returning session."""
        try:
            from sqlalchemy import select

            from src.db.database import async_session
            from src.db.models import ChatMessage

            async with async_session() as session:
                result = await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == self.session_id)
                    .order_by(ChatMessage.created_at)
                    .limit(settings.agent_max_context_messages)
                )
                messages = result.scalars().all()
                self.history = [
                    {"role": m.role, "content": m.content}
                    for m in messages
                    if m.role in ("user", "assistant")
                ]
        except Exception as exc:
            logger.warning("Failed to load history from DB: %s", exc)


# ── Global session registry ─────────────────────────────────────────────────
_sessions: dict[str, InvestmentsAssistantOrchestrator] = {}


def get_or_create_session(session_id: str) -> InvestmentsAssistantOrchestrator:
    if session_id not in _sessions:
        _sessions[session_id] = InvestmentsAssistantOrchestrator(session_id)
    return _sessions[session_id]
