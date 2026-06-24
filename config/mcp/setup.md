# MCP Server Setup — Claude Desktop & Claude Code

The investment assistant can expose all 18 of its tools to **Claude Desktop** or **Claude Code**
via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP).

This lets you use Claude (Opus / Sonnet) for analysis and conversation while all tool
execution — market data, broker queries, trade execution — runs on the Pi.
**No portfolio data ever leaves your network.**

The local LLM interface (`https://10.8.0.1`) continues to work exactly as before.
MCP is an additional, optional access path.

---

## Prerequisites

- WireGuard VPN running on your laptop (see `config/wireguard/setup.md`)
- Python 3.12+ on your laptop
- The project cloned or synced to your laptop

---

## 1. Install the MCP dependency (laptop only)

```bash
# From the project root on your laptop:
pip install mcp>=1.0.0

# Or with Poetry extras:
poetry install -E mcp
```

---

## 2. Configure Claude Desktop

Edit the Claude Desktop config file:

| Platform | Config path |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add the `investment-assistant` server to the `mcpServers` block:

```json
{
  "mcpServers": {
    "investment-assistant": {
      "command": "python",
      "args": [
        "/absolute/path/to/investments-assistant/scripts/mcp_server.py",
        "--base-url", "https://10.8.0.1",
        "--no-verify-ssl"
      ]
    }
  }
}
```

Replace `/absolute/path/to/investments-assistant` with the actual path on your laptop.

`--no-verify-ssl` is needed because the Pi uses a self-signed TLS certificate.
If you replace it with a proper certificate (e.g. via Let's Encrypt + DDNS), remove this flag.

Restart Claude Desktop after saving the config.

---

## 3. Configure Claude Code

Add to your project or global Claude Code settings (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "investment-assistant": {
      "command": "python",
      "args": [
        "/absolute/path/to/investments-assistant/scripts/mcp_server.py",
        "--base-url", "https://10.8.0.1",
        "--no-verify-ssl"
      ]
    }
  }
}
```

---

## 4. Verify

In Claude Desktop, open a new chat. You should see the 18 investment-assistant tools
available in the tool picker. Try:

> "What is the current market overview?"

Claude will call `get_market_overview` on your Pi and return the result.

---

## Available Tools

| Tool | Description |
| --- | --- |
| `get_stock_data` | OHLCV price data for stocks/ETFs |
| `get_crypto_data` | OHLCV price data for crypto |
| `get_market_overview` | Major indices, VIX, yields, commodities |
| `get_technical_indicators` | RSI, MACD, Bollinger Bands, EMA, ATR, OBV |
| `get_options_chain` | Options calls/puts with greeks |
| `search_ticker` | Find ticker symbols by company name |
| `search_market_news` | Live news with sentiment analysis |
| `get_earnings_calendar` | Upcoming earnings announcements |
| `get_portfolio_summary` | Holdings across all brokers |
| `get_account_info` | Balance and buying power per broker |
| `get_trade_history` | Recent trades per broker |
| `execute_trade` | Buy/sell (respects TRADING_MODE setting) |
| `cancel_order` | Cancel an open order |
| `run_simulation` | Backtesting with equity curve + metrics |
| `set_trading_mode` | Switch between recommend/auto mode |
| `generate_report` | HTML + PDF investment report |
| `search_stored_news` | Full-text search of news memory |
| `get_latest_news` | Most recent ingested headlines |

---

## Security Note

The `/api/tools/invoke` endpoint on the Pi is protected by the same IP whitelist as all
other routes — it only accepts connections from the WireGuard VPN subnet (`10.8.0.1/24`)
and your LAN. The MCP server on your laptop connects over the VPN, so this is satisfied
automatically when WireGuard is active.

The `execute_trade` tool respects the `TRADING_MODE` setting on the Pi:
- `recommend` mode — returns a pending confirmation, never executes directly
- `auto` mode — executes immediately within configured safety limits
