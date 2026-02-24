"""
Routes for the requirements blueprint — guided position equipment flow.

Flow: Select Position → Select Hardware → Select Software → Summary.

Restricted to admin, IT staff, and scoped managers.

Tier 1 UX Changes Applied:
    - select_software() now groups software by software_type (mirrors
      the hardware pattern) and passes software_types + items_by_type
      to the template instead of a flat software_products list.
    - Flash messages updated to use friendlier terminology.

Tier 2 UX Changes Applied:
    - select_hardware() and select_software() now pass usage_counts
      (hardware_id/software_id → position count) to display "Used by
      N positions" popularity indicators.  (#9)
    - copy_requirements() route: POST endpoint to copy all equipment
      and software from one position to another.  (#8)
    - htmx_positions_with_requirements(): Returns position <option>
      elements for only those positions that already have equipment
      configured — used by the "copy from" dropdown.  (#8)
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
    Step 1: Choose a position to configure equipment for.

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
          grouped by hardware type inside a Bootstrap accordion.
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
            flash("Hardware selections saved.", "success")
            return redirect(
                url_for(
                    "requirements.select_software",
                    position_id=position_id,
                )
            )
        except Exception:
            logger.exception(
                "Error saving hardware selections for position %d",
                position_id,
            )
            flash(
                "An error occurred while saving hardware selections. "
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

    # Tier 2 (#9): Fetch popularity counts for "Used by N positions".
    hw_usage_counts = requirement_service.get_hardware_usage_counts()

    return render_template(
        "requirements/select_hardware.html",
        position=position,
        hardware_types=all_hw_types,
        items_by_type=items_by_type,
        selected=selected,
        usage_counts=hw_usage_counts,
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

    GET:  Display current software requirements and available products
          grouped by software type inside a Bootstrap accordion.
    POST: Save software selections and advance to summary.

    Tier 1 Change: Software products are now grouped by software_type,
    mirroring the pattern already used on the hardware page.
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
            flash("Software selections saved.", "success")
            return redirect(
                url_for(
                    "requirements.position_summary",
                    position_id=position_id,
                )
            )
        except Exception:
            logger.exception(
                "Error saving software selections for position %d",
                position_id,
            )
            flash(
                "An error occurred while saving software selections. "
                "Please try again.",
                "danger",
            )

    # GET (or POST that failed): Load current requirements and products.
    current_sw = requirement_service.get_software_requirements(position_id)
    all_software = equipment_service.get_software_products()
    # Tier 1: Fetch software types for grouped accordion display.
    all_sw_types = equipment_service.get_software_types()

    selected = {
        req.software_id: {"quantity": req.quantity, "notes": req.notes}
        for req in current_sw
    }

    # Tier 1: Group software by type for display (mirrors hardware pattern).
    items_by_type = {}
    for sw in all_software:
        type_id = sw.software_type_id
        if type_id not in items_by_type:
            items_by_type[type_id] = []
        items_by_type[type_id].append(sw)

    # Tier 2 (#9): Fetch popularity counts for "Used by N positions".
    sw_usage_counts = requirement_service.get_software_usage_counts()

    return render_template(
        "requirements/select_software.html",
        position=position,
        software_types=all_sw_types,
        items_by_type=items_by_type,
        selected=selected,
        usage_counts=sw_usage_counts,
    )


# =========================================================================
# Step 4: Summary
# =========================================================================


@bp.route("/position/<int:position_id>/summary")
@login_required
@role_required("admin", "it_staff", "manager")
def position_summary(position_id):
    """
    Step 4: Display a summary of all equipment selections and costs.

    Shows the position's hardware and software requirements with
    calculated costs, IT review reassurance, and next-step actions.
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
# Tier 2: Copy From Another Position (#8)
# =========================================================================


@bp.route(
    "/position/<int:position_id>/copy-from/<int:source_id>",
    methods=["POST"],
)
@login_required
@role_required("admin", "it_staff", "manager")
def copy_requirements(position_id, source_id):
    """
    Copy all hardware and software requirements from a source position
    to the target position, then redirect to the equipment step so the
    user can review and customize.

    Both positions must be within the user's scope.
    """
    # Scope check: user must have access to *both* positions.
    if not organization_service.user_can_access_position(current_user, position_id):
        flash("You do not have access to the target position.", "warning")
        return redirect(url_for("requirements.select_position"))

    if not organization_service.user_can_access_position(current_user, source_id):
        flash("You do not have access to the source position.", "warning")
        return redirect(url_for("requirements.select_position"))

    try:
        requirement_service.copy_position_requirements(
            source_position_id=source_id,
            target_position_id=position_id,
            user_id=current_user.id,
        )
        flash("Equipment copied. Review and adjust below.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("requirements.select_hardware", position_id=position_id))


# =========================================================================
# Tier 2: HTMX Helper — Positions With Requirements (#8)
# =========================================================================


@bp.route("/htmx/positions-with-requirements/<int:division_id>")
@login_required
@role_required("admin", "it_staff", "manager")
def htmx_positions_with_requirements(division_id):
    """
    Return <option> elements for positions in a division that already
    have at least one hardware or software requirement configured.

    Used by the "copy from another position" dropdown on step 1.

    This uses a simple approach: fetch all positions in the division,
    then check each for existing requirements.  At typical division
    sizes (5–20 positions) the N+1 is negligible and avoids adding
    a custom query to the service layer.
    """
    positions = organization_service.get_positions_for_division(division_id)

    # Filter to only those with at least one requirement.
    positions_with_reqs = []
    for pos in positions:
        hw_count = len(requirement_service.get_hardware_requirements(pos.id))
        sw_count = len(requirement_service.get_software_requirements(pos.id))
        if hw_count > 0 or sw_count > 0:
            positions_with_reqs.append(
                {
                    "id": pos.id,
                    "title": pos.position_title,
                    "hw_count": hw_count,
                    "sw_count": sw_count,
                }
            )

    return render_template(
        "components/_copy_source_select.html",
        positions=positions_with_reqs,
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
        flash("Hardware item removed.", "info")
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
        flash("Software item removed.", "info")
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
