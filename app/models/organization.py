"""
Organization structure models — ``org`` schema.

Synced from NeoGov HR system. Users cannot create or modify org
structure directly; all changes flow through the HR sync service.

Tier 3 (#15): Position model now includes ``requirements_status``
for lightweight submission status tracking.
"""

from app.extensions import db


class Department(db.Model):
    """
    Top-level organizational unit synced from NeoGov.

    Departments contain divisions, which in turn contain positions.
    ``department_code`` is the NeoGov identifier used for correlation
    during sync but is never used as a foreign key.
    """

    __tablename__ = "department"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    department_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    department_name = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    divisions = db.relationship("Division", back_populates="department", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Department {self.department_code}: {self.department_name}>"


class Division(db.Model):
    """
    Mid-level organizational unit within a department.

    Divisions group related positions and are synced from NeoGov.
    """

    __tablename__ = "division"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("org.department.id"),
        nullable=False,
        index=True,
    )
    division_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    division_name = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    department = db.relationship("Department", back_populates="divisions")
    positions = db.relationship("Position", back_populates="division", lazy="dynamic")

    def __repr__(self) -> str:
        return f"<Division {self.division_code}: {self.division_name}>"


class Position(db.Model):
    """
    Individual position (job title) within a division.

    ``authorized_count`` is the budgeted headcount for this position,
    used as the multiplier for per-user cost calculations.
    ``filled_count`` is informational only — it does not affect costs.

    ``requirements_status`` tracks where this position is in the
    equipment-setup workflow (Tier 3 #15):
        None        — Not started.
        'draft'     — Partially configured.
        'submitted' — User completed wizard; awaiting IT review.
        'reviewed'  — IT staff has reviewed.

    Synced from NeoGov (except requirements_status, which is app-managed).
    """

    __tablename__ = "position"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    division_id = db.Column(
        db.Integer,
        db.ForeignKey("org.division.id"),
        nullable=False,
        index=True,
    )
    position_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    position_title = db.Column(db.String(200), nullable=False)
    authorized_count = db.Column(db.Integer, nullable=False, default=0)
    filled_count = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Tier 3 (#15): Lightweight submission status tracking.
    # Values: None (not started), 'draft', 'submitted', 'reviewed'.
    requirements_status = db.Column(db.String(20), nullable=True)

    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    division = db.relationship("Division", back_populates="positions")
    hardware_requirements = db.relationship(
        "PositionHardware", back_populates="position", lazy="dynamic"
    )
    software_requirements = db.relationship(
        "PositionSoftware", back_populates="position", lazy="dynamic"
    )
    employees = db.relationship("Employee", back_populates="position", lazy="dynamic")

    def __repr__(self) -> str:
        return (
            f"<Position {self.position_code}: {self.position_title} "
            f"(auth={self.authorized_count})>"
        )


class Employee(db.Model):
    """
    Individual employee record synced from NeoGov.

    Informational only in Phase 1 — used for filled count display.
    """

    __tablename__ = "employee"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
    employee_code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position = db.relationship("Position", back_populates="employees")

    def __repr__(self) -> str:
        return f"<Employee {self.employee_code}: {self.first_name} {self.last_name}>"
