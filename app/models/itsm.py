"""
ITSM (IT Service Management) models — ``itsm`` schema (Phase 4).

Tables exist in the database for schema planning. UI, services,
and routes will be built in Phase 4.
"""

from app.extensions import db


class Status(db.Model):
    """Workflow status for tickets and incidents (e.g., Open, In Progress)."""

    __tablename__ = "status"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    status_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Status {self.status_name}>"


class Priority(db.Model):
    """Ticket priority levels (e.g., Low, Medium, High, Critical)."""

    __tablename__ = "priority"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    priority_name = db.Column(db.String(50), unique=True, nullable=False)
    sla_hours = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Priority {self.priority_name}>"


class Category(db.Model):
    """Service categories for ticket classification."""

    __tablename__ = "category"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category_name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Category {self.category_name}>"


class Severity(db.Model):
    """Incident severity levels."""

    __tablename__ = "severity"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    severity_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Severity {self.severity_name}>"


class Impact(db.Model):
    """Business impact levels for incidents."""

    __tablename__ = "impact"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    impact_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Impact {self.impact_name}>"


class Ticket(db.Model):
    """Service request / help desk ticket."""

    __tablename__ = "ticket"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ticket_number = db.Column(
        db.String(20), unique=True, nullable=False, index=True
    )
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    requester_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    assigned_to = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("itsm.status.id"), nullable=True
    )
    priority_id = db.Column(
        db.Integer, db.ForeignKey("itsm.priority.id"), nullable=True
    )
    category_id = db.Column(
        db.Integer, db.ForeignKey("itsm.category.id"), nullable=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    closed_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Ticket {self.ticket_number}>"


class Incident(db.Model):
    """IT incident record."""

    __tablename__ = "incident"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    incident_number = db.Column(
        db.String(20), unique=True, nullable=False, index=True
    )
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    reported_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    assigned_to = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("itsm.status.id"), nullable=True
    )
    severity_id = db.Column(
        db.Integer, db.ForeignKey("itsm.severity.id"), nullable=True
    )
    impact_id = db.Column(
        db.Integer, db.ForeignKey("itsm.impact.id"), nullable=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    resolved_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Incident {self.incident_number}>"


class ChangeRequest(db.Model):
    """IT change management request."""

    __tablename__ = "change_request"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    change_number = db.Column(
        db.String(20), unique=True, nullable=False, index=True
    )
    subject = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    requested_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    approved_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("itsm.status.id"), nullable=True
    )
    scheduled_date = db.Column(db.DateTime, nullable=True)
    completed_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<ChangeRequest {self.change_number}>"
