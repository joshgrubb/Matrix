"""
Routes for the admin blueprint — user management, audit logs, and HR sync.

All routes require the 'admin' role unless otherwise noted.  The
audit log viewer is also accessible to 'it_staff'.
"""

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.blueprints.admin import bp
from app.decorators import role_required
from app.models.organization import Department, Division
from app.services import audit_service, hr_sync_service, user_service


# =========================================================================
# User Management (admin only)
# =========================================================================


@bp.route("/users")
@login_required
@role_required("admin")
def manage_users():
    """List all application users with their roles and scopes."""
    page = request.args.get("page", 1, type=int)
    include_inactive = request.args.get("show_inactive", "0") == "1"

    users = user_service.get_all_users(
        include_inactive=include_inactive,
        page=page,
        per_page=25,
    )
    roles = user_service.get_all_roles()

    return render_template(
        "admin/manage_users.html",
        users=users,
        roles=roles,
        show_inactive=include_inactive,
    )


@bp.route("/users/<int:user_id>/edit")
@login_required
@role_required("admin")
def edit_user(user_id):
    """
    Render the user detail / scope editing page.

    Displays the user's current role, scope type, and specific
    department or division assignments.  Provides forms to change
    the role and to replace the user's scopes with a new configuration.
    """
    user = user_service.get_user_by_id(user_id)
    if user is None:
        flash("User not found.", "warning")
        return redirect(url_for("admin.manage_users"))

    roles = user_service.get_all_roles()

    # Fetch all active departments and divisions for the scope editor.
    departments = (
        Department.query.filter_by(is_active=True)
        .order_by(Department.department_name)
        .all()
    )
    divisions = (
        Division.query.filter_by(is_active=True).order_by(Division.division_name).all()
    )

    # Determine the user's current scope state for pre-selecting the form.
    current_scope_type = _get_current_scope_type(user)
    current_dept_ids = [
        s.department_id
        for s in user.scopes
        if s.scope_type == "department" and s.department_id is not None
    ]
    current_div_ids = [
        s.division_id
        for s in user.scopes
        if s.scope_type == "division" and s.division_id is not None
    ]

    return render_template(
        "admin/edit_user.html",
        user=user,
        roles=roles,
        departments=departments,
        divisions=divisions,
        current_scope_type=current_scope_type,
        current_dept_ids=current_dept_ids,
        current_div_ids=current_div_ids,
    )


@bp.route("/users/<int:user_id>/htmx/divisions")
@login_required
@role_required("admin")
def htmx_user_divisions(user_id):
    """
    HTMX partial: return division checkboxes for a selected department.

    Used by the scope editor on the user edit page.  When the admin
    picks a department in the division-scope flow, this endpoint
    returns the checkbox list for that department's divisions.

    Query Parameters:
        department_id (int): The department whose divisions to list.
    """
    department_id = request.args.get("department_id", type=int)
    user = user_service.get_user_by_id(user_id)

    # Build the list of currently selected division IDs for pre-checking.
    current_div_ids = []
    if user is not None:
        current_div_ids = [
            s.division_id
            for s in user.scopes
            if s.scope_type == "division" and s.division_id is not None
        ]

    divisions = []
    if department_id is not None:
        divisions = (
            Division.query.filter_by(department_id=department_id, is_active=True)
            .order_by(Division.division_name)
            .all()
        )

    return render_template(
        "admin/_division_checkboxes.html",
        divisions=divisions,
        current_div_ids=current_div_ids,
    )


