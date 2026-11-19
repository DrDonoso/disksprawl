"""Push notifications: the end of scans and the volumes that fill up, told to the browsers (and the
apps added to a Home Screen) that turned them on, each as it chose.

A device registers from ⚙ Settings → Notifications (`PUT /api/push/subscription`) with its address
at its push service and its keys (`webpush`), and the language of the interface. The interface
registers it again each time it opens, so its language, keys and session stay current. Each device
chooses what it is told (`PATCH /api/push/subscription`). With a password, signing out on the device
pauses its notifications until it signs in there again.

`Notifier` does a round in a thread of its own every ROUND_SECONDS, and as soon as a scan ends:

1. Each finished scan not dealt with yet is queued (`push_messages`) for every device that wants
   it: the end of the scans started with "Scan now", the end of the scheduled ones, and failed or
   interrupted scans. Cancelled scans tell nobody.
2. The level of each volume (below the warning threshold, warning or critical, as the volume bars
   of the interface show it) is compared with the one each device was last told about: a higher
   level is queued; dropping more than HYSTERESIS points below a threshold lowers it silently.
3. What is queued is sent, outside any database transaction (SQLite has a single writer). What a
   push service or the network failed is tried again later, for a day at most, unless a newer
   notification about the same directory or volume replaces it (on the device it would replace the
   newer one); a device its push service no longer knows is forgotten, and so is one whose push
   service refused three in a row.

Notifications carry the names of directories and their sizes, never paths or file names, and the
logs never quote the address of a device.
"""

import hashlib
import logging
import math
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session, sessionmaker

from . import capacity, queries, settings_store, webpush
from .config import Settings
from .db import Directory, PushMessage, PushSubscription, Scan, transaction, utcnow
from .errors import AppError
from .schemas import PushPreferencesIn, PushPreferencesOut, PushSubscriptionIn

log = logging.getLogger(__name__)

ROUND_SECONDS = 30.0
# What could not be sent within a day is dropped.
MESSAGE_WINDOW = timedelta(days=1)
# Minutes to wait before trying again after a push service or the network failed (the last one
# repeats until the end of the window).
RETRY_MINUTES = (2, 5, 15, 30, 60)
# Notifications in a row a push service may refuse for a device before it is forgotten (it
# registers again the next time DiskSprawl opens there).
MAX_FAILURES = 3
# Devices kept: registering one more forgets the one unused for longest.
MAX_DEVICES = 50
# Points below a threshold a volume must drop before reaching it again is told again.
HYSTERESIS = 2.0
MAX_TITLE = 120
# Directories named in a capacity notification; the rest are counted.
MAX_NAMES = 3
FINISHED = ("completed", "failed", "interrupted", "cancelled")
LEVELS = ("normal", "warning", "critical")
LANGUAGE_TAGS = {"en": "en-GB", "es": "es-ES"}
UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")
MINUS = "\u2212"
NBSP = "\u00a0"


@dataclass(frozen=True)
class Texts:
    """The texts of the notifications in one language, as the interface words the same things
    (frontend/src/i18n/locales). Plurals: (one, other)."""

    completed: str
    failed: str
    interrupted: str
    size: str
    size_change: str
    size_unchanged: str
    took: str
    with_errors: tuple[str, str]
    interrupted_body: str
    reasons: dict[str, str]
    unknown_reason: str
    warning: str
    critical: str
    usage: str
    percent: str
    more: tuple[str, str]
    conjunction: str
    # The conjunction before a name that starts with the sound /i/ (Spanish «e»).
    conjunction_before_i: str | None
    decimal: str
    group: str
    seconds: str
    minutes: str
    hours_minutes: str
    days: str


