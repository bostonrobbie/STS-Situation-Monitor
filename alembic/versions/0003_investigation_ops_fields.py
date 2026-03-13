"""investigation ops fields

Revision ID: 0003_investigation_ops_fields
Revises: 0002_research_alerting
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_investigation_ops_fields"
down_revision = "0002_research_alerting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("investigations", sa.Column("priority", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("investigations", sa.Column("owner", sa.String(length=120), nullable=True))
    op.add_column("investigations", sa.Column("status", sa.String(length=30), nullable=False, server_default="open"))
    op.add_column("investigations", sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_investigations_priority"), "investigations", ["priority"], unique=False)
    op.create_index(op.f("ix_investigations_owner"), "investigations", ["owner"], unique=False)
    op.create_index(op.f("ix_investigations_status"), "investigations", ["status"], unique=False)
    op.create_index(op.f("ix_investigations_sla_due_at"), "investigations", ["sla_due_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_investigations_sla_due_at"), table_name="investigations")
    op.drop_index(op.f("ix_investigations_status"), table_name="investigations")
    op.drop_index(op.f("ix_investigations_owner"), table_name="investigations")
    op.drop_index(op.f("ix_investigations_priority"), table_name="investigations")
    op.drop_column("investigations", "sla_due_at")
    op.drop_column("investigations", "status")
    op.drop_column("investigations", "owner")
    op.drop_column("investigations", "priority")
