"""
Routes for the organization blueprint — departments, divisions, positions.

All routes are read-only (org data comes from NeoGov sync).  Includes
HTMX endpoints for dynamic dropdown population.
"""

from flask import render_template, request
from flask_login import current_user, login_required

from app.blueprints.organization import bp
from app.services import organization_service


@bp.route("/departments")
@login_required
def departments():
    """List all departments visible to the current user."""
    include_inactive = request.args.get("show_inactive", "0") == "1"
    dept_list = organization_service.get_departments(
        current_user, include_inactive=include_inactive
    )
    return render_template(
        "organization/departments.html",
        departments=dept_list,
        show_inactive=include_inactive,
    )


@bp.route("/department/<int:department_id>")
@login_required
def department_detail(department_id):
    """Show divisions and positions within a department."""
    department = organization_service.get_department_by_id(department_id)
    if department is None:
        return render_template("errors/404.html"), 404

    # Scope check.
    if not organization_service.user_can_access_department(current_user, department_id):
        return render_template("errors/403.html"), 403

    divisions = organization_service.get_divisions(
        current_user, department_id=department_id
    )
    return render_template(
        "organization/divisions.html",
        department=department,
        divisions=divisions,
    )


@bp.route("/division/<int:division_id>")
@login_required
def division_detail(division_id):
    """Show positions within a division."""
    division = organization_service.get_division_by_id(division_id)
    if division is None:
        return render_template("errors/404.html"), 404

    positions = organization_service.get_positions_for_division(division_id)

    # Compute headcount statistics.
    authorized = sum(p.authorized_count for p in positions)
    filled = organization_service.get_filled_count(division_id=division_id)

    return render_template(
        "organization/positions.html",
        division=division,
        department=division.department,
        positions=positions,
        authorized_count=authorized,
        filled_count=filled,
    )


# =========================================================================
# Flat list views — All Divisions / All Positions
# =========================================================================


@bp.route("/divisions")
@login_required
def all_divisions():
    """
    Flat list of all divisions visible to the current user.

    Supports optional filtering by department and an inactive toggle.
    Provides a single-page alternative to drilling through
    Departments → Department Detail.
    """
    include_inactive = request.args.get("show_inactive", "0") == "1"
    department_id = request.args.get("department_id", type=int)

    # Departments for the filter dropdown (scope-filtered).
    departments = organization_service.get_departments(current_user)

    # Divisions list (scope-filtered, with optional department filter).
    divisions = organization_service.get_divisions(
        current_user,
        department_id=department_id,
        include_inactive=include_inactive,
    )

    return render_template(
        "organization/all_divisions.html",
        divisions=divisions,
        departments=departments,
        selected_department_id=department_id,
        show_inactive=include_inactive,
    )


@bp.route("/positions")
@login_required
def all_positions():
    """
    Flat list of all positions visible to the current user.

    Supports optional filtering by department and/or division,
    plus an inactive toggle.  The division dropdown cascades from
    the department selection via the existing HTMX endpoint.
    """
    include_inactive = request.args.get("show_inactive", "0") == "1"
    department_id = request.args.get("department_id", type=int)
    division_id = request.args.get("division_id", type=int)

    # Departments for the filter dropdown (scope-filtered).
    departments = organization_service.get_departments(current_user)

    # Divisions for the filter dropdown — scoped to selected department
    # if one is active, otherwise show all visible divisions.
    divisions_for_dropdown = organization_service.get_divisions(
        current_user,
        department_id=department_id,
    )

    # Positions list (scope-filtered, with optional department/division filter).
    positions = organization_service.get_positions(
        current_user,
        department_id=department_id,
        division_id=division_id,
        include_inactive=include_inactive,
    )

    return render_template(
        "organization/all_positions.html",
        positions=positions,
        departments=departments,
        divisions_for_dropdown=divisions_for_dropdown,
        selected_department_id=department_id,
        selected_division_id=division_id,
        show_inactive=include_inactive,
    )


# =========================================================================
# HTMX partial endpoints for dynamic dropdowns
# =========================================================================


@bp.route("/htmx/divisions/<int:department_id>")
@login_required
def htmx_divisions(department_id):
    """Return division <option> elements for an HTMX-powered dropdown."""
    divisions = organization_service.get_divisions_for_department(department_id)
    return render_template(
        "components/_division_select.html",
        divisions=divisions,
    )


@bp.route("/htmx/positions/<int:division_id>")
@login_required
def htmx_positions(division_id):
    """Return position <option> elements for an HTMX-powered dropdown."""
    positions = organization_service.get_positions_for_division(division_id)
    return render_template(
        "components/_position_select.html",
        positions=positions,
    )
