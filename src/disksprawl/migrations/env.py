"""Alembic environment: migrations run on SQLite in a transaction (batch mode for ALTERs)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from disksprawl.db import Base

config = context.config
if config.config_file_name and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

url = config.get_main_option("sqlalchemy.url")
if not url:
    from disksprawl.config import Settings

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{settings.database_path}"

engine = create_engine(url)
with engine.connect() as connection:
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
engine.dispose()
