"""FastAPI routes: REST API and WebSocket chat endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.agent.utils.logger import get_logger
from src.config import settings
from src.db.database import async_session
from src.db.models import BrokerAccount, ChatMessage, DailyPnL, Report, Trade, User
from src.scheduler.jobs import get_latest_snapshot
from src.tools.broker_accounts import (
    BROKER_FIELDS,
    SECRET_FIELDS,
    SUPPORTED_BROKERS,
    BrokerAccountConfig,
    BrokerVaultUnavailable,
    decrypt_config,
    encrypt_config,
    ensure_broker_vault,
    load_user_broker_accounts,
    validate_broker_config,
)
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
    principal = websocket_principal(websocket)
    if principal is None:
        await websocket.close(code=4001, reason="Authentication required")
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,36}", session_id):
        await websocket.close(code=4400, reason="Invalid session")
        return
    if principal.user_id:
        # Signed cookies are deliberately stateless, so check the account's
        # active flag when a WebSocket is opened. This makes local account
        # deactivation effective without waiting for cookie expiry.
        try:
            async with async_session() as db_session:
                result = await db_session.execute(
                    select(User).where(
                        User.id == principal.user_id,
                        User.is_active.is_(True),
                    )
                )
                if result.scalar_one_or_none() is None:
                    await websocket.close(code=4001, reason="Authentication required")
                    return
        except Exception as exc:
            logger.error("WebSocket account lookup failed: %s", exc)
            await websocket.close(code=1013, reason="Authentication service unavailable")
            return

    await websocket.accept()
    logger.info("WebSocket connected: session=%s ip=%s", session_id, ip)

    from src.agent.orchestrator import get_or_create_session

    session = get_or_create_session(session_id, principal.user_id)
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
    """Authenticate a local user and issue a user-bound session cookie."""
    ip = _get_client_ip(request)
    if not login_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many failed login attempts")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    user = None
    if len(username) <= 128 and len(password) <= 256 and username and password:
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(User).where(User.username == username, User.is_active.is_(True))
                )
                user = result.scalar_one_or_none()
        except Exception as exc:
            logger.error("User lookup failed during login: %s", exc)
            raise HTTPException(
                status_code=503, detail="Authentication service unavailable"
            ) from exc

    valid = user is not None and verify_password(password, user.password_hash)
    if not valid:
        record_login_failure(ip)
        # Do not distinguish an unknown ID from a bad password.
        raise HTTPException(status_code=401, detail="Invalid ID or password")
    assert user is not None

    clear_login_failures(ip)
    response = JSONResponse(
        {
            "authenticated": True,
            "username": user.username,
            "display_name": user.display_name,
        }
    )
    secure = bool(settings.auth_cookie_secure and settings.is_production)
    response.set_cookie(
        SESSION_COOKIE,
        create_session(user.username, user.id),
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
    return {
        "authenticated": True,
        "username": principal.username,
        "user_id": principal.user_id,
    }


def _valid_session_id(session_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,36}", session_id))


def _profile_payload(user: User) -> dict:
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "description": user.description,
        "preferences": user.preferences or {},
        "trading_mode": getattr(user, "trading_mode", settings.trading_mode),
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


@router.get(
    "/api/profile",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def get_profile(request: Request) -> dict:
    """Return the authenticated user's durable assistant preferences."""
    principal = require_authenticated(request)
    if not principal.user_id:
        return {
            "user_id": None,
            "username": principal.username,
            "display_name": principal.username,
            "description": "",
            "preferences": {},
            "trading_mode": settings.trading_mode,
            "updated_at": None,
        }
    try:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.id == principal.user_id))
            user = result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="Authentication required")
            return _profile_payload(user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Profile service unavailable") from exc


@router.put(
    "/api/profile",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def update_profile(request: Request) -> dict:
    """Persist bounded user description and preference data."""
    principal = require_authenticated(request)
    if not principal.user_id:
        raise HTTPException(status_code=503, detail="Profile persistence is unavailable")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    display_name = str(body.get("display_name", "")).strip()
    description = str(body.get("description", "")).strip()
    preferences = body.get("preferences", {})
    if len(display_name) > 128:
        raise HTTPException(status_code=400, detail="display_name is limited to 128 characters")
    if len(description) > 4_000:
        raise HTTPException(status_code=400, detail="description is limited to 4000 characters")
    if not isinstance(preferences, dict):
        raise HTTPException(status_code=400, detail="preferences must be a JSON object")
    try:
        encoded_preferences = json.dumps(preferences, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="preferences must be JSON serialisable"
        ) from exc
    if len(encoded_preferences) > 8_000 or len(preferences) > 64:
        raise HTTPException(status_code=400, detail="preferences are too large")

    try:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.id == principal.user_id))
            user = result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="Authentication required")
            user.display_name = display_name[:128]
            user.description = description[:4_000]
            user.preferences = preferences
            await session.commit()
            return _profile_payload(user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Profile persistence failed") from exc


@router.put(
    "/api/profile/trading-mode",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def update_trading_mode(request: Request) -> dict:
    """Persist a user's trading mode without mutating the process-global default."""
    principal = require_authenticated(request)
    if not principal.user_id:
        raise HTTPException(status_code=503, detail="Trading mode persistence is unavailable")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    mode = body.get("mode") if isinstance(body, dict) else None
    if mode not in {"recommend", "auto"}:
        raise HTTPException(status_code=400, detail="mode must be 'recommend' or 'auto'")
    try:
        async with async_session() as session:
            result = await session.execute(select(User).where(User.id == principal.user_id))
            user = result.scalar_one_or_none()
            if user is None or not user.is_active:
                raise HTTPException(status_code=401, detail="Authentication required")
            user.trading_mode = mode
            await session.commit()
            return _profile_payload(user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Trading mode persistence failed") from exc


def _broker_account_public(row: BrokerAccount, config: dict) -> dict:
    return BrokerAccountConfig(
        id=row.id,
        user_id=row.user_id,
        broker=row.broker,
        display_name=row.display_name,
        config=config,
    ).public | {"active": bool(row.is_active)}


def _broker_account_body(request_body: object) -> tuple[str, str, dict, bool | None]:
    if not isinstance(request_body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    broker = str(request_body.get("broker", "")).lower().strip()
    if broker not in SUPPORTED_BROKERS:
        raise HTTPException(status_code=400, detail=f"Unknown broker: {broker or 'missing'}")
    display_name = str(request_body.get("display_name", "")).strip()
    if not display_name or len(display_name) > 128:
        raise HTTPException(
            status_code=400,
            detail="display_name is required and limited to 128 characters",
        )
    config = request_body.get("config", {})
    if not isinstance(config, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object")
    active = request_body.get("active")
    if active is not None and not isinstance(active, bool):
        raise HTTPException(status_code=400, detail="active must be a boolean")
    return broker, display_name, config, active


def _validated_account_config(
    broker: str,
    raw_config: dict,
    existing: dict | None = None,
) -> dict:
    merged = dict(existing or {})
    secret_fields = SECRET_FIELDS[broker]
    for field, value in raw_config.items():
        # An empty secret in the edit form means "keep the existing secret";
        # the UI never needs to read a secret back from the server.
        if field in secret_fields and value == "" and field in merged:
            continue
        merged[field] = value
    try:
        normalized = validate_broker_config(broker, merged)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    missing = [
        field
        for field in secret_fields
        if not str(normalized.get(field, "")).strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Missing required credential field(s): {', '.join(sorted(missing))}",
        )
    return normalized


@router.get(
    "/api/broker-accounts/providers",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def broker_account_providers() -> dict:
    """Describe supported connection fields without exposing any credentials."""
    return {
        "providers": {
            broker: {
                "fields": sorted(BROKER_FIELDS[broker]),
                "secret_fields": sorted(SECRET_FIELDS[broker]),
            }
            for broker in SUPPORTED_BROKERS
        }
    }


@router.get(
    "/api/broker-accounts",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def list_broker_accounts(request: Request) -> dict:
    """List the authenticated user's accounts with secrets masked."""
    principal = require_authenticated(request)
    if not principal.user_id:
        raise HTTPException(status_code=503, detail="Broker account persistence is unavailable")
    try:
        ensure_broker_vault()
        accounts = await load_user_broker_accounts(principal.user_id)
        return {"vault_configured": True, "accounts": [account.public for account in accounts]}
    except BrokerVaultUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Could not list broker accounts: %s", exc)
        raise HTTPException(status_code=503, detail="Broker account service unavailable") from exc


@router.post(
    "/api/broker-accounts",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def create_broker_account(request: Request) -> dict:
    """Create one encrypted broker configuration owned by the current user."""
    principal = require_authenticated(request)
    if not principal.user_id:
        raise HTTPException(status_code=503, detail="Broker account persistence is unavailable")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    broker, display_name, raw_config, active = _broker_account_body(body)
    config = _validated_account_config(broker, raw_config)
    try:
        encrypted = encrypt_config(config)
        async with async_session() as session:
            row = BrokerAccount(
                id=str(uuid.uuid4()),
                user_id=principal.user_id,
                broker=broker,
                display_name=display_name,
                config_encrypted=encrypted,
                is_active=True if active is None else active,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="An account with this provider and name already exists.",
                ) from exc
            return _broker_account_public(row, config)
    except BrokerVaultUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not create broker account: %s", exc)
        raise HTTPException(status_code=503, detail="Broker account could not be saved") from exc


@router.put(
    "/api/broker-accounts/{account_id}",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def update_broker_account(account_id: str, request: Request) -> dict:
    """Update one owned account; blank secret fields preserve the old secret."""
    principal = require_authenticated(request)
    if not principal.user_id:
        raise HTTPException(status_code=503, detail="Broker account persistence is unavailable")
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(BrokerAccount).where(
                    BrokerAccount.id == account_id,
                    BrokerAccount.user_id == principal.user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Broker account not found")
            existing = decrypt_config(row.config_encrypted)
            broker = row.broker
            display_name = str(body.get("display_name", row.display_name)).strip()
            if not display_name or len(display_name) > 128:
                raise HTTPException(
                    status_code=400,
                    detail="display_name is required and limited to 128 characters",
                )
            raw_config = body.get("config", {})
            if not isinstance(raw_config, dict):
                raise HTTPException(status_code=400, detail="config must be a JSON object")
            config = _validated_account_config(broker, raw_config, existing)
            row.display_name = display_name
            row.config_encrypted = encrypt_config(config)
            if isinstance(body.get("active"), bool):
                row.is_active = body["active"]
            await session.commit()
            return _broker_account_public(row, config)
    except BrokerVaultUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPException:
        raise
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Broker account update conflicted") from exc
    except Exception as exc:
        logger.error("Could not update broker account %s: %s", account_id, exc)
        raise HTTPException(status_code=503, detail="Broker account could not be updated") from exc


@router.delete(
    "/api/broker-accounts/{account_id}",
    dependencies=[
        Depends(require_allowed_ip),
        Depends(require_authenticated),
        Depends(require_csrf),
    ],
)
async def deactivate_broker_account(account_id: str, request: Request) -> dict:
    """Disable an account without destroying its encrypted audit/config record."""
    principal = require_authenticated(request)
    if not principal.user_id:
        raise HTTPException(status_code=503, detail="Broker account persistence is unavailable")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(BrokerAccount).where(
                    BrokerAccount.id == account_id,
                    BrokerAccount.user_id == principal.user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Broker account not found")
            row.is_active = False
            await session.commit()
            return {"success": True, "id": row.id, "active": False}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Could not deactivate broker account %s: %s", account_id, exc)
        raise HTTPException(status_code=503, detail="Broker account could not be disabled") from exc


@router.get(
    "/api/chat/history",
    dependencies=[Depends(require_allowed_ip), Depends(require_authenticated)],
)
async def chat_history(request: Request, session_id: str, limit: int = 200) -> list[dict]:
    """Return only the authenticated user's messages for one conversation."""
    principal = require_authenticated(request)
    if not _valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session")
    limit = min(max(1, limit), 200)
    user_filter = (
        ChatMessage.user_id == principal.user_id
        if principal.user_id
        else ChatMessage.user_id.is_(None)
    )
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ChatMessage)
                .where(user_filter, ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            messages = list(reversed(result.scalars().all()))
            return [
                {
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
                if message.role in {"user", "assistant"}
            ]
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Chat history unavailable") from exc


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
async def safety_status(request: Request) -> dict:
    """Expose the effective execution policy to the authenticated UI."""
    daily_halted: bool | None = None
    principal = require_authenticated(request)
    trading_mode = settings.trading_mode
    try:
        if principal.user_id:
            async with async_session() as session:
                user_result = await session.execute(
                    select(User).where(
                        User.id == principal.user_id,
                        User.is_active.is_(True),
                    )
                )
                user = user_result.scalar_one_or_none()
                if user is None:
                    raise HTTPException(status_code=401, detail="Authentication required")
                trading_mode = getattr(user, "trading_mode", settings.trading_mode)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        async with async_session() as session:
            result = await session.execute(select(DailyPnL).where(DailyPnL.date == today))
            record = result.scalar_one_or_none()
            daily_halted = bool(record and record.auto_trading_halted)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Could not read daily halt status: %s", exc)
    return {
        "trading_mode": trading_mode,
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
async def list_trades(request: Request, limit: int = 50) -> list[dict]:
    """List recent trades recorded in the database."""
    principal = require_authenticated(request)
    limit = min(max(1, limit), 100)
    user_filter = (
        Trade.user_id == principal.user_id
        if principal.user_id
        else Trade.user_id.is_(None)
    )
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Trade).where(user_filter).order_by(Trade.created_at.desc()).limit(limit)
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

    from src.tools.dispatcher import dispatch_tool, tool_context

    trading_mode = None
    if principal.user_id:
        try:
            async with async_session() as session:
                user_result = await session.execute(
                    select(User).where(
                        User.id == principal.user_id,
                        User.is_active.is_(True),
                    )
                )
                user = user_result.scalar_one_or_none()
                if user is None:
                    raise HTTPException(status_code=401, detail="Authentication required")
                trading_mode = getattr(user, "trading_mode", settings.trading_mode)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="User settings unavailable") from exc

    with tool_context("api", principal.user_id, trading_mode):
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
