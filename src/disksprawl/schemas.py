"""Request and response models: the API contract (exported to OpenAPI, typed in the frontend)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Engine = Literal["native", "du", "ncdu", "gdu"]
Pace = Literal["max", "balanced", "gentle"]
Measure = Literal["apparent", "disk"]
ScanStatus = Literal["queued", "running", "completed", "failed", "cancelled", "interrupted"]
Trigger = Literal["scheduled", "manual"]
Phase = Literal["queued", "scanning", "finishing", "saving"]
ProgressKind = Literal["live", "folders", "none"]

DEFAULT_EXCLUDES = ("#snapshot", ".snapshot", ".zfs")
MAX_EXCLUDES = 50
FORBIDDEN_PATTERN_CHARACTERS = "/\\[]\0"


class Contract(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScanOptions(Contract):
    engine: Engine
    threads: int
    pace: Pace
    low_priority: bool
    measure: Measure
    one_filesystem: bool
    excludes: list[str]


class DirectoryIn(Input):
    name: str = Field(min_length=1, max_length=60)
    path: str = Field(min_length=1, max_length=4096)
    enabled: bool = True
    # Five-field cron expression in the server's time zone; null scans only on demand.
    cron: str | None = Field(default="0 3 * * *", max_length=120)
    engine: Engine = "native"
    threads: int = Field(default=2, ge=1, le=16)
    pace: Pace = "balanced"
    low_priority: bool = True
    measure: Measure = "apparent"
    one_filesystem: bool = True
    excludes: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name_required")
        return value

    @field_validator("excludes")
    @classmethod
    def clean_excludes(cls, value: list[str]) -> list[str]:
        patterns: list[str] = []
        for raw in value:
            pattern = raw.strip()
            if not pattern:
                continue
            if len(pattern) > 255 or any(c in pattern for c in FORBIDDEN_PATTERN_CHARACTERS):
                raise ValueError("invalid_exclude")
            if pattern not in patterns:
                patterns.append(pattern)
        if len(patterns) > MAX_EXCLUDES:
            raise ValueError("too_many_excludes")
        return patterns


class DirectoryOut(Contract):
    id: int
    name: str
    path: str
    enabled: bool
    position: int
    cron: str | None
    engine: Engine
    threads: int
    pace: Pace
    low_priority: bool
    measure: Measure
    one_filesystem: bool
    excludes: list[str]
    created_at: datetime
    updated_at: datetime


class OrderIn(Input):
    ids: list[int] = Field(max_length=10_000)


class ScanError(Contract):
    path: str
    message: str


class ScanOut(Contract):
    id: int
    directory_id: int
    status: ScanStatus
    trigger: Trigger
    engine: Engine
    options: ScanOptions
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    total_bytes: int | None
    file_count: int | None
    dir_count: int | None
    error_count: int
    error_sample: list[ScanError]
    message: str | None
    capacity_total: int | None
    capacity_used: int | None
    capacity_free: int | None
    has_detail: bool


class ScanListOut(Contract):
    items: list[ScanOut]


class ProgressOut(Contract):
    scan_id: int
    directory_id: int
    status: Literal["queued", "running"]
    phase: Phase
    # Place in the queue, 1 being the next scan to start; null once running.
    position: int | None
    queued_at: datetime
    started_at: datetime | None
    elapsed_seconds: float
    # Whether the counters are live: gdu reports nothing until it ends.
    counters: bool
    dirs: int
    files: int | None
    bytes: int
    current: str | None
    # Estimated share done (0–0.99), from the folders and files read compared with the previous
    # completed scan, and the seconds left at the pace so far.
    percent: float | None
    remaining_seconds: float | None
    previous_duration_seconds: float | None
    engine: Engine
    pace: Pace
    threads: int


class QueueOut(Contract):
    items: list[ProgressOut]


class DirectoryRef(Contract):
    id: int
    name: str


class CapacityOut(Contract):
    total: int
    used: int
    free: int
    percent: float
    measured_at: datetime
    # Other directories on the same volume (the figures are the volume's, shared by all).
    shared_with: list[DirectoryRef]


class DirectorySummaryOut(Contract):
    directory: DirectoryOut
    # The latest completed scan: the data on screen.
    last_scan: ScanOut | None
    # The latest finished scan when it did not complete (failed, cancelled, interrupted) and is
    # newer than last_scan.
    last_attempt: ScanOut | None
    progress: ProgressOut | None
    capacity: CapacityOut | None
    next_run: datetime | None


class DirectoryListOut(Contract):
    items: list[DirectorySummaryOut]


class TreeChildOut(Contract):
    name: str
    path: str
    size: int
    files: int | None
    dirs: int
    has_children: bool


class TreeOut(Contract):
    scan: ScanOut
    path: str
    size: int
    files: int | None
    dirs: int
    # Bytes directly in this folder or in folders below the stored minimum size.
    other_size: int
    children: list[TreeChildOut]


class DiffChildOut(Contract):
    name: str
    path: str
    before: int | None
    after: int | None
    has_children: bool


class DiffChangeOut(Contract):
    path: str
    before: int | None
    after: int | None


class DiffOut(Contract):
    from_scan: ScanOut
    to_scan: ScanOut
    path: str
    before: int | None
    after: int | None
    other_before: int
    other_after: int
    children: list[DiffChildOut]
    # The folders below `path` that changed the most, most specific first.
    biggest: list[DiffChangeOut]


class CapacityPointOut(Contract):
    measured_at: datetime
    total: int
    used: int
    free: int
    percent: float


class CapacityHistoryOut(Contract):
    points: list[CapacityPointOut]


class SettingsIn(Input):
    capacity_interval_minutes: int = Field(ge=1, le=1440)
    keep_scans: int = Field(ge=1, le=1000)
    min_entry_mib: int = Field(ge=1, le=1024)
    warn_percent: int = Field(ge=1, le=99)
    critical_percent: int = Field(ge=2, le=100)


class SettingsOut(Contract):
    capacity_interval_minutes: int
    keep_scans: int
    min_entry_mib: int
    warn_percent: int
    critical_percent: int
    # The server's time zone (TZ): schedules run on its clock.
    timezone: str


class EngineOut(Contract):
    id: Engine
    available: bool
    version: str | None
    max_threads: int
    progress: ProgressKind


class EngineListOut(Contract):
    items: list[EngineOut]


class CronPreviewOut(Contract):
    expression: str
    timezone: str
    next_runs: list[datetime]


class BrowseEntryOut(Contract):
    name: str
    path: str


class BrowseOut(Contract):
    root: str
    path: str
    parent: str | None
    entries: list[BrowseEntryOut]


class AuthStatusOut(Contract):
    enabled: bool
    authenticated: bool


class LoginIn(Input):
    password: str = Field(min_length=1, max_length=1024)


class VersionOut(Contract):
    version: str
    build_date: str


class HealthOut(Contract):
    status: str
