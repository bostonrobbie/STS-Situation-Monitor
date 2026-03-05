"""search profiles table

Revision ID: 0006_search_profiles
Revises: 0005_auth_and_audit
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_search_profiles"
down_revision = "0005_auth_and_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=True),
        sa.Column("include_terms_json", sa.Text(), nullable=False),
        sa.Column("exclude_terms_json", sa.Text(), nullable=False),
        sa.Column("synonyms_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_search_profiles_created_at"), "search_profiles", ["created_at"], unique=False)
    op.create_index(op.f("ix_search_profiles_investigation_id"), "search_profiles", ["investigation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_search_profiles_investigation_id"), table_name="search_profiles")
    op.drop_index(op.f("ix_search_profiles_created_at"), table_name="search_profiles")
    op.drop_table("search_profiles")
