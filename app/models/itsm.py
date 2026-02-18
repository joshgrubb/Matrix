"""
IT Service Management models — ``itsm`` schema (Phase 4).

Minimal model definitions to support foreign key relationships and
seed data. Full UI, services, and routes will be built in Phase 4.
"""

from app.extensions import db


class Category(db.Model):
    """ITSM category with optional subcategory via self-referencing FK."""

    __tablename__ = "category"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    category_name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    parent_category_id = db.Column(
        db.Integer, db.ForeignKey("itsm.category.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    parent = db.relationship(
        "Category", remote_side=[id], backref="subcategories"
    )

    def __repr__(self) -> str:
        return f"<Category {self.category_name}>"


class Priority(db.Model):
    """ITSM priority with SLA targets."""

    __tablename__ = "priority"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    priority_name = db.Column(db.String(50), unique=True, nullable=False)
    sort_order = db.Column(db.Integer, unique=True, nullable=False)
    sla_response_hours = db.Column(db.Integer, nullable=True)
    sla_resolve_hours = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Priority {self.priority_name}>"


class Severity(db.Model):
    """Incident severity level (distinct from priority)."""

    __tablename__ = "severity"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    severity_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    sort_order = db.Column(db.Integer, unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Severity {self.severity_name}>"


class Impact(db.Model):
    """Incident impact scope (Organization, Department, Division, Individual)."""

    __tablename__ = "impact"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    impact_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    sort_order = db.Column(db.Integer, unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Impact {self.impact_name}>"


class Status(db.Model):
    """
    Shared status lookup for tickets, incidents, and change requests.

    ``entity_type`` scopes which statuses apply to which entity type.
    """

    __tablename__ = "status"
    __table_args__ = (
        db.UniqueConstraint(
            "status_name", "entity_type", name="UQ_status_name_entity"
        ),
        {"schema": "itsm"},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    status_name = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(200), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False)
    is_closed = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Status {self.entity_type}:{self.status_name}>"


class Ticket(db.Model):
    """Service request / helpdesk ticket."""

    __tablename__ = "ticket"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("itsm.category.id"), nullable=True
    )
    priority_id = db.Column(
        db.Integer, db.ForeignKey("itsm.priority.id"), nullable=True
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("itsm.status.id"), nullable=False
    )
    reporter_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=False
    )
    assignee_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    asset_id = db.Column(
        db.Integer, db.ForeignKey("asset.asset.id"), nullable=True
    )
    position_id = db.Column(
        db.Integer, db.ForeignKey("org.position.id"), nullable=True
    )
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    resolved_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)

    # -- Relationships -----------------------------------------------------
    category = db.relationship("Category")
    priority = db.relationship("Priority")
    status = db.relationship("Status")
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    asset = db.relationship("Asset")
    position = db.relationship("Position")

    def __repr__(self) -> str:
        return f"<Ticket {self.id}: {self.title}>"


class Incident(db.Model):
    """Unplanned interruption or service degradation."""

    __tablename__ = "incident"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("itsm.category.id"), nullable=True
    )
    priority_id = db.Column(
        db.Integer, db.ForeignKey("itsm.priority.id"), nullable=True
    )
    severity_id = db.Column(
        db.Integer, db.ForeignKey("itsm.severity.id"), nullable=False
    )
    impact_id = db.Column(
        db.Integer, db.ForeignKey("itsm.impact.id"), nullable=False
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("itsm.status.id"), nullable=False
    )
    reporter_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=False
    )
    assignee_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    asset_id = db.Column(
        db.Integer, db.ForeignKey("asset.asset.id"), nullable=True
    )
    root_cause = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    resolved_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)

    # -- Relationships -----------------------------------------------------
    category = db.relationship("Category")
    priority = db.relationship("Priority")
    severity = db.relationship("Severity")
    impact = db.relationship("Impact")
    status = db.relationship("Status")
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    asset = db.relationship("Asset")

    def __repr__(self) -> str:
        return f"<Incident {self.id}: {self.title}>"


class ChangeRequest(db.Model):
    """Proposed change to IT infrastructure, equipment, or configuration."""

    __tablename__ = "change_request"
    __table_args__ = {"schema": "itsm"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    justification = db.Column(db.Text, nullable=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("itsm.category.id"), nullable=True
    )
    risk_level = db.Column(
        db.String(20), nullable=False, default="Medium"
    )
    status_id = db.Column(
        db.Integer, db.ForeignKey("itsm.status.id"), nullable=False
    )
    requester_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=False
    )
    approver_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    implementer_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    scheduled_date = db.Column(db.DateTime, nullable=True)
    completed_date = db.Column(db.DateTime, nullable=True)
    rollback_plan = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    category = db.relationship("Category")
    status = db.relationship("Status")
    requester = db.relationship("User", foreign_keys=[requester_id])
    approver = db.relationship("User", foreign_keys=[approver_id])
    implementer = db.relationship("User", foreign_keys=[implementer_id])

    def __repr__(self) -> str:
        return f"<ChangeRequest {self.id}: {self.title}>"
