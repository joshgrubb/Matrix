"""
Routes for the requirements blueprint — guided position requirement flow.

Flow: Select Position → Select Hardware → Select Software → Summary.

Restricted to admin, IT staff, and scoped managers.
"""

import logging

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.requirements import bp
from app.decorators import role_required
from app.services import (
    cost_service,
    equipment_service,
    organization_service,
    requirement_service,
)

logger = logging.getLogger(__name__)


# =========================================================================
# Step 1: Select Position
# =========================================================================


@bp.route("/")
@login_required
@role_required("admin", "it_staff", "manager")
def select_position():
    """
    Step 1: Choose a position to configure requirements for.

    Displays department → division → position cascading dropdowns
    powered by HTMX.
    """
    departments = organization_service.get_departments(current_user)
    return render_template(
        "requirements/select_position.html",
        departments=departments,
    )


# =========================================================================
# Step 2: Select Hardware
# =========================================================================


@bp.route("/position/<int:position_id>/hardware", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff", "manager")
def select_hardware(position_id):
    """
    Step 2: Select hardware items for the position.

    GET:  Display current hardware requirements and available items
          grouped by hardware type.
    POST: Save hardware selections and advance to software step.
    """
    # Scope check.
    if not organization_service.user_can_access_position(current_user, position_id):
        flash("You do not have access to this position.", "warning")
        return redirect(url_for("requirements.select_position"))

    position = organization_service.get_position_by_id(position_id)
    if position is None:
        flash("Position not found.", "warning")
        return redirect(url_for("requirements.select_position"))

    if request.method == "POST":
        # Parse submitted hardware selections.
        items = _parse_hardware_form(request.form)

        # Wrap the service call in try/except so database errors
        # produce a user-visible flash message instead of a bare 500.
        try:
            requirement_service.set_position_hardware(
                position_id=position_id,
                items=items,
                user_id=current_user.id,
            )
            flash("Hardware requirements saved.", "success")
            return redirect(
                url_for(
                    "requirements.select_software",
                    position_id=position_id,
                )
            )
        except Exception:
            logger.exception(
                "Error saving hardware requirements for position %d",
                position_id,
            )
            flash(
                "An error occurred while saving hardware requirements. "
                "Please try again.",
                "danger",
            )

    # GET (or POST that failed): Load current requirements and items.
    current_hw = requirement_service.get_hardware_requirements(position_id)
    all_hw_items = equipment_service.get_hardware_items()
    all_hw_types = equipment_service.get_hardware_types()

    # Build a dict of current selections for template pre-population.
    # Keyed by hardware_id (not hardware_type_id).
    selected = {
        req.hardware_id: {"quantity": req.quantity, "notes": req.notes}
        for req in current_hw
    }

    # Group hardware items by type for display.
    items_by_type = {}
    for hw_item in all_hw_items:
        type_id = hw_item.hardware_type_id
        if type_id not in items_by_type:
            items_by_type[type_id] = []
        items_by_type[type_id].append(hw_item)

    return render_template(
        "requirements/select_hardware.html",
        position=position,
        hardware_types=all_hw_types,
        items_by_type=items_by_type,
        selected=selected,
    )


# =========================================================================
# Step 3: Select Software
# =========================================================================


@bp.route("/position/<int:position_id>/software", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff", "manager")
def select_software(position_id):
    """
    Step 3: Select software products for the position.

    GET:  Display current software requirements and available products.
    POST: Save software selections and advance to summary.
    """
    if not organization_service.user_can_access_position(current_user, position_id):
        flash("You do not have access to this position.", "warning")
        return redirect(url_for("requirements.select_position"))

    position = organization_service.get_position_by_id(position_id)
    if position is None:
        flash("Position not found.", "warning")
        return redirect(url_for("requirements.select_position"))

    if request.method == "POST":
        items = _parse_software_form(request.form)

        try:
            requirement_service.set_position_software(
                position_id=position_id,
                items=items,
                user_id=current_user.id,
            )
            flash("Software requirements saved.", "success")
            return redirect(
                url_for(
                    "requirements.position_summary",
                    position_id=position_id,
                )
            )
        except Exception:
            logger.exception(
                "Error saving software requirements for position %d",
                position_id,
            )
            flash(
                "An error occurred while saving software requirements. "
                "Please try again.",
                "danger",
            )

    # GET (or POST that failed): Load current requirements and products.
    current_sw = requirement_service.get_software_requirements(position_id)
    all_software = equipment_service.get_software_products()

    selected = {
        req.software_id: {"quantity": req.quantity, "notes": req.notes}
        for req in current_sw
    }

    return render_template(
        "requirements/select_software.html",
        position=position,
        software_products=all_software,
        selected=selected,
    )


# =========================================================================
# Step 4: Summary
# =========================================================================


@bp.route("/position/<int:position_id>/summary")
@login_required
@role_required("admin", "it_staff", "manager")
def position_summary(position_id):
    """
    Step 4: Display a summary of all requirements and costs.

    Shows the position's hardware and software requirements with
    calculated costs.
    """
    if not organization_service.user_can_access_position(current_user, position_id):
        flash("You do not have access to this position.", "warning")
        return redirect(url_for("requirements.select_position"))

    position = organization_service.get_position_by_id(position_id)
    if position is None:
        flash("Position not found.", "warning")
        return redirect(url_for("requirements.select_position"))

    # Calculate costs using the cost service.
    cost_summary = cost_service.calculate_position_cost(position_id)

    return render_template(
        "requirements/position_summary.html",
        position=position,
        cost_summary=cost_summary,
    )


# =========================================================================
# Individual requirement CRUD (HTMX endpoints)
# =========================================================================


@bp.route("/hardware/<int:req_id>/remove", methods=["POST"])
@login_required
@role_required("admin", "it_staff", "manager")
def remove_hardware(req_id):
    """Remove a single hardware requirement via HTMX."""
    try:
        requirement_service.remove_hardware_requirement(
            requirement_id=req_id,
            user_id=current_user.id,
        )
        flash("Hardware requirement removed.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    # Return to the referring page.
    return redirect(request.referrer or url_for("requirements.select_position"))


@bp.route("/software/<int:req_id>/remove", methods=["POST"])
@login_required
@role_required("admin", "it_staff", "manager")
def remove_software(req_id):
    """Remove a single software requirement via HTMX."""
    try:
        requirement_service.remove_software_requirement(
            requirement_id=req_id,
            user_id=current_user.id,
        )
        flash("Software requirement removed.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("requirements.select_position"))


# =========================================================================
# Form parsing helpers
# =========================================================================


def _parse_hardware_form(form) -> list[dict]:
    """
    Parse hardware selections from the form.

    Form fields follow the pattern:
        hw_<hardware_id>_selected = 'on'
        hw_<hardware_id>_quantity = '2'
        hw_<hardware_id>_notes = 'Optional note'

    NOTE: This now parses hardware_id (specific item), not hardware_type_id.
    """
    items = []
    for key in form:
        if key.endswith("_selected") and key.startswith("hw_"):
            hw_id_str = key.replace("hw_", "").replace("_selected", "")
            try:
                hardware_id = int(hw_id_str)
            except ValueError:
                continue

            quantity = form.get(f"hw_{hardware_id}_quantity", "1")
            notes = form.get(f"hw_{hardware_id}_notes", "").strip() or None

            try:
                quantity = max(1, int(quantity))
            except ValueError:
                quantity = 1

            items.append(
                {
                    "hardware_id": hardware_id,
                    "quantity": quantity,
                    "notes": notes,
                }
            )

    logger.debug("Parsed %d hardware items from form", len(items))
    return items


def _parse_software_form(form) -> list[dict]:
    """
    Parse software selections from the form.

    Form fields follow the pattern:
        sw_<software_id>_selected = 'on'
        sw_<software_id>_quantity = '1'
        sw_<software_id>_notes = 'Optional note'
    """
    items = []
    for key in form:
        if key.endswith("_selected") and key.startswith("sw_"):
            sw_id_str = key.replace("sw_", "").replace("_selected", "")
            try:
                sw_id = int(sw_id_str)
            except ValueError:
                continue

            quantity = form.get(f"sw_{sw_id}_quantity", "1")
            notes = form.get(f"sw_{sw_id}_notes", "").strip() or None

            try:
                quantity = max(1, int(quantity))
            except ValueError:
                quantity = 1

            items.append(
                {
                    "software_id": sw_id,
                    "quantity": quantity,
                    "notes": notes,
                }
            )

    logger.debug("Parsed %d software items from form", len(items))
    return items