TEXTS: dict[str, Texts] = {
    "en": Texts(
        completed="Scan finished: {name}",
        failed="Scan failed: {name}",
        interrupted="Scan interrupted: {name}",
        size="{size}.",
        size_change="{size}, {change} since the previous scan.",
        size_unchanged="{size}, no change since the previous scan.",
        took="Took {duration}.",
        with_errors=("Completed with {count} error.", "Completed with {count} errors."),
        interrupted_body="A restart of DiskSprawl interrupted it.",
        reasons={
            "path_invalid": "The folder's path is not valid.",
            "path_not_found": "The folder does not exist (it may not be mounted).",
            "path_unreadable": "The folder cannot be read: check its permissions.",
            "path_outside_root": "The folder must be inside /data.",
            "path_not_directory": "The path is a file, not a folder.",
            "path_unavailable": "The folder is not available.",
            "engine_unavailable": "Its scan engine is not installed.",
            "engine_failed": "The scan engine failed.",
            "scan_failed": "The scan failed.",
            "internal_error": "Unexpected server error: check the container logs.",
        },
        unknown_reason="Open DiskSprawl to see why.",
        warning="Volume nearly full",
        critical="Volume almost out of space",
        usage="{names}: {percent} used, {free} free.",
        percent="{value}%",
        more=("{names} and {count} more", "{names} and {count} more"),
        conjunction="and",
        conjunction_before_i=None,
        decimal=".",
        group=",",
        seconds="{value} s",
        minutes="{value} min",
        hours_minutes="{hours} h {minutes} min",
        days="{value} days",
    ),
    "es": Texts(
        completed="Escaneo terminado: {name}",
        failed="Escaneo fallido: {name}",
        interrupted="Escaneo interrumpido: {name}",
        size="{size}.",
        size_change="{size}, {change} desde el escaneo anterior.",
        size_unchanged="{size}, sin cambios desde el escaneo anterior.",
        took="Ha tardado {duration}.",
        with_errors=("Completado con {count} error.", "Completado con {count} errores."),
        interrupted_body="Un reinicio de DiskSprawl lo ha interrumpido.",
        reasons={
            "path_invalid": "La ruta de la carpeta no es válida.",
            "path_not_found": "La carpeta no existe (quizá no esté montada).",
            "path_unreadable": "No se puede leer la carpeta: revisa sus permisos.",
            "path_outside_root": "La carpeta debe estar dentro de /data.",
            "path_not_directory": "La ruta es un fichero, no una carpeta.",
            "path_unavailable": "La carpeta no está disponible.",
            "engine_unavailable": "Su motor de escaneo no está instalado.",
            "engine_failed": "El motor de escaneo ha fallado.",
            "scan_failed": "El escaneo ha fallado.",
            "internal_error": "Error inesperado del servidor: revisa los logs del contenedor.",
        },
        unknown_reason="Abre DiskSprawl para ver el motivo.",
        warning="Volumen casi lleno",
        critical="Volumen casi sin espacio",
        usage="{names}: {percent} usado, {free} libres.",
        percent="{value}\u00a0%",
        more=("{names} y {count} más", "{names} y {count} más"),
        conjunction="y",
        conjunction_before_i="e",
        decimal=",",
        group="",
        seconds="{value} s",
        minutes="{value} min",
        hours_minutes="{hours} h {minutes} min",
        days="{value} días",
    ),
}


def texts_of(locale: str) -> Texts:
    return TEXTS.get(locale, TEXTS["en"])


def half_up(value: float) -> int:
    """Rounds as JavaScript's Math.round does for the positive numbers of the interface."""
    return math.floor(value + 0.5)


def number(value: float, digits: int, texts: Texts) -> str:
    """`value` with at most `digits` decimals, without trailing zeros, as Intl.NumberFormat
    writes it (English groups thousands; Spanish only from five digits, which never happens
    here)."""
    text = f"{value:,.{digits}f}" if texts.group else f"{value:.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    return text.replace(",", "\0").replace(".", texts.decimal).replace("\0", texts.group)


def format_bytes(size: int, texts: Texts) -> str:
    """As the interface's formatBytes: binary units, three significant digits (`1.43 TiB`)."""
    value = float(abs(size))
    unit = 0
    while value >= 1024 and unit < len(UNITS) - 1:
        value /= 1024
        unit += 1
    digits = 0 if unit == 0 or value >= 100 else 1 if value >= 10 else 2
    return f"{MINUS if size < 0 else ''}{number(value, digits, texts)}{NBSP}{UNITS[unit]}"


