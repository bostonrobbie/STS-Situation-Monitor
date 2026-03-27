"""region profiles table

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "region_profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("country_code", sa.String(3), server_default="USA"),
        sa.Column("country_name", sa.String(100), server_default="United States"),
        sa.Column("bbox_lat_min", sa.Float, nullable=False),
        sa.Column("bbox_lon_min", sa.Float, nullable=False),
        sa.Column("bbox_lat_max", sa.Float, nullable=False),
        sa.Column("bbox_lon_max", sa.Float, nullable=False),
        sa.Column("center_lat", sa.Float, nullable=False),
        sa.Column("center_lon", sa.Float, nullable=False),
        sa.Column("radius_km", sa.Float, server_default="120.0"),
        sa.Column("map_zoom", sa.Float, server_default="8.0"),
        sa.Column("is_active", sa.Boolean, server_default="0", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    # Seed Massachusetts as the default active region
    op.execute(
        sa.text(
            "INSERT INTO region_profiles "
            "(name, display_name, state_code, country_code, country_name, "
            "bbox_lat_min, bbox_lon_min, bbox_lat_max, bbox_lon_max, "
            "center_lat, center_lon, radius_km, map_zoom, is_active) "
            "VALUES "
            "('massachusetts', 'Massachusetts', 'MA', 'USA', 'United States', "
            "41.0, -73.5, 42.9, -69.9, "
            "42.36, -71.06, 120.0, 8.0, 1)"
        )
    )


def downgrade() -> None:
    op.drop_table("region_profiles")
