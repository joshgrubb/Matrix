"""Add CHECK constraints for quick wins

Revision ID: f39d32785055
Revises: e27989991835
Create Date: 2026-02-26 08:24:32.927749

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f39d32785055"
down_revision = "e27989991835"
branch_labels = None
depends_on = None


def upgrade():
    """Add database-level invariant constraints (Quick Wins #2, #3, #7)."""
    # --- Quick Win #2: quantity >= 1 on requirement junction tables ---
    op.execute(
        "ALTER TABLE equip.position_hardware "
        "ADD CONSTRAINT CK_pos_hw_qty CHECK (quantity >= 1);"
    )
    op.execute(
        "ALTER TABLE equip.position_software "
        "ADD CONSTRAINT CK_pos_sw_qty CHECK (quantity >= 1);"
    )

    # --- Quick Win #3: license_model restricted to known values ---
    op.execute(
        "ALTER TABLE equip.software "
        "ADD CONSTRAINT CK_sw_license_model "
        "CHECK (license_model IN ('per_user', 'tenant'));"
    )

    # --- Quick Win #7: unique software name ---
    op.execute(
        "ALTER TABLE equip.software " "ADD CONSTRAINT UQ_software_name UNIQUE (name);"
    )


def downgrade():
    """Remove the constraints added in upgrade()."""
    op.execute("ALTER TABLE equip.software " "DROP CONSTRAINT UQ_software_name;")
    op.execute("ALTER TABLE equip.software " "DROP CONSTRAINT CK_sw_license_model;")
    op.execute("ALTER TABLE equip.position_software " "DROP CONSTRAINT CK_pos_sw_qty;")
    op.execute("ALTER TABLE equip.position_hardware " "DROP CONSTRAINT CK_pos_hw_qty;")