@bp.route("/users/provision", methods=["POST"])
@login_required
@role_required("admin")
def provision_user():
    """Pre-provision a new user with a role assignment."""
    email = request.form.get("email", "").strip()
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    role_name = request.form.get("role_name", "read_only")

    if not email or not first_name or not last_name:
        flash("Email, first name, and last name are required.", "danger")
        return redirect(url_for("admin.manage_users"))

    try:
        user_service.provision_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role_name=role_name,
            provisioned_by=current_user.id,
        )
        flash(f"User '{email}' provisioned with role '{role_name}'.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("admin.manage_users"))


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@login_required
@role_required("admin")
def update_user_role(user_id):
    """Change a user's role."""
    new_role = request.form.get("role_name", "read_only")

    try:
        user_service.update_user_role(
            user_id=user_id,
            new_role_name=new_role,
            changed_by=current_user.id,
        )
        flash("User role updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("admin.edit_user", user_id=user_id))


@bp.route("/users/<int:user_id>/scopes", methods=["POST"])
@login_required
@role_required("admin")
def update_user_scopes(user_id):
    """
    Update a user's organizational scopes.

    Accepts the scope_type radio value and corresponding department
    or division checkbox selections from the edit user form.
    """
    scope_type = request.form.get("scope_type", "organization")

    scopes = []
    if scope_type == "organization":
        scopes.append({"scope_type": "organization"})
    elif scope_type == "department":
        # Parse department IDs from form checkboxes.
        dept_ids = request.form.getlist("department_ids", type=int)
        if not dept_ids:
            flash(
                "Please select at least one department for department scope.",
                "warning",
            )
            return redirect(url_for("admin.edit_user", user_id=user_id))
        for dept_id in dept_ids:
            scopes.append(
                {
                    "scope_type": "department",
                    "department_id": dept_id,
                }
            )
    elif scope_type == "division":
        # Parse division IDs from form checkboxes.
        div_ids = request.form.getlist("division_ids", type=int)
        if not div_ids:
            flash(
                "Please select at least one division for division scope.",
                "warning",
            )
            return redirect(url_for("admin.edit_user", user_id=user_id))
        for div_id in div_ids:
            scopes.append(
                {
                    "scope_type": "division",
                    "division_id": div_id,
                }
            )

    try:
        user_service.set_user_scopes(
            user_id=user_id,
            scopes=scopes,
            changed_by=current_user.id,
        )
        flash("User scopes updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("admin.edit_user", user_id=user_id))


@bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@role_required("admin")
def deactivate_user(user_id):
    """Soft-delete a user."""
    try:
        user_service.deactivate_user(
            user_id=user_id,
            changed_by=current_user.id,
        )
        flash("User deactivated.", "info")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("admin.manage_users"))


@bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
@login_required
@role_required("admin")
def reactivate_user(user_id):
    """Re-enable a previously deactivated user."""
    try:
        user_service.reactivate_user(
            user_id=user_id,
            changed_by=current_user.id,
        )
        flash("User reactivated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("admin.manage_users"))


# =========================================================================
# Audit Logs (admin + IT staff)
# =========================================================================


@bp.route("/audit-logs")
@login_required
@role_required("admin", "it_staff")
def audit_logs():
    """View paginated audit logs with optional filters."""
    page = request.args.get("page", 1, type=int)
    user_id_filter = request.args.get("user_id", type=int)
    action_filter = request.args.get("action_type")
    entity_filter = request.args.get("entity_type")

    logs = audit_service.get_audit_logs(
        page=page,
        per_page=50,
        user_id=user_id_filter,
        action_type=action_filter,
        entity_type=entity_filter,
    )

    # Provide filter options for the UI.
    entity_types = audit_service.get_distinct_entity_types()

    return render_template(
        "admin/audit_logs.html",
        logs=logs,
        entity_types=entity_types,
        selected_action=action_filter,
        selected_entity=entity_filter,
        selected_user_id=user_id_filter,
    )


# =========================================================================
# HR Sync (admin + IT staff)
# =========================================================================


@bp.route("/hr-sync")
@login_required
@role_required("admin", "it_staff")
def hr_sync():
    """Display HR sync status and history."""
    page = request.args.get("page", 1, type=int)
    sync_logs = hr_sync_service.get_sync_logs(page=page)

    return render_template(
        "admin/hr_sync.html",
        sync_logs=sync_logs,
    )


@bp.route("/hr-sync/run", methods=["POST"])
@login_required
@role_required("admin", "it_staff")
def hr_sync_run():
    """Trigger a full NeoGov HR sync."""
    sync_log = hr_sync_service.run_full_sync(user_id=current_user.id)

    if sync_log.status == "completed":
        flash("HR sync completed successfully.", "success")
    else:
        flash(
            f"HR sync failed: {sync_log.error_message or 'Unknown error'}",
            "danger",
        )

    return redirect(url_for("admin.hr_sync"))


# =========================================================================
# Internal helpers
# =========================================================================


def _get_current_scope_type(user) -> str:
    """
    Determine the user's primary scope type for pre-selecting the form.

    Returns:
        One of ``'organization'``, ``'department'``, ``'division'``,
        or ``'none'`` if the user has no scopes assigned.
    """
    if not user.scopes:
        return "none"

    # Organization scope takes precedence.
    if any(s.scope_type == "organization" for s in user.scopes):
        return "organization"

    # Check for department-level scopes.
    if any(s.scope_type == "department" for s in user.scopes):
        return "department"

    # Check for division-level scopes.
    if any(s.scope_type == "division" for s in user.scopes):
        return "division"

    return "none"
