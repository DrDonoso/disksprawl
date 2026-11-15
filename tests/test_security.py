"""Optional password, sessions, origin checks, allowed hosts and security headers."""

from pathlib import Path

import pytest
from conftest import WRITE
from fastapi.testclient import TestClient
from pydantic import SecretStr

from disksprawl.app import create_app
from disksprawl.auth import COOKIE, LOGIN_FAILURES_PER_CLIENT, LoginLimiter
from disksprawl.config import Settings
from disksprawl.security import CSP

PASSWORD = "correct horse battery"


@pytest.fixture
def protected(settings: Settings):  # noqa: ANN201
    config = settings.model_copy(update={"auth_password": SecretStr(PASSWORD)})
    with TestClient(create_app(config)) as client:
        yield client


def login(client: TestClient, password: str = PASSWORD) -> int:
    return client.post("/api/auth/login", json={"password": password}, headers=WRITE).status_code


def test_without_a_password_everything_is_open(client: TestClient) -> None:
    assert client.get("/api/auth/status").json() == {"enabled": False, "authenticated": True}
    assert client.get("/api/directories").status_code == 200
    assert login(client) == 409


def test_with_a_password_a_session_is_required(protected: TestClient) -> None:
    assert protected.get("/api/auth/status").json() == {"enabled": True, "authenticated": False}
    assert protected.get("/api/directories").json() == {"code": "unauthenticated"}
    assert protected.get("/api/directories").status_code == 401
    # The probes a container healthcheck and a version check need stay public.
    assert protected.get("/health").status_code == 200
    assert protected.get("/api/version").status_code == 200
    assert login(protected, "wrong password!") == 401
    assert login(protected) == 204
    cookie = protected.cookies.get(COOKIE)
    assert cookie
    set_cookie = protected.post("/api/auth/login", json={"password": PASSWORD},
                                headers=WRITE).headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie
    assert protected.get("/api/directories").status_code == 200
    assert protected.get("/api/auth/status").json()["authenticated"] is True


def test_logout_revokes_the_session(protected: TestClient) -> None:
    login(protected)
    cookie = protected.cookies.get(COOKIE)
    assert protected.post("/api/auth/logout", headers=WRITE).status_code == 204
    protected.cookies.clear()
    protected.cookies.set(COOKIE, cookie or "")
    assert protected.get("/api/directories").status_code == 401


def test_tampered_cookies_are_rejected(protected: TestClient) -> None:
    login(protected)
    token, expires, signature = (protected.cookies.get(COOKIE) or "").split(".")
    for forged in (f"{token}.{int(expires) + 999}.{signature}", f"{token}x.{expires}.{signature}",
                   "garbage", ""):
        protected.cookies.clear()
        protected.cookies.set(COOKIE, forged)
        assert protected.get("/api/directories").status_code == 401


def test_sign_in_attempts_are_limited(protected: TestClient) -> None:
    for _ in range(LOGIN_FAILURES_PER_CLIENT):
        assert login(protected, "wrong password!") == 401
    response = protected.post("/api/auth/login", json={"password": PASSWORD}, headers=WRITE)
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


def test_limiter_window() -> None:
    now = [0.0]
    limiter = LoginLimiter(clock=lambda: now[0])
    for _ in range(LOGIN_FAILURES_PER_CLIENT):
        limiter.failed("a")
    assert limiter.retry_after("a") == 15 * 60 + 1
    assert limiter.retry_after("b") is None
    now[0] = 15 * 60 + 1
    assert limiter.retry_after("a") is None


def test_short_passwords_and_secrets_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, auth_password="short", _env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, auth_secret="short", _env_file=None)  # type: ignore[call-arg]


def test_writes_need_the_header_and_the_same_origin(client: TestClient, scan_root: Path) -> None:
    body = {"name": "x", "path": str(scan_root)}
    for headers in (
        {},
        {"Origin": "http://testserver"},
        {"X-DiskSprawl-Request": "1"},
        {"Origin": "http://evil.example", "X-DiskSprawl-Request": "1"},
        {"Origin": "http://testserver:8080", "X-DiskSprawl-Request": "1"},
        {"Origin": "null", "X-DiskSprawl-Request": "1"},
    ):
        response = client.post("/api/directories", json=body, headers=headers)
        assert response.json() == {"code": "invalid_origin"}, headers
    assert client.post("/api/directories", json=body, headers=WRITE).status_code == 201


def test_hosts(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        for host in ("localhost:7780", "192.0.2.10:7780", "[::1]:7780", "nas", "nas.local",
                     "disks.home.arpa"):
            assert client.get("/api/version", headers={"Host": host}).status_code == 200, host
        for host in ("rebind.example.com", "evil.com:7780", ""):
            response = client.get("/api/version", headers={"Host": host})
            assert response.status_code == 400, host
        assert client.get("/", headers={"Host": "rebind.example.com"}).text.startswith(
            "DiskSprawl does not answer"
        )
    config = settings.model_copy(update={"allowed_hosts": "disks.example.com, .example.org"})
    with TestClient(create_app(config)) as client:
        for host in ("disks.example.com", "a.example.org", "example.org"):
            assert client.get("/api/version", headers={"Host": host}).status_code == 200, host
        assert client.get("/api/version", headers={"Host": "example.com"}).status_code == 400


def test_security_headers_and_body_limit(client: TestClient) -> None:
    response = client.get("/api/version")
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    huge = client.post("/api/auth/login", content=b"{" + b" " * 70_000 + b"}",
                       headers={**WRITE, "Content-Type": "application/json"})
    assert huge.status_code == 413


def test_web_app_routes(client: TestClient, settings: Settings) -> None:
    assert client.get("/").json()["code"] == "frontend_not_built"
    settings.frontend_dir.mkdir()
    (settings.frontend_dir / "index.html").write_text('<div id="root"></div>')
    (settings.frontend_dir / "assets").mkdir()
    (settings.frontend_dir / "assets" / "app.js").write_text("ok")
    for path in ("/", "/directories/3", "/settings"):
        assert client.get(path).text == '<div id="root"></div>', path
    assert client.get("/assets/app.js").headers["cache-control"].endswith("immutable")
    for path in ("/directories/x", "/missing", "/assets/../index.html", "/assets/missing.js"):
        assert client.get(path).status_code == 404, path
