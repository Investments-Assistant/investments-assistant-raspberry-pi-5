# Agent and Tool Use

## The ReAct pattern

The agent uses the **ReAct** (Reason + Act) pattern: on every turn, the LLM is given the
conversation history, a system prompt, and a list of available tools. It can either:
- Respond with text (the turn is done)
- Call one or more tools, observe the results, and continue reasoning

This loop continues until the model stops calling tools and produces a final response.

```
User: "What are the technical indicators for AAPL?"

LLM turn 1:
  → tool_call: get_technical_indicators(symbol="AAPL")

Tool: returns RSI=58.2, MACD bullish, price above EMA200...

LLM turn 2:
  → text: "AAPL shows bullish momentum: RSI at 58.2 (neutral zone, not
    overbought), MACD bullish crossover, and price sitting 12% above the
    200-day EMA. This suggests the uptrend is intact..."
```

---

## Why ReAct instead of RAG?

Retrieval-Augmented Generation (RAG) pre-fetches documents and stuffs them into the
context. The problem with financial data is that it's **highly time-sensitive** — a
portfolio value or news article from 10 minutes ago may already be stale. ReAct fetches
data on demand, ensuring the model always has the freshest numbers. The model also
decides *what* to fetch based on the question, avoiding noise from irrelevant data.

---

## Orchestrator (`src/agent/orchestrator.py`)

The `InvestmentsAssistantOrchestrator` class manages one `(authenticated user, chat session)`.
It:

