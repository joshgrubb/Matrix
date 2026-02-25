"""Add COPY to audit_log action_type CHECK constraint

Revision ID: e27989991835
Revises: a1b2c3d4e5f6
Create Date: 2026-02-25 08:42:52.807843

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e27989991835"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("CK_audit_log_action_type", "audit_log", schema="audit")
    op.create_check_constraint(
        "CK_audit_log_action_type",
        "audit_log",
        "action_type IN ('CREATE', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT', 'SYNC', 'COPY')",
        schema="audit",
    )


def downgrade():
    op.drop_constraint("CK_audit_log_action_type", "audit_log", schema="audit")
    op.create_check_constraint(
        "CK_audit_log_action_type",
        "audit_log",
        "action_type IN ('CREATE', 'UPDATE', 'DELETE', 'LOGIN', 'LOGOUT', 'SYNC')",
        schema="audit",
    )
