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
