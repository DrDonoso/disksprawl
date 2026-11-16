"""Initial schema: directories, scans, folders, capacity samples, settings and sessions.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "directories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("path", sa.String(4096), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("cron", sa.String(120), nullable=True),
        sa.Column("engine", sa.String(10), nullable=False),
        sa.Column("threads", sa.Integer(), nullable=False),
        sa.Column("pace", sa.String(10), nullable=False),
        sa.Column("low_priority", sa.Boolean(), nullable=False),
        sa.Column("measure", sa.String(10), nullable=False),
        sa.Column("one_filesystem", sa.Boolean(), nullable=False),
        sa.Column("excludes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "directory_id",
            sa.Integer(),
            sa.ForeignKey("directories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("engine", sa.String(10), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("total_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_count", sa.BigInteger(), nullable=True),
        sa.Column("dir_count", sa.BigInteger(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("error_sample", sa.JSON(), nullable=False),
        sa.Column("message", sa.String(1000), nullable=True),
        sa.Column("capacity_total", sa.BigInteger(), nullable=True),
        sa.Column("capacity_used", sa.BigInteger(), nullable=True),
        sa.Column("capacity_free", sa.BigInteger(), nullable=True),
        sa.Column("has_detail", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_scans_directory_status", "scans", ["directory_id", "status", "finished_at"]
    )
    op.create_index("ix_scans_status_queued", "scans", ["status", "queued_at"])
    op.create_table(
        "paths",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "directory_id",
            sa.Integer(),
            sa.ForeignKey("directories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id", sa.Integer(), sa.ForeignKey("paths.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.String(4096), nullable=False),
        sa.UniqueConstraint("directory_id", "parent_id", "name", name="uq_paths_parent_name"),
    )
    op.create_index("ix_paths_parent", "paths", ["parent_id"])
    op.create_index(
        "uq_paths_root",
        "paths",
        ["directory_id"],
        unique=True,
        sqlite_where=sa.text("parent_id IS NULL"),
    )
    op.create_table(
        "scan_entries",
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "path_id",
            sa.Integer(),
            sa.ForeignKey("paths.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("files", sa.BigInteger(), nullable=True),
        sa.Column("dirs", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_scan_entries_path", "scan_entries", ["path_id"])
    op.create_table(
        "capacity_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "directory_id",
            sa.Integer(),
            sa.ForeignKey("directories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("measured_at", sa.DateTime(), nullable=False),
        sa.Column("total", sa.BigInteger(), nullable=False),
        sa.Column("used", sa.BigInteger(), nullable=False),
        sa.Column("free", sa.BigInteger(), nullable=False),
        sa.Column("device", sa.String(40), nullable=False),
    )
    op.create_index(
        "ix_capacity_directory_time", "capacity_samples", ["directory_id", "measured_at"]
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "auth_sessions",
        "settings",
        "capacity_samples",
        "scan_entries",
        "paths",
        "scans",
        "directories",
    ):
        op.drop_table(table)
