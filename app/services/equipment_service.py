"""
Equipment service — CRUD for the hardware and software catalog.

Manages hardware types (generic categories like "Laptop", "Monitor"),
hardware items (specific products like "Standard Laptop", "32-inch
Monitor"), software types, software families, and individual software
products.

Cost changes are tracked in the budget schema history tables.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.models.budget import (
    HardwareCostHistory,
    HardwareTypeCostHistory,
    SoftwareCostHistory,
)
from app.models.equipment import (
    Hardware,
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareFamily,
    SoftwareType,
)
from app.services import audit_service

logger = logging.getLogger(__name__)


# =========================================================================
# Hardware Types (categories)
# =========================================================================


def get_hardware_types(include_inactive: bool = False) -> list[HardwareType]:
    """Return all hardware types ordered by name."""
    query = HardwareType.query.order_by(HardwareType.type_name)
    if not include_inactive:
        query = query.filter(HardwareType.is_active == True)  # noqa: E712
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
        type_name:      Display name (e.g., "Laptop").
        estimated_cost: Reference cost for this hardware category.
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

    # Record initial cost in the type-level history table.
    _record_hardware_type_cost_history(hw_type, user_id=user_id)

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

    # Track whether the cost changed for history purposes.
    cost_changed = (
        estimated_cost is not None and estimated_cost != hw_type.estimated_cost
    )

    if type_name is not None:
        hw_type.type_name = type_name
    if estimated_cost is not None:
        hw_type.estimated_cost = estimated_cost
    if description is not None:
        hw_type.description = description
    hw_type.updated_at = datetime.now(timezone.utc)

    if cost_changed:
        _close_hardware_type_cost_history(hw_type)
        _record_hardware_type_cost_history(hw_type, user_id=user_id)

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
    """
    Soft-delete a hardware type by setting ``is_active`` to False.

    Raises:
        ValueError: If the hardware type is not found.
    """
    hw_type = get_hardware_type_by_id(hw_type_id)
    if hw_type is None:
        raise ValueError(f"Hardware type ID {hw_type_id} not found.")

    hw_type.is_active = False
    hw_type.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DEACTIVATE",
        entity_type="equip.hardware_type",
        entity_id=hw_type.id,
    )
    db.session.commit()

    logger.info("Deactivated hardware type ID %d", hw_type_id)
    return hw_type


# -- Hardware type cost history helpers ------------------------------------


def _record_hardware_type_cost_history(
    hw_type: HardwareType,
    user_id: int | None = None,
) -> None:
    """Insert a new effective-dated cost row for a hardware type (no commit)."""
    history = HardwareTypeCostHistory(
        hardware_type_id=hw_type.id,
        estimated_cost=hw_type.estimated_cost,
        changed_by=user_id,
    )
    db.session.add(history)


def _close_hardware_type_cost_history(hw_type: HardwareType) -> None:
    """Set end_date on the current open type cost history row (no commit)."""
    current = HardwareTypeCostHistory.query.filter_by(
        hardware_type_id=hw_type.id, end_date=None
    ).first()
    if current:
        current.end_date = datetime.now(timezone.utc)


# =========================================================================
# Hardware Items (specific products within a type)
# =========================================================================


def get_hardware_items(
    include_inactive: bool = False,
    hardware_type_id: int | None = None,
) -> list[Hardware]:
    """
    Return hardware items, optionally filtered by type.

    Args:
        include_inactive:  If True, include deactivated items.
        hardware_type_id:  Filter to a specific hardware category.

    Returns:
        List of Hardware records ordered by name.
    """
    query = Hardware.query.order_by(Hardware.name)
    if not include_inactive:
        query = query.filter(Hardware.is_active == True)  # noqa: E712
    if hardware_type_id is not None:
        query = query.filter(Hardware.hardware_type_id == hardware_type_id)
    return query.all()


def get_hardware_by_id(hardware_id: int) -> Hardware | None:
    """Return a hardware item by primary key."""
    return db.session.get(Hardware, hardware_id)


def create_hardware(
    name: str,
    hardware_type_id: int,
    estimated_cost: Decimal,
    description: str | None = None,
    user_id: int | None = None,
) -> Hardware:
    """
    Create a new hardware item and record initial cost history.

    Args:
        name:              Display name (e.g., "Standard Laptop").
        hardware_type_id:  FK to the parent hardware type category.
        estimated_cost:    Budgetary cost per unit.
        description:       Optional description.
        user_id:           ID of the user creating the record.

    Returns:
        The newly created Hardware record.
    """
    hw = Hardware(
        name=name,
        hardware_type_id=hardware_type_id,
        estimated_cost=estimated_cost,
        description=description,
    )
    db.session.add(hw)
    db.session.flush()

    # Record initial cost in the item-level history table.
    _record_hardware_cost_history(hw, user_id=user_id)

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.hardware",
        entity_id=hw.id,
        new_value={
            "name": name,
            "hardware_type_id": hardware_type_id,
            "estimated_cost": str(estimated_cost),
            "description": description,
        },
    )
    db.session.commit()

    logger.info("Created hardware item: %s", name)
    return hw


def update_hardware(
    hardware_id: int,
    name: str | None = None,
    hardware_type_id: int | None = None,
    estimated_cost: Decimal | None = None,
    description: str | None = None,
    user_id: int | None = None,
) -> Hardware:
    """
    Update an existing hardware item.  If the cost changes, a new
    cost history record is created.

    Returns:
        The updated Hardware record.

    Raises:
        ValueError: If the hardware item is not found.
    """
    hw = get_hardware_by_id(hardware_id)
    if hw is None:
        raise ValueError(f"Hardware ID {hardware_id} not found.")

    previous = {
        "name": hw.name,
        "hardware_type_id": hw.hardware_type_id,
        "estimated_cost": str(hw.estimated_cost),
        "description": hw.description,
    }

    # Track whether the cost changed for history purposes.
    cost_changed = estimated_cost is not None and estimated_cost != hw.estimated_cost

    if name is not None:
        hw.name = name
    if hardware_type_id is not None:
        hw.hardware_type_id = hardware_type_id
    if estimated_cost is not None:
        hw.estimated_cost = estimated_cost
    if description is not None:
        hw.description = description
    hw.updated_at = datetime.now(timezone.utc)

    if cost_changed:
        _close_hardware_cost_history(hw)
        _record_hardware_cost_history(hw, user_id=user_id)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.hardware",
        entity_id=hw.id,
        previous_value=previous,
        new_value={
            "name": hw.name,
            "hardware_type_id": hw.hardware_type_id,
            "estimated_cost": str(hw.estimated_cost),
            "description": hw.description,
        },
    )
    db.session.commit()

    logger.info("Updated hardware item ID %d", hardware_id)
    return hw


def deactivate_hardware(
    hardware_id: int,
    user_id: int | None = None,
) -> Hardware:
    """
    Soft-delete a hardware item by setting ``is_active`` to False.

    Raises:
        ValueError: If the hardware item is not found.
    """
    hw = get_hardware_by_id(hardware_id)
    if hw is None:
        raise ValueError(f"Hardware ID {hardware_id} not found.")

    hw.is_active = False
    hw.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DEACTIVATE",
        entity_type="equip.hardware",
        entity_id=hw.id,
    )
    db.session.commit()

    logger.info("Deactivated hardware item ID %d", hardware_id)
    return hw


# -- Hardware item cost history helpers ------------------------------------


def _record_hardware_cost_history(
    hw: Hardware,
    user_id: int | None = None,
) -> None:
    """Insert a new effective-dated cost row for a hardware item (no commit)."""
    history = HardwareCostHistory(
        hardware_id=hw.id,
        estimated_cost=hw.estimated_cost,
        changed_by=user_id,
    )
    db.session.add(history)


def _close_hardware_cost_history(hw: Hardware) -> None:
    """Set end_date on the current open item cost history row (no commit)."""
    current = HardwareCostHistory.query.filter_by(
        hardware_id=hw.id, end_date=None
    ).first()
    if current:
        current.end_date = datetime.now(timezone.utc)


# =========================================================================
# Software Types (categories)
# =========================================================================


def get_software_types(include_inactive: bool = False) -> list[SoftwareType]:
    """Return all software type categories ordered by name."""
    query = SoftwareType.query.order_by(SoftwareType.type_name)
    if not include_inactive:
        query = query.filter(SoftwareType.is_active == True)  # noqa: E712
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


def deactivate_software_type(
    sw_type_id: int,
    user_id: int | None = None,
) -> SoftwareType:
    """
    Soft-delete a software type category.

    Existing software products referencing this type will retain
    their foreign key but the type will no longer appear in active
    lists or dropdowns.

    Args:
        sw_type_id: Primary key of the software type to deactivate.
        user_id:    ID of the user performing the action.

    Returns:
        The deactivated SoftwareType record.

    Raises:
        ValueError: If the software type is not found.
    """
    sw_type = get_software_type_by_id(sw_type_id)
    if sw_type is None:
        raise ValueError(f"Software type ID {sw_type_id} not found.")

    sw_type.is_active = False
    sw_type.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DEACTIVATE",
        entity_type="equip.software_type",
        entity_id=sw_type.id,
    )
    db.session.commit()

    logger.info("Deactivated software type ID %d", sw_type_id)
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
        query = query.filter(SoftwareFamily.is_active == True)  # noqa: E712
    return query.all()


def get_software_family_by_id(family_id: int) -> SoftwareFamily | None:
    """Return a software family by primary key."""
    return db.session.get(SoftwareFamily, family_id)


def create_software_family(
    family_name: str,
    description: str | None = None,
    user_id: int | None = None,
) -> SoftwareFamily:
    """
    Create a new software family grouping.

    Args:
        family_name: Display name (e.g., "Microsoft 365").
        description: Optional description.
        user_id:     ID of the user creating the record.

    Returns:
        The newly created SoftwareFamily record.
    """
    family = SoftwareFamily(family_name=family_name, description=description)
    db.session.add(family)
    db.session.flush()

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.software_family",
        entity_id=family.id,
        new_value={"family_name": family_name, "description": description},
    )
    db.session.commit()

    logger.info("Created software family: %s", family_name)
    return family


def update_software_family(
    family_id: int,
    family_name: str | None = None,
    description: str | None = None,
    user_id: int | None = None,
) -> SoftwareFamily:
    """
    Update an existing software family.

    Args:
        family_id:   Primary key of the family to update.
        family_name: New display name (or None to keep current).
        description: New description (or None to keep current).
        user_id:     ID of the user performing the update.

    Returns:
        The updated SoftwareFamily record.

    Raises:
        ValueError: If the software family is not found.
    """
    family = get_software_family_by_id(family_id)
    if family is None:
        raise ValueError(f"Software family ID {family_id} not found.")

    if family_name is not None:
        family.family_name = family_name
    if description is not None:
        family.description = description
    family.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.software_family",
        entity_id=family.id,
        new_value={
            "family_name": family.family_name,
            "description": family.description,
        },
    )
    db.session.commit()

    logger.info("Updated software family ID %d", family_id)
    return family


def deactivate_software_family(
    family_id: int,
    user_id: int | None = None,
) -> SoftwareFamily:
    """
    Soft-delete a software family.

    Existing software products referencing this family will retain
    their foreign key but the family will no longer appear in active
    lists or dropdowns.

    Args:
        family_id: Primary key of the family to deactivate.
        user_id:   ID of the user performing the action.

    Returns:
        The deactivated SoftwareFamily record.

    Raises:
        ValueError: If the software family is not found.
    """
    family = get_software_family_by_id(family_id)
    if family is None:
        raise ValueError(f"Software family ID {family_id} not found.")

    family.is_active = False
    family.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DEACTIVATE",
        entity_type="equip.software_family",
        entity_id=family.id,
    )
    db.session.commit()

    logger.info("Deactivated software family ID %d", family_id)
    return family


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
        query = query.filter(Software.is_active == True)  # noqa: E712
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
        "cost_per_license" in kwargs
        and kwargs["cost_per_license"] != sw.cost_per_license
    ) or ("total_cost" in kwargs and kwargs["total_cost"] != sw.total_cost)

    previous = {
        "name": sw.name,
        "cost_per_license": str(sw.cost_per_license) if sw.cost_per_license else None,
        "total_cost": str(sw.total_cost) if sw.total_cost else None,
    }

    # Apply updates from kwargs.
    for field_name, value in kwargs.items():
        if hasattr(sw, field_name):
            setattr(sw, field_name, value)
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
            "cost_per_license": (
                str(sw.cost_per_license) if sw.cost_per_license else None
            ),
            "total_cost": str(sw.total_cost) if sw.total_cost else None,
        },
    )
    db.session.commit()

    logger.info("Updated software product ID %d", software_id)
    return sw


def deactivate_software(
    software_id: int,
    user_id: int | None = None,
) -> Software:
    """
    Soft-delete a software product.

    Raises:
        ValueError: If the software product is not found.
    """
    sw = get_software_by_id(software_id)
    if sw is None:
        raise ValueError(f"Software ID {software_id} not found.")

    sw.is_active = False
    sw.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=user_id,
        action_type="DEACTIVATE",
        entity_type="equip.software",
        entity_id=sw.id,
    )
    db.session.commit()

    logger.info("Deactivated software ID %d", software_id)
    return sw


# -- Software cost history helpers -----------------------------------------


def _record_software_cost_history(
    sw: Software,
    user_id: int | None = None,
) -> None:
    """Insert a new effective-dated cost row for a software product (no commit)."""
    history = SoftwareCostHistory(
        software_id=sw.id,
        cost_per_license=sw.cost_per_license,
        total_cost=sw.total_cost,
        changed_by=user_id,
    )
    db.session.add(history)


def _close_software_cost_history(sw: Software) -> None:
    """Set end_date on the current open cost history row (no commit)."""
    current = SoftwareCostHistory.query.filter_by(
        software_id=sw.id, end_date=None
    ).first()
    if current:
        current.end_date = datetime.now(timezone.utc)
