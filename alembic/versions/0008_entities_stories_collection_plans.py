"""Add entity mentions, stories, and collection plan tables.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Entity mentions extracted from observations
    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("observation_id", sa.Integer, sa.ForeignKey("observations.id", ondelete="CASCADE"), index=True),
        sa.Column("investigation_id", sa.String(36), sa.ForeignKey("investigations.id", ondelete="CASCADE"), index=True),
        sa.Column("entity_text", sa.String(300), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False, index=True),  # person, organization, location, etc.
        sa.Column("normalized", sa.String(300), nullable=False, index=True),
        sa.Column("confidence", sa.Float, default=0.5),
        sa.Column("start_pos", sa.Integer, default=0),
        sa.Column("end_pos", sa.Integer, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    # Stories — clusters of related observations
    op.create_table(
        "stories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("investigation_id", sa.String(36), sa.ForeignKey("investigations.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("key_terms_json", sa.Text, default="[]"),
        sa.Column("entities_json", sa.Text, default="[]"),
        sa.Column("source_count", sa.Integer, default=0),
        sa.Column("observation_count", sa.Integer, default=0),
        sa.Column("avg_reliability", sa.Float, default=0.5),
        sa.Column("trending_score", sa.Float, default=0.0),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    # Story-observation link table
    op.create_table(
        "story_observations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("story_id", sa.Integer, sa.ForeignKey("stories.id", ondelete="CASCADE"), index=True),
        sa.Column("observation_id", sa.Integer, sa.ForeignKey("observations.id", ondelete="CASCADE"), index=True),
    )

    # Discovered topics — auto-surfaced investigation candidates
    op.create_table(
        "discovered_topics",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("score", sa.Float, default=0.5),
        sa.Column("source", sa.String(50), nullable=False, index=True),  # burst, convergence, entity_spike, trending
        sa.Column("key_terms_json", sa.Text, default="[]"),
        sa.Column("entities_json", sa.Text, default="[]"),
        sa.Column("sample_urls_json", sa.Text, default="[]"),
        sa.Column("suggested_seed_query", sa.String(500), default=""),
        sa.Column("suggested_connectors_json", sa.Text, default="[]"),
        sa.Column("status", sa.String(30), default="new", index=True),  # new, reviewed, promoted, dismissed
        sa.Column("promoted_investigation_id", sa.String(36), sa.ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )

    # Collection plans — scheduled multi-source collection requirements
    op.create_table(
        "collection_plans",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("investigation_id", sa.String(36), sa.ForeignKey("investigations.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, default=""),
        sa.Column("connectors_json", sa.Text, nullable=False),  # ["gdelt", "usgs", ...]
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("priority", sa.Integer, default=50),
        sa.Column("interval_seconds", sa.Integer, default=3600),
        sa.Column("filters_json", sa.Text, default="{}"),
        sa.Column("active", sa.Boolean, default=True, index=True),
        sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_collected", sa.Integer, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )


def downgrade() -> None:
    op.drop_table("collection_plans")
    op.drop_table("discovered_topics")
    op.drop_table("story_observations")
    op.drop_table("stories")
    op.drop_table("entity_mentions")
