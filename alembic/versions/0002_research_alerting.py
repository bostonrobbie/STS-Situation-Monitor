"""research sources and alerting tables

Revision ID: 0002_research_alerting
Revises: 0001_initial
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_research_alerting"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.String(length=1200), nullable=False),
        sa.Column("trust_score", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("tags_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_research_sources_created_at"), "research_sources", ["created_at"], unique=False)

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("min_observations", sa.Integer(), nullable=False),
        sa.Column("min_disputed_claims", sa.Integer(), nullable=False),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_rules_created_at"), "alert_rules", ["created_at"], unique=False)
    op.create_index(op.f("ix_alert_rules_investigation_id"), "alert_rules", ["investigation_id"], unique=False)
    op.create_index(op.f("ix_alert_rules_updated_at"), "alert_rules", ["updated_at"], unique=False)

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(length=30), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_events_investigation_id"), "alert_events", ["investigation_id"], unique=False)
    op.create_index(op.f("ix_alert_events_rule_id"), "alert_events", ["rule_id"], unique=False)
    op.create_index(op.f("ix_alert_events_triggered_at"), "alert_events", ["triggered_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_alert_events_triggered_at"), table_name="alert_events")
    op.drop_index(op.f("ix_alert_events_rule_id"), table_name="alert_events")
    op.drop_index(op.f("ix_alert_events_investigation_id"), table_name="alert_events")
    op.drop_table("alert_events")

    op.drop_index(op.f("ix_alert_rules_updated_at"), table_name="alert_rules")
    op.drop_index(op.f("ix_alert_rules_investigation_id"), table_name="alert_rules")
    op.drop_index(op.f("ix_alert_rules_created_at"), table_name="alert_rules")
    op.drop_table("alert_rules")

    op.drop_index(op.f("ix_research_sources_created_at"), table_name="research_sources")
    op.drop_table("research_sources")
