"""
Audit logging and HR sync tracking models — ``audit`` schema.

``AuditLog`` records all data changes in the application.
``HRSyncLog`` tracks each NeoGov sync operation.
"""

from app.extensions import db


class AuditLog(db.Model):
    """
    Records all data changes in the application.

    Change details are stored as JSON blobs for flexibility.
    Retention policy: minimum 1 year.

    ``action_type`` values: CREATE, UPDATE, DELETE, LOGIN, LOGOUT, SYNC.

    JSON conventions for ``previous_value`` / ``new_value``:
      - CREATE: previous_value is NULL, new_value has full record.
      - UPDATE: both contain only the changed fields.
      - DELETE: previous_value has full record, new_value is NULL.
    """

    __tablename__ = "audit_log"
    __table_args__ = {"schema": "audit"}

    # Use BigInteger to match BIGINT IDENTITY in the DDL.
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    action_type = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(100), nullable=False)
    entity_id = db.Column(db.Integer, nullable=True)
    previous_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    user = db.relationship("User")

    def __repr__(self) -> str:
        return (
            f"<AuditLog {self.action_type} {self.entity_type}"
            f":{self.entity_id}>"
        )


class HRSyncLog(db.Model):
    """
    Tracks each NeoGov sync operation: what was synced, how many
    records were affected, and whether it succeeded or failed.

    ``sync_type`` values: full, department, division, position, employee.
    ``status`` values: started, completed, failed.
    """

    __tablename__ = "hr_sync_log"
    __table_args__ = {"schema": "audit"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    triggered_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    sync_type = db.Column(db.String(50), nullable=False)
    records_processed = db.Column(db.Integer, nullable=False, default=0)
    records_created = db.Column(db.Integer, nullable=False, default=0)
    records_updated = db.Column(db.Integer, nullable=False, default=0)
    records_deactivated = db.Column(db.Integer, nullable=False, default=0)
    records_errors = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False)
    error_message = db.Column(db.Text, nullable=True)
    started_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    # -- Relationships -----------------------------------------------------
    triggered_by_user = db.relationship("User", foreign_keys=[triggered_by])

    def __repr__(self) -> str:
        return f"<HRSyncLog {self.sync_type} status={self.status}>"
