"""
Organization models — ``org`` schema.

These tables are populated via NeoGov API sync. Users cannot create,
rename, or delete org records through the application UI. IT staff
trigger syncs from the admin panel. Records no longer present in
NeoGov are soft-deleted (``is_active = False``).
"""

from app.extensions import db


class Department(db.Model):
    """
    Top-level organizational unit synced from NeoGov.

    Example: Public Works, Finance, Police.
    """

    __tablename__ = "department"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    department_code = db.Column(
        db.String(50), unique=True, nullable=False, index=True
    )
    department_name = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    divisions = db.relationship(
        "Division", back_populates="department", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Department {self.department_code}: {self.department_name}>"


class Division(db.Model):
    """
    Second-level organizational unit within a department.

    Example: Water, Sewer, Streets (under Public Works).
    """

    __tablename__ = "division"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    division_code = db.Column(
        db.String(50), unique=True, nullable=False, index=True
    )
    division_name = db.Column(db.String(200), nullable=False)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("org.department.id"),
        nullable=False,
        index=True,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    department = db.relationship("Department", back_populates="divisions")
    positions = db.relationship(
        "Position", back_populates="division", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Division {self.division_code}: {self.division_name}>"


class Position(db.Model):
    """
    A unique job within a division as defined in NeoGov.

    The same title (e.g., "Administrative Assistant") can exist in
    multiple divisions as separate records, each with its own
    ``position_code`` and its own hardware/software requirements.

    ``authorized_count`` is the number of approved seats (headcount)
    for this position. It is the denominator for tenant software
    cost distribution.
    """

    __tablename__ = "position"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    position_code = db.Column(
        db.String(50), unique=True, nullable=False, index=True
    )
    position_title = db.Column(db.String(200), nullable=False)
    division_id = db.Column(
        db.Integer,
        db.ForeignKey("org.division.id"),
        nullable=False,
        index=True,
    )
    authorized_count = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    division = db.relationship("Division", back_populates="positions")
    employees = db.relationship(
        "Employee", back_populates="position", lazy="dynamic"
    )
    hardware_requirements = db.relationship(
        "PositionHardware", back_populates="position", lazy="dynamic"
    )
    software_requirements = db.relationship(
        "PositionSoftware", back_populates="position", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Position {self.position_code}: {self.position_title}>"


class Employee(db.Model):
    """
    Employee synced from NeoGov, linked to a position.

    Used in Phase 2 for asset assignment and in Phase 1 to derive
    filled count vs authorized count on the dashboard.
    """

    __tablename__ = "employee"
    __table_args__ = {"schema": "org"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    neogov_employee_id = db.Column(
        db.String(100), unique=True, nullable=False
    )
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
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
        return f"<Employee {self.neogov_employee_id}: {self.first_name} {self.last_name}>"
