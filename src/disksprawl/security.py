"""HTTP hardening for every request: allowed hosts (DNS rebinding), same-origin writes with a
custom header (CSRF), body size limits and the security headers, CSP included."""

from collections.abc import Callable
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings, normalize_hostname

MUTATION_HEADER = "x-disksprawl-request"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY_BYTES = 64 * 1024
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'; object-src 'none'"
)
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CSP.encode()),
]


def split_host(value: str) -> tuple[str, int | None] | None:
    """(host name, port) of a Host header; None when it is malformed."""
    try:
        parts = urlsplit("//" + value)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not hostname or parts.username is not None or parts.path or parts.query:
        return None
    return normalize_hostname(hostname), port


def same_origin(origin: str, host: tuple[str, int | None]) -> bool:
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    origin_port = port or (443 if parts.scheme == "https" else 80)
    host_name, host_port = host
    if normalize_hostname(parts.hostname) != host_name:
        return False
    # A Host header without a port means the default port of the browser's scheme.
    return host_port == origin_port or (host_port is None and origin_port in (80, 443))


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, config: Callable[[], Settings]):
        self.app = app
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        config = self.config()
        path: str = scope.get("path", "")

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [*message.get("headers", []), *SECURITY_HEADERS]
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    immutable = path.startswith("/assets/")
                    headers.append((
                        b"cache-control",
                        b"public, max-age=31536000, immutable" if immutable else b"no-store",
                    ))
                if config.auth_cookie_secure:
                    headers.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = headers
            await send(message)

        headers = Headers(scope=scope)
        host_values = headers.getlist("host")
        host = split_host(host_values[0]) if len(host_values) == 1 else None
        if host is None or not config.host_allowed(host[0]):
            response = (
                JSONResponse({"code": "invalid_host"}, 400)
                if path.startswith("/api/")
                else PlainTextResponse(
                    "DiskSprawl does not answer to this host name. Add it to ALLOWED_HOSTS "
                    "(see docs/configuration.md).",
                    400,
                )
            )
            await response(scope, receive, secure_send)
            return
        if scope["method"] not in MUTATING_METHODS:
            await self.app(scope, receive, secure_send)
            return
        origins = headers.getlist("origin")
        if (
            len(origins) != 1
            or not same_origin(origins[0], host)
            or headers.getlist(MUTATION_HEADER) != ["1"]
        ):
            await JSONResponse({"code": "invalid_origin"}, 403)(scope, receive, secure_send)
            return
        lengths = headers.getlist("content-length")
        if lengths and (
            len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > MAX_BODY_BYTES
        ):
            await JSONResponse({"code": "request_too_large"}, 413)(scope, receive, secure_send)
            return
        # Request bodies are small JSON documents: read them whole, within the limit, then replay.
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > MAX_BODY_BYTES:
                await JSONResponse({"code": "request_too_large"}, 413)(
                    scope, receive, secure_send
                )
                return
            if not message.get("more_body"):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, secure_send)
