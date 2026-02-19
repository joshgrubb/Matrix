"""
Requirement service — manage position hardware and software requirements.

Provides add/update/remove operations for the ``PositionHardware`` and
``PositionSoftware`` junction tables.  All changes are audit-logged and
recorded in ``budget.requirement_history`` for historical tracking.
"""

import logging
from datetime import datetime, timezone

from app.extensions import db
from app.models.budget import RequirementHistory
from app.models.requirement import PositionHardware, PositionSoftware
from app.services import audit_service

logger = logging.getLogger(__name__)


# =========================================================================
# Position Hardware Requirements
# =========================================================================

def get_hardware_requirements(position_id: int) -> list[PositionHardware]:
    """Return all hardware requirements for a position, with related types."""
    return (
        PositionHardware.query
        .filter_by(position_id=position_id)
        .order_by(PositionHardware.id)
        .all()
    )


def add_hardware_requirement(
    position_id: int,
    hardware_type_id: int,
    quantity: int = 1,
    notes: str | None = None,
    user_id: int | None = None,
) -> PositionHardware:
    """
    Add a hardware type requirement to a position.

    If the position already has this hardware type, the existing record
    is updated instead of creating a duplicate.

    Args:
        position_id:      The position to add the requirement to.
        hardware_type_id: The hardware type to require.
        quantity:         Number per person in the position.
        notes:            Optional notes about this requirement.
        user_id:          ID of the user making the change.

    Returns:
        The created or updated PositionHardware record.
    """
    # Check for existing record (enforce uniqueness).
    existing = PositionHardware.query.filter_by(
        position_id=position_id,
        hardware_type_id=hardware_type_id,
    ).first()

    if existing:
        return update_hardware_requirement(
            requirement_id=existing.id,
            quantity=quantity,
            notes=notes,
            user_id=user_id,
        )

    req = PositionHardware(
        position_id=position_id,
        hardware_type_id=hardware_type_id,
        quantity=quantity,
        notes=notes,
    )
    db.session.add(req)
    db.session.flush()

    # Record in requirement history.
    _record_requirement_history(
        position_id=position_id,
        item_type="hardware",
        item_id=hardware_type_id,
        action_type="ADDED",
        quantity=quantity,
        user_id=user_id,
    )

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.position_hardware",
        entity_id=req.id,
        new_value={
            "position_id": position_id,
            "hardware_type_id": hardware_type_id,
            "quantity": quantity,
            "notes": notes,
        },
    )
    db.session.commit()

    logger.info(
        "Added hardware requirement: position=%d hw_type=%d qty=%d",
        position_id,
        hardware_type_id,
        quantity,
    )
    return req


def update_hardware_requirement(
    requirement_id: int,
    quantity: int | None = None,
    notes: str | None = None,
    user_id: int | None = None,
) -> PositionHardware:
    """
    Update an existing hardware requirement's quantity or notes.

    Returns:
        The updated PositionHardware record.

    Raises:
        ValueError: If the requirement is not found.
    """
    req = db.session.get(PositionHardware, requirement_id)
    if req is None:
        raise ValueError(f"Hardware requirement ID {requirement_id} not found.")

    previous = {"quantity": req.quantity, "notes": req.notes}

    if quantity is not None:
        req.quantity = quantity
    if notes is not None:
        req.notes = notes
    req.updated_at = datetime.now(timezone.utc)

    _record_requirement_history(
        position_id=req.position_id,
        item_type="hardware",
        item_id=req.hardware_type_id,
        action_type="MODIFIED",
        quantity=req.quantity,
        user_id=user_id,
    )

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.position_hardware",
        entity_id=req.id,
        previous_value=previous,
        new_value={"quantity": req.quantity, "notes": req.notes},
    )
    db.session.commit()
    return req


