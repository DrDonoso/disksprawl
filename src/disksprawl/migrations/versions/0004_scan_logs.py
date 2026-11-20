"""Scan logs: the log file of each scan that started (scans.log_file), relative to DATA_DIR/logs.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: SQLite adds it in place, without rebuilding the scans table.
    op.add_column("scans", sa.Column("log_file", sa.String(300), nullable=True))


def downgrade() -> None:
    # The files stay in DATA_DIR/logs, without a scan to download them from.
    with op.batch_alter_table("scans") as batch:
        batch.drop_column("log_file")
