"""
Audit service — records all data changes and queries audit logs.

Every CREATE, UPDATE, and DELETE operation in the application passes
through this service so that a complete audit trail is maintained.
The ``log_change`` function is the primary entry point, called by
other services after committing a database change.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from flask import request
from sqlalchemy import desc

from app.extensions import db
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


# -- Write audit entries ---------------------------------------------------

def log_change(
    user_id: int | None,
    action_type: str,
    entity_type: str,
    entity_id: int | None,
    previous_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> AuditLog:
    """
    Record a data change in the audit log.

    Args:
        user_id:        ID of the user who made the change, or None for
                        system actions (e.g., HR sync).
        action_type:    One of CREATE, UPDATE, DELETE, LOGIN, LOGOUT, SYNC.
        entity_type:    Dot-notation entity name (e.g., 'equip.hardware_type').
        entity_id:      Primary key of the affected record.
        previous_value: Dict of the record state before the change.
        new_value:      Dict of the record state after the change.

    Returns:
        The newly created AuditLog record.
    """
    # Capture request metadata when available (inside a request context).
    ip_address = None
    user_agent = None
    try:
        ip_address = request.remote_addr
        user_agent = str(request.user_agent)[:500]
    except RuntimeError:
        # Outside of a request context (e.g., CLI or background task).
        pass

    entry = AuditLog(
        user_id=user_id,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_value=json.dumps(previous_value) if previous_value else None,
        new_value=json.dumps(new_value) if new_value else None,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.session.add(entry)
    db.session.flush()  # Ensure the entry gets an ID immediately.

    logger.info(
        "Audit: %s %s:%s by user %s",
        action_type,
        entity_type,
        entity_id,
        user_id,
    )
    return entry


def log_login(user_id: int) -> AuditLog:
    """Record a successful user login."""
    return log_change(
        user_id=user_id,
        action_type="LOGIN",
        entity_type="auth.user",
        entity_id=user_id,
    )


def log_logout(user_id: int) -> AuditLog:
    """Record a user logout."""
    return log_change(
        user_id=user_id,
        action_type="LOGOUT",
        entity_type="auth.user",
        entity_id=user_id,
    )


# -- Query audit logs ------------------------------------------------------

def get_audit_logs(
    page: int = 1,
    per_page: int = 50,
    user_id: int | None = None,
    action_type: str | None = None,
    entity_type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    """
    Query audit logs with optional filters and pagination.

    Args:
        page:        Page number (1-indexed).
        per_page:    Records per page.
        user_id:     Filter by the user who made the change.
        action_type: Filter by action (CREATE, UPDATE, DELETE, etc.).
        entity_type: Filter by entity (e.g., 'equip.hardware_type').
        start_date:  Include only entries on or after this datetime.
        end_date:    Include only entries on or before this datetime.

    Returns:
        A SQLAlchemy pagination object with ``.items``, ``.pages``,
        ``.total``, etc.
    """
    query = AuditLog.query.order_by(desc(AuditLog.created_at))

    # Apply optional filters.
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if start_date:
        query = query.filter(AuditLog.created_at >= start_date)
    if end_date:
        query = query.filter(AuditLog.created_at <= end_date)

    return query.paginate(page=page, per_page=per_page, error_out=False)


def get_distinct_entity_types() -> list[str]:
    """Return a sorted list of distinct entity_type values in the audit log."""
    rows = (
        db.session.query(AuditLog.entity_type)
        .distinct()
        .order_by(AuditLog.entity_type)
        .all()
    )
    return [row[0] for row in rows]
