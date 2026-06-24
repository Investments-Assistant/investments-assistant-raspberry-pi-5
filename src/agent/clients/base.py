"""Abstract base class for all LLM clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any


class BaseLLMClient(ABC):
    """
    Common interface every LLM backend must implement.

    stream_response() drives the full agent loop — text streaming *and*
    tool-use — so the orchestrator stays provider-agnostic.
    """

    @abstractmethod
    async def stream_response(
        self,
        messages: list[dict[str, Any]],
        system: str,
    ) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields typed events until the turn is complete.

        Events
        ------
        {"type": "text_delta",   "text": str}
        {"type": "tool_call",    "name": str, "input": dict, "id": str}
        {"type": "tool_result",  "name": str, "result": str, "id": str}
        {"type": "done"}
        """
        # Make the abstract method a proper async generator for type-checking.
        # Subclasses override this; the yield here satisfies the return type.
        yield {}  # pragma: no cover
