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

    return redirect(url_for("admin.manage_users"))


@bp.route("/users/<int:user_id>/scopes", methods=["POST"])
@login_required
@role_required("admin")
def update_user_scopes(user_id):
    """Update a user's organizational scopes."""
    scope_type = request.form.get("scope_type", "organization")

    scopes = []
    if scope_type == "organization":
        scopes.append({"scope_type": "organization"})
    elif scope_type == "department":
        # Parse department IDs from form checkboxes.
        dept_ids = request.form.getlist("department_ids", type=int)
        for dept_id in dept_ids:
            scopes.append({
                "scope_type": "department",
                "department_id": dept_id,
            })
    elif scope_type == "division":
        div_ids = request.form.getlist("division_ids", type=int)
        for div_id in div_ids:
            scopes.append({
                "scope_type": "division",
                "division_id": div_id,
            })

    try:
        user_service.set_user_scopes(
            user_id=user_id,
            scopes=scopes,
            changed_by=current_user.id,
        )
        flash("User scopes updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("admin.manage_users"))


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
