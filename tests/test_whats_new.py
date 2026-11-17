import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import WRITE
from fastapi.testclient import TestClient
from pydantic import SecretStr

from disksprawl.app import create_app
from disksprawl.config import Settings

FIRST = "2026-11-17-whats-new"


def test_requires_a_session_when_a_password_is_set(settings: Settings) -> None:
    protected = settings.model_copy(update={"auth_password": SecretStr("correct horse")})
    with TestClient(create_app(protected)) as anonymous:
        assert anonymous.get("/api/whats-new").status_code == 401
        assert anonymous.post("/api/whats-new/seen", json={"ids": [FIRST]}, headers=WRITE).status_code == 401


def test_nothing_is_recorded_until_the_first_visit_then_notes_merge(client: TestClient) -> None:
    assert client.get("/api/whats-new").json() == {"seen": None}
    read = ["2026-11-17-b", "2026-11-18-a"]
    assert client.post("/api/whats-new/seen", json={"ids": read[::-1]}, headers=WRITE).json() == {"seen": read}
    again = client.post("/api/whats-new/seen", json={"ids": ["2026-11-18-a", "2026-12-01-c"]}, headers=WRITE)
    assert again.json() == {"seen": [*read, "2026-12-01-c"]}
    assert client.get("/api/whats-new").json() == again.json()


def test_keeps_the_newest_thousand(client: TestClient) -> None:
    for start in (0, 500):
        ids = [f"2026-01-01-n{index:04d}" for index in range(start, start + 500)]
        assert client.post("/api/whats-new/seen", json={"ids": ids}, headers=WRITE).status_code == 200
    extra = client.post("/api/whats-new/seen", json={"ids": ["2026-02-01-last"]}, headers=WRITE)
    seen = extra.json()["seen"]
    assert len(seen) == 1000
    assert seen[-1] == "2026-02-01-last"
    assert "2026-01-01-n0000" not in seen


@pytest.mark.parametrize(
    "ids",
    [[], ["novedades"], ["2026-11-17-Whats-New"], [7], "2026-11-17-a", [f"2026-11-17-{'a' * 70}"],
     [f"2026-01-01-n{index}" for index in range(501)]],
)
def test_checks_the_ids(client: TestClient, ids: object) -> None:
    answer = client.post("/api/whats-new/seen", json={"ids": ids}, headers=WRITE)
    assert answer.status_code == 422
    assert answer.json()["code"] == "invalid_request"
    assert client.get("/api/whats-new").json() == {"seen": None}


def test_forbids_other_fields(client: TestClient) -> None:
    answer = client.post("/api/whats-new/seen", json={"ids": [FIRST], "all": True}, headers=WRITE)
    assert answer.status_code == 422


def upgrade_config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", "disksprawl:migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    config.attributes["configure_logger"] = False
    return config


def stored(path: Path) -> list[str] | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = 'whats_new_seen'").fetchone()
    return None if row is None else json.loads(row[0])


def test_the_migration_marks_only_existing_installs(tmp_path: Path) -> None:
    fresh, existing = tmp_path / "fresh.db", tmp_path / "existing.db"
    command.upgrade(upgrade_config(fresh), "head")
    assert stored(fresh) is None

    command.upgrade(upgrade_config(existing), "0001")
    with sqlite3.connect(existing) as connection:
        connection.execute(
            "INSERT INTO directories (name, path, enabled, position, engine, threads, pace,"
            " low_priority, measure, one_filesystem, excludes, created_at, updated_at)"
            " VALUES ('Storage', '/mnt/storage', 1, 0, 'native', 1, 'gentle', 1, 'disk', 1, '[]',"
            " '2026-11-17 00:00:00', '2026-11-17 00:00:00')"
        )
    command.upgrade(upgrade_config(existing), "head")
    assert stored(existing) == []
    command.downgrade(upgrade_config(existing), "0001")
    assert stored(existing) is None
