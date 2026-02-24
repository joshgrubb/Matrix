"""
Requirement service — manage position hardware and software requirements.

Provides add/update/remove operations for the ``PositionHardware`` and
``PositionSoftware`` junction tables.  All changes are audit-logged and
recorded in ``budget.requirement_history`` for historical tracking.

Tier 2 Additions:
    - ``copy_position_requirements()``: Copy all hardware and software
      requirements from one position to another, with audit logging.
    - ``get_hardware_usage_counts()``: Return a dict mapping each
      hardware_id to the number of positions that use it.
    - ``get_software_usage_counts()``: Same for software_id.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func

from app.extensions import db
from app.models.budget import RequirementHistory
from app.models.requirement import PositionHardware, PositionSoftware
from app.services import audit_service

logger = logging.getLogger(__name__)


# =========================================================================
# Position Hardware Requirements
# =========================================================================


def get_hardware_requirements(position_id: int) -> list[PositionHardware]:
    """Return all hardware requirements for a position, with related items."""
    return (
        PositionHardware.query.filter_by(position_id=position_id)
        .order_by(PositionHardware.id)
        .all()
    )


def add_hardware_requirement(
    position_id: int,
    hardware_id: int,
    quantity: int = 1,
    notes: str | None = None,
    user_id: int | None = None,
) -> PositionHardware:
    """
    Add a hardware item requirement to a position.

    If the position already has this hardware item, the existing record
    is updated instead of creating a duplicate.

    Args:
        position_id: The position to add the requirement to.
        hardware_id: The specific hardware item to require.
        quantity:    Number per person in the position.
        notes:       Optional notes about this requirement.
        user_id:     ID of the user making the change.

    Returns:
        The created or updated PositionHardware record.
    """
    # Check for existing record (enforce uniqueness).
    existing = PositionHardware.query.filter_by(
        position_id=position_id,
        hardware_id=hardware_id,
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
        hardware_id=hardware_id,
        quantity=quantity,
        notes=notes,
    )
    db.session.add(req)
    db.session.flush()

    # Record in requirement history.
    _record_requirement_history(
        position_id=position_id,
        item_type="hardware",
        item_id=hardware_id,
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
            "hardware_id": hardware_id,
            "quantity": quantity,
            "notes": notes,
        },
    )
    db.session.commit()

    logger.info(
        "Added hardware requirement: position=%d hardware=%d qty=%d",
        position_id,
        hardware_id,
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
    Update an existing hardware requirement.

    Args:
        requirement_id: PK of the PositionHardware record.
        quantity:        New quantity (if changing).
        notes:           New notes (if changing).
        user_id:         ID of the user making the change.

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
        item_id=req.hardware_id,
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
    Remove a hardware requirement from a position (hard delete).

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
        item_id=req.hardware_id,
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
            "hardware_id": req.hardware_id,
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
        PositionSoftware.query.filter_by(position_id=position_id)
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
        "Added software requirement: position=%d software=%d qty=%d",
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
    """
    Update an existing software requirement.

    Args:
        requirement_id: PK of the PositionSoftware record.
        quantity:        New quantity (if changing).
        notes:           New notes (if changing).
        user_id:         ID of the user making the change.

    Returns:
        The updated PositionSoftware record.

    Raises:
        ValueError: If the requirement is not found.
    """
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
        items:       List of dicts with ``hardware_id``, ``quantity``,
                     and optionally ``notes``.
        user_id:     ID of the user making the change.

    Returns:
        The new list of PositionHardware records.
    """
    try:
        # Record history for existing requirements being removed.
        existing = get_hardware_requirements(position_id)
        for req in existing:
            _record_requirement_history(
                position_id=position_id,
                item_type="hardware",
                item_id=req.hardware_id,
                action_type="REMOVED",
                quantity=req.quantity,
                user_id=user_id,
            )

        # Use synchronize_session="fetch" so SQLAlchemy correctly
        # updates the identity map after the bulk DELETE.
        PositionHardware.query.filter_by(position_id=position_id).delete(
            synchronize_session="fetch"
        )

        # Flush the delete so the unique constraint is satisfied
        # before inserting new rows.
        db.session.flush()

        # Add new requirements.
        new_reqs = []
        for item in items:
            req = PositionHardware(
                position_id=position_id,
                hardware_id=item["hardware_id"],
                quantity=item.get("quantity", 1),
                notes=item.get("notes"),
            )
            db.session.add(req)
            db.session.flush()

            _record_requirement_history(
                position_id=position_id,
                item_type="hardware",
                item_id=item["hardware_id"],
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

        logger.info(
            "Replaced hardware requirements for position %d: %d items",
            position_id,
            len(new_reqs),
        )
        return new_reqs

    except Exception:
        # Roll back so the session is usable for the error response.
        db.session.rollback()
        logger.exception(
            "Failed to save hardware requirements for position %d",
            position_id,
        )
        raise


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
    try:
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

        PositionSoftware.query.filter_by(position_id=position_id).delete(
            synchronize_session="fetch"
        )
        db.session.flush()

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

        logger.info(
            "Replaced software requirements for position %d: %d items",
            position_id,
            len(new_reqs),
        )
        return new_reqs

    except Exception:
        db.session.rollback()
        logger.exception(
            "Failed to save software requirements for position %d",
            position_id,
        )
        raise


# =========================================================================
# Tier 2: Copy Requirements Between Positions (#8)
# =========================================================================


def copy_position_requirements(
    source_position_id: int,
    target_position_id: int,
    user_id: int | None = None,
) -> None:
    """
    Copy all hardware and software requirements from one position to another.

    Clears existing requirements on the target before copying.  Both
    the clear and the copy are handled by ``set_position_hardware()``
    and ``set_position_software()``, which already audit-log every
    change and record requirement history.

    A separate "COPY" audit entry is written so administrators can
    trace where the data came from.

    Args:
        source_position_id: Position to copy FROM.
        target_position_id: Position to copy TO.
        user_id:            ID of the user performing the copy.

    Raises:
        ValueError: If either position has no matching record or
                    the source has zero requirements to copy.
    """
    # Fetch source requirements.
    source_hw = get_hardware_requirements(source_position_id)
    source_sw = get_software_requirements(source_position_id)

    if not source_hw and not source_sw:
        raise ValueError("The source position has no equipment or software to copy.")

    # Copy hardware: delegate to set_position_hardware so audit
    # logging and requirement_history are handled consistently.
    set_position_hardware(
        position_id=target_position_id,
        items=[
            {
                "hardware_id": req.hardware_id,
                "quantity": req.quantity,
                "notes": req.notes,
            }
            for req in source_hw
        ],
        user_id=user_id,
    )

    # Copy software: same pattern.
    set_position_software(
        position_id=target_position_id,
        items=[
            {
                "software_id": req.software_id,
                "quantity": req.quantity,
                "notes": req.notes,
            }
            for req in source_sw
        ],
        user_id=user_id,
    )

    # Write a top-level audit entry linking the copy.
    audit_service.log_change(
        user_id=user_id,
        action_type="COPY",
        entity_type="position_requirements",
        entity_id=target_position_id,
        new_value={"copied_from": source_position_id},
    )

    logger.info(
        "Copied requirements from position %d to %d " "(%d hardware, %d software)",
        source_position_id,
        target_position_id,
        len(source_hw),
        len(source_sw),
    )


# =========================================================================
# Tier 2: Usage / Popularity Counts (#9)
# =========================================================================


def get_hardware_usage_counts() -> dict[int, int]:
    """
    Return a dict mapping hardware_id to the count of distinct positions
    using it.

    Used to display "Used by N positions" popularity indicators on the
    hardware selection page.  Runs a single aggregate query — no N+1.

    Returns:
        Dict of ``{hardware_id: position_count}``.
    """
    rows = (
        db.session.query(
            PositionHardware.hardware_id,
            func.count(PositionHardware.position_id.distinct()),
        )
        .group_by(PositionHardware.hardware_id)
        .all()
    )
    return {row[0]: row[1] for row in rows}


def get_software_usage_counts() -> dict[int, int]:
    """
    Return a dict mapping software_id to the count of distinct positions
    using it.

    Used to display "Used by N positions" popularity indicators on the
    software selection page.  Runs a single aggregate query — no N+1.

    Returns:
        Dict of ``{software_id: position_count}``.
    """
    rows = (
        db.session.query(
            PositionSoftware.software_id,
            func.count(PositionSoftware.position_id.distinct()),
        )
        .group_by(PositionSoftware.software_id)
        .all()
    )
    return {row[0]: row[1] for row in rows}


# =========================================================================
# Internal helpers
# =========================================================================


def _record_requirement_history(
    position_id: int,
    item_type: str,
    item_id: int,
    action_type: str,
    quantity: int,
    user_id: int | None = None,
) -> None:
    """
    Insert a row into ``budget.requirement_history`` (no commit).

    This preserves the full audit trail of requirement changes
    even after hard-deletes of position_hardware / position_software.
    """
    history = RequirementHistory(
        position_id=position_id,
        item_type=item_type,
        item_id=item_id,
        action_type=action_type,
        quantity=quantity,
        changed_by=user_id,
    )
    db.session.add(history)
