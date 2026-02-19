"""
Equipment service — CRUD for the hardware and software catalog.

Manages hardware types (generic categories like "Laptop", "Monitor"),
software types (categories like "Productivity", "Security"), software
families (tier groupings like "Microsoft 365"), and individual
software products.

Cost changes are tracked in the budget schema history tables.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models.budget import HardwareTypeCostHistory, SoftwareCostHistory
from app.models.equipment import (
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareFamily,
    SoftwareType,
)
from app.services import audit_service

logger = logging.getLogger(__name__)


# =========================================================================
# Hardware Types
# =========================================================================

def get_hardware_types(include_inactive: bool = False) -> list[HardwareType]:
    """Return all hardware types ordered by name."""
    query = HardwareType.query.order_by(HardwareType.type_name)
    if not include_inactive:
        query = query.filter(HardwareType.is_active.is_(True))
    return query.all()


def get_hardware_type_by_id(hw_type_id: int) -> HardwareType | None:
    """Return a hardware type by primary key."""
    return db.session.get(HardwareType, hw_type_id)


def create_hardware_type(
    type_name: str,
    estimated_cost: Decimal,
    description: str | None = None,
    user_id: int | None = None,
) -> HardwareType:
    """
    Create a new hardware type and record the initial cost history.

    Args:
        type_name:      Display name (e.g., "Standard Laptop").
        estimated_cost: Budgetary cost for this hardware type.
        description:    Optional description.
        user_id:        ID of the user creating the record.

    Returns:
        The newly created HardwareType record.
    """
    hw_type = HardwareType(
        type_name=type_name,
        description=description,
        estimated_cost=estimated_cost,
    )
    db.session.add(hw_type)
    db.session.flush()

    # Record initial cost in the history table.
    _record_hardware_cost_history(hw_type, user_id=user_id)

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.hardware_type",
        entity_id=hw_type.id,
        new_value={
            "type_name": type_name,
            "estimated_cost": str(estimated_cost),
            "description": description,
        },
    )
    db.session.commit()

    logger.info("Created hardware type: %s", type_name)
    return hw_type


def update_hardware_type(
    hw_type_id: int,
    type_name: str | None = None,
    estimated_cost: Decimal | None = None,
    description: str | None = None,
    user_id: int | None = None,
) -> HardwareType:
    """
    Update an existing hardware type.  If the cost changes, a new
    cost history record is created.

    Returns:
        The updated HardwareType record.

    Raises:
        ValueError: If the hardware type is not found.
    """
    hw_type = get_hardware_type_by_id(hw_type_id)
    if hw_type is None:
        raise ValueError(f"Hardware type ID {hw_type_id} not found.")

    previous = {
        "type_name": hw_type.type_name,
        "estimated_cost": str(hw_type.estimated_cost),
        "description": hw_type.description,
    }

    # Track whether cost changed for history recording.
    cost_changed = (
        estimated_cost is not None
        and estimated_cost != hw_type.estimated_cost
    )

    if type_name is not None:
        hw_type.type_name = type_name
    if estimated_cost is not None:
        hw_type.estimated_cost = estimated_cost
    if description is not None:
        hw_type.description = description
    hw_type.updated_at = datetime.now(timezone.utc)

    # Close the old cost history record and open a new one.
    if cost_changed:
        _close_hardware_cost_history(hw_type)
        _record_hardware_cost_history(hw_type, user_id=user_id)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.hardware_type",
        entity_id=hw_type.id,
        previous_value=previous,
        new_value={
            "type_name": hw_type.type_name,
            "estimated_cost": str(hw_type.estimated_cost),
            "description": hw_type.description,
        },
    )
    db.session.commit()

    logger.info("Updated hardware type ID %d", hw_type_id)
    return hw_type


def deactivate_hardware_type(
    hw_type_id: int,
    user_id: int | None = None,
) -> HardwareType:
    """Soft-delete a hardware type."""
    hw_type = get_hardware_type_by_id(hw_type_id)
    if hw_type is None:
        raise ValueError(f"Hardware type ID {hw_type_id} not found.")

    hw_type.is_active = False
    hw_type.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DELETE",
        entity_type="equip.hardware_type",
        entity_id=hw_type.id,
        previous_value={"is_active": True},
        new_value={"is_active": False},
    )
    db.session.commit()
    return hw_type


def _record_hardware_cost_history(
    hw_type: HardwareType,
    user_id: int | None = None,
) -> None:
    """Insert a new cost history row for a hardware type (no commit)."""
    history = HardwareTypeCostHistory(
        hardware_type_id=hw_type.id,
        estimated_cost=hw_type.estimated_cost,
        changed_by=user_id,
    )
    db.session.add(history)


def _close_hardware_cost_history(hw_type: HardwareType) -> None:
    """Set end_date on the current open cost history row (no commit)."""
    current = (
        HardwareTypeCostHistory.query
        .filter_by(hardware_type_id=hw_type.id, end_date=None)
        .first()
    )
    if current:
        current.end_date = datetime.now(timezone.utc)


# =========================================================================
# Software Types (categories)
# =========================================================================

def get_software_types(include_inactive: bool = False) -> list[SoftwareType]:
    """Return all software type categories ordered by name."""
    query = SoftwareType.query.order_by(SoftwareType.type_name)
    if not include_inactive:
        query = query.filter(SoftwareType.is_active.is_(True))
    return query.all()


def get_software_type_by_id(sw_type_id: int) -> SoftwareType | None:
    """Return a software type by primary key."""
    return db.session.get(SoftwareType, sw_type_id)


def create_software_type(
    type_name: str,
    description: str | None = None,
    user_id: int | None = None,
) -> SoftwareType:
    """Create a new software type category."""
    sw_type = SoftwareType(type_name=type_name, description=description)
    db.session.add(sw_type)
    db.session.flush()

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.software_type",
        entity_id=sw_type.id,
        new_value={"type_name": type_name, "description": description},
    )
    db.session.commit()
    return sw_type


def update_software_type(
    sw_type_id: int,
    type_name: str | None = None,
    description: str | None = None,
    user_id: int | None = None,
) -> SoftwareType:
    """Update an existing software type category."""
    sw_type = get_software_type_by_id(sw_type_id)
    if sw_type is None:
        raise ValueError(f"Software type ID {sw_type_id} not found.")

    if type_name is not None:
        sw_type.type_name = type_name
    if description is not None:
        sw_type.description = description
    sw_type.updated_at = datetime.now(timezone.utc)

    db.session.commit()
    return sw_type


# =========================================================================
# Software Families
# =========================================================================

def get_software_families(
    include_inactive: bool = False,
) -> list[SoftwareFamily]:
    """Return all software families ordered by name."""
    query = SoftwareFamily.query.order_by(SoftwareFamily.family_name)
    if not include_inactive:
        query = query.filter(SoftwareFamily.is_active.is_(True))
    return query.all()


def get_software_family_by_id(family_id: int) -> SoftwareFamily | None:
    """Return a software family by primary key."""
    return db.session.get(SoftwareFamily, family_id)


# =========================================================================
# Software Products
# =========================================================================

def get_software_products(
    include_inactive: bool = False,
    software_type_id: int | None = None,
) -> list[Software]:
    """
    Return software products, optionally filtered by type.

    Args:
        include_inactive:  If True, include deactivated products.
        software_type_id:  Filter to a specific software category.

    Returns:
        List of Software records ordered by name.
    """
    query = Software.query.order_by(Software.name)
    if not include_inactive:
        query = query.filter(Software.is_active.is_(True))
    if software_type_id is not None:
        query = query.filter(Software.software_type_id == software_type_id)
    return query.all()


def get_software_by_id(software_id: int) -> Software | None:
    """Return a software product by primary key."""
    return db.session.get(Software, software_id)


def create_software(
    name: str,
    software_type_id: int,
    license_model: str,
    cost_per_license: Decimal | None = None,
    total_cost: Decimal | None = None,
    license_tier: str | None = None,
    software_family_id: int | None = None,
    description: str | None = None,
    user_id: int | None = None,
) -> Software:
    """
    Create a new software product and record initial cost history.

    Args:
        name:               Display name (e.g., "Microsoft 365 E3").
        software_type_id:   FK to software type category.
        license_model:      'per_user' or 'tenant'.
        cost_per_license:   Per-seat cost (for per_user model).
        total_cost:         Flat total cost (for tenant model).
        license_tier:       Tier label (e.g., E1, E3, E5).
        software_family_id: Optional FK to software family.
        description:        Optional description.
        user_id:            ID of the user creating the record.

    Returns:
        The newly created Software record.
    """
    sw = Software(
        name=name,
        software_type_id=software_type_id,
        license_model=license_model,
        cost_per_license=cost_per_license,
        total_cost=total_cost,
        license_tier=license_tier,
        software_family_id=software_family_id,
        description=description,
    )
    db.session.add(sw)
    db.session.flush()

    # Record initial cost in the history table.
    _record_software_cost_history(sw, user_id=user_id)

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.software",
        entity_id=sw.id,
        new_value={
            "name": name,
            "license_model": license_model,
            "cost_per_license": str(cost_per_license) if cost_per_license else None,
            "total_cost": str(total_cost) if total_cost else None,
        },
    )
    db.session.commit()

    logger.info("Created software product: %s", name)
    return sw


def update_software(
    software_id: int,
    user_id: int | None = None,
    **kwargs,
) -> Software:
    """
    Update an existing software product.

    Accepts keyword arguments matching Software model fields.  If cost
    fields change, a new cost history record is created.

    Returns:
        The updated Software record.

    Raises:
        ValueError: If the software product is not found.
    """
    sw = get_software_by_id(software_id)
    if sw is None:
        raise ValueError(f"Software ID {software_id} not found.")

    # Check if cost fields are changing.
    cost_changed = (
        ("cost_per_license" in kwargs and kwargs["cost_per_license"] != sw.cost_per_license)
        or ("total_cost" in kwargs and kwargs["total_cost"] != sw.total_cost)
    )

    previous = {
        "name": sw.name,
        "cost_per_license": str(sw.cost_per_license) if sw.cost_per_license else None,
        "total_cost": str(sw.total_cost) if sw.total_cost else None,
    }

    # Apply updates from kwargs.
    allowed_fields = {
        "name", "software_type_id", "software_family_id", "description",
        "license_model", "license_tier", "cost_per_license", "total_cost",
    }
    for field, value in kwargs.items():
        if field in allowed_fields:
            setattr(sw, field, value)
    sw.updated_at = datetime.now(timezone.utc)

    if cost_changed:
        _close_software_cost_history(sw)
        _record_software_cost_history(sw, user_id=user_id)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.software",
        entity_id=sw.id,
        previous_value=previous,
        new_value={
            "name": sw.name,
            "cost_per_license": str(sw.cost_per_license) if sw.cost_per_license else None,
            "total_cost": str(sw.total_cost) if sw.total_cost else None,
        },
    )
    db.session.commit()

    logger.info("Updated software ID %d", software_id)
    return sw


def deactivate_software(
    software_id: int,
    user_id: int | None = None,
) -> Software:
    """Soft-delete a software product."""
    sw = get_software_by_id(software_id)
    if sw is None:
        raise ValueError(f"Software ID {software_id} not found.")

    sw.is_active = False
    sw.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DELETE",
        entity_type="equip.software",
        entity_id=sw.id,
        previous_value={"is_active": True},
        new_value={"is_active": False},
    )
    db.session.commit()
    return sw


def _record_software_cost_history(
    sw: Software,
    user_id: int | None = None,
) -> None:
    """Insert a new cost history row for a software product (no commit)."""
    history = SoftwareCostHistory(
        software_id=sw.id,
        cost_per_license=sw.cost_per_license,
        total_cost=sw.total_cost,
        changed_by=user_id,
    )
    db.session.add(history)


def _close_software_cost_history(sw: Software) -> None:
    """Set end_date on the current open cost history row (no commit)."""
    current = (
        SoftwareCostHistory.query
        .filter_by(software_id=sw.id, end_date=None)
        .first()
    )
    if current:
        current.end_date = datetime.now(timezone.utc)


# =========================================================================
# Software Coverage (tenant license scope definitions)
# =========================================================================

def get_coverage_for_software(software_id: int) -> list[SoftwareCoverage]:
    """Return all coverage rows for a tenant-licensed software product."""
    return (
        SoftwareCoverage.query
        .filter_by(software_id=software_id)
        .all()
    )


def set_software_coverage(
    software_id: int,
    coverage_rows: list[dict],
    user_id: int | None = None,
) -> list[SoftwareCoverage]:
    """
    Replace all coverage rows for a software product.

    Args:
        software_id:   The software product to update.
        coverage_rows: List of dicts with ``scope_type`` and optional
                       ``department_id``, ``division_id``, ``position_id``.
        user_id:       ID of the user making the change.

    Returns:
        The new list of SoftwareCoverage records.
    """
    # Remove existing coverage rows.
    SoftwareCoverage.query.filter_by(software_id=software_id).delete()

    new_rows = []
    for row_data in coverage_rows:
        cov = SoftwareCoverage(
            software_id=software_id,
            scope_type=row_data["scope_type"],
            department_id=row_data.get("department_id"),
            division_id=row_data.get("division_id"),
            position_id=row_data.get("position_id"),
        )
        db.session.add(cov)
        new_rows.append(cov)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.software_coverage",
        entity_id=software_id,
        new_value={"coverage": coverage_rows},
    )
    db.session.commit()
    return new_rows
