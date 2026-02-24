"""
Routes for the equipment blueprint — hardware and software catalog CRUD.

Restricted to admin and IT staff roles.  All changes are audit-logged
by the equipment service.
"""

from decimal import Decimal, InvalidOperation
import logging
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.equipment import bp
from app.decorators import role_required
from app.services import equipment_service
from app.models.organization import Department, Division, Position

logger = logging.getLogger(__name__)

# =========================================================================
# Hardware Types (categories)
# =========================================================================


@bp.route("/hardware-types")
@login_required
def hardware_type_list():
    """List all hardware type categories in the catalog."""
    include_inactive = request.args.get("show_inactive", "0") == "1"
    hw_types = equipment_service.get_hardware_types(include_inactive=include_inactive)
    return render_template(
        "equipment/hardware_type_list.html",
        hardware_types=hw_types,
        show_inactive=include_inactive,
    )


@bp.route("/hardware-types/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def hardware_type_create():
    """Create a new hardware type category."""
    if request.method == "POST":
        type_name = request.form.get("type_name", "").strip()
        description = request.form.get("description", "").strip() or None
        cost_str = request.form.get("estimated_cost", "0")

        # Validate input.
        errors = []
        if not type_name:
            errors.append("Type name is required.")
        try:
            estimated_cost = Decimal(cost_str)
        except (InvalidOperation, ValueError):
            errors.append("Estimated cost must be a valid number.")
            estimated_cost = Decimal("0")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "equipment/hardware_type_form.html",
                mode="create",
                hw_type=None,
                form_data=request.form,
            )

        try:
            equipment_service.create_hardware_type(
                type_name=type_name,
                estimated_cost=estimated_cost,
                description=description,
                user_id=current_user.id,
            )
            flash(f"Hardware type '{type_name}' created.", "success")
            return redirect(url_for("equipment.hardware_type_list"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flash(f"Error creating hardware type: {exc}", "danger")

    return render_template(
        "equipment/hardware_type_form.html",
        mode="create",
        hw_type=None,
        form_data={},
    )


@bp.route("/hardware-types/<int:hw_type_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def hardware_type_edit(hw_type_id):
    """Edit an existing hardware type category."""
    hw_type = equipment_service.get_hardware_type_by_id(hw_type_id)
    if hw_type is None:
        flash("Hardware type not found.", "warning")
        return redirect(url_for("equipment.hardware_type_list"))

    if request.method == "POST":
        type_name = request.form.get("type_name", "").strip()
        description = request.form.get("description", "").strip() or None
        cost_str = request.form.get("estimated_cost", "0")

        try:
            estimated_cost = Decimal(cost_str)
        except (InvalidOperation, ValueError):
            flash("Estimated cost must be a valid number.", "danger")
            return render_template(
                "equipment/hardware_type_form.html",
                mode="edit",
                hw_type=hw_type,
                form_data=request.form,
            )

        try:
            equipment_service.update_hardware_type(
                hw_type_id=hw_type_id,
                type_name=type_name or None,
                estimated_cost=estimated_cost,
                description=description,
                user_id=current_user.id,
            )
            flash(f"Hardware type '{hw_type.type_name}' updated.", "success")
            return redirect(url_for("equipment.hardware_type_list"))
        except ValueError as exc:
            flash(str(exc), "danger")

    return render_template(
        "equipment/hardware_type_form.html",
        mode="edit",
        hw_type=hw_type,
        form_data={},
    )


@bp.route("/hardware-types/<int:hw_type_id>/deactivate", methods=["POST"])
@login_required
@role_required("admin", "it_staff")
def hardware_type_deactivate(hw_type_id):
    """Soft-delete a hardware type category."""
    try:
        equipment_service.deactivate_hardware_type(
            hw_type_id=hw_type_id,
            user_id=current_user.id,
        )
        flash("Hardware type deactivated.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("equipment.hardware_type_list"))


# =========================================================================
# Hardware Items (specific products within a type)
# =========================================================================


@bp.route("/hardware")
@login_required
def hardware_list():
    """List all hardware items in the catalog."""
    include_inactive = request.args.get("show_inactive", "0") == "1"
    hw_type_filter = request.args.get("hardware_type_id", type=int)

    hw_items = equipment_service.get_hardware_items(
        include_inactive=include_inactive,
        hardware_type_id=hw_type_filter,
    )
    hw_types = equipment_service.get_hardware_types()

    return render_template(
        "equipment/hardware_list.html",
        hardware_items=hw_items,
        hardware_types=hw_types,
        show_inactive=include_inactive,
        selected_type_id=hw_type_filter,
    )


@bp.route("/hardware/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def hardware_create():
    """Create a new hardware item."""
    hw_types = equipment_service.get_hardware_types()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        hw_type_id = request.form.get("hardware_type_id", type=int)
        description = request.form.get("description", "").strip() or None
        cost_str = request.form.get("estimated_cost", "0")

        # Validate input.
        errors = []
        if not name:
            errors.append("Hardware name is required.")
        if not hw_type_id:
            errors.append("Hardware type is required.")
        try:
            estimated_cost = Decimal(cost_str)
        except (InvalidOperation, ValueError):
            errors.append("Estimated cost must be a valid number.")
            estimated_cost = Decimal("0")

        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template(
                "equipment/hardware_form.html",
                mode="create",
                hardware=None,
                hardware_types=hw_types,
                form_data=request.form,
            )

        try:
            equipment_service.create_hardware(
                name=name,
                hardware_type_id=hw_type_id,
                estimated_cost=estimated_cost,
                description=description,
                user_id=current_user.id,
            )
            flash(f"Hardware '{name}' created.", "success")
            return redirect(url_for("equipment.hardware_list"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flash(f"Error creating hardware: {exc}", "danger")

    return render_template(
        "equipment/hardware_form.html",
        mode="create",
        hardware=None,
        hardware_types=hw_types,
        form_data={},
    )


@bp.route("/hardware/<int:hardware_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def hardware_edit(hardware_id):
    """Edit an existing hardware item."""
    hw = equipment_service.get_hardware_by_id(hardware_id)
    if hw is None:
        flash("Hardware item not found.", "warning")
        return redirect(url_for("equipment.hardware_list"))

    hw_types = equipment_service.get_hardware_types()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        hw_type_id = request.form.get("hardware_type_id", type=int)
        description = request.form.get("description", "").strip() or None
        cost_str = request.form.get("estimated_cost", "0")

        try:
            estimated_cost = Decimal(cost_str)
        except (InvalidOperation, ValueError):
            flash("Estimated cost must be a valid number.", "danger")
            return render_template(
                "equipment/hardware_form.html",
                mode="edit",
                hardware=hw,
                hardware_types=hw_types,
                form_data=request.form,
            )

        try:
            equipment_service.update_hardware(
                hardware_id=hardware_id,
                name=name or None,
                hardware_type_id=hw_type_id,
                estimated_cost=estimated_cost,
                description=description,
                user_id=current_user.id,
            )
            flash(f"Hardware '{hw.name}' updated.", "success")
            return redirect(url_for("equipment.hardware_list"))
        except ValueError as exc:
            flash(str(exc), "danger")

    return render_template(
        "equipment/hardware_form.html",
        mode="edit",
        hardware=hw,
        hardware_types=hw_types,
        form_data={},
    )


@bp.route("/hardware/<int:hardware_id>/deactivate", methods=["POST"])
@login_required
@role_required("admin", "it_staff")
def hardware_deactivate(hardware_id):
    """Soft-delete a hardware item."""
    try:
        equipment_service.deactivate_hardware(
            hardware_id=hardware_id,
            user_id=current_user.id,
        )
        flash("Hardware item deactivated.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("equipment.hardware_list"))


# =========================================================================
# Software Products
# =========================================================================


@bp.route("/software")
@login_required
def software_list():
    """List all software products in the catalog."""
    include_inactive = request.args.get("show_inactive", "0") == "1"
    sw_type_filter = request.args.get("software_type_id", type=int)

    sw_products = equipment_service.get_software_products(
        include_inactive=include_inactive,
        software_type_id=sw_type_filter,
    )
    sw_types = equipment_service.get_software_types()

    # Build coverage summaries for the list view.
    coverage_summaries = {}
    for sw in sw_products:
        coverage_summaries[sw.id] = equipment_service.get_coverage_summary(sw)

    return render_template(
        "equipment/software_list.html",
        software_products=sw_products,
        software_types=sw_types,
        show_inactive=include_inactive,
        selected_type_id=sw_type_filter,
        coverage_summaries=coverage_summaries,
    )


@bp.route("/software/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_create():
    """Create a new software product with optional coverage definitions."""
    sw_types = equipment_service.get_software_types()
    sw_families = equipment_service.get_software_families()

    # Fetch org structure for coverage scope selectors.
    departments = (
        Department.query.filter_by(is_active=True)
        .order_by(Department.department_name)
        .all()
    )
    divisions = (
        Division.query.filter_by(is_active=True).order_by(Division.division_name).all()
    )
    positions = (
        Position.query.filter_by(is_active=True).order_by(Position.position_title).all()
    )

    # Serialize org data for the JavaScript coverage selectors.
    departments_json, divisions_json, positions_json = _build_coverage_json(
        departments, divisions, positions
    )

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        sw_type_id = request.form.get("software_type_id", type=int)
        license_model = request.form.get("license_model", "per_user")
        license_tier = request.form.get("license_tier", "").strip() or None
        family_id = request.form.get("software_family_id", type=int) or None
        description = request.form.get("description", "").strip() or None

        # Parse cost fields.
        cost_per_license = _parse_decimal(request.form.get("cost_per_license"))
        total_cost = _parse_decimal(request.form.get("total_cost"))

        if not name:
            flash("Software name is required.", "danger")
            return render_template(
                "equipment/software_form.html",
                mode="create",
                software=None,
                software_types=sw_types,
                software_families=sw_families,
                departments_json=departments_json,
                divisions_json=divisions_json,
                positions_json=positions_json,
                existing_coverage_json=[],
                form_data=request.form,
            )

        try:
            sw = equipment_service.create_software(
                name=name,
                software_type_id=sw_type_id,
                license_model=license_model,
                cost_per_license=cost_per_license,
                total_cost=total_cost,
                license_tier=license_tier,
                software_family_id=family_id,
                description=description,
                user_id=current_user.id,
            )

            # Save coverage rows if the license model is tenant.
            if license_model == "tenant":
                coverage_rows = _parse_coverage_form(request.form)
                if coverage_rows:
                    equipment_service.set_software_coverage(
                        software_id=sw.id,
                        coverage_rows=coverage_rows,
                        user_id=current_user.id,
                    )

            flash(f"Software '{name}' created.", "success")
            return redirect(url_for("equipment.software_list"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flash(f"Error creating software: {exc}", "danger")

    return render_template(
        "equipment/software_form.html",
        mode="create",
        software=None,
        software_types=sw_types,
        software_families=sw_families,
        departments_json=departments_json,
        divisions_json=divisions_json,
        positions_json=positions_json,
        existing_coverage_json=[],
        form_data={},
    )


@bp.route("/software/<int:software_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_edit(software_id):
    """Edit an existing software product and its coverage definitions."""
    sw = equipment_service.get_software_by_id(software_id)
    if sw is None:
        flash("Software product not found.", "warning")
        return redirect(url_for("equipment.software_list"))

    sw_types = equipment_service.get_software_types()
    sw_families = equipment_service.get_software_families()

    # Fetch org structure for coverage scope selectors.
    departments = (
        Department.query.filter_by(is_active=True)
        .order_by(Department.department_name)
        .all()
    )
    divisions = (
        Division.query.filter_by(is_active=True).order_by(Division.division_name).all()
    )
    positions = (
        Position.query.filter_by(is_active=True).order_by(Position.position_title).all()
    )

    # Serialize org data for the JavaScript coverage selectors.
    departments_json, divisions_json, positions_json = _build_coverage_json(
        departments, divisions, positions
    )

    # Serialize existing coverage rows for pre-population.
    existing_coverage_json = [
        {
            "scope_type": cov.scope_type,
            "department_id": cov.department_id,
            "division_id": cov.division_id,
            "position_id": cov.position_id,
        }
        for cov in (sw.coverage or [])
    ]

    if request.method == "POST":
        kwargs = {
            "name": request.form.get("name", "").strip(),
            "software_type_id": request.form.get("software_type_id", type=int),
            "license_model": request.form.get("license_model", "per_user"),
            "license_tier": request.form.get("license_tier", "").strip() or None,
            "software_family_id": (
                request.form.get("software_family_id", type=int) or None
            ),
            "description": request.form.get("description", "").strip() or None,
            "cost_per_license": _parse_decimal(request.form.get("cost_per_license")),
            "total_cost": _parse_decimal(request.form.get("total_cost")),
        }

        try:
            equipment_service.update_software(
                software_id=software_id,
                user_id=current_user.id,
                **kwargs,
            )

            # Update coverage rows.
            license_model = kwargs.get("license_model", "per_user")
            if license_model == "tenant":
                coverage_rows = _parse_coverage_form(request.form)
                equipment_service.set_software_coverage(
                    software_id=software_id,
                    coverage_rows=coverage_rows,
                    user_id=current_user.id,
                )
            else:
                # Clear any leftover coverage if model switched to per_user.
                equipment_service.set_software_coverage(
                    software_id=software_id,
                    coverage_rows=[],
                    user_id=current_user.id,
                )

            flash(f"Software '{sw.name}' updated.", "success")
            return redirect(url_for("equipment.software_list"))
        except ValueError as exc:
            flash(str(exc), "danger")

    return render_template(
        "equipment/software_form.html",
        mode="edit",
        software=sw,
        software_types=sw_types,
        software_families=sw_families,
        departments_json=departments_json,
        divisions_json=divisions_json,
        positions_json=positions_json,
        existing_coverage_json=existing_coverage_json,
        form_data={},
    )


@bp.route("/software/<int:software_id>/deactivate", methods=["POST"])
@login_required
@role_required("admin", "it_staff")
def software_deactivate(software_id):
    """Soft-delete a software product."""
    try:
        equipment_service.deactivate_software(
            software_id=software_id,
            user_id=current_user.id,
        )
        flash("Software product deactivated.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("equipment.software_list"))


# =========================================================================
# Coverage helpers
# =========================================================================


def _build_coverage_json(
    departments: list,
    divisions: list,
    positions: list,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Serialize org structure into plain dicts for safe JSON embedding.

    SQLAlchemy model objects cannot be directly serialized with
    ``tojson``.  This helper extracts only the fields the JavaScript
    coverage selectors need, as plain Python dicts.

    Args:
        departments: List of Department model instances.
        divisions:   List of Division model instances.
        positions:   List of Position model instances.

    Returns:
        Tuple of (departments_json, divisions_json, positions_json)
        where each element is a list of dicts ready for ``tojson``.
    """
    departments_json = [{"id": d.id, "name": d.department_name} for d in departments]
    divisions_json = [
        {
            "id": d.id,
            "name": d.division_name,
            "department_id": d.department_id,
        }
        for d in divisions
    ]
    positions_json = [
        {
            "id": p.id,
            "title": p.position_title,
            "division_id": p.division_id,
        }
        for p in positions
    ]
    return departments_json, divisions_json, positions_json


def _parse_coverage_form(form) -> list[dict]:
    """
    Parse software coverage rows from the form.

    The form uses a dynamic row pattern with indexed field names:
        coverage_scope_type_0    = 'organization'
        coverage_department_id_0 = ''  (unused for org scope)
        coverage_division_id_0   = ''
        coverage_position_id_0   = ''

        coverage_scope_type_1    = 'department'
        coverage_department_id_1 = '5'
        ...

    Args:
        form: The Flask request.form MultiDict.

    Returns:
        List of dicts suitable for ``equipment_service.set_software_coverage``.
    """
    rows = []
    index = 0

    while True:
        scope_key = f"coverage_scope_type_{index}"
        scope_type = form.get(scope_key, "").strip().lower()

        # No more rows once we hit a missing index.
        if not scope_type:
            break

        row = {"scope_type": scope_type}

        if scope_type == "department":
            dept_id = form.get(f"coverage_department_id_{index}", type=int)
            row["department_id"] = dept_id
        elif scope_type == "division":
            div_id = form.get(f"coverage_division_id_{index}", type=int)
            row["division_id"] = div_id
        elif scope_type == "position":
            pos_id = form.get(f"coverage_position_id_{index}", type=int)
            row["position_id"] = pos_id
        # 'organization' scope requires no additional FK.

        rows.append(row)
        index += 1

    logger.debug("Parsed %d coverage rows from form", len(rows))
    return rows


# =========================================================================
# Coverage form parsing helper
# =========================================================================


def _parse_coverage_form(form) -> list[dict]:
    """
    Parse software coverage rows from the form.

    The form uses a dynamic row pattern with indexed field names:
        coverage_scope_type_0   = 'organization'
        coverage_department_id_0 = ''  (unused for org scope)
        coverage_division_id_0   = ''
        coverage_position_id_0   = ''

        coverage_scope_type_1   = 'department'
        coverage_department_id_1 = '5'
        ...

    Args:
        form: The Flask request.form MultiDict.

    Returns:
        List of dicts suitable for ``equipment_service.set_software_coverage``.
    """
    rows = []
    index = 0

    while True:
        scope_key = f"coverage_scope_type_{index}"
        scope_type = form.get(scope_key, "").strip().lower()

        # No more rows once we hit a missing index.
        if not scope_type:
            break

        row = {"scope_type": scope_type}

        if scope_type == "department":
            dept_id = form.get(f"coverage_department_id_{index}", type=int)
            row["department_id"] = dept_id
        elif scope_type == "division":
            div_id = form.get(f"coverage_division_id_{index}", type=int)
            row["division_id"] = div_id
        elif scope_type == "position":
            pos_id = form.get(f"coverage_position_id_{index}", type=int)
            row["position_id"] = pos_id
        # 'organization' scope requires no additional FK.

        rows.append(row)
        index += 1

    logger.debug("Parsed %d coverage rows from form", len(rows))
    return rows


# =========================================================================
# Software Types (categories)
# =========================================================================


@bp.route("/software-types")
@login_required
@role_required("admin", "it_staff")
def software_type_list():
    """
    List all software type categories.

    Supports an optional ``show_inactive`` query parameter to include
    deactivated types in the listing.
    """
    show_inactive = request.args.get("show_inactive", "0") == "1"
    sw_types = equipment_service.get_software_types(include_inactive=show_inactive)
    return render_template(
        "equipment/software_type_list.html",
        software_types=sw_types,
        show_inactive=show_inactive,
    )


@bp.route("/software-types/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_type_create():
    """
    Create a new software type category.

    GET:  Display the empty creation form.
    POST: Validate input and create the record.
    """
    if request.method == "POST":
        type_name = request.form.get("type_name", "").strip()
        description = request.form.get("description", "").strip() or None

        # Basic validation.
        if not type_name:
            flash("Type name is required.", "danger")
            return render_template(
                "equipment/software_type_form.html",
                mode="create",
                form_data=request.form,
            )

        try:
            equipment_service.create_software_type(
                type_name=type_name,
                description=description,
                user_id=current_user.id,
            )
            flash(f'Software type "{type_name}" created.', "success")
            return redirect(url_for("equipment.software_type_list"))
        except Exception:
            logger.exception("Error creating software type")
            flash(
                "An error occurred while creating the software type. "
                "Please try again.",
                "danger",
            )

    return render_template(
        "equipment/software_type_form.html",
        mode="create",
        form_data={},
    )


@bp.route("/software-types/<int:sw_type_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_type_edit(sw_type_id):
    """
    Edit an existing software type category.

    GET:  Populate the form with the current values.
    POST: Validate and apply changes.
    """
    sw_type = equipment_service.get_software_type_by_id(sw_type_id)
    if sw_type is None:
        flash("Software type not found.", "warning")
        return redirect(url_for("equipment.software_type_list"))

    if request.method == "POST":
        type_name = request.form.get("type_name", "").strip()
        description = request.form.get("description", "").strip() or None

        if not type_name:
            flash("Type name is required.", "danger")
            return render_template(
                "equipment/software_type_form.html",
                mode="edit",
                software_type=sw_type,
                form_data=request.form,
            )

        try:
            equipment_service.update_software_type(
                sw_type_id=sw_type_id,
                type_name=type_name,
                description=description,
                user_id=current_user.id,
            )
            flash(f'Software type "{type_name}" updated.', "success")
            return redirect(url_for("equipment.software_type_list"))
        except Exception:
            logger.exception("Error updating software type %d", sw_type_id)
            flash(
                "An error occurred while updating the software type. "
                "Please try again.",
                "danger",
            )

    return render_template(
        "equipment/software_type_form.html",
        mode="edit",
        software_type=sw_type,
        form_data={},
    )


@bp.route(
    "/software-types/<int:sw_type_id>/deactivate",
    methods=["POST"],
)
@login_required
@role_required("admin", "it_staff")
def software_type_deactivate(sw_type_id):
    """Soft-delete a software type category."""
    try:
        equipment_service.deactivate_software_type(
            sw_type_id=sw_type_id,
            user_id=current_user.id,
        )
        flash("Software type deactivated.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("equipment.software_type_list"))


# =========================================================================
# Software Families
# =========================================================================


@bp.route("/software-families")
@login_required
@role_required("admin", "it_staff")
def software_family_list():
    """
    List all software families.

    Supports an optional ``show_inactive`` query parameter to include
    deactivated families in the listing.
    """
    show_inactive = request.args.get("show_inactive", "0") == "1"
    families = equipment_service.get_software_families(include_inactive=show_inactive)
    return render_template(
        "equipment/software_family_list.html",
        software_families=families,
        show_inactive=show_inactive,
    )


@bp.route("/software-families/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_family_create():
    """
    Create a new software family.

    GET:  Display the empty creation form.
    POST: Validate input and create the record.
    """
    if request.method == "POST":
        family_name = request.form.get("family_name", "").strip()
        description = request.form.get("description", "").strip() or None

        # Basic validation.
        if not family_name:
            flash("Family name is required.", "danger")
            return render_template(
                "equipment/software_family_form.html",
                mode="create",
                form_data=request.form,
            )

        try:
            equipment_service.create_software_family(
                family_name=family_name,
                description=description,
                user_id=current_user.id,
            )
            flash(f'Software family "{family_name}" created.', "success")
            return redirect(url_for("equipment.software_family_list"))
        except Exception:
            logger.exception("Error creating software family")
            flash(
                "An error occurred while creating the software family. "
                "Please try again.",
                "danger",
            )

    return render_template(
        "equipment/software_family_form.html",
        mode="create",
        form_data={},
    )


@bp.route(
    "/software-families/<int:family_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@role_required("admin", "it_staff")
def software_family_edit(family_id):
    """
    Edit an existing software family.

    GET:  Populate the form with the current values.
    POST: Validate and apply changes.
    """
    family = equipment_service.get_software_family_by_id(family_id)
    if family is None:
        flash("Software family not found.", "warning")
        return redirect(url_for("equipment.software_family_list"))

    if request.method == "POST":
        family_name = request.form.get("family_name", "").strip()
        description = request.form.get("description", "").strip() or None

        if not family_name:
            flash("Family name is required.", "danger")
            return render_template(
                "equipment/software_family_form.html",
                mode="edit",
                software_family=family,
                form_data=request.form,
            )

        try:
            equipment_service.update_software_family(
                family_id=family_id,
                family_name=family_name,
                description=description,
                user_id=current_user.id,
            )
            flash(f'Software family "{family_name}" updated.', "success")
            return redirect(url_for("equipment.software_family_list"))
        except Exception:
            logger.exception("Error updating software family %d", family_id)
            flash(
                "An error occurred while updating the software family. "
                "Please try again.",
                "danger",
            )

    return render_template(
        "equipment/software_family_form.html",
        mode="edit",
        software_family=family,
        form_data={},
    )


@bp.route(
    "/software-families/<int:family_id>/deactivate",
    methods=["POST"],
)
@login_required
@role_required("admin", "it_staff")
def software_family_deactivate(family_id):
    """Soft-delete a software family."""
    try:
        equipment_service.deactivate_software_family(
            family_id=family_id,
            user_id=current_user.id,
        )
        flash("Software family deactivated.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("equipment.software_family_list"))


# =========================================================================
# Helpers
# =========================================================================


def _parse_decimal(value: str | None) -> Decimal | None:
    """Safely parse a decimal value from a form field."""
    if not value or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None
