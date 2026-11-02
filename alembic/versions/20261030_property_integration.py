"""Request leases and recommendation attribution.

Revision ID: 20261030_ai_integration
Revises: 3f74f84bdcff
"""

import sqlalchemy as sa

from alembic import op

revision = "20261030_ai_integration"
down_revision = "3f74f84bdcff"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "ai_user_locks", sa.Column("user_id", sa.String(36), primary_key=True)
    )
    op.add_column(
        "recommendation_requests", sa.Column("listing_ids", sa.JSON(), nullable=True)
    )
    op.create_table(
        "ai_chat_leases",
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_chat_lease_user_expiry", "ai_chat_leases", ["user_id", "expires_at"]
    )


def downgrade():
    op.drop_table("ai_chat_leases")
    op.drop_table("ai_user_locks")
    op.drop_column("recommendation_requests", "listing_ids")
