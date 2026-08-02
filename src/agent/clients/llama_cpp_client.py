"""llama-cpp-python backend — loads GGUF models directly into process memory.

No server, no HTTP calls. The model file lives on disk; this client maps it
into RAM and runs inference in a thread pool so the async event loop stays free.

Recommended for Raspberry Pi 5 (ARM64, CPU-only):
  - GGUF is a quantised format designed for CPU inference
  - llama-cpp-python uses hand-optimised GGML/BLAS kernels (ARM NEON on Pi 5)
  - A Q4_K_M 7B model fits in ~4.7 GB, leaving room for the rest of the stack

Install
-------
    pip install llama-cpp-python
    # ARM64 / Pi 5 pre-built wheel:
    pip install llama-cpp-python \\
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

Download a model
----------------
    python3 scripts/download_model.py          # interactive picker
    python3 scripts/download_model.py qwen2.5-7b

Tested models on Pi 5 (8 GB RAM)
---------------------------------
    qwen2.5-7b-instruct-q4_k_m.gguf   ~4.7 GB  best quality/speed
    llama-3.2-3b-instruct-q8_0.gguf   ~3.4 GB  faster, lighter
    mistral-7b-instruct-q4_k_m.gguf   ~4.4 GB  solid all-rounder
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import json
from typing import Any

from src.agent.clients.base import BaseLLMClient
from src.agent.utils.logger import get_logger
from src.config import settings
from src.tools import dispatch_tool
from src.tools.definitions import TOOL_DEFINITIONS, to_openai_tools

logger = get_logger(__name__)

_TOOLS = to_openai_tools(TOOL_DEFINITIONS)

# Singleton — the model is large; load it once and share across all sessions.
_instance: LlamaCppClient | None = None


class LlamaCppClient(BaseLLMClient):
    """In-process GGUF inference via llama-cpp-python."""

    def __init__(self) -> None:
        try:
            from llama_cpp import Llama  # noqa: F401 — checked at init time

            self._Llama = Llama
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python is required for the llama_cpp backend.\n"
                "Install:  pip install llama-cpp-python\n"
                "ARM64/Pi 5:  pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
            ) from exc

        logger.info(
            "Loading GGUF model: %s  (n_ctx=%d, n_gpu_layers=%d, n_threads=%d, n_batch=%d)",
            settings.llm_model_path,
            settings.llm_context_size,
            settings.llm_n_gpu_layers,
            settings.llm_n_threads,
            settings.llm_n_batch,
        )
        self._llm = self._Llama(
            model_path=settings.llm_model_path,
            n_ctx=settings.llm_context_size,
            n_gpu_layers=settings.llm_n_gpu_layers,
            n_threads=settings.llm_n_threads,
            n_batch=settings.llm_n_batch,
            verbose=False,
        )
        self._inference_lock = asyncio.Lock()
        logger.info("GGUF model loaded")

    async def stream_response(
        self,
        messages: list[dict[str, Any]],
        system: str,
    ) -> AsyncGenerator[dict, None]:
        """Run the agentic tool-use loop, dispatching tools until the model stops."""
        loop = asyncio.get_event_loop()
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *messages,
        ]

        completed = False
        max_rounds = max(1, settings.agent_max_tool_rounds)
        for _round_number in range(max_rounds):
            # llama-cpp is synchronous — run in thread pool to keep event loop free.
            async with self._inference_lock:
                response = await loop.run_in_executor(
                    None,
                    lambda msgs=full_messages: self._llm.create_chat_completion(
                        messages=msgs,
                        tools=_TOOLS,
                        tool_choice="auto",
                        max_tokens=settings.agent_max_tokens,
                        temperature=settings.agent_temperature,
                    ),
                )

            choice = response["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason")

            if message.get("content"):
                yield {"type": "text_delta", "text": message["content"]}

            # Append assistant turn to the running history.
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content"),
            }
            if message.get("tool_calls"):
                assistant_msg["tool_calls"] = message["tool_calls"]
            full_messages.append(assistant_msg)

            tool_calls: list[dict] = message.get("tool_calls") or []
            if finish_reason != "tool_calls" or not tool_calls:
                yield {"type": "done"}
                completed = True
                break

            # Dispatch every tool call and feed results back.
            tool_result_messages: list[dict[str, Any]] = []
            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                tool_id = tc["id"]
                try:
                    tool_input = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    tool_input = {}

                yield {"type": "tool_call", "name": tool_name, "input": tool_input, "id": tool_id}
                result_str = await dispatch_tool(tool_name, tool_input)
                if len(result_str) > settings.agent_max_tool_result_chars:
                    result_str = (
                        result_str[: settings.agent_max_tool_result_chars]
                        + "\n[tool result truncated for local context budget]"
                    )
                yield {
                    "type": "tool_result",
                    "name": tool_name,
                    "result": result_str,
                    "id": tool_id,
                }

                tool_result_messages.append(
                    {"role": "tool", "tool_call_id": tool_id, "content": result_str}
                )

            full_messages.extend(tool_result_messages)

        if not completed:
            logger.warning("Agent stopped after %d tool rounds", max_rounds)
            yield {
                "type": "text_delta",
                "text": "I stopped the tool loop after reaching the local safety limit. "
                "Please narrow the request or ask me to continue.",
            }
            yield {"type": "done"}


def get_llama_cpp_client() -> LlamaCppClient:
    """Return the singleton LlamaCppClient, loading the model on first call."""
    global _instance
    if _instance is None:
        _instance = LlamaCppClient()
    return _instance