def format_change(change: int, texts: Texts) -> str:
    """As formatDelta: `+12.3 GiB`, `−4 MiB`."""
    return f"+{format_bytes(change, texts)}" if change > 0 else format_bytes(change, texts)


def format_percent(value: float, texts: Texts) -> str:
    """As formatPercent: `91%`, `91 %` in Spanish."""
    return texts.percent.format(value=half_up(value))


def format_duration(seconds: float, texts: Texts) -> str:
    """As formatDuration: `12 s`, `42 min`, `1 h 05 min`, `3 days`."""
    total = max(0, half_up(seconds))
    if total < 60:
        return texts.seconds.format(value=total)
    minutes = half_up(total / 60)
    if minutes < 60:
        return texts.minutes.format(value=minutes)
    hours = minutes // 60
    if hours < 48:
        return texts.hours_minutes.format(hours=hours, minutes=f"{minutes % 60:02d}")
    return texts.days.format(value=half_up(hours / 24))


def plural(forms: tuple[str, str], count: int) -> str:
    return forms[0] if count == 1 else forms[1]


def sounds_like_i(word: str) -> bool:
    """Whether Spanish «y» becomes «e» before `word`: it starts with the sound /i/ (`i`, `hi`),
    accented too («Imágenes»), but not `hie`/`hia`, a diphthong («Hierro»)."""
    letters = "".join(
        character
        for character in unicodedata.normalize("NFD", word.casefold())
        if not unicodedata.combining(character)
    )
    return letters.startswith("i") or (
        letters.startswith("hi") and not letters.startswith(("hia", "hie"))
    )


def listed(names: Sequence[str], texts: Texts) -> str:
    """`names` in running text: «Media, Photos and Backups», at most MAX_NAMES of them and then
    how many more."""
    if len(names) > MAX_NAMES:
        shown = ", ".join(names[:MAX_NAMES])
        hidden = len(names) - MAX_NAMES
        return plural(texts.more, hidden).format(names=shown, count=hidden)
    if len(names) < 2:
        return "".join(names)
    conjunction = texts.conjunction
    if texts.conjunction_before_i and sounds_like_i(names[-1]):
        conjunction = texts.conjunction_before_i
    return f"{', '.join(names[:-1])} {conjunction} {names[-1]}"


def shortened(text: str, limit: int = MAX_TITLE) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def notification(
    origin: str, locale: str, *, title: str, body: str, path: str, tag: str
) -> dict[str, Any]:
    """A declarative push message (`"web_push": 8030`, shown by Safari itself and by the service
    worker elsewhere): the notification, and the page of `origin` it opens. Its title says what
    it is about, never the app's name: iOS adds "from DiskSprawl" under every title."""
    return {
        "web_push": 8030,
        "notification": {
            "title": shortened(title),
            "body": body,
            "lang": LANGUAGE_TAGS.get(locale, "en-GB"),
            "dir": "auto",
            "navigate": origin + path,
            "tag": tag,
            "silent": False,
            "icon": f"{origin}/icon-192.png",
            # Android's status bar (a white silhouette); Safari ignores it.
            "badge": f"{origin}/badge-96.png",
        },
    }


def subject_of(kind: str, directory_id: int | None, payload: dict[str, Any] | None) -> str:
    """What a notification is about, as its tag and its topic: a directory's scans, or a volume.
    On a device, and at its push service, a newer notification about it replaces an older one."""
    if kind == "capacity":
        return f"volume-{(payload or {}).get('volume', '')}"
    return f"scan-{directory_id}"


