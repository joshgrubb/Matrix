"""
Organization service — query departments, divisions, and positions.

All queries respect user scope: admins and IT staff see everything,
while managers and read-only users see only their scoped departments
or divisions.  This is the single enforcement point for scope-based
access control on organizational data.
"""

import logging

from sqlalchemy import func

from app.extensions import db
from app.models.organization import Department, Division, Employee, Position
from app.models.user import User

logger = logging.getLogger(__name__)


# -- Department queries ----------------------------------------------------


def get_departments(user: User, include_inactive: bool = False):
    """
    Return departments visible to the given user.

    Args:
        user:             The current user (scope determines visibility).
        include_inactive: If True, include soft-deleted departments.

    Returns:
        List of Department records ordered by name.
    """
    query = Department.query.order_by(Department.department_name)

    if not include_inactive:
        query = query.filter(Department.is_active == True)

    # Scope filtering: org-wide users see everything.
    if not user.has_org_scope():
        dept_ids = user.scoped_department_ids()
        div_ids = user.scoped_division_ids()

        if div_ids:
            # If user has division-level scopes, include the parent departments.
            div_dept_ids = (
                db.session.query(Division.department_id)
                .filter(Division.id.in_(div_ids))
                .all()
            )
            dept_ids = list(set(dept_ids + [r[0] for r in div_dept_ids]))

        query = query.filter(Department.id.in_(dept_ids))

    return query.all()


def get_department_by_id(department_id: int) -> Department | None:
    """Return a department by primary key, or None if not found."""
    return db.session.get(Department, department_id)


# -- Division queries ------------------------------------------------------


def get_divisions(
    user: User,
    department_id: int | None = None,
    include_inactive: bool = False,
):
    """
    Return divisions visible to the given user.

    Args:
        user:             The current user.
        department_id:    Optional filter to a single department.
        include_inactive: If True, include soft-deleted divisions.

    Returns:
        List of Division records ordered by name.
    """
    query = Division.query.order_by(Division.division_name)

    if not include_inactive:
        query = query.filter(Division.is_active == True)

    # Filter to a specific department if requested.
    if department_id is not None:
        query = query.filter(Division.department_id == department_id)

    # Scope filtering.
    if not user.has_org_scope():
        dept_ids = user.scoped_department_ids()
        div_ids = user.scoped_division_ids()

        if dept_ids and div_ids:
            # User has both department and division scopes.
            query = query.filter(
                db.or_(
                    Division.department_id.in_(dept_ids),
                    Division.id.in_(div_ids),
                )
            )
        elif dept_ids:
            query = query.filter(Division.department_id.in_(dept_ids))
        elif div_ids:
            query = query.filter(Division.id.in_(div_ids))

    return query.all()


def get_divisions_for_department(department_id: int):
    """
    Return active divisions for a department (no scope filtering).

    Used by HTMX dynamic dropdowns where the user has already been
    authorized to view the parent department.
    """
    return (
        Division.query.filter_by(department_id=department_id, is_active=True)
        .order_by(Division.division_name)
        .all()
    )


def get_division_by_id(division_id: int) -> Division | None:
    """Return a division by primary key, or None if not found."""
    return db.session.get(Division, division_id)


# -- Position queries ------------------------------------------------------


def get_positions(
    user: User,
    division_id: int | None = None,
    department_id: int | None = None,
    include_inactive: bool = False,
):
    """
    Return positions visible to the given user.

    Args:
        user:             The current user.
        division_id:      Optional filter to a single division.
        department_id:    Optional filter to a single department
                          (returns positions across all divisions in it).
        include_inactive: If True, include soft-deleted positions.

    Returns:
        List of Position records ordered by title.
    """
    query = Position.query.order_by(Position.position_title)

    if not include_inactive:
        query = query.filter(Position.is_active == True)

    # Filter by division or department.
    if division_id is not None:
        query = query.filter(Position.division_id == division_id)
    elif department_id is not None:
        # Join to divisions to filter by department.
        query = query.join(Division).filter(Division.department_id == department_id)

    # Scope filtering.
    if not user.has_org_scope():
        dept_ids = user.scoped_department_ids()
        div_ids = user.scoped_division_ids()

        if dept_ids or div_ids:
            query = query.join(Division, Position.division_id == Division.id)
            conditions = []
            if dept_ids:
                conditions.append(Division.department_id.in_(dept_ids))
            if div_ids:
                conditions.append(Division.id.in_(div_ids))
            query = query.filter(db.or_(*conditions))

    return query.all()


def get_positions_for_division(division_id: int):
    """
    Return active positions for a division (no scope filtering).

    Used by HTMX dynamic dropdowns where the user has already been
    authorized to view the parent division.
    """
    return (
        Position.query.filter_by(division_id=division_id, is_active=True)
        .order_by(Position.position_title)
        .all()
    )


def get_position_by_id(position_id: int) -> Position | None:
    """Return a position by primary key, or None if not found."""
    return db.session.get(Position, position_id)


# -- Aggregate helpers -----------------------------------------------------


def get_total_authorized_count(
    department_id: int | None = None,
    division_id: int | None = None,
) -> int:
    """
    Return the total authorized headcount across active positions.

    Args:
        department_id: Limit to positions in this department.
        division_id:   Limit to positions in this division.

    Returns:
        Integer sum of authorized_count for matching positions.
    """
    query = db.session.query(
        func.coalesce(func.sum(Position.authorized_count), 0)
    ).filter(Position.is_active == True)

    if division_id is not None:
        query = query.filter(Position.division_id == division_id)
    elif department_id is not None:
        query = query.join(Division).filter(Division.department_id == department_id)

    return query.scalar()


def get_filled_count(
    department_id: int | None = None,
    division_id: int | None = None,
) -> int:
    """
    Return the count of active employees across matching positions.

    Args:
        department_id: Limit to employees in this department.
        division_id:   Limit to employees in this division.

    Returns:
        Integer count of active employees.
    """
    query = (
        db.session.query(func.count(Employee.id))
        .join(Position)
        .filter(Employee.is_active == True, Position.is_active == True)
    )

    if division_id is not None:
        query = query.filter(Position.division_id == division_id)
    elif department_id is not None:
        query = query.join(Division).filter(Division.department_id == department_id)

    return query.scalar()


# -- Scope authorization helpers -------------------------------------------


def user_can_access_department(user: User, department_id: int) -> bool:
    """Check whether a user's scope allows access to a department."""
    if user.has_org_scope():
        return True
    if department_id in user.scoped_department_ids():
        return True
    # Check if user has division-level scope in this department.
    div_ids = user.scoped_division_ids()
    if div_ids:
        match = Division.query.filter(
            Division.id.in_(div_ids), Division.department_id == department_id
        ).first()
        return match is not None
    return False


def user_can_access_position(user: User, position_id: int) -> bool:
    """Check whether a user's scope allows access to a position."""
    if user.has_org_scope():
        return True

    position = get_position_by_id(position_id)
    if position is None:
        return False

    division = position.division
    dept_ids = user.scoped_department_ids()
    div_ids = user.scoped_division_ids()

    if division.department_id in dept_ids:
        return True
    if division.id in div_ids:
        return True

    return False
