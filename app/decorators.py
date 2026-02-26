"""
Authorization decorators for route-level access control.

These decorators enforce role and permission checks on blueprint
routes.  They are used in combination with Flask-Login's
``@login_required`` to provide layered security:

    @bp.route('/admin/users')
    @login_required
    @role_required('admin')
    def manage_users():
        ...

    @bp.route('/equipment/new')
    @login_required
    @permission_required('equipment.create')
    def create_equipment():
        ...
"""

import logging
from functools import wraps

from flask import abort, flash, request
from flask_login import current_user

logger = logging.getLogger(__name__)


def role_required(*role_names: str):
    """
    Decorator that restricts access to users with one of the specified roles.

    Args:
        role_names: One or more role name strings (e.g., 'admin', 'it_staff').

    Usage::

        @role_required('admin', 'it_staff')
        def protected_view():
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # current_user is guaranteed authenticated by @login_required.
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role.role_name not in role_names:
                logger.warning(
                    "Access denied: user %d (%s) with role '%s' "
                    "attempted %s %s (requires one of: %s)",
                    current_user.id,
                    current_user.email,
                    current_user.role.role_name,
                    request.method,
                    request.path,
                    ", ".join(role_names),
                )
                flash("You do not have permission to access this page.", "danger")
                abort(403)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def permission_required(permission_name: str):
    """
    Decorator that restricts access to users whose role grants
    the specified permission.

    Args:
        permission_name: Dotted permission string (e.g., 'equipment.create').

    Usage::

        @permission_required('equipment.create')
        def create_equipment():
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.has_permission(permission_name):
                logger.warning(
                    "Access denied: user %d (%s) lacks permission '%s' " "for %s %s",
                    current_user.id,
                    current_user.email,
                    permission_name,
                    request.method,
                    request.path,
                )
                flash("You do not have permission to perform this action.", "danger")
                abort(403)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def scope_check(entity_type: str, entity_id_kwarg: str = "id"):
    """
    Decorator that verifies the current user's scope allows access
    to the requested entity.

    Args:
        entity_type:    'department', 'division', or 'position'.
        entity_id_kwarg: Name of the route keyword argument containing
                         the entity's primary key.

    Usage::

        @bp.route('/org/department/<int:id>')
        @login_required
        @scope_check('department', 'id')
        def view_department(id):
            ...
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Import here to avoid circular imports.
            from app.services import (
                organization_service,
            )  # pylint: disable=import-outside-toplevel

            if not current_user.is_authenticated:
                abort(401)

            entity_id = kwargs.get(entity_id_kwarg)
            if entity_id is None:
                abort(400)

            # Org-wide users bypass scope checks.
            if current_user.has_org_scope():
                return func(*args, **kwargs)

            # Check scope based on entity type.
            has_access = False
            if entity_type == "department":
                has_access = organization_service.user_can_access_department(
                    current_user, entity_id
                )
            elif entity_type == "position":
                has_access = organization_service.user_can_access_position(
                    current_user, entity_id
                )

            if not has_access:
                flash(
                    "You do not have access to this resource.",
                    "warning",
                )
                abort(403)

            return func(*args, **kwargs)

        return wrapper

    return decorator
