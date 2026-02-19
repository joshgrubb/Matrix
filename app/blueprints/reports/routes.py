"""
Routes for the reports blueprint — cost summaries and data exports.

Provides department-level and position-level cost views with
CSV and Excel export capabilities.
"""

from flask import make_response, render_template, request
from flask_login import current_user, login_required

from app.blueprints.reports import bp
from app.services import cost_service, export_service, organization_service


@bp.route("/cost-summary")
@login_required
def cost_summary():
    """
    Display department-level cost summaries.

    Shows a table of all departments the user can access with
    hardware, software, and total cost columns.
    """
    dept_summaries = cost_service.get_department_cost_breakdown(
        user=current_user
    )
    org_summary = cost_service.calculate_organization_costs()

    return render_template(
        "reports/cost_summary.html",
        department_summaries=dept_summaries,
        org_summary=org_summary,
    )


@bp.route("/equipment-report")
@login_required
def equipment_report():
    """
    Display position-level cost and equipment details.

    Allows filtering by department and division.  Shows per-position
    hardware/software costs and requirements.
    """
    department_id = request.args.get("department_id", type=int)
    division_id = request.args.get("division_id", type=int)

    # Get departments for the filter dropdown.
    departments = organization_service.get_departments(current_user)

    # Build list of position cost summaries.
    positions = organization_service.get_positions(
        current_user,
        department_id=department_id,
        division_id=division_id,
    )

    position_summaries = []
    for pos in positions:
        try:
            summary = cost_service.calculate_position_cost(pos.id)
            position_summaries.append(summary)
        except ValueError:
            continue

    return render_template(
        "reports/equipment_report.html",
        departments=departments,
        position_summaries=position_summaries,
        selected_department_id=department_id,
        selected_division_id=division_id,
    )


# =========================================================================
# Export endpoints
# =========================================================================

@bp.route("/export/department-costs/<fmt>")
@login_required
def export_department_costs(fmt):
    """
    Export department cost summaries as CSV or Excel.

    Args:
        fmt: Export format — 'csv' or 'xlsx'.
    """
    dept_summaries = cost_service.get_department_cost_breakdown(
        user=current_user
    )

    if fmt == "xlsx":
        buffer = export_service.export_department_costs_excel(dept_summaries)
        response = make_response(buffer.read())
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response.headers["Content-Disposition"] = (
            "attachment; filename=department_costs.xlsx"
        )
    else:
        buffer = export_service.export_department_costs_csv(dept_summaries)
        response = make_response(buffer.read())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = (
            "attachment; filename=department_costs.csv"
        )

    return response


@bp.route("/export/position-costs/<fmt>")
@login_required
def export_position_costs(fmt):
    """
    Export position-level cost details as CSV or Excel.

    Respects the same filters as the equipment report page.
    """
    department_id = request.args.get("department_id", type=int)
    division_id = request.args.get("division_id", type=int)

    positions = organization_service.get_positions(
        current_user,
        department_id=department_id,
        division_id=division_id,
    )

    position_summaries = []
    for pos in positions:
        try:
            summary = cost_service.calculate_position_cost(pos.id)
            position_summaries.append(summary)
        except ValueError:
            continue

    if fmt == "xlsx":
        buffer = export_service.export_position_costs_excel(position_summaries)
        response = make_response(buffer.read())
        response.headers["Content-Type"] = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response.headers["Content-Disposition"] = (
            "attachment; filename=position_costs.xlsx"
        )
    else:
        buffer = export_service.export_position_costs_csv(position_summaries)
        response = make_response(buffer.read())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = (
            "attachment; filename=position_costs.csv"
        )

    return response