def scan_message(
    scan: Scan, name: str, previous_total: int | None, origin: str, locale: str
) -> dict[str, Any]:
    """The notification of a scan's end: its size and how it changed, or why it failed."""
    texts = texts_of(locale)
    sentences: list[str] = []
    if scan.status == "completed":
        title = texts.completed.format(name=name)
        if scan.total_bytes is not None:
            size = format_bytes(scan.total_bytes, texts)
            if previous_total is None:
                sentences.append(texts.size.format(size=size))
            elif scan.total_bytes == previous_total:
                sentences.append(texts.size_unchanged.format(size=size))
            else:
                change = format_change(scan.total_bytes - previous_total, texts)
                sentences.append(texts.size_change.format(size=size, change=change))
        if scan.duration_seconds is not None:
            duration = format_duration(scan.duration_seconds, texts)
            sentences.append(texts.took.format(duration=duration))
        if scan.error_count:
            errors = plural(texts.with_errors, scan.error_count)
            sentences.append(errors.format(count=scan.error_count))
    elif scan.status == "interrupted":
        title = texts.interrupted.format(name=name)
        sentences.append(texts.interrupted_body)
    else:
        title = texts.failed.format(name=name)
        # Only the code: the details may quote folder names or an engine's output.
        code = (scan.message or "").split(":", 1)[0].strip()
        sentences.append(texts.reasons.get(code, texts.unknown_reason))
    return notification(
        origin,
        locale,
        title=title,
        body=" ".join(sentences),
        path=f"/directories/{scan.directory_id}",
        tag=subject_of("scan", scan.directory_id, None),
    )


def capacity_message(
    payload: dict[str, Any], names: Sequence[str], first: int, origin: str, locale: str
) -> dict[str, Any]:
    """The notification of a volume reaching a threshold: its directories, use and free space."""
    texts = texts_of(locale)
    body = texts.usage.format(
        names=listed(names, texts),
        percent=format_percent(float(payload["percent"]), texts),
        free=format_bytes(int(payload["free"]), texts),
    )
    return notification(
        origin,
        locale,
        title=texts.critical if payload["level"] == "critical" else texts.warning,
        body=body,
        path=f"/directories/{first}",
        tag=subject_of("capacity", None, payload),
    )


def level_of(percent: float, warn: int, critical: int, previous: str) -> str:
    """The level of a volume (normal, warning or critical), as the bars of the interface color
    it; a volume keeps the level it was told about until it drops HYSTERESIS points below its
    threshold, so a volume hovering around one is not told again and again."""
    if percent >= critical or (previous == "critical" and percent >= critical - HYSTERESIS):
        return "critical"
    if percent >= warn or (previous != "normal" and percent >= warn - HYSTERESIS):
        return "warning"
    return "normal"


@dataclass
class Volume:
    """A volume, as the latest capacity samples of its directories (same device) measure it."""

    key: str
    directories: list[int]
    percent: float
    free: int
    measured_at: datetime

    def payload(self, level: str) -> dict[str, Any]:
        return {
            "level": level,
            # Notifications of the same volume replace each other (their tag and topic).
            "volume": hashlib.sha256(self.key.encode()).hexdigest()[:12],
            "directories": list(self.directories),
            "percent": self.percent,
            "free": self.free,
        }


def volumes(db: Session) -> dict[str, Volume]:
    """The volumes of the directories, by device, each directory in the order of the menu."""
    samples = queries.latest_capacity(db)
    order = {
        directory_id: index
        for index, directory_id in enumerate(
            db.scalars(select(Directory.id).order_by(Directory.position, Directory.id))
        )
    }
    found: dict[str, Volume] = {}
    for directory_id in sorted(samples.keys() & order.keys(), key=order.__getitem__):
        sample = samples[directory_id]
        share = capacity.percent(sample.used, sample.free)
        volume = found.get(sample.device)
        if volume is None:
            found[sample.device] = Volume(
                sample.device, [directory_id], share, sample.free, sample.measured_at
            )
            continue
        volume.directories.append(directory_id)
        if sample.measured_at > volume.measured_at:
            volume.percent, volume.free, volume.measured_at = share, sample.free, sample.measured_at
    return found


def wants(device: PushSubscription, kind: str, trigger: str | None = None) -> bool:
    """Whether the device chose to be told about this kind of notification."""
    if kind == "scan_completed":
        return device.notify_manual_scans if trigger == "manual" else device.notify_scheduled_scans
    if kind == "scan_failed":
        return device.notify_failed_scans
    return kind == "capacity" and device.notify_capacity


