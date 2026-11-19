"""Helpers of the notification tests: a browser that subscribes (its keys, and reading the messages
it receives as it would) and a push service that records what is sent to it."""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from disksprawl import webpush
from disksprawl.webpush import b64encode, hkdf, public_bytes


@dataclass
class Browser:
    endpoint: str = "https://fcm.googleapis.com/fcm/send/device-1"
    locale: str = "en"
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    secret: bytes = field(default_factory=lambda: os.urandom(16))

    def subscription(self, **changes: Any) -> dict[str, Any]:
        """The body of `PUT /api/push/subscription` for this browser."""
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": b64encode(public_bytes(self.key)), "auth": b64encode(self.secret)},
            "locale": self.locale,
            **changes,
        }

    def read(self, body: bytes) -> Any:
        """Decrypts a push message as the browser does (RFC 8291), checking its framing."""
        salt, record_size, key_length = body[:16], int.from_bytes(body[16:20]), body[20]
        sender_key = body[21 : 21 + key_length]
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_key)
        key_info = b"WebPush: info\x00" + public_bytes(self.key) + sender_key
        keying = hkdf(self.secret, self.key.exchange(ec.ECDH(), sender), key_info, 32)
        content_key = hkdf(salt, keying, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, keying, b"Content-Encoding: nonce\x00", 12)
        record = AESGCM(content_key).decrypt(nonce, body[21 + key_length :], None)
        assert record_size == 4096 and record.endswith(b"\x02")
        return json.loads(record[:-1])


class PushService:
    """Stands in for every push service (`webpush.TRANSPORT`): records the requests and answers
    `status` (or what `answers` says for an address), or cannot be reached while it is None."""

    def __init__(self) -> None:
        self.requests: list[webpush.Request] = []
        self.status: int | None = 201
        self.answers: dict[str, int | None] = {}

    def __call__(self, request: webpush.Request) -> webpush.Response:
        self.requests.append(request)
        status = self.answers.get(request.url, self.status)
        if status is None:
            raise ConnectionRefusedError("unreachable")
        if status >= 400:
            return webpush.Response(status, b'{"reason": "TestRefusal"}')
        return webpush.Response(status)

    def messages(self, browser: Browser) -> list[Any]:
        """What `browser` received, decrypted, oldest first."""
        return [
            browser.read(request.body)
            for request in self.requests
            if request.url == browser.endpoint
        ]

    def notifications(self, browser: Browser) -> list[dict[str, Any]]:
        return [message["notification"] for message in self.messages(browser)]
