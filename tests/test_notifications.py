"""Notifications: devices register and choose what they are told; the end of scans and the volumes
that fill up are queued for them and sent, tried again while push services fail, and forgotten
when push services say so."""

import sqlite3
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from conftest import OPTIONS, WRITE, add_directory, scan, write_tree
from fastapi.testclient import TestClient
from push_helpers import Browser, PushService
from pydantic import SecretStr
from sqlalchemy import func, select

from disksprawl import webpush
from disksprawl.app import create_app
from disksprawl.config import Settings
from disksprawl.db import (
    CapacitySample,
    Directory,
    PushMessage,
    PushSubscription,
    Scan,
    transaction,
    utcnow,
)
from disksprawl.notifications import (
    MAX_DEVICES,
    Notifier,
    format_bytes,
    format_duration,
    listed,
    texts_of,
)

GIB = 1024**3
MIB = 1024**2
PASSWORD = "correct horse battery"
SCANS_ONLY = {"capacity": False}
CAPACITY_ONLY = {"manual_scans": False, "failed_scans": False}


def register(client: TestClient, browser: Browser, **choices: bool) -> dict[str, Any]:
    """Turns notifications on for `browser`, as the interface does, then makes its choices."""
    response = client.put("/api/push/subscription", json=browser.subscription(), headers=WRITE)
    assert response.status_code == 200, response.text
    if choices:
        body = {"endpoint": browser.endpoint, **choices}
        response = client.patch("/api/push/subscription", json=body, headers=WRITE)
        assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def tick(client: TestClient, minutes: float = 0) -> None:
    notifier = client.app.state.notifier  # type: ignore[attr-defined]
    notifier.tick(utcnow() + timedelta(minutes=minutes))


def sessions(client: TestClient) -> Any:
    return client.app.state.sessions  # type: ignore[attr-defined]


def new_directory(client: TestClient, name: str) -> int:
    """A directory stored without measuring its volume (tests choose what it measures)."""
    with transaction(sessions(client)) as db:
        position = (db.scalar(select(func.max(Directory.position))) or 0) + 1
        directory = Directory(
            name=name, path=f"/data/{name.lower()}", enabled=True, position=position, cron=None,
            engine="native", threads=1, pace="max", low_priority=False, measure="apparent",
            one_filesystem=True, excludes=[],
        )
        db.add(directory)
        db.flush()
        return directory.id


def finished(
    client: TestClient,
    directory: int,
    status: str = "completed",
    *,
    trigger: str = "manual",
    total: int = 3 * GIB,
    message: str | None = None,
) -> int:
    """A scan that has just ended."""
    now = utcnow()
    with transaction(sessions(client)) as db:
        row = Scan(
            directory_id=directory, status=status, trigger=trigger, engine="native",
            options=OPTIONS, queued_at=now, started_at=now, finished_at=now,
            duration_seconds=720.0, total_bytes=total if status == "completed" else None,
            file_count=1, dir_count=1, error_count=0, error_sample=[], message=message,
            has_detail=False,
        )
        db.add(row)
        db.flush()
        return row.id


def measure(client: TestClient, directory: int, percent: float, device: str) -> None:
    """A capacity sample of a 100 GiB volume used at `percent`."""
    used = int(100 * GIB * percent / 100)
    with transaction(sessions(client)) as db:
        db.add(CapacitySample(directory_id=directory, measured_at=utcnow(), total=100 * GIB,
                              used=used, free=100 * GIB - used, device=device))


def told(service: PushService, browser: Browser) -> list[tuple[str, str]]:
    return [(item["title"], item["body"]) for item in service.notifications(browser)]