def scan_kind(scan: Scan) -> str | None:
    if scan.status == "completed":
        return "scan_completed"
    if scan.status in ("failed", "interrupted"):
        return "scan_failed"
    return None


# Registrations and choices (the API)


def preferences_out(device: PushSubscription) -> PushPreferencesOut:
    return PushPreferencesOut(
        manual_scans=device.notify_manual_scans,
        scheduled_scans=device.notify_scheduled_scans,
        failed_scans=device.notify_failed_scans,
        capacity=device.notify_capacity,
    )


def subscribe(
    db: Session, body: PushSubscriptionIn, origin: str, session_hash: str | None
) -> PushPreferencesOut:
    """Registers a device, or refreshes its registration (its choices stay); its choices.
    422 `push_service_unsupported`: its browser uses a push service DiskSprawl does not send to."""
    if webpush.push_service(body.endpoint) is None:
        raise AppError("push_service_unsupported", fields={"endpoint": "push_service_unsupported"})
    now = utcnow()
    statement = insert(PushSubscription).values(
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        origin=origin,
        locale=body.locale,
        session_hash=session_hash,
        notify_manual_scans=True,
        notify_scheduled_scans=False,
        notify_failed_scans=True,
        notify_capacity=True,
        capacity_levels={},
        failures=0,
        created_at=now,
        updated_at=now,
    )
    # Two tabs of the same browser may register it at once: an upsert, never a duplicate.
    fresh = ("p256dh", "auth", "origin", "locale", "session_hash", "failures", "updated_at")
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={name: statement.excluded[name] for name in fresh},
        )
    )
    unused = db.scalars(
        select(PushSubscription.id)
        .order_by(PushSubscription.updated_at.desc(), PushSubscription.id.desc())
        .offset(MAX_DEVICES)
    ).all()
    if unused:
        db.execute(delete(PushSubscription).where(PushSubscription.id.in_(unused)))
    device = db.scalars(
        select(PushSubscription)
        .where(PushSubscription.endpoint == body.endpoint)
        .execution_options(populate_existing=True)
    ).one()
    return preferences_out(device)


def choose(db: Session, body: PushPreferencesIn) -> PushPreferencesOut:
    """Changes what a device is told. 404 `push_subscription_not_found`: it is not registered
    (any more)."""
    device = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if device is None:
        raise AppError("push_subscription_not_found", 404)
    if body.manual_scans is not None:
        device.notify_manual_scans = body.manual_scans
    if body.scheduled_scans is not None:
        device.notify_scheduled_scans = body.scheduled_scans
    if body.failed_scans is not None:
        device.notify_failed_scans = body.failed_scans
    if body.capacity is not None:
        device.notify_capacity = body.capacity
        if not body.capacity:
            # Turned on again, they start by telling the volumes already full.
            device.capacity_levels = {}
    return preferences_out(device)


def unsubscribe(db: Session, endpoint: str) -> None:
    db.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))


# Sending


@dataclass(frozen=True)
class Retry:
    """A notification its push service or the network failed: tried again from `not_before`."""

    attempts: int
    not_before: datetime


def retry_after(previous: Retry | None, now: datetime) -> Retry:
    attempts = previous.attempts + 1 if previous is not None else 1
    wait = RETRY_MINUTES[min(attempts, len(RETRY_MINUTES)) - 1]
    return Retry(attempts, now + timedelta(minutes=wait))


@dataclass(frozen=True)
class Outgoing:
    """A queued notification ready to send to one device."""

    message_id: int
    device_id: int
    subscription: webpush.Subscription
    subject: str
    message: dict[str, Any]
    ttl: int
    urgency: webpush.Urgency = "normal"
    topic: str | None = None


@dataclass
class Round:
    """What a round did with the queued notifications."""

    dropped: list[int] = field(default_factory=list)
    results: list[tuple[Outgoing, webpush.Outcome]] = field(default_factory=list)


