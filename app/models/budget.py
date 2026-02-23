"""
Budget tracking models — ``budget`` schema.

These models track historical costs and requirement changes over time.
The service layer writes to these tables whenever costs or requirements
change, enabling point-in-time reporting.
"""

from app.extensions import db


class HardwareTypeCostHistory(db.Model):
    """
    Effective-dated history of hardware *type* cost changes.

    NOTE: This table is retained for backward compatibility and for
    tracking the reference cost on ``HardwareType``.  New cost tracking
    for specific hardware items uses ``HardwareCostHistory`` below.

    Uses effective_date / end_date pattern: the current cost has
    ``end_date`` = NULL.  When the cost changes, the old record's
    end_date is set and a new record is inserted.
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
    estimated_cost = db.Column(db.Numeric(12, 2), nullable=False)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    end_date = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(db.Integer, db.ForeignKey("auth.user.id"), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    hardware_type = db.relationship("HardwareType")
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return (
            f"<HWTypeCostHistory hw_type={self.hardware_type_id} "
            f"cost=${self.estimated_cost}>"
        )


class HardwareCostHistory(db.Model):
    """
    Effective-dated history of specific hardware *item* cost changes.

    Mirrors ``SoftwareCostHistory`` but for ``equip.hardware`` records.
    The current cost has ``end_date`` = NULL.
    """

    __tablename__ = "hardware_cost_history"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    hardware_id = db.Column(
        db.Integer,
        db.ForeignKey("equip.hardware.id"),
        nullable=False,
        index=True,
    )
    estimated_cost = db.Column(db.Numeric(12, 2), nullable=False)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    end_date = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(db.Integer, db.ForeignKey("auth.user.id"), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    hardware = db.relationship("Hardware")
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return f"<HWCostHistory hw={self.hardware_id} " f"cost=${self.estimated_cost}>"


class SoftwareCostHistory(db.Model):
    """
    Effective-dated history of software cost changes.

    Tracks both ``cost_per_license`` and ``total_cost`` depending
    on the software's license model.
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
    cost_per_license = db.Column(db.Numeric(12, 2), nullable=True)
    total_cost = db.Column(db.Numeric(14, 2), nullable=True)
    effective_date = db.Column(
        db.DateTime,
        nullable=False,
        server_default=db.text("SYSUTCDATETIME()"),
    )
    end_date = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(db.Integer, db.ForeignKey("auth.user.id"), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    software = db.relationship("Software")
    changed_by_user = db.relationship("User", foreign_keys=[changed_by])

    def __repr__(self) -> str:
        return f"<SWCostHistory sw={self.software_id}>"


class RequirementHistory(db.Model):
    """
    Discrete event log of requirement changes.

    Unlike the effective/end_date pattern, requirements are discrete
    events: ADDED, MODIFIED, REMOVED.

    ``item_type``: ``hardware`` or ``software``.
    ``item_id``: References ``equip.hardware.id`` or ``equip.software.id``.
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
    changed_by = db.Column(db.Integer, db.ForeignKey("auth.user.id"), nullable=True)
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

    Created whenever ``Position.authorized_count`` is modified.
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
    changed_by = db.Column(db.Integer, db.ForeignKey("auth.user.id"), nullable=True)
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
    Point-in-time cost snapshot for reporting and trend analysis.

    Generated periodically (daily/weekly) by a scheduled job or
    on-demand via CLI command.
    """

    __tablename__ = "cost_snapshot"
    __table_args__ = {"schema": "budget"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    snapshot_date = db.Column(db.DateTime, nullable=False)
    position_id = db.Column(
        db.Integer,
        db.ForeignKey("org.position.id"),
        nullable=False,
        index=True,
    )
    hardware_cost = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    software_cost = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_cost = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    position = db.relationship("Position")

    def __repr__(self) -> str:
        return f"<CostSnapshot pos={self.position_id} " f"date={self.snapshot_date}>"
