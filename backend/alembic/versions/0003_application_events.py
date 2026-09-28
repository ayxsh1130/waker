"""application connector events

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "application_events",
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(length=100), nullable=True),
        sa.Column("task_name", sa.String(length=200), nullable=True),
        sa.Column("worker_id", sa.String(length=200), nullable=True),
        sa.Column("queue", sa.String(length=100), nullable=True),
        sa.Column("correlation_id", sa.String(length=100), nullable=True),
        sa.Column("trace_id", sa.String(length=100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_application_events_event_id", "application_events", ["event_id"], unique=True)
    op.create_index("ix_application_events_application_id", "application_events", ["application_id"], unique=False)
    op.create_index("ix_application_events_event_type", "application_events", ["event_type"], unique=False)
    op.create_index("ix_application_events_occurred_at", "application_events", ["occurred_at"], unique=False)
    op.create_index("ix_application_events_task_id", "application_events", ["task_id"], unique=False)
    op.create_index("ix_application_events_worker_id", "application_events", ["worker_id"], unique=False)
    op.create_index("ix_application_events_queue", "application_events", ["queue"], unique=False)
    op.create_index("ix_application_events_correlation_id", "application_events", ["correlation_id"], unique=False)


def downgrade():
    for name in [
        "ix_application_events_correlation_id",
        "ix_application_events_queue",
        "ix_application_events_worker_id",
        "ix_application_events_task_id",
        "ix_application_events_occurred_at",
        "ix_application_events_event_type",
        "ix_application_events_application_id",
        "ix_application_events_event_id",
    ]:
        op.drop_index(name, table_name="application_events")
    op.drop_table("application_events")
