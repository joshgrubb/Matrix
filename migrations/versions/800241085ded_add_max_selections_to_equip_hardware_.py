"""Add max_selections to equip.hardware_type

Controls how many items a user may select within a hardware type
group on the requirements page.

    NULL  — Unlimited (multi-select checkboxes, current behavior).
    0     — Treated as unlimited at the application layer.
    1     — Single-select (radio buttons).
    N > 1 — Pick up to N (reserved for future use).

The column is nullable so that all existing hardware types default
to NULL (unlimited) without requiring a data migration.

An optional CHECK constraint ensures the value is non-negative when
set, preventing nonsensical values like -1 from being stored.

Revision ID: 800241085ded
Revises: f39d32785055
Create Date: 2026-02-26 12:27:38.877869

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "800241085ded"
down_revision = "f39d32785055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add max_selections column and CHECK constraint."""
    # Add the nullable INT column — existing rows get NULL (unlimited).
    with op.batch_alter_table("hardware_type", schema="equip") as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_selections",
                sa.Integer(),
                nullable=True,
                comment=(
                    "Max items selectable per type group: "
                    "NULL/0=unlimited, 1=single-select, N=pick up to N"
                ),
            )
        )

    # Guard against negative values at the database level.
    op.execute(
        "ALTER TABLE equip.hardware_type "
        "ADD CONSTRAINT CK_hw_type_max_selections "
        "CHECK (max_selections >= 0);"
    )


def downgrade() -> None:
    """Remove max_selections column and its CHECK constraint."""
    op.execute(
        "ALTER TABLE equip.hardware_type " "DROP CONSTRAINT CK_hw_type_max_selections;"
    )
    with op.batch_alter_table("hardware_type", schema="equip") as batch_op:
        batch_op.drop_column("max_selections")