def count(client: TestClient, model: Any) -> int:
    with sessions(client)() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_a_device_registers_and_chooses_what_it_is_told(client: TestClient) -> None:
    key = client.get("/api/push").json()["public_key"]
    assert webpush.browser_key(key)
    browser = Browser()
    defaults = {"manual_scans": True, "scheduled_scans": False, "failed_scans": True,
                "capacity": True}
    assert register(client, browser) == defaults
    chosen = register(client, browser, scheduled_scans=True, capacity=False)
    assert chosen == {**defaults, "scheduled_scans": True, "capacity": False}
    # Each opening registers it again (in Spanish now): its choices stay.
    browser.locale = "es"
    assert register(client, browser) == chosen
    with sessions(client)() as db:
        device = db.scalars(select(PushSubscription)).one()
    assert (device.locale, device.origin, device.session_hash) == ("es", "http://testserver", None)

    assert client.post("/api/push/unsubscribe", json={"endpoint": browser.endpoint},
                       headers=WRITE).status_code == 204
    answer = client.patch("/api/push/subscription",
                          json={"endpoint": browser.endpoint, "capacity": True}, headers=WRITE)
    assert (answer.status_code, answer.json()) == (404, {"code": "push_subscription_not_found"})


def test_registrations_are_checked(client: TestClient) -> None:
    browser = Browser()
    keys = browser.subscription()["keys"]
    for body, code in (
        (browser.subscription(endpoint="https://push.example.net/x"), "push_service_unsupported"),
        (browser.subscription(endpoint="http://fcm.googleapis.com/x"), "push_service_unsupported"),
        (browser.subscription(keys={**keys, "p256dh": "AAAA"}), "invalid_request"),
        (browser.subscription(keys={**keys, "auth": "c2hvcnQ"}), "invalid_request"),
        (browser.subscription(locale="fr"), "invalid_request"),
        (browser.subscription(extra=True), "invalid_request"),
    ):
        answer = client.put("/api/push/subscription", json=body, headers=WRITE)
        assert (answer.status_code, answer.json()["code"]) == (422, code), body
    assert count(client, PushSubscription) == 0


def test_the_devices_unused_for_longest_are_forgotten(client: TestClient) -> None:
    browsers = [Browser(endpoint=f"https://fcm.googleapis.com/fcm/send/d{index}")
                for index in range(MAX_DEVICES + 1)]
    for browser in browsers:
        register(client, browser)
    with sessions(client)() as db:
        endpoints = set(db.scalars(select(PushSubscription.endpoint)))
    assert endpoints == {browser.endpoint for browser in browsers[1:]}


def test_the_end_of_a_scan_is_told(client: TestClient, scan_root: Path,
                                   push_service: PushService) -> None:
    write_tree(scan_root / "media", {"a": 1000, "b": {"c": 2000}})
    directory = add_directory(client, scan_root / "media")
    browser = Browser()
    register(client, browser, **SCANS_ONLY)
    scan(client, directory)
    tick(client)
    (first,) = push_service.messages(browser)
    assert first["web_push"] == 8030
    notification = first["notification"]
    assert notification["title"] == "Scan finished: media"
    assert notification["body"].endswith(" s.") and "Took" in notification["body"]
    assert notification["navigate"] == f"http://testserver/directories/{directory}"
    assert notification["tag"] == f"scan-{directory}"
    assert notification["lang"] == "en-GB"
    assert notification["icon"] == "http://testserver/icon-192.png"
    assert notification["badge"] == "http://testserver/badge-96.png"
    assert push_service.requests[0].headers["Topic"] == f"scan-{directory}"

    # Told once; the next scan compares with this one.
    tick(client)
    scan(client, directory)
    tick(client)
    assert len(push_service.requests) == 2
    assert "no change since the previous scan" in push_service.notifications(browser)[1]["body"]


