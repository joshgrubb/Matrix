"""
HR sync service — NeoGov API integration and organizational data sync.

Syncs departments, divisions, positions, and employees from the NeoGov
HR system into the local database.  New records are created, changed
records are updated, and records no longer present in NeoGov are
soft-deleted (is_active=False) to preserve audit history.

The sync is triggered manually by IT staff from the admin panel.
"""

import logging
from datetime import datetime, timezone

import requests
from flask import current_app

from app.extensions import db
from app.models.audit import HRSyncLog
from app.models.organization import Department, Division, Employee, Position
from app.services import audit_service

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
        # Fetch all data from NeoGov API.
        api_data = _fetch_neogov_data()

        # Sync each entity type in order.
        dept_stats = _sync_departments(api_data.get("departments", []), user_id)
        div_stats = _sync_divisions(api_data.get("divisions", []), user_id)
        pos_stats = _sync_positions(api_data.get("positions", []), user_id)
        emp_stats = _sync_employees(api_data.get("employees", []), user_id)

        # Aggregate statistics.
        total_stats = _merge_stats([dept_stats, div_stats, pos_stats, emp_stats])
        _complete_sync_log(sync_log, total_stats)

        logger.info(
            "Full HR sync completed: %d processed, %d created, "
            "%d updated, %d deactivated",
            total_stats["processed"],
            total_stats["created"],
            total_stats["updated"],
            total_stats["deactivated"],
        )

    except Exception as exc:  # pylint: disable=broad-exception-caught
        _fail_sync_log(sync_log, str(exc))
        logger.error("HR sync failed: %s", exc)

    return sync_log


# =========================================================================
# NeoGov API communication
# =========================================================================

