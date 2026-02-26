"""
HR sync service — NeoGov API integration and organizational data sync.

Syncs departments, divisions, positions, and employees from the NeoGov
HR system into the local database.  New records are created, changed
records are updated, and records no longer present in NeoGov are
soft-deleted (``is_active=False``) to preserve audit history.

The sync is triggered manually by IT staff from the admin panel or
via the ``flask hr-sync`` CLI command.

Architecture:
    ``NeoGovApiClient``  (neogov_client.py)  handles API communication.
    This module                              handles database diffing.
"""

import logging
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.audit import HRSyncLog
from app.models.organization import Department, Division, Employee, Position
from app.services import audit_service
from app.services.neogov_client import NeoGovApiClient

logger = logging.getLogger(__name__)


# =========================================================================
# Public sync API
# =========================================================================


def run_full_sync(user_id: int | None = None) -> HRSyncLog:
    """
    Run a full sync of all organizational data from NeoGov.

    Syncs in dependency order: departments → divisions → positions
    → employees.  Each entity type is diffed against local data.

    Args:
        user_id: ID of the user who triggered the sync.

    Returns:
        The HRSyncLog record with sync results.
    """
    sync_log = _create_sync_log("full", user_id)

    try:
        # Initialize the API client and fetch all data.
        client = NeoGovApiClient()
        api_data = client.fetch_all_organization_data()

        # Sync each entity type in dependency order.
        dept_stats = _sync_departments(api_data.get("departments", []), user_id)
        # Flush so new departments have IDs for division FK lookups.
        db.session.flush()

        div_stats = _sync_divisions(api_data.get("divisions", []), user_id)
        # Flush so new divisions have IDs for position FK lookups.
        db.session.flush()

        pos_stats = _sync_positions(api_data.get("positions", []), user_id)
        # Flush so new positions have IDs for employee FK lookups.
        db.session.flush()

        emp_stats = _sync_employees(api_data.get("employees", []), user_id)

        # Aggregate statistics from all entity syncs.
        total_stats = _merge_stats([dept_stats, div_stats, pos_stats, emp_stats])
        _complete_sync_log(sync_log, total_stats)

        # Record a SYNC audit entry for traceability.
        audit_service.log_change(
            user_id=user_id,
            action_type="SYNC",
            entity_type="org.hr_sync",
            entity_id=sync_log.id,
            new_value={
                "sync_type": "full",
                "processed": total_stats["processed"],
                "created": total_stats["created"],
                "updated": total_stats["updated"],
                "deactivated": total_stats["deactivated"],
                "errors": total_stats["errors"],
            },
        )

        # Single atomic commit: all entity creates/updates/deactivations,
        # the sync log completion, and the audit entry.  If anything
        # above raised, the except block's rollback() undoes everything.
        db.session.commit()

        logger.info(
            "Full HR sync completed: %d processed, %d created, "
            "%d updated, %d deactivated, %d errors",
            total_stats["processed"],
            total_stats["created"],
            total_stats["updated"],
            total_stats["deactivated"],
            total_stats["errors"],
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        db.session.rollback()
        _fail_sync_log(sync_log, str(exc))
        logger.error("HR sync failed: %s", exc, exc_info=True)

    return sync_log


# =========================================================================
# Entity sync logic
# =========================================================================


def _sync_departments(
    api_departments: list[dict],
    user_id: int | None,
) -> dict:
    """
    Sync departments: create new, update changed, deactivate removed.

    Args:
        api_departments: Normalized department dicts from the API client.
        user_id:         ID of the user who triggered the sync.

    Returns:
        Dict with keys: processed, created, updated, deactivated, errors.
    """
    stats = _new_stats()
    # Track which codes the API returned so we can deactivate the rest.
    api_codes: set[str] = set()

    for dept_data in api_departments:
        stats["processed"] += 1
        code = dept_data.get("department_code", "")
        api_codes.add(code)

        try:
            existing = Department.query.filter_by(department_code=code).first()

            if existing is None:
                # Create a new department record.
                dept = Department(
                    department_code=code,
                    department_name=dept_data.get("department_name", code),
                )
                db.session.add(dept)
                stats["created"] += 1
                logger.debug("Created department: %s", code)
            else:
                # Update only if the name changed or record was inactive.
                new_name = dept_data.get("department_name", existing.department_name)
                if existing.department_name != new_name or not existing.is_active:
                    existing.department_name = new_name
                    existing.is_active = True
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1
                    logger.debug("Updated department: %s", code)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing department %s: %s", code, exc)
            stats["errors"] += 1

    # Deactivate departments no longer present in the API response.
    # Guard: only run deactivation if the API actually returned data.
    # An empty response likely indicates an API outage, not that every
    # department was deleted.  Mirrors the existing _sync_employees guard.
    if api_codes:
        active_local = Department.query.filter_by(is_active=True).all()
        for dept in active_local:
            if dept.department_code not in api_codes:
                dept.is_active = False
                dept.updated_at = datetime.now(timezone.utc)
                stats["deactivated"] += 1
                logger.debug("Deactivated department: %s", dept.department_code)
    else:
        logger.warning(
            "No department data received from NeoGov — "
            "department deactivation skipped."
        )

    # No commit here — run_full_sync() commits atomically after all
    # entity syncs succeed.
    return stats


def _sync_divisions(
    api_divisions: list[dict],
    user_id: int | None,
) -> dict:
    """
    Sync divisions: create new, update changed, deactivate removed.

    Each division is linked to its parent department by looking up
    the ``department_code`` provided by the API.

    Args:
        api_divisions: Normalized division dicts from the API client.
        user_id:       ID of the user who triggered the sync.

    Returns:
        Dict with keys: processed, created, updated, deactivated, errors.
    """
    stats = _new_stats()
    api_codes: set[str] = set()

    for div_data in api_divisions:
        stats["processed"] += 1
        code = div_data.get("division_code", "")
        api_codes.add(code)

        try:
            # Resolve the parent department by its NeoGov code.
            dept_code = div_data.get("department_code", "")
            department = Department.query.filter_by(
                department_code=dept_code,
            ).first()

            if department is None:
                logger.warning(
                    "Division %s: parent department %s not found — skipping",
                    code,
                    dept_code,
                )
                stats["errors"] += 1
                continue

            existing = Division.query.filter_by(division_code=code).first()

            if existing is None:
                # Create a new division record.
                div = Division(
                    division_code=code,
                    division_name=div_data.get("division_name", code),
                    department_id=department.id,
                )
                db.session.add(div)
                stats["created"] += 1
                logger.debug("Created division: %s", code)
            else:
                # Update if name, parent department, or active flag changed.
                new_name = div_data.get("division_name", existing.division_name)
                if (
                    existing.division_name != new_name
                    or existing.department_id != department.id
                    or not existing.is_active
                ):
                    existing.division_name = new_name
                    existing.department_id = department.id
                    existing.is_active = True
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1
                    logger.debug("Updated division: %s", code)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing division %s: %s", code, exc)
            stats["errors"] += 1

    # Deactivate divisions no longer present in the API response.
    # Guard: skip deactivation if API returned no data (likely outage).
    if api_codes:
        active_local = Division.query.filter_by(is_active=True).all()
        for div in active_local:
            if div.division_code not in api_codes:
                div.is_active = False
                div.updated_at = datetime.now(timezone.utc)
                stats["deactivated"] += 1
                logger.debug("Deactivated division: %s", div.division_code)
    else:
        logger.warning(
            "No division data received from NeoGov — " "division deactivation skipped."
        )

    # No commit here — run_full_sync() commits atomically.
    return stats


def _sync_positions(
    api_positions: list[dict],
    user_id: int | None,
) -> dict:
    """
    Sync positions: create new, update changed, deactivate removed.

    Each position is linked to its parent division by looking up
    the ``division_code`` provided by the API.

    Args:
        api_positions: Normalized position dicts from the API client.
        user_id:       ID of the user who triggered the sync.

    Returns:
        Dict with keys: processed, created, updated, deactivated, errors.
    """
    stats = _new_stats()
    api_codes: set[str] = set()

    for pos_data in api_positions:
        stats["processed"] += 1
        code = pos_data.get("position_code", "")
        api_codes.add(code)

        try:
            # Resolve the parent division by its NeoGov code.
            div_code = pos_data.get("division_code", "")
            division = Division.query.filter_by(division_code=div_code).first()

            if division is None:
                logger.warning(
                    "Position %s: parent division %s not found — skipping",
                    code,
                    div_code,
                )
                stats["errors"] += 1
                continue

            existing = Position.query.filter_by(position_code=code).first()
            auth_count = pos_data.get("authorized_count", 1)

            if existing is None:
                # Create a new position record.
                pos = Position(
                    position_code=code,
                    position_title=pos_data.get("position_title", code),
                    division_id=division.id,
                    authorized_count=auth_count,
                )
                db.session.add(pos)
                stats["created"] += 1
                logger.debug("Created position: %s", code)
            else:
                # Update if any synced field changed.
                new_title = pos_data.get(
                    "position_title",
                    existing.position_title,
                )
                changed = (
                    existing.position_title != new_title
                    or existing.division_id != division.id
                    or existing.authorized_count != auth_count
                    or not existing.is_active
                )
                if changed:
                    existing.position_title = new_title
                    existing.division_id = division.id
                    existing.authorized_count = auth_count
                    existing.is_active = True
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1
                    logger.debug("Updated position: %s", code)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing position %s: %s", code, exc)
            stats["errors"] += 1

    # Deactivate positions no longer present in the API response.
    # Guard: skip deactivation if API returned no data (likely outage).
    if api_codes:
        active_local = Position.query.filter_by(is_active=True).all()
        for pos in active_local:
            if pos.position_code not in api_codes:
                pos.is_active = False
                pos.updated_at = datetime.now(timezone.utc)
                stats["deactivated"] += 1
                logger.debug("Deactivated position: %s", pos.position_code)
    else:
        logger.warning(
            "No position data received from NeoGov — " "position deactivation skipped."
        )

    # No commit here — run_full_sync() commits atomically.
    return stats


def _sync_employees(
    api_employees: list[dict],
    user_id: int | None,
) -> dict:
    """
    Sync employees: create new, update changed, deactivate removed.

    Employee data is fetched from the ``/persons`` list endpoint
    and then ``/employees/{code}`` detail endpoint concurrently
    by ``NeoGovApiClient``.

    Args:
        api_employees: Normalized employee dicts from the API client.
                       Each dict has keys: ``employee_id``,
                       ``first_name``, ``last_name``, ``email``,
                       ``position_code``.
        user_id:       ID of the user who triggered the sync.

    Returns:
        Dict with keys: processed, created, updated, deactivated, errors.
    """
    stats = _new_stats()

    # Return early if no employee data was provided (e.g., API issue).
    if not api_employees:
        logger.warning(
            "No employee data received from NeoGov — "
            "employee sync skipped.  Check API connectivity "
            "and the /persons endpoint."
        )
        return stats

    api_ids: set[str] = set()

    for emp_data in api_employees:
        stats["processed"] += 1
        neogov_id = emp_data.get("employee_id", "")
        api_ids.add(neogov_id)

        try:
            # Resolve the position by its NeoGov code.
            pos_code = emp_data.get("position_code", "")
            position = Position.query.filter_by(position_code=pos_code).first()

            if position is None:
                logger.warning(
                    "Employee %s: position %s not found — skipping",
                    neogov_id,
                    pos_code,
                )
                stats["errors"] += 1
                continue

            existing = Employee.query.filter_by(
                neogov_employee_id=neogov_id,
            ).first()

            if existing is None:
                # Create a new employee record.
                emp = Employee(
                    neogov_employee_id=neogov_id,
                    first_name=emp_data.get("first_name", ""),
                    last_name=emp_data.get("last_name", ""),
                    email=emp_data.get("email"),
                    position_id=position.id,
                )
                db.session.add(emp)
                stats["created"] += 1
            else:
                # Update if any field changed.
                changed = (
                    existing.first_name
                    != emp_data.get("first_name", existing.first_name)
                    or existing.last_name
                    != emp_data.get("last_name", existing.last_name)
                    or existing.position_id != position.id
                    or not existing.is_active
                )
                if changed:
                    existing.first_name = emp_data.get(
                        "first_name",
                        existing.first_name,
                    )
                    existing.last_name = emp_data.get(
                        "last_name",
                        existing.last_name,
                    )
                    existing.email = emp_data.get("email", existing.email)
                    existing.position_id = position.id
                    existing.is_active = True
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing employee %s: %s", neogov_id, exc)
            stats["errors"] += 1

    # Deactivate employees no longer present in the API response.
    # Only run if we actually received employee data (avoid mass-deactivation
    # when the employees list is intentionally empty).
    if api_ids:
        active_local = Employee.query.filter_by(is_active=True).all()
        for emp in active_local:
            if emp.neogov_employee_id not in api_ids:
                emp.is_active = False
                emp.updated_at = datetime.now(timezone.utc)
                stats["deactivated"] += 1

    # No commit here — run_full_sync() commits atomically.
    return stats


# =========================================================================
# Sync log management
# =========================================================================


def _create_sync_log(sync_type: str, user_id: int | None) -> HRSyncLog:
    """Create a new sync log entry with 'started' status."""
    sync_log = HRSyncLog(
        triggered_by=user_id,
        sync_type=sync_type,
        status="started",
        started_at=datetime.now(timezone.utc),
    )
    db.session.add(sync_log)
    # This commit is intentional.  The sync_log must be persisted
    # before the try block so that if run_full_sync's except block
    # calls rollback(), the "started" row survives and _fail_sync_log
    # can update it to "failed".
    db.session.commit()
    return sync_log


def _complete_sync_log(sync_log: HRSyncLog, stats: dict) -> None:
    """
    Mark a sync log as completed with summary statistics.

    Note:
        The HRSyncLog model columns should be INTEGER for proper
        counts.  If your DDL still uses BIT columns, run the
        migration in ``migrations/versions/`` to alter them to INT.
    """
    sync_log.status = "completed"
    sync_log.completed_at = datetime.now(timezone.utc)
    sync_log.records_processed = stats["processed"]
    sync_log.records_created = stats["created"]
    sync_log.records_updated = stats["updated"]
    sync_log.records_deactivated = stats["deactivated"]
    sync_log.records_errors = stats["errors"]
    # Flush only — the caller (run_full_sync) issues the final commit.
    db.session.flush()


def _fail_sync_log(sync_log: HRSyncLog, error_message: str) -> None:
    """Mark a sync log as failed with an error message."""
    sync_log.status = "failed"
    # Truncate for NVARCHAR(MAX) safety.
    sync_log.error_message = error_message[:4000]
    sync_log.completed_at = datetime.now(timezone.utc)
    # This commit is intentional.  _fail_sync_log runs AFTER
    # db.session.rollback() in the except block of run_full_sync.
    # The sync_log row was committed by _create_sync_log before the
    # try block, so it survived the rollback.  We must commit here
    # to persist the "failed" status update.
    db.session.commit()


def get_sync_logs(page: int = 1, per_page: int = 20):
    """
    Return paginated sync log entries, most recent first.

    Args:
        page:     Page number (1-indexed).
        per_page: Records per page.

    Returns:
        Flask-SQLAlchemy pagination object.
    """
    return HRSyncLog.query.order_by(HRSyncLog.started_at.desc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )


# =========================================================================
# Internal helpers
# =========================================================================


def _new_stats() -> dict:
    """Return a fresh stats dict for sync tracking."""
    return {
        "processed": 0,
        "created": 0,
        "updated": 0,
        "deactivated": 0,
        "errors": 0,
    }


def _merge_stats(stats_list: list[dict]) -> dict:
    """Merge multiple stats dicts into one by summing all values."""
    merged = _new_stats()
    for stats in stats_list:
        for key in merged:
            merged[key] += stats[key]
    return merged
