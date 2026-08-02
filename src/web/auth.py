"""Small, dependency-free authentication layer for the private Pi UI.

User accounts live in PostgreSQL. The environment credentials bootstrap the
first account; additional accounts are provisioned with the local CLI. A
salted scrypt password hash and an HMAC-signed, short-lived cookie avoid adding
an identity service to the Pi. The cookie contains no portfolio data and is
invalidated by changing ``AUTH_SESSION_SECRET``.
"""

from __future__ import annotations

import base64
from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, WebSocket

from src import config

SESSION_COOKIE = "ia_session"
CSRF_COOKIE = "ia_csrf"
_SALT_BYTES = 16
_KEY_BYTES = 32
_LOGIN_WINDOW_SECONDS = 15 * 60
_MAX_LOGIN_FAILURES = 5
_failed_logins: dict[str, deque[float]] = defaultdict(deque)


@dataclass(frozen=True)
class Principal:
    """Authenticated local user or explicitly authorised MCP client."""

    username: str
    mechanism: str = "cookie"
    user_id: str | None = None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    """Create a portable scrypt password hash for ``.env``."""
    if not password or len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=_KEY_BYTES,
        maxmem=64 * 1024 * 1024,
    )
    return f"scrypt$v1$16384$8$1${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password hash without leaking timing information."""
    try:
        algorithm, version, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algorithm != "scrypt" or version != "v1":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_unb64(digest_b64)),
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(digest, _unb64(digest_b64))
    except (TypeError, ValueError, UnicodeError):
        return False


def _secret() -> bytes:
    return config.settings.auth_session_secret.encode("utf-8")


def create_session(username: str | None = None, user_id: str | None = None) -> str:
    """Create an HMAC-signed session cookie value."""
    subject = username or config.settings.auth_username
    expires = int(time.time()) + max(5, config.settings.auth_session_ttl_minutes) * 60
    nonce = secrets.token_urlsafe(24)
    payload = (
        f"{user_id}|{subject}|{expires}|{nonce}"
        if user_id
        else f"{subject}|{expires}|{nonce}"
    )
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return f"{_b64(payload.encode())}.{_b64(signature)}"


def verify_session(token: str | None) -> Principal | None:
    """Validate a session cookie and return its principal, if valid."""
    # Reject oversized attacker-controlled cookies before base64/HMAC work.
    if not token or len(token) > 4096 or not config.settings.authentication_ready:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        payload_bytes = _unb64(encoded_payload)
        expected = hmac.new(_secret(), payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(encoded_signature)):
            return None
        parts = payload_bytes.decode("utf-8").split("|")
        if len(parts) != 4:
            return None
        user_id, username, expires_raw, nonce = parts
        if (
            not nonce
            or int(expires_raw) <= int(time.time())
            or not user_id
        ):
            return None
        return Principal(username=username, user_id=user_id)
    except (TypeError, ValueError, UnicodeError):
        return None


def login_allowed(ip: str) -> bool:
    now = time.monotonic()
    failures = _failed_logins[ip]
    while failures and now - failures[0] > _LOGIN_WINDOW_SECONDS:
        failures.popleft()
    return len(failures) < _MAX_LOGIN_FAILURES


def record_login_failure(ip: str) -> None:
    _failed_logins[ip].append(time.monotonic())


def clear_login_failures(ip: str) -> None:
    _failed_logins.pop(ip, None)


def require_authenticated(request: Request) -> Principal:
    """FastAPI dependency for private browser routes."""
    if config.settings.is_development or not config.settings.auth_require_login:
        return Principal(
            username=config.settings.auth_username or "development",
            mechanism="development",
            user_id=None,
        )
    if not config.settings.authentication_ready:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    principal = verify_session(request.cookies.get(SESSION_COOKIE))
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return principal


def require_csrf(request: Request) -> None:
    """Require a double-submit CSRF token on state-changing browser requests."""
    if config.settings.is_development or not config.settings.auth_require_login:
        return
    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get("X-CSRF-Token")
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def require_mcp_or_browser(request: Request) -> Principal:
    """Allow the browser session or a separately configured local MCP token."""
    if config.settings.mcp_enabled and config.settings.mcp_auth_token:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if (
            scheme.lower() == "bearer"
            and hmac.compare_digest(token, config.settings.mcp_auth_token)
        ):
            return Principal(username="mcp", mechanism="bearer", user_id=None)
    return require_authenticated(request)


def websocket_principal(websocket: WebSocket) -> Principal | None:
    """Authenticate a WebSocket using the same session cookie as HTTP."""
    if config.settings.is_development or not config.settings.auth_require_login:
        return Principal(
            username=config.settings.auth_username or "development",
            mechanism="development",
            user_id=None,
        )
    if not config.settings.authentication_ready:
        return None
    return verify_session(websocket.cookies.get(SESSION_COOKIE))


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    """Reject cross-origin WebSocket upgrades in production."""
    if config.settings.is_development:
        return True
    origin = websocket.headers.get("Origin")
    if not origin:
        return False
    forwarded_proto = websocket.headers.get("X-Forwarded-Proto", "https")
    host = websocket.headers.get("Host", "")
    return origin.rstrip("/") == f"{forwarded_proto}://{host}".rstrip("/")
