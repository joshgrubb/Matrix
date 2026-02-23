"""
Routes for the equipment blueprint — hardware and software catalog CRUD.

Restricted to admin and IT staff roles.  All changes are audit-logged
by the equipment service.
"""

from decimal import Decimal, InvalidOperation

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.equipment import bp
from app.decorators import role_required
from app.services import equipment_service


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

    return render_template(
        "equipment/software_list.html",
        software_products=sw_products,
        software_types=sw_types,
        show_inactive=include_inactive,
        selected_type_id=sw_type_filter,
    )


@bp.route("/software/new", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_create():
    """Create a new software product."""
    sw_types = equipment_service.get_software_types()
    sw_families = equipment_service.get_software_families()

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
                form_data=request.form,
            )

        try:
            equipment_service.create_software(
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
            flash(f"Software '{name}' created.", "success")
            return redirect(url_for("equipment.software_list"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flash(f"Error creating software: {exc}", "danger")

    return render_template(
        "equipment/software_form.html",
        mode="create",
        software=None,
        software_types=sw_types,
        software_families=sw_families,
        form_data={},
    )


@bp.route("/software/<int:software_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin", "it_staff")
def software_edit(software_id):
    """Edit an existing software product."""
    sw = equipment_service.get_software_by_id(software_id)
    if sw is None:
        flash("Software product not found.", "warning")
        return redirect(url_for("equipment.software_list"))

    sw_types = equipment_service.get_software_types()
    sw_families = equipment_service.get_software_families()

    if request.method == "POST":
        kwargs = {
            "name": request.form.get("name", "").strip(),
            "software_type_id": request.form.get("software_type_id", type=int),
            "license_model": request.form.get("license_model", "per_user"),
            "license_tier": request.form.get("license_tier", "").strip() or None,
            "software_family_id": request.form.get("software_family_id", type=int)
            or None,
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
