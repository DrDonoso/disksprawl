"""Push notifications: the devices that turned them on, with their choices (push_subscriptions),
the notifications still to send (push_messages), and which finished scans were dealt with
(scans.notified: every scan of earlier versions counts as notified, so nothing old is told).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: SQLite adds it in place, without rebuilding the scans table.
    op.add_column(
        "scans", sa.Column("notified", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.execute("UPDATE scans SET notified = 1")
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint", sa.String(2048), nullable=False, unique=True),
        sa.Column("p256dh", sa.String(100), nullable=False),
        sa.Column("auth", sa.String(40), nullable=False),
        sa.Column("origin", sa.String(300), nullable=False),
        sa.Column("locale", sa.String(2), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=True),
        sa.Column("notify_manual_scans", sa.Boolean(), nullable=False),
        sa.Column("notify_scheduled_scans", sa.Boolean(), nullable=False),
        sa.Column("notify_failed_scans", sa.Boolean(), nullable=False),
        sa.Column("notify_capacity", sa.Boolean(), nullable=False),
        sa.Column("capacity_levels", sa.JSON(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "push_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Integer(),
            sa.ForeignKey("push_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column(
            "scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_push_messages_subscription", "push_messages", ["subscription_id"])
    op.create_index("ix_push_messages_scan", "push_messages", ["scan_id"])


def downgrade() -> None:
    op.drop_table("push_messages")
    op.drop_table("push_subscriptions")
    # SQLite rebuilds the table to drop the column (foreign keys are off while migrating, so the
    # rebuild deletes nothing that refers to it).
    with op.batch_alter_table("scans") as batch:
        batch.drop_column("notified")
