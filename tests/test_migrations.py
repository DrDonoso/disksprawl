from pathlib import Path

from alembic.autogenerate import compare_metadata
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
