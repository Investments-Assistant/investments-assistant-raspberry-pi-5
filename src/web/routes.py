"""FastAPI routes: REST API and WebSocket chat endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from src.agent.utils.logger import get_logger
from src.config import settings
from src.db.database import async_session
from src.db.models import Report, Trade
from src.scheduler.jobs import get_latest_snapshot

logger = get_logger(__name__)

router = APIRouter()

STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(STATIC_DIR))


# ── IP Whitelist middleware ────────────────────────────────────────────────────


def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def require_allowed_ip(request: Request) -> None:
    """FastAPI dependency that raises 403 for non-whitelisted IPs."""
    ip = _get_client_ip(request)
    if not settings.is_development and not settings.is_ip_allowed(ip):
        logger.warning("Blocked request from %s", ip)
        raise HTTPException(status_code=403, detail="Access denied")


# ── Chat WebSocket ─────────────────────────────────────────────────────────────


@router.websocket("/ws/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for real-time streaming chat with the agent."""
    # IP check for WebSocket
    ip = _get_client_ip(websocket)
    if not settings.is_development and not settings.is_ip_allowed(ip):
        logger.warning("WS blocked from %s", ip)
        await websocket.close(code=4003, reason="Access denied")
        return

    await websocket.accept()
    logger.info("WebSocket connected: session=%s ip=%s", session_id, ip)

    from src.agent.orchestrator import get_or_create_session

    session = get_or_create_session(session_id)
    await session.load_history_from_db()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                user_message = data.get("message", "").strip()
            except json.JSONDecodeError:
                user_message = raw.strip()

            if not user_message:
                continue

            # Stream agent response events back over WebSocket
            async for event in session.chat(user_message):
                await websocket.send_json(event)

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


# ── REST API ──────────────────────────────────────────────────────────────────


@router.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "trading_mode": settings.trading_mode,
        "model": settings.llm_model_path,
    }


@router.get("/api/market/snapshot", dependencies=[Depends(require_allowed_ip)])
async def market_snapshot() -> dict:
    """Return the latest cached market data snapshot."""
    snap = get_latest_snapshot()
    if not snap:
        return {"message": "Snapshot not yet available — check back in a few minutes."}
    return snap


@router.get(
    "/api/reports",
    dependencies=[Depends(require_allowed_ip)],
    responses={500: {"description": "Database error"}},
)
async def list_reports() -> list[dict]:
    """List all generated reports."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Report).order_by(Report.created_at.desc()).limit(20)
            )
            reports = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "title": r.title,
                    "period_start": r.period_start.isoformat(),
                    "period_end": r.period_end.isoformat(),
                    "pdf_available": r.pdf_path is not None,
                    "created_at": r.created_at.isoformat(),
                }
                for r in reports
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/reports/{report_id}/pdf",
    dependencies=[Depends(require_allowed_ip)],
    responses={
        404: {"description": "Report not found or PDF not available"},
        500: {"description": "Database error"},
    },
)
async def download_report_pdf(report_id: str) -> FileResponse:
    """Download a report as PDF."""
    try:
        async with async_session() as session:
            result = await session.execute(select(Report).where(Report.id == report_id))
            report = result.scalar_one_or_none()
            if not report:
                raise HTTPException(status_code=404, detail="Report not found")
            if not report.pdf_path or not Path(report.pdf_path).exists():
                raise HTTPException(status_code=404, detail="PDF not available")
            return FileResponse(
                report.pdf_path,
                media_type="application/pdf",
                filename=Path(report.pdf_path).name,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/trades",
    dependencies=[Depends(require_allowed_ip)],
    responses={500: {"description": "Database error"}},
)
async def list_trades(limit: int = 50) -> list[dict]:
    """List recent trades recorded in the database."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Trade).order_by(Trade.created_at.desc()).limit(limit)
            )
            trades = result.scalars().all()
            return [
                {
                    "id": t.id,
                    "broker": t.broker,
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "price": t.price,
                    "order_type": t.order_type,
                    "status": t.status,
                    "mode": t.mode,
                    "reason": t.reason,
                    "created_at": t.created_at.isoformat(),
                }
                for t in trades
            ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── MCP tool invocation ───────────────────────────────────────────────────────


@router.post(
    "/api/tools/invoke",
    dependencies=[Depends(require_allowed_ip)],
    responses={400: {"description": "Missing tool_name"}, 500: {"description": "Tool error"}},
)
async def invoke_tool(request: Request) -> dict:
    """Invoke any agent tool by name. Used by the MCP server to forward Claude Desktop calls."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    tool_name = body.get("tool_name")
    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing 'tool_name'")

    tool_input = body.get("tool_input", {})

    from src.tools.dispatcher import dispatch_tool

    result_json = await dispatch_tool(tool_name, tool_input)
    return {"result": result_json}


# ── Main chat UI ───────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_allowed_ip)])
async def chat_ui(request: Request) -> HTMLResponse:
    index = STATIC_DIR / "index.html"
    return HTMLResponse(content=index.read_text(), status_code=200)
