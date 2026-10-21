"""Add the platform fee to existing campaign targets in place."""
from alembic import op


revision = "20261020netgoal"
down_revision = "20261020coverurl"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE campaign_revisions "
        "SET goal_amount_minor = goal_amount_minor + (goal_amount_minor + 9) / 10"
    )


def downgrade():
    op.execute(
        "UPDATE campaign_revisions "
        "SET goal_amount_minor = goal_amount_minor * 10 / 11"
    )
