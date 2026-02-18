"""
Asset management models — ``asset`` schema (Phase 2).

Tables exist in the database for FK planning and schema stability.
UI, services, and routes will be built in Phase 2.
"""

from app.extensions import db


class Manufacturer(db.Model):
    """Canonical list of hardware manufacturers (e.g., Dell, Lenovo, HP)."""

    __tablename__ = "manufacturer"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    manufacturer_name = db.Column(
        db.String(100), unique=True, nullable=False
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    assets = db.relationship(
        "Asset", back_populates="manufacturer", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Manufacturer {self.manufacturer_name}>"


class OperatingSystem(db.Model):
    """Canonical list of operating systems (e.g., Windows 11 Pro, macOS Sequoia)."""

    __tablename__ = "operating_system"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    os_name = db.Column(db.String(100), unique=True, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    assets = db.relationship(
        "Asset", back_populates="operating_system", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<OperatingSystem {self.os_name}>"


class LocationType(db.Model):
    """Categorization of physical locations (e.g., Building, Floor, Room)."""

    __tablename__ = "location_type"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    locations = db.relationship(
        "Location", back_populates="location_type", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<LocationType {self.type_name}>"


class Location(db.Model):
    """
    Physical location where assets can be deployed.

    Supports hierarchical locations via ``parent_location_id``.
    Example: City Hall → 2nd Floor → Room 205.
    """

    __tablename__ = "location"
    __table_args__ = (
        db.UniqueConstraint(
            "location_name",
            "parent_location_id",
            name="UQ_location_name_parent",
        ),
        {"schema": "asset"},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    location_name = db.Column(db.String(200), nullable=False)
    location_type_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.location_type.id"),
        nullable=False,
        index=True,
    )
    parent_location_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.location.id"),
        nullable=True,
        index=True,
    )
    address = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    location_type = db.relationship(
        "LocationType", back_populates="locations"
    )
    parent = db.relationship(
        "Location", remote_side=[id], backref="children"
    )
    assets = db.relationship(
        "Asset", back_populates="location", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Location {self.location_name}>"


class Condition(db.Model):
    """
    Asset condition states that control lifecycle progression.

    ``is_deployable``: Can an asset in this condition be assigned?
    ``is_terminal``: End-of-life state — no further assignments.
    """

    __tablename__ = "condition"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    condition_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    sort_order = db.Column(db.Integer, unique=True, nullable=False)
    is_deployable = db.Column(db.Boolean, nullable=False, default=True)
    is_terminal = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    assets = db.relationship(
        "Asset", back_populates="condition", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Condition {self.condition_name}>"


class Asset(db.Model):
    """
    A specific physical piece of IT equipment.

    Links to ``equip.hardware_type`` for categorization against the
    budget model. ``is_deployed`` is a denormalized flag kept in sync
    by the service layer.
    """

    __tablename__ = "asset"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    hardware_type_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.hardware_type.id"),
        nullable=False,
        index=True,
    )
    manufacturer_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.manufacturer.id"),
        nullable=True,
        index=True,
    )
    operating_system_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.operating_system.id"),
        nullable=True,
    )
    location_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.location.id"),
        nullable=True,
        index=True,
    )
    condition_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.condition.id"),
        nullable=False,
        index=True,
    )
    asset_tag = db.Column(db.String(50), unique=True, nullable=True)
    serial_number = db.Column(db.String(100), nullable=True)
    hostname = db.Column(db.String(100), nullable=True)
    model = db.Column(db.String(100), nullable=True)
    is_deployed = db.Column(db.Boolean, nullable=False, default=False)
    purchase_date = db.Column(db.Date, nullable=True)
    purchase_cost = db.Column(db.Numeric(10, 2), nullable=True)
    warranty_expiration = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    hardware_type = db.relationship("HardwareType", back_populates="assets")
    manufacturer = db.relationship("Manufacturer", back_populates="assets")
    operating_system = db.relationship(
        "OperatingSystem", back_populates="assets"
    )
    location = db.relationship("Location", back_populates="assets")
    condition = db.relationship("Condition", back_populates="assets")
    assignments = db.relationship(
        "AssetAssignment", back_populates="asset", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Asset {self.asset_tag or self.serial_number}>"


class AssetAssignment(db.Model):
    """
    Tracks the assignment of a specific asset to a person and position.

    A NULL ``returned_date`` means the asset is currently assigned.
    Historical assignments are preserved for the audit trail.
    """

    __tablename__ = "asset_assignment"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    asset_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.asset.id"),
        nullable=False,
        index=True,
    )
    employee_id = db.Column(
        db.Integer, db.ForeignKey("org.employee.id"), nullable=True
    )
    position_id = db.Column(
        db.Integer, db.ForeignKey("org.position.id"), nullable=True
    )
    assigned_to_name = db.Column(db.String(200), nullable=False)
    assigned_date = db.Column(db.Date, nullable=False)
    returned_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    asset = db.relationship("Asset", back_populates="assignments")
    employee = db.relationship("Employee")
    position = db.relationship("Position")

    def __repr__(self) -> str:
        return f"<AssetAssignment asset={self.asset_id} to={self.assigned_to_name}>"