def remove_hardware_requirement(
    requirement_id: int,
    user_id: int | None = None,
) -> None:
    """
    Remove a hardware requirement from a position.

    This is a hard delete since the requirement history table preserves
    the audit trail.

    Raises:
        ValueError: If the requirement is not found.
    """
    req = db.session.get(PositionHardware, requirement_id)
    if req is None:
        raise ValueError(f"Hardware requirement ID {requirement_id} not found.")

    _record_requirement_history(
        position_id=req.position_id,
        item_type="hardware",
        item_id=req.hardware_type_id,
        action_type="REMOVED",
        quantity=req.quantity,
        user_id=user_id,
    )

    audit_service.log_change(
        user_id=user_id,
        action_type="DELETE",
        entity_type="equip.position_hardware",
        entity_id=req.id,
        previous_value={
            "position_id": req.position_id,
            "hardware_type_id": req.hardware_type_id,
            "quantity": req.quantity,
        },
    )

    db.session.delete(req)
    db.session.commit()

    logger.info("Removed hardware requirement ID %d", requirement_id)


# =========================================================================
# Position Software Requirements
# =========================================================================

def get_software_requirements(position_id: int) -> list[PositionSoftware]:
    """Return all software requirements for a position."""
    return (
        PositionSoftware.query
        .filter_by(position_id=position_id)
        .order_by(PositionSoftware.id)
        .all()
    )


def add_software_requirement(
    position_id: int,
    software_id: int,
    quantity: int = 1,
    notes: str | None = None,
    user_id: int | None = None,
) -> PositionSoftware:
    """
    Add a software requirement to a position.

    If the position already has this software product, the existing
    record is updated instead of creating a duplicate.

    Args:
        position_id: The position to add the requirement to.
        software_id: The software product to require.
        quantity:    Number of licenses per person.
        notes:       Optional notes about this requirement.
        user_id:     ID of the user making the change.

    Returns:
        The created or updated PositionSoftware record.
    """
    existing = PositionSoftware.query.filter_by(
        position_id=position_id,
        software_id=software_id,
    ).first()

    if existing:
        return update_software_requirement(
            requirement_id=existing.id,
            quantity=quantity,
            notes=notes,
            user_id=user_id,
        )

    req = PositionSoftware(
        position_id=position_id,
        software_id=software_id,
        quantity=quantity,
        notes=notes,
    )
    db.session.add(req)
    db.session.flush()

    _record_requirement_history(
        position_id=position_id,
        item_type="software",
        item_id=software_id,
        action_type="ADDED",
        quantity=quantity,
        user_id=user_id,
    )

    audit_service.log_change(
        user_id=user_id,
        action_type="CREATE",
        entity_type="equip.position_software",
        entity_id=req.id,
        new_value={
            "position_id": position_id,
            "software_id": software_id,
            "quantity": quantity,
            "notes": notes,
        },
    )
    db.session.commit()

    logger.info(
        "Added software requirement: position=%d sw=%d qty=%d",
        position_id,
        software_id,
        quantity,
    )
    return req


def update_software_requirement(
    requirement_id: int,
    quantity: int | None = None,
    notes: str | None = None,
    user_id: int | None = None,
) -> PositionSoftware:
    """Update an existing software requirement's quantity or notes."""
    req = db.session.get(PositionSoftware, requirement_id)
    if req is None:
        raise ValueError(f"Software requirement ID {requirement_id} not found.")

    previous = {"quantity": req.quantity, "notes": req.notes}

    if quantity is not None:
        req.quantity = quantity
    if notes is not None:
        req.notes = notes
    req.updated_at = datetime.now(timezone.utc)

    _record_requirement_history(
        position_id=req.position_id,
        item_type="software",
        item_id=req.software_id,
        action_type="MODIFIED",
        quantity=req.quantity,
        user_id=user_id,
    )

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.position_software",
        entity_id=req.id,
        previous_value=previous,
        new_value={"quantity": req.quantity, "notes": req.notes},
    )
    db.session.commit()
    return req


