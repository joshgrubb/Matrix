"""
Position requirement models — ``equip`` schema.

These junction tables define what hardware items and software products
each position requires. Changes are tracked in
``budget.requirement_history`` by the service layer.
"""

from app.extensions import db


class PositionHardware(db.Model):
    """
    Defines what specific hardware a position requires and in what quantity.

    Points to ``Hardware`` (a specific item like "Standard Laptop" or
    "32-inch Monitor"), not the generic ``HardwareType`` category.

    ``quantity`` is per person in the position (e.g., 2 monitors per person).
    Total cost = quantity × hardware.estimated_cost × position.authorized_count.
    """

    __tablename__ = "position_hardware"
    __table_args__ = (
        # Each position can only have one entry per hardware item.
        db.UniqueConstraint(
            "position_id",
            "hardware_id",
            name="UQ_position_hardware_position_hardware",
        ),
        {"schema": "equip"},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
    hardware_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.hardware.id"),
        nullable=False,
        index=True,
    )
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position = db.relationship("Position", back_populates="hardware_requirements")
    hardware = db.relationship("Hardware", back_populates="position_hardware")

    def __repr__(self) -> str:
        return (
            f"<PositionHardware position={self.position_id} "
            f"hw={self.hardware_id} qty={self.quantity}>"
        )


class PositionSoftware(db.Model):
    """
    Defines what software a position requires.

    Used for BOTH per-user and tenant-licensed software:
      - Per-user: This record IS the requirement and cost driver.
        Cost = quantity × software.cost_per_license × position.authorized_count.
      - Tenant: This record tracks that the position uses the software.
        Cost distribution is calculated via ``SoftwareCoverage``.
    """

    __tablename__ = "position_software"
    __table_args__ = (
        # Each position can only have one entry per software product.
        db.UniqueConstraint(
            "position_id",
            "software_id",
            name="UQ_position_software_position_sw",
        ),
        {"schema": "equip"},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
    software_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software.id"),
        nullable=False,
        index=True,
    )
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position = db.relationship("Position", back_populates="software_requirements")
    software = db.relationship("Software", back_populates="position_software")

    def __repr__(self) -> str:
        return (
            f"<PositionSoftware position={self.position_id} "
            f"sw={self.software_id} qty={self.quantity}>"
        )
