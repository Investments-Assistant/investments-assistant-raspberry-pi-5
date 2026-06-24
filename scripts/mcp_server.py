#!/usr/bin/env python3
"""
Investment Assistant MCP Server

Exposes all 18 investment-assistant tools to Claude Desktop / Claude Code
via the Model Context Protocol (stdio transport).

Run on your laptop (not the Pi) — it forwards tool calls to the Pi over VPN:

    python scripts/mcp_server.py --base-url https://10.8.0.1

Configure in Claude Desktop's config (see config/mcp/setup.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

import httpx

# ---------------------------------------------------------------------------
# Resolve project root so we can import tool definitions without installing
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from src.tools.definitions import TOOL_DEFINITIONS  # noqa: E402


def _to_mcp_tools(definitions: list[dict]) -> list:
    """Convert internal tool definitions to mcp.types.Tool objects."""
    from mcp import types

    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["input_schema"],
        )
        for t in definitions
    ]


async def _call_tool(base_url: str, tool_name: str, arguments: dict, verify_ssl: bool) -> str:
    """POST to the Pi's /api/tools/invoke and return the raw result string."""
    async with httpx.AsyncClient(verify=verify_ssl, timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/api/tools/invoke",
            json={"tool_name": tool_name, "tool_input": arguments},
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("result", json.dumps(payload))


def build_server(base_url: str, verify_ssl: bool):
    from mcp import types
    from mcp.server import Server

    server = Server("investment-assistant")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return _to_mcp_tools(TOOL_DEFINITIONS)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            result = await _call_tool(base_url, name, arguments, verify_ssl)
        except httpx.HTTPStatusError as exc:
            result = json.dumps(
                {
                    "error": f"HTTP {exc.response.status_code}",
                    "detail": exc.response.text,
                }
            )
        except Exception as exc:
            result = json.dumps({"error": str(exc)})
        return [types.TextContent(type="text", text=result)]

    return server


async def main(base_url: str, verify_ssl: bool) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(base_url, verify_ssl)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment Assistant MCP Server")
    parser.add_argument(
        "--base-url",
        default="https://10.8.0.1",
        help="Base URL of the investment assistant (default: https://10.8.0.1)",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification (needed for self-signed certs)",
    )
    args = parser.parse_args()

    verify_ssl = not args.no_verify_ssl
    if not verify_ssl:
        import warnings

        import urllib3

        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

    asyncio.run(main(args.base_url, verify_ssl))
