"""LLM client factory.

The Raspberry Pi deployment supports one local backend: llama.cpp with GGUF
models loaded directly into process memory. No external AI API or sidecar model
server is used.
"""

from __future__ import annotations

from src.agent.clients.base import BaseLLMClient


def create_llm_client() -> BaseLLMClient:
    """Return the singleton llama.cpp client."""
    from src.agent.clients.llama_cpp_client import get_llama_cpp_client

    return get_llama_cpp_client()


__all__ = ["BaseLLMClient", "create_llm_client"]
