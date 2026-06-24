# MCP Integration

The investment assistant exposes all 18 agent tools to **Claude Desktop** and **Claude Code**
via the **Model Context Protocol (MCP)**.

This means you can open Claude Desktop on your laptop and say:
> "Check the technical indicators for AAPL and run a buy-and-hold simulation since 2022"

Claude Desktop will call the investment assistant's tools on the Pi, fetch real data, and
respond with a full analysis — all through your WireGuard VPN.

---

## Architecture

```
Claude Desktop (your laptop)
    │
    │  MCP stdio transport
    ▼
mcp_server.py  (runs on your laptop, not the Pi)
    │
    │  HTTPS POST /api/tools/invoke
    │  (through WireGuard VPN)
    ▼
Pi 5: Investment Assistant API
    │
    ▼
Tool Dispatcher → yfinance / brokers / news / DB
```

The MCP server is a **thin proxy** — it translates MCP `call_tool` requests into
HTTP POST requests to the Pi's `/api/tools/invoke` endpoint, and returns the result.

The server runs **locally on your laptop** (not in Docker on the Pi). This is intentional:
- Claude Desktop's MCP integration uses stdio transport — the MCP server must be a process
  Claude Desktop can spawn
- The Pi is the data source, not the process host

---

## Why MCP?

Claude Desktop natively supports MCP (Model Context Protocol) — it can call tools defined
in an MCP server and incorporate the results into its responses. This lets you use
Claude's full capabilities (Claude 3.5 Sonnet, Claude 3 Opus) against your investment
data, rather than the local 7B model running on the Pi.

Use cases:
- Deeper analysis that benefits from a frontier model's reasoning
- Natural language queries from Claude Desktop while away from home
- Using Claude Code to programmatically query your portfolio data

---

## Installation

### 1. Install the MCP package on your laptop

```bash
pip install mcp httpx
```

### 2. Configure Claude Desktop

On macOS, edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "investment-assistant": {
      "command": "python",
      "args": [
        "/path/to/investments-assistant/scripts/mcp_server.py",
        "--base-url", "https://10.8.0.1",
        "--no-verify-ssl"
      ]
    }
  }
}
```

On Windows, the config is at
`%APPDATA%\Claude\claude_desktop_config.json`.

### 3. Connect via VPN

Enable WireGuard on your laptop before starting Claude Desktop.

---

## `--no-verify-ssl`

The Pi uses a **self-signed TLS certificate**. Python's `httpx` rejects self-signed
certs by default. The `--no-verify-ssl` flag sets `verify=False` on the httpx client.

This is safe in this context because:
- You're connecting to your own Pi through an encrypted WireGuard tunnel
- The WireGuard connection is already cryptographically authenticated (no MITM possible)
- `--no-verify-ssl` only affects TLS certificate validation, not the underlying connection

If you install a proper TLS certificate (e.g. from Let's Encrypt via a DNS challenge),
you can remove `--no-verify-ssl`.

---

## Tool registration

`scripts/mcp_server.py` imports `TOOL_DEFINITIONS` from `src/tools/definitions.py`
and converts them to `mcp.types.Tool` objects:

```python
from src.tools.definitions import TOOL_DEFINITIONS

def _to_mcp_tools(definitions):
    from mcp import types
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["input_schema"],
        )
        for t in definitions
    ]
```

This means there's one source of truth for all 18 tool schemas. When a new tool is added
to `definitions.py`, it automatically becomes available in Claude Desktop without any
changes to the MCP server.

---

## `/api/tools/invoke` endpoint

On the Pi, the route handler that receives MCP tool calls:

```python
@router.post("/api/tools/invoke", dependencies=[Depends(require_allowed_ip)])
async def invoke_tool(request: Request) -> dict:
    body = await request.json()
    tool_name = body.get("tool_name")
    tool_input = body.get("tool_input", {})
    result_json = await dispatch_tool(tool_name, tool_input)
    return {"result": result_json}
```

The response is `{"result": "<json string>"}`. The MCP server unwraps this and returns
the inner JSON string as a `TextContent` block to Claude Desktop.

---

## Claude Code integration

Claude Code (the CLI) also supports MCP servers. Run:

```bash
claude mcp add investment-assistant \
  python /path/to/investments-assistant/scripts/mcp_server.py \
  -- --base-url https://10.8.0.1 --no-verify-ssl
```

Then in a Claude Code session:
```
> use the get_portfolio_summary tool to check my Alpaca account
```

---

## Limitations

- All 18 tools are exposed — including `execute_trade`. Claude Desktop (using Claude
  Opus/Sonnet) can place real orders if `TRADING_MODE=auto` on the Pi. Be aware of this
  when using MCP in auto mode.
- The MCP server has no authentication of its own — it inherits the IP whitelist from
  the Pi's Nginx layer. Only clients connected via WireGuard can reach `/api/tools/invoke`.
- Tool call latency through MCP is higher than direct chat (one HTTPS round-trip per tool
  call through the VPN).
