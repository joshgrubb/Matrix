"""
Equipment catalog models — ``equip`` schema.

Contains hardware type categories, software products with tiered
licensing, software families for grouping tiers, and coverage
definitions for tenant-licensed software cost distribution.
"""

from app.extensions import db


class HardwareType(db.Model):
    """
    Generic category of hardware for position requirements and budgeting.

    Position requirements point to hardware types (e.g., "Laptop",
    "Monitor"), not to specific physical assets. ``estimated_cost``
    is the budgetary cost used for position requirement calculations.

    Changes to ``estimated_cost`` are tracked automatically in
    ``budget.hardware_type_cost_history`` by the service layer.
    """

    __tablename__ = "hardware_type"
    __table_args__ = {"schema": "equip"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type_name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    estimated_cost = db.Column(
        db.Numeric(10, 2), nullable=False, default=0
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position_hardware = db.relationship(
        "PositionHardware", back_populates="hardware_type", lazy="dynamic"
    )
    assets = db.relationship(
        "Asset", back_populates="hardware_type", lazy="dynamic"
    )
    cost_history = db.relationship(
        "HardwareTypeCostHistory",
        back_populates="hardware_type",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return f"<HardwareType {self.type_name}>"


class SoftwareType(db.Model):
    """
    Categorization of software by function.

    Example: Productivity, Security, GIS, Finance, HR.
    """

    __tablename__ = "software_type"
    __table_args__ = {"schema": "equip"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type_name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    software = db.relationship(
        "Software", back_populates="software_type", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<SoftwareType {self.type_name}>"


class SoftwareFamily(db.Model):
    """
    Groups software products with multiple tiers/editions.

    Example: "Microsoft 365" groups E1, E3, E5 tiers.
    A software record may optionally belong to a family.
    """

    __tablename__ = "software_family"
    __table_args__ = {"schema": "equip"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    family_name = db.Column(db.String(200), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    software = db.relationship(
        "Software", back_populates="software_family", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<SoftwareFamily {self.family_name}>"


class Software(db.Model):
    """
    Individual software license/tier record.

    Each tier of a product is its own record (e.g., "Microsoft 365 E3"
    and "Microsoft 365 E5" are separate rows).

    ``license_model`` determines cost calculation:
      - ``per_user``:  Cost is per license per seat. Uses ``cost_per_license``.
      - ``tenant``:    Flat cost shared across a coverage group. Uses ``total_cost``.
                       Coverage scope is defined in ``SoftwareCoverage``.

    Changes to cost fields are tracked in ``budget.software_cost_history``.
    """

    __tablename__ = "software"
    __table_args__ = {"schema": "equip"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    software_type_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software_type.id"),
        nullable=False,
        index=True,
    )
    software_family_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software_family.id"),
        nullable=True,
        index=True,
    )
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    license_model = db.Column(db.String(20), nullable=False)
    license_tier = db.Column(db.String(50), nullable=True)
    cost_per_license = db.Column(db.Numeric(10, 2), nullable=True)
    total_cost = db.Column(db.Numeric(12, 2), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    software_type = db.relationship(
        "SoftwareType", back_populates="software"
    )
    software_family = db.relationship(
        "SoftwareFamily", back_populates="software"
    )
    coverage = db.relationship(
        "SoftwareCoverage", back_populates="software", lazy="dynamic"
    )
    position_software = db.relationship(
        "PositionSoftware", back_populates="software", lazy="dynamic"
    )
    cost_history = db.relationship(
        "SoftwareCostHistory", back_populates="software", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Software {self.name}>"


class SoftwareCoverage(db.Model):
    """
    Defines which organizational units are covered by a tenant-licensed
    software product.

    Multiple rows per software allow arbitrary groupings. The cost
    service calculates the denominator by unioning all positions
    that fall within coverage rows, using set union to prevent
    double-counting.

    ``scope_type`` values:
      - ``organization`` — Entire org.  No FK columns needed.
      - ``department``   — One department.  Uses ``department_id``.
      - ``division``     — One division.  Uses ``division_id``.
      - ``position``     — One specific position.  Uses ``position_id``.
    """

    __tablename__ = "software_coverage"
    __table_args__ = {"schema": "equip"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    software_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software.id"),
        nullable=False,
        index=True,
    )
    scope_type = db.Column(db.String(20), nullable=False)
    department_id = db.Column(
        db.Integer, db.ForeignKey("org.department.id"), nullable=True
    )
    division_id = db.Column(
        db.Integer, db.ForeignKey("org.division.id"), nullable=True
    )
    position_id = db.Column(
        db.Integer, db.ForeignKey("org.position.id"), nullable=True
    )

    # -- Relationships -----------------------------------------------------
    software = db.relationship("Software", back_populates="coverage")
    department = db.relationship("Department")
    division = db.relationship("Division")
    position = db.relationship("Position")

    def __repr__(self) -> str:
        return f"<SoftwareCoverage software={self.software_id} scope={self.scope_type}>"
