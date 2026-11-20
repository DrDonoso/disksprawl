import sqlite3
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from disksprawl.db import Base, create_db_engine, migrate


def test_the_migrations_create_exactly_the_models(tmp_path: Path) -> None:
    path = tmp_path / "disksprawl.db"
    migrate(path)
    engine = create_db_engine(path)
    try:
        with engine.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        engine.dispose()
    assert differences == []


def test_the_scan_logs_migration_goes_back(tmp_path: Path) -> None:
    path = tmp_path / "disksprawl.db"
    config = Config()
    config.set_main_option("script_location", "disksprawl:migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    config.attributes["configure_logger"] = False

    def columns() -> set[str]:
        with sqlite3.connect(path) as connection:
            return {row[1] for row in connection.execute("PRAGMA table_info(scans)")}

    command.upgrade(config, "head")
    assert "log_file" in columns()
    command.downgrade(config, "0003")
    assert "log_file" not in columns() and "notified" in columns()
    command.upgrade(config, "head")
    assert "log_file" in columns()