def _fetch_neogov_data() -> dict:
    """
    Fetch organizational data from the NeoGov API.

    Returns:
        Dict with keys: departments, divisions, positions, employees.
        Each value is a list of dicts from the API.

    Raises:
        ConnectionError: If the API is unreachable.
        ValueError: If the API returns an unexpected response.
    """
    base_url = current_app.config["NEOGOV_API_BASE_URL"]
    api_key = current_app.config["NEOGOV_API_KEY"]
    headers = {"Authorization": f"Bearer {api_key}"}

    # In development, if no API key is configured, return empty data.
    if not api_key:
        logger.warning(
            "NEOGOV_API_KEY not configured — returning empty data. "
            "Set the key in .env for production."
        )
        return {
            "departments": [],
            "divisions": [],
            "positions": [],
            "employees": [],
        }

    try:
        resp = requests.get(
            f"{base_url}/organization",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise ConnectionError(
            f"Failed to connect to NeoGov API: {exc}"
        ) from exc


# =========================================================================
# Entity sync logic
# =========================================================================

def _sync_departments(
    api_departments: list[dict],
    user_id: int | None,
) -> dict:
    """
    Sync departments: create new, update changed, deactivate removed.

    Returns:
        Dict with keys: processed, created, updated, deactivated, errors.
    """
    stats = _new_stats()
    api_codes = set()

    for dept_data in api_departments:
        stats["processed"] += 1
        code = dept_data.get("department_code", "")
        api_codes.add(code)

        try:
            existing = Department.query.filter_by(department_code=code).first()
            if existing is None:
                # Create new department.
                dept = Department(
                    department_code=code,
                    department_name=dept_data.get("department_name", code),
                )
                db.session.add(dept)
                stats["created"] += 1
            else:
                # Update if name changed.
                new_name = dept_data.get("department_name", existing.department_name)
                if existing.department_name != new_name or not existing.is_active:
                    existing.department_name = new_name
                    existing.is_active = True
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing department %s: %s", code, exc)
            stats["errors"] += 1

    # Deactivate departments not in the API response.
    active_local = Department.query.filter_by(is_active=True).all()
    for dept in active_local:
        if dept.department_code not in api_codes:
            dept.is_active = False
            dept.updated_at = datetime.now(timezone.utc)
            stats["deactivated"] += 1

    db.session.commit()
    return stats


def _sync_divisions(
    api_divisions: list[dict],
    user_id: int | None,
) -> dict:
    """Sync divisions from NeoGov API data."""
    stats = _new_stats()
    api_codes = set()

    for div_data in api_divisions:
        stats["processed"] += 1
        code = div_data.get("division_code", "")
        api_codes.add(code)

        try:
            # Look up the parent department by code.
            dept_code = div_data.get("department_code", "")
            department = Department.query.filter_by(department_code=dept_code).first()
            if department is None:
                logger.warning("Division %s: parent department %s not found", code, dept_code)
                stats["errors"] += 1
                continue

            existing = Division.query.filter_by(division_code=code).first()
            if existing is None:
                div = Division(
                    division_code=code,
                    division_name=div_data.get("division_name", code),
                    department_id=department.id,
                )
                db.session.add(div)
                stats["created"] += 1
            else:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing division %s: %s", code, exc)
            stats["errors"] += 1

    # Deactivate divisions not in the API response.
    active_local = Division.query.filter_by(is_active=True).all()
    for div in active_local:
        if div.division_code not in api_codes:
            div.is_active = False
            div.updated_at = datetime.now(timezone.utc)
            stats["deactivated"] += 1

    db.session.commit()
    return stats


def _sync_positions(
    api_positions: list[dict],
    user_id: int | None,
) -> dict:
    """Sync positions from NeoGov API data."""
    stats = _new_stats()
    api_codes = set()

    for pos_data in api_positions:
        stats["processed"] += 1
        code = pos_data.get("position_code", "")
        api_codes.add(code)

        try:
            div_code = pos_data.get("division_code", "")
            division = Division.query.filter_by(division_code=div_code).first()
            if division is None:
                logger.warning("Position %s: parent division %s not found", code, div_code)
                stats["errors"] += 1
                continue

            existing = Position.query.filter_by(position_code=code).first()
            auth_count = pos_data.get("authorized_count", 1)

            if existing is None:
                pos = Position(
                    position_code=code,
                    position_title=pos_data.get("position_title", code),
                    division_id=division.id,
                    authorized_count=auth_count,
                )
                db.session.add(pos)
                stats["created"] += 1
            else:
                new_title = pos_data.get("position_title", existing.position_title)
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing position %s: %s", code, exc)
            stats["errors"] += 1

    # Deactivate positions not in the API response.
    active_local = Position.query.filter_by(is_active=True).all()
    for pos in active_local:
        if pos.position_code not in api_codes:
            pos.is_active = False
            pos.updated_at = datetime.now(timezone.utc)
            stats["deactivated"] += 1

    db.session.commit()
    return stats


def _sync_employees(
    api_employees: list[dict],
    user_id: int | None,
) -> dict:
    """Sync employees from NeoGov API data."""
    stats = _new_stats()
    api_ids = set()

    for emp_data in api_employees:
        stats["processed"] += 1
        neogov_id = emp_data.get("employee_id", "")
        api_ids.add(neogov_id)

        try:
            pos_code = emp_data.get("position_code", "")
            position = Position.query.filter_by(position_code=pos_code).first()
            if position is None:
                logger.warning("Employee %s: position %s not found", neogov_id, pos_code)
                stats["errors"] += 1
                continue

            existing = Employee.query.filter_by(neogov_employee_id=neogov_id).first()

            if existing is None:
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
                changed = (
                    existing.first_name != emp_data.get("first_name", existing.first_name)
                    or existing.last_name != emp_data.get("last_name", existing.last_name)
                    or existing.position_id != position.id
                    or not existing.is_active
                )
                if changed:
                    existing.first_name = emp_data.get("first_name", existing.first_name)
                    existing.last_name = emp_data.get("last_name", existing.last_name)
                    existing.email = emp_data.get("email", existing.email)
                    existing.position_id = position.id
                    existing.is_active = True
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Error syncing employee %s: %s", neogov_id, exc)
            stats["errors"] += 1

    # Deactivate employees not in the API response.
    active_local = Employee.query.filter_by(is_active=True).all()
    for emp in active_local:
        if emp.neogov_employee_id not in api_ids:
            emp.is_active = False
            emp.updated_at = datetime.now(timezone.utc)
            stats["deactivated"] += 1

    db.session.commit()
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
    db.session.commit()
    return sync_log


def _complete_sync_log(sync_log: HRSyncLog, stats: dict) -> None:
    """Mark a sync log as completed with summary statistics."""
    sync_log.status = "completed"
    sync_log.completed_at = datetime.now(timezone.utc)
    # NOTE: Model currently uses BIT columns for these fields.
    # A migration should change them to INT for proper counts.
    # For now, set to True/1 if any records were affected.
    sync_log.records_processed = bool(stats["processed"])
    sync_log.records_created = bool(stats["created"])
    sync_log.records_updated = bool(stats["updated"])
    sync_log.records_deactivated = bool(stats["deactivated"])
    sync_log.records_errors = bool(stats["errors"])
    db.session.commit()


def _fail_sync_log(sync_log: HRSyncLog, error_message: str) -> None:
    """Mark a sync log as failed with an error message."""
    sync_log.status = "failed"
    sync_log.error_message = error_message[:4000]  # Truncate for NVARCHAR(MAX).
    sync_log.completed_at = datetime.now(timezone.utc)
    db.session.commit()


def get_sync_logs(page: int = 1, per_page: int = 20):
    """Return paginated sync log entries, most recent first."""
    return (
        HRSyncLog.query
        .order_by(HRSyncLog.started_at.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
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