class Notifier:
    """Queues and sends the notifications (see the module's description): `tick` does one round,
    `start`/`stop` run them in a thread of the app's process."""

    # Tests start it paused and call tick() themselves, so that no round runs in the middle of one.
    paused = False

    def __init__(
        self,
        sessions: sessionmaker[Session],
        config: Settings,
        sender: webpush.WebPush | None = None,
    ):
        self.sessions = sessions
        self.auth_enabled = config.auth_enabled
        self.sender = sender or webpush.WebPush(config.signing_key())
        # Queued notifications to try again, by id.
        self.retries: dict[int, Retry] = {}
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._round = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def public_key(self) -> str:
        return self.sender.public_key

    def start(self) -> None:
        if self.paused:
            return
        self._thread = threading.Thread(target=self._loop, name="notifier", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 20) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def wake(self, _scan_id: int | None = None) -> None:
        """Starts a round now (when a scan ends, or a device registers or chooses)."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                self.tick()
            except Exception:
                log.exception("Notifications could not be sent; trying again")
            self._wake.wait(ROUND_SECONDS)

    def active(self, device: PushSubscription) -> bool:
        """Whether a device gets notifications: with a password, only while it is signed in."""
        return not self.auth_enabled or device.session_hash is not None

    def devices(self, db: Session) -> list[PushSubscription]:
        """The devices that get notifications now."""
        return [device for device in db.scalars(select(PushSubscription)) if self.active(device)]

    def tick(self, now: datetime | None = None) -> None:
        """One round: queue what happened, then send what is queued."""
        with self._round:
            moment = now or utcnow()
            self.queue_scans(moment)
            self.queue_capacity(moment)
            self.deliver(moment)

    def queue_scans(self, now: datetime) -> None:
        with transaction(self.sessions) as db:
            scans = db.scalars(
                select(Scan)
                .where(Scan.notified.is_(False), Scan.status.in_(FINISHED))
                .order_by(Scan.id)
            ).all()
            if not scans:
                return
            devices = self.devices(db)
            for scan in scans:
                kind = scan_kind(scan)
                for device in devices:
                    if kind is not None and wants(device, kind, scan.trigger):
                        db.add(
                            PushMessage(
                                subscription_id=device.id,
                                kind=kind,
                                scan_id=scan.id,
                                created_at=now,
                            )
                        )
                scan.notified = True

    def queue_capacity(self, now: datetime) -> None:
        with transaction(self.sessions) as db:
            devices = self.devices(db)
            if not devices:
                return
            found = volumes(db)
            limits = settings_store.load(db)
            warn, critical = limits["warn_percent"], limits["critical_percent"]
            for device in devices:
                if not device.notify_capacity:
                    continue
                told = dict(device.capacity_levels or {})
                levels: dict[str, str] = {}
                for key, volume in found.items():
                    previous = told.get(key, "normal")
                    level = level_of(volume.percent, warn, critical, previous)
                    if level != "normal":
                        levels[key] = level
                    if LEVELS.index(level) > LEVELS.index(previous):
                        db.add(
                            PushMessage(
                                subscription_id=device.id,
                                kind="capacity",
                                payload=volume.payload(level),
                                created_at=now,
                            )
                        )
                if levels != told:
                    device.capacity_levels = levels

    def deliver(self, now: datetime) -> None:
        done = Round()
        outgoing: list[Outgoing] = []
        with self.sessions() as db:
            rows = db.execute(
                select(PushMessage, PushSubscription, Scan.directory_id)
                .join(PushSubscription, PushSubscription.id == PushMessage.subscription_id)
                .outerjoin(Scan, Scan.id == PushMessage.scan_id)
                .order_by(PushMessage.id)
            ).all()
            queued = {message.id for message, _, _ in rows}
            self.retries = {key: retry for key, retry in self.retries.items() if key in queued}
            # The newest message of each device about each subject: an older one still queued
            # (waiting to be tried again, for example) would replace it on the device.
            newest = {
                (device.id, subject_of(message.kind, directory_id, message.payload)): message.id
                for message, device, directory_id in rows
            }
            for message, device, directory_id in rows:
                until = message.created_at + MESSAGE_WINDOW
                subject = (device.id, subject_of(message.kind, directory_id, message.payload))
                if until <= now or not self.active(device) or newest[subject] != message.id:
                    done.dropped.append(message.id)
                    continue
                retry = self.retries.get(message.id)
                if retry is not None and retry.not_before > now:
                    continue
                ttl = max(60, int((until - now).total_seconds()))
                ready = self.prepare(db, message, device, ttl)
                if ready is None:
                    done.dropped.append(message.id)
                else:
                    outgoing.append(ready)
        gone: set[int] = set()
        for item in outgoing:
            # Its push service forgot the device: what else was queued for it goes with it.
            if item.device_id in gone:
                continue
            result = self.send(item)
            if result == "gone":
                gone.add(item.device_id)
            done.results.append((item, result))
        if done.dropped or done.results:
            self.record(now, done)

    def prepare(
        self, db: Session, message: PushMessage, device: PushSubscription, ttl: int
    ) -> Outgoing | None:
        """The notification of a queued message for its device, or None when there is nothing to
        tell any more: the device no longer wants it, or its scan or directories are gone."""
        if message.kind == "capacity":
            payload = message.payload or {}
            ids = [int(item) for item in payload.get("directories", [])]
            names = {
                directory_id: name
                for directory_id, name in db.execute(
                    select(Directory.id, Directory.name).where(Directory.id.in_(ids))
                )
            }
            present = [directory_id for directory_id in ids if directory_id in names]
            if not wants(device, "capacity") or not present:
                return None
            content = capacity_message(
                payload, [names[item] for item in present], present[0], device.origin, device.locale
            )
            urgency: webpush.Urgency = "high" if payload.get("level") == "critical" else "normal"
            topic = content["notification"]["tag"]
        else:
            scan = db.get(Scan, message.scan_id) if message.scan_id is not None else None
            directory = db.get(Directory, scan.directory_id) if scan is not None else None
            if scan is None or directory is None or not wants(device, message.kind, scan.trigger):
                return None
            previous = db.scalar(
                select(Scan.total_bytes)
                .where(
                    Scan.directory_id == scan.directory_id,
                    Scan.status == "completed",
                    Scan.id < scan.id,
                )
                .order_by(Scan.id.desc())
                .limit(1)
            )
            content = scan_message(scan, directory.name, previous, device.origin, device.locale)
            urgency = "normal"
            topic = content["notification"]["tag"]
        return Outgoing(
            message_id=message.id,
            device_id=device.id,
            subscription=webpush.Subscription(device.endpoint, device.p256dh, device.auth),
            subject=webpush.vapid_subject(device.origin),
            message=content,
            ttl=ttl,
            urgency=urgency,
            topic=topic,
        )

    def send(self, item: Outgoing) -> webpush.Outcome:
        """Sends one notification; never fails (one that cannot be sent now is tried again)."""
        try:
            return self.sender.send(
                item.subscription,
                item.message,
                ttl=item.ttl,
                subject=item.subject,
                urgency=item.urgency,
                topic=item.topic,
            )
        except Exception as error:
            log.error("Notification %s not sent (%s)", item.message_id, type(error).__name__)
            return "retry"

    def record(self, now: datetime, done: Round) -> None:
        finished = list(done.dropped)
        with transaction(self.sessions) as db:
            for item, result in done.results:
                if result == "retry":
                    previous = self.retries.get(item.message_id)
                    self.retries[item.message_id] = retry_after(previous, now)
                    continue
                self.retries.pop(item.message_id, None)
                if result == "gone":
                    db.execute(
                        delete(PushSubscription).where(PushSubscription.id == item.device_id)
                    )
                    continue
                finished.append(item.message_id)
                failures = 0 if result == "delivered" else PushSubscription.failures + 1
                db.execute(
                    update(PushSubscription)
                    .where(PushSubscription.id == item.device_id)
                    .values(failures=failures)
                )
            if finished:
                db.execute(delete(PushMessage).where(PushMessage.id.in_(finished)))
            db.execute(delete(PushSubscription).where(PushSubscription.failures >= MAX_FAILURES))
        sent = sum(result == "delivered" for _, result in done.results)
        if sent:
            log.info("%d notification(s) sent", sent)
