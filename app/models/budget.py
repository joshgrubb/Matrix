"""
Budget cost history models — ``budget`` schema.

Every cost-affecting change is recorded with ``effective_date`` and
``end_date`` timestamps so the cost service can reconstruct the full
budget at any historical date.

Query pattern for value at date X::

    WHERE effective_date <= X AND (end_date IS NULL OR end_date > X)
"""

from app.extensions import db


class HardwareTypeCostHistory(db.Model):
    """
    Effective-dated record of hardware type estimated costs.

    Written automatically by the equipment service when
    ``equip.hardware_type.estimated_cost`` is updated.
    """

    __tablename__ = "hardware_type_cost_history"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    hardware_type_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.hardware_type.id"),
        nullable=False,
        index=True,
    )
    estimated_cost = db.Column(db.Numeric(10, 2), nullable=False)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    end_date = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    change_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    hardware_type = db.relationship(
        "HardwareType", back_populates="cost_history"
    )
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return (
            f"<HWCostHistory hw_type={self.hardware_type_id} "
            f"cost={self.estimated_cost}>"
        )


class SoftwareCostHistory(db.Model):
    """
    Effective-dated record of software cost changes.

    Captures both ``cost_per_license`` (per-user) and ``total_cost``
    (tenant) in one row, matching the software table.
    """

    __tablename__ = "software_cost_history"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    software_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.software.id"),
        nullable=False,
        index=True,
    )
    cost_per_license = db.Column(db.Numeric(10, 2), nullable=True)
    total_cost = db.Column(db.Numeric(12, 2), nullable=True)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    end_date = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    change_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    software = db.relationship("Software", back_populates="cost_history")
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return f"<SWCostHistory sw={self.software_id}>"


class RequirementHistory(db.Model):
    """
    Event-sourced record of position requirement changes.

    Unlike the effective/end_date pattern, requirements are discrete
    events: ADDED, MODIFIED, REMOVED.

    ``item_type``: ``hardware`` or ``software``.
    ``item_id``: References ``equip.hardware_type.id`` or ``equip.software.id``.
    """

    __tablename__ = "requirement_history"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
    item_type = db.Column(db.String(20), nullable=False)
    item_id = db.Column(db.Integer, nullable=False)
    action_type = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    changed_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    change_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position = db.relationship("Position")
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return (
            f"<ReqHistory pos={self.position_id} "
            f"{self.action_type} {self.item_type}:{self.item_id}>"
        )


class AuthorizedCountHistory(db.Model):
    """
    Effective-dated record of position authorized count (headcount) changes.

    The authorized count is the denominator for tenant software cost
    distribution. Tracked when ``org.position.authorized_count`` changes.
    """

    __tablename__ = "authorized_count_history"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
    authorized_count = db.Column(db.Integer, nullable=False)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    end_date = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    change_reason = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position = db.relationship("Position")
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return (
            f"<AuthCountHistory pos={self.position_id} "
            f"count={self.authorized_count}>"
        )


class CostSnapshot(db.Model):
    """
    Optional materialized cache for frequently-queried budget dates.

    Generated by the cost service using point-in-time reconstruction,
    then cached. ``snapshot_data`` stores a full denormalized JSON
    breakdown.
    """

    __tablename__ = "cost_snapshot"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    snapshot_name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    snapshot_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    created_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    total_hardware_cost = db.Column(
        db.Numeric(14, 2), nullable=False, default=0
    )
    total_software_cost = db.Column(
        db.Numeric(14, 2), nullable=False, default=0
    )
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    snapshot_data = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    created_by_user = db.relationship("User", foreign_keys=[created_by])

    def __repr__(self) -> str:
        return f"<CostSnapshot {self.snapshot_name}>"