def test_the_end_of_a_scan_is_told_at_once(settings: Settings, scan_root: Path,
                                           push_service: PushService,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """In the app, rounds run in a thread of their own, and the end of a scan starts one."""
    monkeypatch.setattr(Notifier, "paused", False)
    write_tree(scan_root / "media", {"a": 1000})
    with TestClient(create_app(settings)) as client:
        directory = add_directory(client, scan_root / "media")
        browser = Browser()
        register(client, browser, **SCANS_ONLY)
        scan(client, directory)
        deadline = time.monotonic() + 10
        while not push_service.requests and time.monotonic() < deadline:
            time.sleep(0.05)
    assert [item["title"] for item in push_service.notifications(browser)] == [
        "Scan finished: media"
    ]


def test_each_device_is_told_what_it_chose(client: TestClient,
                                           push_service: PushService) -> None:
    directory = new_directory(client, "Media")
    english = Browser()
    spanish = Browser(endpoint="https://web.push.apple.com/device-2", locale="es")
    register(client, english, **SCANS_ONLY)
    register(client, spanish, manual_scans=False, scheduled_scans=True, failed_scans=False,
             capacity=False)
    for status, trigger, total, message in (
        ("completed", "manual", 3 * GIB, None),
        ("completed", "scheduled", 3 * GIB + 512 * MIB, None),
        ("failed", "manual", 0, "path_not_found"),
        ("failed", "scheduled", 0, "engine_failed: exit status 2: /data/x"),
        ("interrupted", "scheduled", 0, "interrupted"),
        # Cancelling tells nobody.
        ("cancelled", "manual", 0, None),
    ):
        finished(client, directory, status, trigger=trigger, total=total, message=message)
        tick(client)
    assert told(push_service, english) == [
        ("Scan finished: Media", "3\u00a0GiB. Took 12 min."),
        ("Scan failed: Media", "The folder does not exist (it may not be mounted)."),
        # Never the details, which may quote folders.
        ("Scan failed: Media", "The scan engine failed."),
        ("Scan interrupted: Media", "A restart of DiskSprawl interrupted it."),
    ]
    assert told(push_service, spanish) == [
        ("Escaneo terminado: Media",
         "3,5\u00a0GiB, +512\u00a0MiB desde el escaneo anterior. Ha tardado 12 min."),
    ]
    assert count(client, PushMessage) == 0


def test_scans_of_earlier_versions_are_never_told(settings: Settings,
                                                  push_service: PushService) -> None:
    settings.data_dir.mkdir(parents=True)
    config = Config()
    config.set_main_option("script_location", "disksprawl:migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database_path}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "0002")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO directories (id, name, path, enabled, position, engine, threads, pace,"
            " low_priority, measure, one_filesystem, excludes, created_at, updated_at)"
            " VALUES (1, 'Storage', '/data/storage', 1, 0, 'native', 1, 'gentle', 1, 'disk', 1,"
            " '[]', '2026-11-17 00:00:00', '2026-11-17 00:00:00')"
        )
        connection.execute(
            "INSERT INTO scans (directory_id, status, trigger, engine, options, queued_at,"
            " finished_at, total_bytes, error_count, error_sample, has_detail)"
            " VALUES (1, 'completed', 'manual', 'native', '{}', '2026-11-18 03:00:00',"
            " '2026-11-18 03:10:00', 1000, 0, '[]', 0)"
        )
    with TestClient(create_app(settings)) as client:
        register(client, Browser(), **SCANS_ONLY)
        tick(client)
    assert push_service.requests == []


def test_with_a_password_signing_out_pauses_them(settings: Settings,
                                                 push_service: PushService) -> None:
    protected = settings.model_copy(update={"auth_password": SecretStr(PASSWORD)})
    with TestClient(create_app(protected)) as client:
        browser = Browser()
        assert client.put("/api/push/subscription", json=browser.subscription(),
                          headers=WRITE).status_code == 401

        def sign_in() -> None:
            body = {"password": PASSWORD}
            assert client.post("/api/auth/login", json=body, headers=WRITE).status_code == 204

        sign_in()
        register(client, browser, **SCANS_ONLY)
        directory = new_directory(client, "Media")
        finished(client, directory)
        tick(client)
        assert len(push_service.requests) == 1

        client.post("/api/auth/logout", headers=WRITE)
        finished(client, directory)
        tick(client)
        assert len(push_service.requests) == 1

        # Signed in again, the interface registers the device again: its choices were kept.
        sign_in()
        assert register(client, browser)["capacity"] is False
        finished(client, directory)
        tick(client)
        assert len(push_service.requests) == 2


def test_what_push_services_answer(client: TestClient, push_service: PushService) -> None:
    directory = new_directory(client, "Media")
    gone, refusing, flaky = (Browser(endpoint=f"https://fcm.googleapis.com/fcm/send/{name}")
                             for name in ("gone", "refusing", "flaky"))
    for browser in (gone, refusing, flaky):
        register(client, browser, **SCANS_ONLY)
    push_service.answers = {gone.endpoint: 410, refusing.endpoint: 400, flaky.endpoint: None}

    finished(client, directory)
    tick(client)
    with sessions(client)() as db:
        failures = {
            endpoint: value
            for endpoint, value in db.execute(
                select(PushSubscription.endpoint, PushSubscription.failures)
            )
        }
    # Gone: forgotten at once. Refused: not sent again. Unreachable: tried again later.
    assert failures == {refusing.endpoint: 1, flaky.endpoint: 0}
    assert count(client, PushMessage) == 1
    tick(client, minutes=1)
    assert len(push_service.requests) == 3
    tick(client, minutes=3)
    assert len(push_service.requests) == 4
    push_service.answers[flaky.endpoint] = 201
    tick(client, minutes=6)
    assert len(push_service.requests) == 4
    tick(client, minutes=9)
    assert [request.url for request in push_service.requests[4:]] == [flaky.endpoint]
    assert len(told(push_service, flaky)) == 3
    assert count(client, PushMessage) == 0

    # Refused three times in a row: forgotten.
    for minutes in (10, 11):
        finished(client, directory)
        tick(client, minutes=minutes)
    with sessions(client)() as db:
        assert set(db.scalars(select(PushSubscription.endpoint))) == {flaky.endpoint}

    # What cannot be sent within a day is dropped.
    push_service.answers[flaky.endpoint] = None
    finished(client, directory)
    tick(client, minutes=12)
    tick(client, minutes=12 + 24 * 60)
    assert count(client, PushMessage) == 0


def test_a_newer_notification_replaces_one_still_waiting(client: TestClient,
                                                         push_service: PushService) -> None:
    """Notifications about the same directory replace each other on a device: one waiting to be
    tried again is dropped when a newer one comes, or it would arrive later and replace it."""
    directory = new_directory(client, "Media")
    browser = Browser()
    register(client, browser, **SCANS_ONLY)
    push_service.status = None
    finished(client, directory, "failed", message="path_not_found")
    tick(client)
    push_service.status = 201
    finished(client, directory)
    tick(client, minutes=3)
    assert told(push_service, browser)[1:] == [("Scan finished: Media", "3\u00a0GiB. Took 12 min.")]
    assert count(client, PushMessage) == 0


def test_a_choice_changed_meanwhile_is_respected(client: TestClient,
                                                 push_service: PushService) -> None:
    directory = new_directory(client, "Media")
    browser = Browser()
    register(client, browser, **SCANS_ONLY)
    push_service.status = 503
    finished(client, directory)
    tick(client)
    push_service.status = 201
    register(client, browser, manual_scans=False)
    tick(client, minutes=3)
    assert len(push_service.requests) == 1
    assert count(client, PushMessage) == 0


def test_volumes_reaching_a_threshold_are_told_once(client: TestClient,
                                                    push_service: PushService) -> None:
    media = new_directory(client, "Media")
    images = new_directory(client, "Imágenes")
    backups = new_directory(client, "Backups")
    english = Browser()
    spanish = Browser(endpoint="https://web.push.apple.com/device-2", locale="es")
    for browser in (english, spanish):
        register(client, browser, **CAPACITY_ONLY)
    # Media and Imágenes share a volume; Backups has another one.
    measure(client, media, 85, "volume-a")
    measure(client, images, 85, "volume-a")
    measure(client, backups, 50, "volume-b")
    tick(client)
    assert told(push_service, english) == [
        ("Volume nearly full", "Media and Imágenes: 85% used, 15\u00a0GiB free.")
    ]
    assert told(push_service, spanish) == [
        ("Volumen casi lleno", "Media e Imágenes: 85\u00a0% usado, 15\u00a0GiB libres.")
    ]
    navigate = push_service.notifications(english)[0]["navigate"]
    assert navigate == f"http://testserver/directories/{media}"

    def levels(*steps: float) -> list[tuple[str, str]]:
        """Measures volume a at each step, a round after each one: what English was told."""
        before = len(told(push_service, english))
        for percent in steps:
            measure(client, media, percent, "volume-a")
            tick(client)
        return told(push_service, english)[before:]

    def critical(text: str) -> list[tuple[str, str]]:
        return [("Volume almost out of space", f"Media and Imágenes: {text}")]

    assert levels(85, 86) == []
    assert levels(91) == critical("91% used, 9\u00a0GiB free.")
    # Hovering around a threshold tells nothing: it must drop two points below it first.
    assert levels(89, 90, 88.5) == []
    assert levels(87, 90) == critical("90% used, 10\u00a0GiB free.")
    assert levels(70, 81) == [
        ("Volume nearly full", "Media and Imágenes: 81% used, 19\u00a0GiB free.")
    ]

    # A device that turns them on later is told the volumes already full, and so is one that
    # turns them off and on again.
    late = Browser(endpoint="https://updates.push.services.mozilla.com/wpush/v2/late")
    register(client, late, **CAPACITY_ONLY)
    register(client, spanish, capacity=False)
    register(client, spanish, capacity=True)
    before = len(told(push_service, spanish))
    tick(client)
    assert told(push_service, late) == [
        ("Volume nearly full", "Media and Imágenes: 81% used, 19\u00a0GiB free.")
    ]
    assert told(push_service, spanish)[before:] == [
        ("Volumen casi lleno", "Media e Imágenes: 81\u00a0% usado, 19\u00a0GiB libres.")
    ]


@pytest.mark.parametrize(
    ("locale", "size", "text"),
    [
        ("en", 512, "512\u00a0B"),
        ("en", 1023 * 1024, "1,023\u00a0KiB"),
        ("en", int(1.5 * GIB), "1.5\u00a0GiB"),
        ("en", int(12.34 * 1024**4), "12.3\u00a0TiB"),
        ("es", int(1.5 * GIB), "1,5\u00a0GiB"),
        ("es", 1023 * 1024, "1023\u00a0KiB"),
        ("es", -4 * MIB, "\u22124\u00a0MiB"),
    ],
)
def test_sizes_are_written_as_the_interface_writes_them(locale: str, size: int, text: str) -> None:
    assert format_bytes(size, texts_of(locale)) == text


def test_durations_and_lists_are_written_as_the_interface_writes_them() -> None:
    english, spanish = texts_of("en"), texts_of("es")
    assert [format_duration(value, english) for value in (12.4, 89, 3900, 200_000)] == [
        "12 s", "1 min", "1 h 05 min", "2 days"
    ]
    assert format_duration(200_000, spanish) == "2 días"
    assert listed(["Media"], english) == "Media"
    assert listed(["Media", "Photos", "Backups"], english) == "Media, Photos and Backups"
    assert listed(["Media", "Hijos"], spanish) == "Media e Hijos"
    assert listed(["Media", "Hielo"], spanish) == "Media y Hielo"
    assert listed(["A", "B", "C", "D", "E"], spanish) == "A, B, C y 2 más"
