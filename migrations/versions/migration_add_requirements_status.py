"""Add requirements_status to position table.

Tier 3 (#15): Lightweight submission status tracking.

Adds a nullable status column to track where each position is in
the equipment-setup workflow.  Values:
    NULL        — Not started (no requirements configured).
    'draft'     — Requirements partially configured (has some items
                  but the user has not visited the summary page).
    'submitted' — User has completed the wizard and viewed the
                  summary page.  Awaiting IT review.
    'reviewed'  — IT staff has reviewed and approved the setup.

The column is nullable so that existing positions (which have never
gone through the wizard) default to NULL without a data migration.

Revision ID: a1b2c3d4e5f6
Revises: f2329ebdc2c9
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa


# Revision identifiers — update 'revises' to match your current head.
revision = "a1b2c3d4e5f6"
down_revision = "f2329ebdc2c9"  # ← Replace with your current migration head.
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add requirements_status column to org.position."""
    with op.batch_alter_table("position", schema="org") as batch_op:
        batch_op.add_column(
            sa.Column(
                "requirements_status",
                sa.String(length=20),
                nullable=True,
                server_default=None,
                comment=(
                    "Equipment setup workflow status: "
                    "NULL=not started, draft, submitted, reviewed"
                ),
            )
        )


def downgrade() -> None:
    """Remove requirements_status column from org.position."""
    with op.batch_alter_table("position", schema="org") as batch_op:
        batch_op.drop_column("requirements_status")
