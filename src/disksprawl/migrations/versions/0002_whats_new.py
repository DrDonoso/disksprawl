"""What's new: installs that already hold data start with an empty list of read notes.

A new install records nothing, so its first visit has no news to catch up on; an existing install
records an empty list, so the notes shipped from now on reach it.

Revision ID: 0002
Revises: 0001
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

KEY = "whats_new_seen"


def upgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM directories LIMIT 1")).first() is None:
        return
    connection.execute(
        sa.text("INSERT OR IGNORE INTO settings (key, value) VALUES (:key, :value)"),
        {"key": KEY, "value": json.dumps([])},
    )


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM settings WHERE key = :key"), {"key": KEY})
