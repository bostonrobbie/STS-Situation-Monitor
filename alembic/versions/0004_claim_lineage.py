"""claim lineage tables

Revision ID: 0004_claim_lineage
Revises: 0003_investigation_ops_fields
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_claim_lineage"
down_revision = "0003_investigation_ops_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("stance", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claims_investigation_id"), "claims", ["investigation_id"], unique=False)
    op.create_index(op.f("ix_claims_report_id"), "claims", ["report_id"], unique=False)
    op.create_index(op.f("ix_claims_stance"), "claims", ["stance"], unique=False)
    op.create_index(op.f("ix_claims_created_at"), "claims", ["created_at"], unique=False)

    op.create_table(
        "claim_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("observation_id", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("rationale", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["observation_id"], ["observations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_claim_evidence_claim_id"), "claim_evidence", ["claim_id"], unique=False)
    op.create_index(op.f("ix_claim_evidence_observation_id"), "claim_evidence", ["observation_id"], unique=False)
    op.create_index(op.f("ix_claim_evidence_created_at"), "claim_evidence", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_claim_evidence_created_at"), table_name="claim_evidence")
    op.drop_index(op.f("ix_claim_evidence_observation_id"), table_name="claim_evidence")
    op.drop_index(op.f("ix_claim_evidence_claim_id"), table_name="claim_evidence")
    op.drop_table("claim_evidence")

    op.drop_index(op.f("ix_claims_created_at"), table_name="claims")
    op.drop_index(op.f("ix_claims_stance"), table_name="claims")
    op.drop_index(op.f("ix_claims_report_id"), table_name="claims")
    op.drop_index(op.f("ix_claims_investigation_id"), table_name="claims")
    op.drop_table("claims")
