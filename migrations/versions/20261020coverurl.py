"""Add campaign cover image URLs."""
from alembic import op
import sqlalchemy as sa


revision = "20261020coverurl"
down_revision = "cb05b67fa2ec"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaign_revisions",
        sa.Column("cover_image_url", sa.String(length=512), nullable=True),
    )


def downgrade():
    op.drop_column("campaign_revisions", "cover_image_url")
