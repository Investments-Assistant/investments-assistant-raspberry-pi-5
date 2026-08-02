"""FastAPI routes: REST API and WebSocket chat endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text

from src.agent.utils.logger import get_logger
from src.config import settings
from src.db.database import async_session
from src.db.models import DailyPnL, Report, Trade
from src.scheduler.jobs import get_latest_snapshot
from src.web.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    clear_login_failures,
    create_session,
    login_allowed,
    record_login_failure,
    require_authenticated,
    require_csrf,
    require_mcp_or_browser,
    verify_password,
    websocket_origin_allowed,
    websocket_principal,
)

logger = get_logger(__name__)

router = APIRouter()

STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(STATIC_DIR))


# ── IP Whitelist middleware ────────────────────────────────────────────────────


def _get_client_ip(request: Request | WebSocket) -> str:
    # Nginx overwrites X-Real-IP in this deployment.  X-Forwarded-For is kept
    # as a compatibility fallback for tests and other trusted reverse proxies.
    if settings.trust_proxy_headers:
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
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
    if not websocket_origin_allowed(websocket):
        await websocket.close(code=4003, reason="Origin not allowed")
        return
    if websocket_principal(websocket) is None:
        await websocket.close(code=4001, reason="Authentication required")
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", session_id):
        await websocket.close(code=4400, reason="Invalid session")
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
            if len(user_message) > settings.max_chat_message_chars:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": (
                            f"Message is limited to {settings.max_chat_message_chars} characters."
                        ),
                    }
                )
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
        "local_reasoning": True,
    }


@router.get("/api/ready")
async def ready() -> dict:
    """Readiness probe used by Compose; it checks the DB and model file."""
    checks = {"database": False, "model": False}
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        logger.warning("Readiness database check failed: %s", exc)
    checks["model"] = Path(settings.llm_model_path).is_file()
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks, "timestamp": datetime.now(UTC).isoformat()}


@router.get(
    "/login",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_allowed_ip)],
)
async def login_page(request: Request) -> HTMLResponse | RedirectResponse:
    """Serve the login form without exposing any private application data."""
    if request.cookies.get(SESSION_COOKIE) and settings.is_development is False:
        # A redirect is only an optimisation; private routes still verify the
        # signature and expiry on every request.
        from src.web.auth import verify_session

        if verify_session(request.cookies.get(SESSION_COOKIE)) is not None:
            return RedirectResponse("/", status_code=303)
    return HTMLResponse(content=(STATIC_DIR / "login.html").read_text(), status_code=200)


@router.post("/api/auth/login", dependencies=[Depends(require_allowed_ip)])
async def login(request: Request) -> JSONResponse:
    """Authenticate the single local operator and issue fresh cookies."""
    ip = _get_client_ip(request)
    if not login_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many failed login attempts")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    valid = (
        len(username) <= 128
        and len(password) <= 256
        and bool(username)
        and bool(password)
        and username == settings.auth_username
        and verify_password(password, settings.auth_password_hash)
    )
    if not valid:
        record_login_failure(ip)
        # Do not distinguish an unknown ID from a bad password.
        raise HTTPException(status_code=401, detail="Invalid ID or password")

    clear_login_failures(ip)
    response = JSONResponse({"authenticated": True, "username": username})
    secure = bool(settings.auth_cookie_secure and settings.is_production)
    response.set_cookie(
        SESSION_COOKIE,
        create_session(username),
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=max(300, settings.auth_session_ttl_minutes * 60),
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        httponly=False,
        secure=secure,
        samesite="strict",
        max_age=max(300, settings.auth_session_ttl_minutes * 60),
        path="/",
    )
    return response


@router.get(
    "/api/auth/me",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def auth_me(request: Request) -> dict:
    principal = require_authenticated(request)
    return {"authenticated": True, "username": principal.username}


@router.post(
    "/api/auth/logout",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def logout() -> JSONResponse:
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get(
    "/api/market/snapshot",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def market_snapshot() -> dict:
    """Return the latest cached market data snapshot."""
    snap = get_latest_snapshot()
    if not snap:
        return {"message": "Snapshot not yet available — check back in a few minutes."}
    return snap


@router.get(
    "/api/safety",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def safety_status() -> dict:
    """Expose the effective execution policy to the authenticated UI."""
    daily_halted: bool | None = None
    try:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        async with async_session() as session:
            result = await session.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            daily_halted = bool(record and record.auto_trading_halted)
    except Exception as exc:
        logger.warning("Could not read daily halt status: %s", exc)
    return {
        "trading_mode": settings.trading_mode,
        "auto_max_trade_usd": settings.auto_max_trade_usd,
        "auto_daily_loss_limit_usd": settings.auto_daily_loss_limit_usd,
        "auto_allowed_symbols": sorted(settings.auto_allowed_symbols_set),
        "auto_allow_market_orders": settings.auto_allow_market_orders,
        "live_trading_enabled": settings.live_trading_enabled,
        "daily_halted": daily_halted,
        "confirmation_required_in_recommend_mode": True,
        "database_failure_blocks_auto_trading": True,
    }


@router.post(
    "/api/safety/kill-switch",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def activate_kill_switch() -> dict:
    """Halt future auto orders for today; do not claim to cancel broker orders."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    try:
        async with async_session() as session:
            result = await session.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            if record is None:
                record = DailyPnL(date=today, realized_usd=0.0)
                session.add(record)
            record.auto_trading_halted = True
            await session.commit()
        logger.warning("Auto-trading kill switch activated for %s", today)
        return {
            "halted": True,
            "date": today,
            "message": "Future auto orders are blocked for today; check brokers for open orders.",
        }
    except Exception as exc:
        logger.error("Could not persist auto-trading kill switch: %s", exc)
        raise HTTPException(status_code=503, detail="Could not persist kill switch") from exc


@router.get(
    "/api/reports",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
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
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
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
            report_path = Path(report.pdf_path).resolve()
            reports_root = Path(settings.reports_dir).resolve()
            if reports_root not in report_path.parents:
                logger.error("Refusing report path outside reports directory: %s", report_path)
                raise HTTPException(status_code=404, detail="PDF not available")
            return FileResponse(
                report_path,
                media_type="application/pdf",
                filename=report_path.name,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get(
    "/api/trades",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
    responses={500: {"description": "Database error"}},
)
async def list_trades(limit: int = 50) -> list[dict]:
    """List recent trades recorded in the database."""
    limit = min(max(1, limit), 100)
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
    dependencies=[Depends(require_allowed_ip), Depends(require_mcp_or_browser)],
    responses={400: {"description": "Missing tool_name"}, 500: {"description": "Tool error"}},
)
async def invoke_tool(request: Request) -> dict:
    """Invoke any agent tool by name. Used by the MCP server to forward Claude Desktop calls."""
    principal = require_mcp_or_browser(request)
    if principal.mechanism != "bearer":
        require_csrf(request)
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


@router.get(
    "/",
    response_class=HTMLResponse,
    response_model=None,
    dependencies=[Depends(require_allowed_ip)],
)
async def chat_ui(request: Request) -> HTMLResponse | RedirectResponse:
    try:
        require_authenticated(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        raise
    index = STATIC_DIR / "index.html"
    return HTMLResponse(content=index.read_text(), status_code=200)