1. Holds an in-memory `history` list of `{"role": ..., "content": ...}` dicts
2. Trims history to the last `settings.agent_max_context_messages` turns before each LLM call
3. Formats the system prompt by injecting live `trading_mode` and safety limits
4. Calls `_client.stream_response()` and yields all events to the WebSocket handler
5. After the stream ends, appends the assistant's full response to history
6. Persists both the user and assistant messages to PostgreSQL (best-effort — errors are
   logged but never raised, so a DB outage doesn't break the chat)

**Session registry**: a module-level `_sessions: dict[(user_id, session_id), Orchestrator]`
dict maps the authenticated owner and conversation ID to an orchestrator. Sessions are created on first WebSocket connection and
are bounded to 128 in-memory objects. Older objects are evicted if a VPN client rotates
IDs; durable history remains in PostgreSQL and is restored on reconnect.

**History restoration**: on WebSocket connect, `load_history_from_db()` queries
`chat_messages` filtered by both `user_id` and `session_id`, ordered by `created_at`, and
rebuilds the in-memory history. This means a browser refresh or reconnect picks up where the
conversation left off without exposing another user's messages.

Before every turn, the profile description and JSON preferences are refreshed from the `users`
row and added inside a delimited context block. The model is explicitly told that this is
user-provided context, not a command, so preferences cannot override server-side trading
limits or confirmation requirements. The same context contains only masked metadata for the
authenticated user's active brokerage accounts; encrypted credentials never enter the prompt.

Trading mode is part of the authenticated user context. The `set_trading_mode` tool persists
the requesting user's mode; it no longer mutates the process-global setting used as the
scheduler/default fallback.

Broker tools are capability-aware. A user may use all market/news/simulation tools without any
broker credentials. Portfolio/account/history/trade tools resolve that user's active accounts;
missing access returns a structured explanation, and multiple matching accounts return a safe
choice list with `account_id_required=true`. The dispatcher never silently falls back to the
global environment credentials for an authenticated user.

---

## System prompt (`src/agent/prompts.py`)

The system prompt is a template string with three placeholders that are filled at runtime:
```python
SYSTEM_PROMPT.format(
    trading_mode=settings.trading_mode,           # "recommend" or "auto"
    auto_max_trade_usd=settings.auto_max_trade_usd,
    auto_daily_loss_limit_usd=settings.auto_daily_loss_limit_usd,
)
```

Key sections of the prompt:

1. **Identity**: "expert investment assistant with deep knowledge of financial markets..."
2. **Capabilities list**: explicit enumeration so the model knows what tools exist and what
   they can do, reducing hallucination of non-existent capabilities
3. **Trading mode block**: tells the model whether it must wait for user confirmation
   (`recommend`) or can execute autonomously (`auto`), with the exact USD limits
4. **Analysis methodology**: a 6-step checklist (price → technicals → news → macro →
   risk/reward → documentation) that biases the model toward structured analysis
5. **Output style**: concise, data-driven, cite the source of every claim, format trade
   proposals with entry/target/stop-loss
6. **Disclaimer**: "Past performance does not guarantee future results"

The methodology checklist is important: without it, a smaller model like Qwen 2.5 7B tends
to skip steps (e.g. checking news before recommending a trade). Explicit instructions
compensate for the reduced world-model of a 7B model compared to a 70B+ model.

---

## Tool definitions (`src/tools/definitions.py`)

All 23 tools are defined as JSON Schema objects in a single list `TOOL_DEFINITIONS`.
Each definition follows the Claude/Anthropic format:
```json
{
  "name": "get_stock_data",
  "description": "...",
  "input_schema": {
    "type": "object",
    "properties": { ... },
    "required": ["symbols"]
  }
}
```

**Why this format?** The tool definitions are kept in provider-neutral JSON Schema,
then converted to the OpenAI function-calling format expected by llama.cpp:

```python
def to_openai_tools(definitions):
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],  # rename
            }
        }
        for t in definitions
    ]
```

This means there is one source of truth (`TOOL_DEFINITIONS`) and two consumers:
- The MCP server uses the raw `input_schema` format
- Both LLM backends call `to_openai_tools()` to get the format they need

---

## Tool dispatcher (`src/tools/dispatcher.py`)

The dispatcher maps tool names to Python callables. It separates sync and async tools:

- `_SYNC_DISPATCH`: tools whose implementations are synchronous (yfinance, ta, broker SDKs).
  These run in worker threads so slow data providers do not block the event loop.
- `_ASYNC_DISPATCH`: tools that use `async with async_session()` — the news memory tools.
- Special-cased: `execute_trade`, `confirm_trade`, `cancel_order`, `generate_report` have
  non-trivial logic (safety checks, DB persistence, report generation) that warrants their
  own functions.

**Error handling**: every tool call is wrapped in a try/except. If a tool raises, the
dispatcher returns `{"error": str(exc), "tool": tool_name}` as a JSON string. The LLM
receives this error as a tool result and can adapt (e.g. try a different symbol, explain
that data is unavailable, etc.).

---

## Trading mode safety

### AUTO mode — server-side guards applied in order

In `AUTO` mode, `_execute_trade()` runs several checks before routing the order:

1. **Symbol allowlist**: if `AUTO_ALLOWED_SYMBOLS` is non-empty, only those tickers can
   be traded autonomously. An attempt to trade an unlisted symbol returns
   `{"blocked": True, "reason": "..."}` — the LLM sees this and should not retry.

2. **Daily loss-limit halt**: `_is_daily_halted()` queries the `daily_pnl` table for
   today's row. If `auto_trading_halted = True`, the trade is blocked with a clear
   message. The flag is set automatically by `_check_and_enforce_daily_limit()` when
   `realized_usd` drops below `-AUTO_DAILY_LOSS_LIMIT_USD`.

3. **Per-trade size and order-type limit**: the server, not the prompt, requires a positive
   notional estimate or limit price, enforces `AUTO_MAX_TRADE_USD`, and blocks market orders
   unless `AUTO_ALLOW_MARKET_ORDERS=true`.

4. **Route and audit guards**: live-money routes require `LIVE_TRADING_ENABLED=true`;
   paper/testnet routes are selected explicitly by broker settings. A pending trade intent
   must be committed to PostgreSQL before any broker call. If the database is unavailable,
   the order is not sent. A failed post-order finalization is surfaced as an audit warning.

The daily halt flag is fail-closed: a database error blocks auto-trading. Realized P&L is
updated only when a broker adapter returns an explicit realized-P&L value; the current order
submission adapters do not invent P&L from an unfilled order. Production use should add
broker-specific fill reconciliation before relying on the daily loss limit as a complete
accounting system.

### RECOMMEND mode — confirm/cancel flow

In `RECOMMEND` mode, `execute_trade` never routes to a broker. It returns:

```json
{
  "status": "pending_confirmation",
  "message": "RECOMMENDATION: BUY 10 AAPL via alpaca (market order). Reason: ...\nReply 'confirm trade' to execute, or 'cancel trade' to discard.",
  "trade_details": {
    "broker": "alpaca", "symbol": "AAPL", "side": "buy",
    "quantity": 10, "order_type": "market", ...
  }
}
```

The LLM presents the recommendation to the user. When the user replies "confirm trade",
the LLM must call `confirm_trade` with the short-lived, server-generated `confirmation_id`.
The server ignores user-supplied replacement trade fields, binds the proposal to the
originating chat session, and persists the manual order intent before routing it. This
prevents confirmation of an arbitrary or cross-session order.

---

## Context window management

The Pi 5 runs models with `LLM_CONTEXT_SIZE=4096` tokens by default. A long conversation
quickly fills this. The orchestrator trims history before every LLM call:

```python
def _trimmed_history(self) -> list[dict]:
    return self.history[-settings.agent_max_context_messages:]
```

`AGENT_MAX_CONTEXT_MESSAGES` defaults to **15**. The token budget at the default 4096-token
context:

| Component | Tokens (approx.) |
| --- | --- |
| System prompt | ~400 |
| `agent_max_tokens` (response budget) | 2048 |
| Remaining for history | ~1648 |
| At ~110 tok/message average | **~15 messages** |

Set `AGENT_MAX_CONTEXT_MESSAGES` higher in `.env` when using a larger local context
window:

```env
# For llama_cpp with 8192-token context:
LLM_CONTEXT_SIZE=8192
AGENT_MAX_CONTEXT_MESSAGES=30

```

The same setting also caps how many rows are loaded from `chat_messages` on WebSocket
reconnect (`load_history_from_db`), so the DB query and the in-memory trim stay in sync.
