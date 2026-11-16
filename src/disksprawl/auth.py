"""Optional single-password access (AUTH_PASSWORD) with signed, revocable session cookies."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .db import AuthSession, transaction, utcnow
from .errors import AppError

COOKIE = "disksprawl_session"
LOGIN_WINDOW = 15 * 60
LOGIN_FAILURES_PER_CLIENT = 5
LOGIN_FAILURES_TOTAL = 50


def token_digest(token: str) -> str:
    """How a session token is stored: a random 256-bit token needs no slow hash, unlike a password."""
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """Failed sign-ins per client address and in total, in a sliding window of 15 minutes."""

    def __init__(self, clock=time.monotonic) -> None:  # noqa: ANN001
        self.clock = clock
        self.failures: dict[str, deque[float]] = {}
        self.total: deque[float] = deque()
        self.lock = threading.Lock()

    def _prune(self, events: deque[float], now: float) -> None:
        while events and events[0] <= now - LOGIN_WINDOW:
            events.popleft()

    def retry_after(self, client: str) -> int | None:
        with self.lock:
            now = self.clock()
            self._prune(self.total, now)
            events = self.failures.get(client, deque())
            self._prune(events, now)
            if not events:
                self.failures.pop(client, None)
            if len(events) >= LOGIN_FAILURES_PER_CLIENT:
                return int(events[0] + LOGIN_WINDOW - now) + 1
            if len(self.total) >= LOGIN_FAILURES_TOTAL:
                return int(self.total[0] + LOGIN_WINDOW - now) + 1
            return None

    def failed(self, client: str) -> None:
        with self.lock:
            now = self.clock()
            self.failures.setdefault(client, deque()).append(now)
            self.total.append(now)

    def succeeded(self, client: str) -> None:
        with self.lock:
            self.failures.pop(client, None)


class Auth:
    def __init__(self, config: Settings, sessions: sessionmaker[Session]):
        self.enabled = config.auth_enabled
        self.sessions = sessions
        self.hours = config.session_hours
        self.secure = config.auth_cookie_secure
        self.key = config.signing_key() if self.enabled else b""
        self._password = config.auth_password.get_secret_value().encode()
        self.limiter = LoginLimiter()

    def _signature(self, token: str, expires: int) -> str:
        return hmac.new(self.key, f"{token}.{expires}".encode(), hashlib.sha256).hexdigest()

    def password_matches(self, password: str) -> bool:
        # Constant time for the contents; only a difference in length can show.
        return bool(self._password) and hmac.compare_digest(password.encode(), self._password)

    def create(self) -> tuple[str, datetime]:
        """A new session: (cookie value, expiry)."""
        token = secrets.token_urlsafe(32)
        expires_at = utcnow() + timedelta(hours=self.hours)
        expires = int(expires_at.timestamp())
        with transaction(self.sessions) as db:
            db.add(AuthSession(token_hash=token_digest(token), expires_at=expires_at))
        return f"{token}.{expires}.{self._signature(token, expires)}", expires_at

    def _token(self, cookie: str | None) -> str | None:
        """The token of a well-signed, unexpired cookie."""
        if not cookie or len(cookie) > 300:
            return None
        parts = cookie.split(".")
        if len(parts) != 3 or not parts[1].isdigit():
            return None
        token, expires_text, signature = parts
        expires = int(expires_text)
        if not hmac.compare_digest(signature, self._signature(token, expires)):
            return None
        if expires <= time.time():
            return None
        return token

    def valid(self, cookie: str | None) -> bool:
        token = self._token(cookie)
        if token is None:
            return False
        with self.sessions() as db:
            session = db.get(AuthSession, token_digest(token))
            return session is not None and session.expires_at > utcnow()

    def revoke(self, cookie: str | None) -> None:
        token = self._token(cookie)
        if token is None:
            return
        with transaction(self.sessions) as db:
            session = db.get(AuthSession, token_digest(token))
            if session is not None:
                db.delete(session)


def get_auth(request: Request) -> Auth:
    auth: Auth = request.app.state.auth
    return auth


def require_session(request: Request) -> None:
    """Dependency of every private route: a valid session when a password is configured."""
    auth = get_auth(request)
    if auth.enabled and not auth.valid(request.cookies.get(COOKIE)):
        raise AppError("unauthenticated", 401)


def client_address(request: Request) -> str:
    # Uvicorn already replaced it with X-Forwarded-For when the proxy is in TRUSTED_PROXY_IPS.
    return request.client.host if request.client else "unknown"
