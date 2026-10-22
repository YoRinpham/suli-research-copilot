"""Record the parsing claim time for safe interrupted-parse recovery."""

import sqlalchemy as sa
from alembic import op


revision = "0003_m1_parse_recovery"
down_revision = "0002_m1_project_papers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("parse_started_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE papers
        SET parse_started_at = CURRENT_TIMESTAMP
        WHERE parse_status = 'parsing' AND parse_started_at IS NULL
    """)


def downgrade() -> None:
    op.drop_column("papers", "parse_started_at")
