"""
Equipment catalog models — ``equip`` schema.

Hardware types and software products define the catalog of IT items
that can be assigned as position requirements.  Cost changes are
tracked in the ``budget`` schema via the service layer.
"""

from app.extensions import db


class HardwareType(db.Model):
    """
    Generic hardware category (e.g., Laptop, Monitor, Docking Station).

    Not a specific asset — that's Phase 2.  ``estimated_cost`` is the
    per-unit cost used in budgetary calculations.
    """

    __tablename__ = "hardware_type"
    __table_args__ = {"schema": "equip"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type_name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(500), nullable=True)
    estimated_cost = db.Column(
        db.Numeric(12, 2), nullable=False, default=0
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

    def __repr__(self) -> str:
        return f"<HardwareType {self.type_name} ${self.estimated_cost}>"


class SoftwareType(db.Model):
    """
    Category / functional grouping for software products.

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
    software_name = db.Column(db.String(200), nullable=False)
    software_type_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software_type.id"),
        nullable=True,
        index=True,
    )
    software_family_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software_family.id"),
        nullable=True,
        index=True,
    )
    license_model = db.Column(
        db.String(20), nullable=False, default="per_user"
    )
    cost_per_license = db.Column(
        db.Numeric(12, 2), nullable=True, default=0
    )
    total_cost = db.Column(db.Numeric(14, 2), nullable=True, default=0)
    tier = db.Column(db.String(50), nullable=True)
    description = db.Column(db.String(500), nullable=True)
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
    position_software = db.relationship(
        "PositionSoftware", back_populates="software", lazy="dynamic"
    )
    coverage = db.relationship(
        "SoftwareCoverage", back_populates="software", lazy="joined"
    )

    def __repr__(self) -> str:
        return (
            f"<Software {self.software_name} "
            f"({self.license_model})>"
        )


class SoftwareCoverage(db.Model):
    """
    Defines the organizational scope for tenant-licensed software.

    A tenant license's total cost is divided across the sum of
    authorized_count for all positions in the coverage scope.
    Coverage can be at the organization, department, or division level.

    ``scope_type`` values: organization, department, division.
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
        db.Integer,
        db.ForeignKey("org.department.id"),
        nullable=True,
    )
    division_id = db.Column(
        db.Integer,
        db.ForeignKey("org.division.id"),
        nullable=True,
    )
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    software = db.relationship(
        "Software", back_populates="coverage"
    )
    department = db.relationship("Department")
    division = db.relationship("Division")

    def __repr__(self) -> str:
        return (
            f"<SoftwareCoverage sw={self.software_id} "
            f"scope={self.scope_type}>"
        )
