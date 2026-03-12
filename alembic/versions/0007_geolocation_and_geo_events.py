"""geolocation fields and geo_events table

Revision ID: 0007_geolocation_and_geo_events
Revises: 0006_search_profiles
Create Date: 2026-03-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_geolocation_and_geo_events"
down_revision = "0006_search_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add geolocation columns to observations
    op.add_column("observations", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("observations", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column("observations", sa.Column("geo_source", sa.String(length=30), server_default="", nullable=False))
    op.add_column("observations", sa.Column("connector_type", sa.String(length=50), server_default="rss", nullable=False))
    op.create_index("ix_observations_connector_type", "observations", ["connector_type"], unique=False)

    # Geo events table for transient spatial data (aircraft, ships, fires, quakes)
    op.create_table(
        "geo_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("layer", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("altitude", sa.Float(), nullable=True),
        sa.Column("magnitude", sa.Float(), nullable=True),
        sa.Column("properties_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("investigation_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_geo_events_layer", "geo_events", ["layer"], unique=False)
    op.create_index("ix_geo_events_event_time", "geo_events", ["event_time"], unique=False)
    op.create_index("ix_geo_events_expires", "geo_events", ["expires_at"], unique=False)
    op.create_index("ix_geo_events_source_id", "geo_events", ["source_id"], unique=False)

    # Convergence zones table
    op.create_table(
        "convergence_zones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("center_lat", sa.Float(), nullable=False),
        sa.Column("center_lon", sa.Float(), nullable=False),
        sa.Column("radius_km", sa.Float(), server_default="50.0", nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("signal_types_json", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=20), server_default="low", nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("investigation_id", sa.String(length=36), nullable=True),
        sa.Column("detail_json", sa.Text(), server_default="{}", nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_convergence_zones_severity", "convergence_zones", ["severity"], unique=False)

    # Dashboard configs table
    op.create_table(
        "dashboard_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("layout_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("active_layers_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("map_center_lat", sa.Float(), server_default="20.0", nullable=False),
        sa.Column("map_center_lon", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("map_zoom", sa.Float(), server_default="2.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("dashboard_configs")
    op.drop_index("ix_convergence_zones_severity", table_name="convergence_zones")
    op.drop_table("convergence_zones")
    op.drop_index("ix_geo_events_source_id", table_name="geo_events")
    op.drop_index("ix_geo_events_expires", table_name="geo_events")
    op.drop_index("ix_geo_events_event_time", table_name="geo_events")
    op.drop_index("ix_geo_events_layer", table_name="geo_events")
    op.drop_table("geo_events")
    op.drop_index("ix_observations_connector_type", table_name="observations")
    op.drop_column("observations", "connector_type")
    op.drop_column("observations", "geo_source")
    op.drop_column("observations", "longitude")
    op.drop_column("observations", "latitude")