def remove_software_requirement(
    requirement_id: int,
    user_id: int | None = None,
) -> None:
    """Remove a software requirement from a position (hard delete)."""
    req = db.session.get(PositionSoftware, requirement_id)
    if req is None:
        raise ValueError(f"Software requirement ID {requirement_id} not found.")

    _record_requirement_history(
        position_id=req.position_id,
        item_type="software",
        item_id=req.software_id,
        action_type="REMOVED",
        quantity=req.quantity,
        user_id=user_id,
    )

    audit_service.log_change(
        user_id=user_id,
        action_type="DELETE",
        entity_type="equip.position_software",
        entity_id=req.id,
        previous_value={
            "position_id": req.position_id,
            "software_id": req.software_id,
            "quantity": req.quantity,
        },
    )

    db.session.delete(req)
    db.session.commit()

    logger.info("Removed software requirement ID %d", requirement_id)


# =========================================================================
# Bulk operations (for the guided selection flow)
# =========================================================================

def set_position_hardware(
    position_id: int,
    items: list[dict],
    user_id: int | None = None,
) -> list[PositionHardware]:
    """
    Replace all hardware requirements for a position.

    Args:
        position_id: The position to update.
        items:       List of dicts with ``hardware_type_id``, ``quantity``,
                     and optionally ``notes``.
        user_id:     ID of the user making the change.

    Returns:
        The new list of PositionHardware records.
    """
    # Remove existing requirements.
    existing = get_hardware_requirements(position_id)
    for req in existing:
        _record_requirement_history(
            position_id=position_id,
            item_type="hardware",
            item_id=req.hardware_type_id,
            action_type="REMOVED",
            quantity=req.quantity,
            user_id=user_id,
        )
    PositionHardware.query.filter_by(position_id=position_id).delete()

    # Add new requirements.
    new_reqs = []
    for item in items:
        req = PositionHardware(
            position_id=position_id,
            hardware_type_id=item["hardware_type_id"],
            quantity=item.get("quantity", 1),
            notes=item.get("notes"),
        )
        db.session.add(req)
        db.session.flush()

        _record_requirement_history(
            position_id=position_id,
            item_type="hardware",
            item_id=item["hardware_type_id"],
            action_type="ADDED",
            quantity=item.get("quantity", 1),
            user_id=user_id,
        )
        new_reqs.append(req)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.position_hardware_bulk",
        entity_id=position_id,
        new_value={"items": items},
    )
    db.session.commit()
    return new_reqs


def set_position_software(
    position_id: int,
    items: list[dict],
    user_id: int | None = None,
) -> list[PositionSoftware]:
    """
    Replace all software requirements for a position.

    Args:
        position_id: The position to update.
        items:       List of dicts with ``software_id``, ``quantity``,
                     and optionally ``notes``.
        user_id:     ID of the user making the change.

    Returns:
        The new list of PositionSoftware records.
    """
    existing = get_software_requirements(position_id)
    for req in existing:
        _record_requirement_history(
            position_id=position_id,
            item_type="software",
            item_id=req.software_id,
            action_type="REMOVED",
            quantity=req.quantity,
            user_id=user_id,
        )
    PositionSoftware.query.filter_by(position_id=position_id).delete()

    new_reqs = []
    for item in items:
        req = PositionSoftware(
            position_id=position_id,
            software_id=item["software_id"],
            quantity=item.get("quantity", 1),
            notes=item.get("notes"),
        )
        db.session.add(req)
        db.session.flush()

        _record_requirement_history(
            position_id=position_id,
            item_type="software",
            item_id=item["software_id"],
            action_type="ADDED",
            quantity=item.get("quantity", 1),
            user_id=user_id,
        )
        new_reqs.append(req)

    audit_service.log_change(
        user_id=user_id,
        action_type="UPDATE",
        entity_type="equip.position_software_bulk",
        entity_id=position_id,
        new_value={"items": items},
    )
    db.session.commit()
    return new_reqs


# =========================================================================
# Requirement History
# =========================================================================

def _record_requirement_history(
    position_id: int,
    item_type: str,
    item_id: int,
    action_type: str,
    quantity: int,
    user_id: int | None = None,
    change_reason: str | None = None,
) -> None:
    """Insert a requirement history event (no commit)."""
    history = RequirementHistory(
        position_id=position_id,
        item_type=item_type,
        item_id=item_id,
        action_type=action_type,
        quantity=quantity,
        changed_by=user_id,
        change_reason=change_reason,
    )
    db.session.add(history)
