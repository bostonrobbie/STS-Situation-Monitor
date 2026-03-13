"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-03-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("topic", sa.String(length=300), nullable=False),
        sa.Column("seed_query", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "job_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("job_type", sa.String(length=60), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_job_schedules_created_at"), "job_schedules", ["created_at"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.String(length=60), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dead_lettered", sa.Boolean(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_created_at"), "jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_jobs_dead_lettered"), "jobs", ["dead_lettered"], unique=False)
    op.create_index(op.f("ix_jobs_job_type"), "jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_jobs_priority"), "jobs", ["priority"], unique=False)
    op.create_index(op.f("ix_jobs_run_at"), "jobs", ["run_at"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)
    op.create_index(op.f("ix_jobs_updated_at"), "jobs", ["updated_at"], unique=False)

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(length=50), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedback_created_at"), "feedback", ["created_at"], unique=False)
    op.create_index(op.f("ix_feedback_investigation_id"), "feedback", ["investigation_id"], unique=False)

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("connector", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ingestion_runs_investigation_id"), "ingestion_runs", ["investigation_id"], unique=False)
    op.create_index(op.f("ix_ingestion_runs_started_at"), "ingestion_runs", ["started_at"], unique=False)

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=600), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=1200), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reliability_hint", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_observations_investigation_id"), "observations", ["investigation_id"], unique=False)

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("investigation_id", sa.String(length=36), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("accepted_json", sa.Text(), nullable=False),
        sa.Column("dropped_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reports_generated_at"), "reports", ["generated_at"], unique=False)
    op.create_index(op.f("ix_reports_investigation_id"), "reports", ["investigation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reports_investigation_id"), table_name="reports")
    op.drop_index(op.f("ix_reports_generated_at"), table_name="reports")
    op.drop_table("reports")

    op.drop_index(op.f("ix_observations_investigation_id"), table_name="observations")
    op.drop_table("observations")

    op.drop_index(op.f("ix_ingestion_runs_started_at"), table_name="ingestion_runs")
    op.drop_index(op.f("ix_ingestion_runs_investigation_id"), table_name="ingestion_runs")
    op.drop_table("ingestion_runs")

    op.drop_index(op.f("ix_feedback_investigation_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_created_at"), table_name="feedback")
    op.drop_table("feedback")

    op.drop_index(op.f("ix_jobs_updated_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_run_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_priority"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_job_type"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_dead_lettered"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_created_at"), table_name="jobs")
    op.drop_table("jobs")

    op.drop_index(op.f("ix_job_schedules_created_at"), table_name="job_schedules")
    op.drop_table("job_schedules")

    op.drop_table("investigations")
