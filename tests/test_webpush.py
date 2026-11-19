"""Web Push: messages encrypted as RFC 8291 specifies, signed with the VAPID key derived from the
session signing key (RFC 8292), sent only to the push services of the major browsers and never
redirected, and what each answer of a push service means."""

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from push_helpers import Browser, PushService

from disksprawl import webpush
from disksprawl.webpush import b64decode, b64encode

# RFC 8291, section 5 and appendix A.
RFC_RECEIVER = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
RFC_SENDER = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
RFC_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
RFC_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
RFC_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAm"
    "S6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Q"
    "ulcy4a-fN"
)
MESSAGE = {"web_push": 8030, "notification": {"title": "Hi", "navigate": "https://nas.local/"}}
SECRET = b"a" * 64


def subscription_of(browser: Browser) -> webpush.Subscription:
    return webpush.Subscription(browser.endpoint, **browser.subscription()["keys"])


def claims_of(header: str, public_key: str) -> dict[str, Any]:
    """The claims of a VAPID `Authorization` header, after checking its ES256 signature."""
    scheme, credentials = header.split(" ", 1)
    fields = dict(item.split("=", 1) for item in credentials.split(", "))
    assert scheme == "vapid" and fields["k"] == public_key
    head, payload, signature = fields["t"].split(".")
    assert json.loads(b64decode(head)) == {"typ": "JWT", "alg": "ES256"}
    raw = b64decode(signature)
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64decode(fields["k"]))
    key.verify(
        encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:])),
        f"{head}.{payload}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    claims: dict[str, Any] = json.loads(b64decode(payload))
    return claims


def test_messages_are_encrypted_as_rfc_8291_shows() -> None:
    sender = ec.derive_private_key(int.from_bytes(b64decode(RFC_SENDER)), ec.SECP256R1())
    body = webpush.encrypt(
        b"When I grow up, I want to be a watermelon",
        b64decode(RFC_RECEIVER),
        b64decode(RFC_AUTH),
        sender=sender,
        salt=b64decode(RFC_SALT),
    )
    assert b64encode(body) == RFC_BODY
    # Push services take 4096 bytes at most.
    with pytest.raises(ValueError):
        webpush.encrypt(b"x" * 4000, b64decode(RFC_RECEIVER), b64decode(RFC_AUTH))


def test_a_message_reaches_its_push_service_signed_and_encrypted() -> None:
    browser, service = Browser(), PushService()
    sender = webpush.WebPush(SECRET, service)
    result = sender.send(
        subscription_of(browser),
        MESSAGE,
        ttl=600,
        subject="https://disks.example.com",
        urgency="high",
        topic="scan-1",
    )
    assert result == "delivered"
    (request,) = service.requests
    assert request.url == browser.endpoint
    headers = ("TTL", "Urgency", "Topic", "Content-Encoding", "Content-Type")
    assert [request.headers[name] for name in headers] == [
        "600", "high", "scan-1", "aes128gcm", "application/octet-stream"
    ]
    assert browser.read(request.body) == MESSAGE
    claims = claims_of(request.headers["Authorization"], sender.public_key)
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "https://disks.example.com"
    # Push services refuse tokens valid for more than a day.
    assert time.time() < claims["exp"] <= time.time() + 24 * 3600


def test_the_vapid_key_comes_from_the_session_signing_key() -> None:
    def public_key(secret: bytes) -> str:
        return webpush.WebPush(secret).public_key

    assert public_key(b"a" * 40) == public_key(b"a" * 40) != public_key(b"b" * 40)
    assert webpush.browser_key(public_key(b"a" * 40))


@pytest.mark.parametrize(
    ("origin", "subject"),
    [
        ("https://disks.example.com", "https://disks.example.com"),
        ("https://nas.local:8443", "https://nas.local:8443"),
        ("http://192.168.1.10:7780", webpush.PROJECT_URL),
        ("https://localhost:7780", webpush.PROJECT_URL),
        ("", webpush.PROJECT_URL),
    ],
)
def test_the_vapid_subject(origin: str, subject: str) -> None:
    assert webpush.vapid_subject(origin) == subject


@pytest.mark.parametrize(
    ("endpoint", "service"),
    [
        ("https://fcm.googleapis.com/fcm/send/dQw4w9WgXcQ:APA91bH", "fcm.googleapis.com"),
        ("https://web.push.apple.com/QGuQyavXutnMH", "web.push.apple.com"),
        (
            "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABl",
            "updates.push.services.mozilla.com",
        ),
        (
            "https://wns2-par02p.notify.windows.com/w/?token=BQYAAAB%2b",
            "wns2-par02p.notify.windows.com",
        ),
        ("https://WEB.push.apple.com:443/QGuQ", "web.push.apple.com"),
        ("http://fcm.googleapis.com/fcm/send/x", None),
        ("https://fcm.googleapis.com:8443/fcm/send/x", None),
        ("https://evilfcm.googleapis.com/fcm/send/x", None),
        ("https://push.apple.com.example.net/x", None),
        ("https://user:pass@web.push.apple.com/x", None),
        ("https://web.push.apple.com/x#fragment", None),
        ("https://web.push.apple.com/x y", None),
        ("https://127.0.0.1/x", None),
        ("https://localhost/x", None),
        ("https://web.push.apple.com/" + "x" * 2048, None),
        ("not a url", None),
    ],
)
def test_only_the_push_services_of_the_major_browsers(endpoint: str, service: str | None) -> None:
    assert webpush.push_service(endpoint) == service


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (201, "delivered"),
        (404, "gone"),
        (410, "gone"),
        (429, "retry"),
        (503, "retry"),
        (None, "retry"),
        (301, "rejected"),
        (400, "rejected"),
        (403, "rejected"),
        (413, "rejected"),
    ],
)
def test_what_each_answer_means(
    status: int | None, outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    browser, service = Browser(), PushService()
    service.status = status
    sender = webpush.WebPush(SECRET, service)
    assert sender.send(subscription_of(browser), MESSAGE, ttl=60) == outcome
    # The address of a subscription is never logged, only its push service (and its reason).
    assert browser.endpoint not in caplog.text
    if outcome in {"retry", "rejected"}:
        assert "fcm.googleapis.com" in caplog.text
    if status in {400, 403, 413}:
        assert "TestRefusal" in caplog.text


def test_nothing_is_sent_to_an_unknown_address_or_with_broken_keys() -> None:
    browser, service = Browser(), PushService()
    sender = webpush.WebPush(SECRET, service)
    keys = browser.subscription()["keys"]
    unknown = webpush.Subscription("https://push.example.net/x", **keys)
    broken = webpush.Subscription(browser.endpoint, keys["p256dh"], b64encode(b"short"))
    assert sender.send(unknown, MESSAGE, ttl=60) == "gone"
    assert sender.send(broken, MESSAGE, ttl=60) == "rejected"
    assert service.requests == []


def test_redirects_are_never_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A push service that answers with a redirect gets its status back: the request is never
    sent elsewhere."""
    # The real opener, without the proxies a machine may set for its outgoing requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), webpush._NoRedirects)
    monkeypatch.setattr(webpush, "_OPENER", opener)
    visited: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            visited.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(307)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/push"
        response = webpush.post(webpush.Request(url, {"TTL": "60"}, b"body"))
    finally:
        server.shutdown()
        server.server_close()
    assert response.status == 307
    assert visited == ["/push"]
