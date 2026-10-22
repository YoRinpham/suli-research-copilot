"""Prepare populated M4 data for the historical M4-to-M3 downgrade.

0007 is already in use and must remain unchanged. Its downgrade restores the
M3 GenerationRun stage check, so remove M4 domain rows and their runs first.
"""

from alembic import op


revision = "0008_m4_gate_fixes"
down_revision = "0007_m4_gap_hypothesis_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No forward schema change is required. This revision supplies a safe
    # downgrade path for databases that already ran 0007.
    pass


def downgrade() -> None:
    # Drop descendants and join rows before parents to satisfy foreign keys.
    op.execute("DELETE FROM experiment_plans")
    op.execute("DELETE FROM hypothesis_claims")
    op.execute("DELETE FROM hypothesis_evidence")
    op.execute("DELETE FROM hypotheses")
    op.execute("DELETE FROM gap_evidence")
    op.execute("DELETE FROM gap_claims")
    op.execute("DELETE FROM gap_landscape_items")
    op.execute("DELETE FROM research_gaps")
    op.execute("DELETE FROM generation_runs WHERE stage IN ('gap', 'hypothesis', 'experiment_plan')")
