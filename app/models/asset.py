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
    """Canonical list of operating systems (e.g., Windows 11 Pro)."""

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
    """Physical location where assets can be deployed."""

    __tablename__ = "location"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    location_type_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.location_type.id"),
        nullable=False,
    )
    location_name = db.Column(db.String(200), nullable=False)
    parent_location_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.location.id"),
        nullable=True,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    location_type = db.relationship(
        "LocationType", back_populates="locations"
    )
    parent = db.relationship(
        "Location", remote_side=[id], backref="children"
    )

    def __repr__(self) -> str:
        return f"<Location {self.location_name}>"


class Condition(db.Model):
    """Asset condition lookup (e.g., New, Good, Fair, Poor, Retired)."""

    __tablename__ = "condition"
    __table_args__ = {"schema": "asset"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    condition_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    def __repr__(self) -> str:
        return f"<Condition {self.condition_name}>"


class Asset(db.Model):
    """
    Physical IT asset (specific device with a serial number).

    Phase 2: CRUD, assignment, warranty tracking.
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
    )
    condition_id = db.Column(
        db.Integer,
        db.ForeignKey("asset.condition.id"),
        nullable=True,
    )
    asset_tag = db.Column(
        db.String(50), unique=True, nullable=False, index=True
    )
    serial_number = db.Column(db.String(100), nullable=True)
    model_name = db.Column(db.String(200), nullable=True)
    purchase_date = db.Column(db.Date, nullable=True)
    purchase_cost = db.Column(db.Numeric(12, 2), nullable=True)
    warranty_end_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    hardware_type = db.relationship("HardwareType")
    manufacturer = db.relationship("Manufacturer", back_populates="assets")
    operating_system = db.relationship(
        "OperatingSystem", back_populates="assets"
    )
    location = db.relationship("Location")
    condition = db.relationship("Condition")
    assignments = db.relationship(
        "AssetAssignment", back_populates="asset", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Asset {self.asset_tag}>"


class AssetAssignment(db.Model):
    """
    Links an asset to a position or employee.

    Uses effective/end date pattern for assignment history.
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
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=True,
    )
    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("org.employee.id"),
        nullable=True,
    )
    assigned_date = db.Column(db.DateTime, nullable=False)
    returned_date = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    asset = db.relationship("Asset", back_populates="assignments")
    position = db.relationship("Position")
    employee = db.relationship("Employee")

    def __repr__(self) -> str:
        return f"<AssetAssignment asset={self.asset_id}>"
