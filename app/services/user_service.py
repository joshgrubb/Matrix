"""
User service — user lookup, role assignment, and scope management.

Handles CRUD for application users and their organizational access
scopes.  Authentication is handled by Entra ID; this service manages
the local user records that store role and scope data.
"""

import logging
from datetime import datetime, timezone

from app.extensions import db
from app.models.user import Role, User, UserScope
from app.services import audit_service

logger = logging.getLogger(__name__)


# -- User lookup -----------------------------------------------------------


def get_user_by_id(user_id: int) -> User | None:
    """Return a user by primary key, or None if not found."""
    return db.session.get(User, user_id)


def get_user_by_email(email: str) -> User | None:
    """Return a user by email address (case-insensitive)."""
    return User.query.filter(User.email.ilike(email)).first()


def get_user_by_entra_id(entra_object_id: str) -> User | None:
    """Return a user by their Entra ID (Azure AD) object ID."""
    return User.query.filter_by(entra_object_id=entra_object_id).first()


def get_all_users(
    include_inactive: bool = False,
    page: int = 1,
    per_page: int = 50,
):
    """
    Return a paginated list of users, ordered by last name.

    Args:
        include_inactive: If True, include deactivated users.
        page:             Page number (1-indexed).
        per_page:         Records per page.

    Returns:
        A SQLAlchemy pagination object.
    """
    query = User.query.order_by(User.last_name, User.first_name)
    if not include_inactive:
        query = query.filter(User.is_active == True)
    return query.paginate(page=page, per_page=per_page, error_out=False)


# -- User creation and provisioning ----------------------------------------


def provision_user(
    email: str,
    first_name: str,
    last_name: str,
    role_name: str = "read_only",
    provisioned_by: int | None = None,
    entra_object_id: str | None = None,
) -> User:
    """
    Create a new user with the specified role.

    Used by admins to pre-provision users before their first login,
    or by the auth service to auto-create users on first OAuth login.

    Args:
        email:            User's email address.
        first_name:       User's first name.
        last_name:        User's last name.
        role_name:        Role to assign (defaults to read_only).
        provisioned_by:   ID of the admin who created the user, or None.
        entra_object_id:  Azure AD object ID if known.

    Returns:
        The newly created User record.

    Raises:
        ValueError: If the role_name is not found.
    """
    # Look up the role by name.
    role = Role.query.filter_by(role_name=role_name).first()
    if role is None:
        raise ValueError(f"Role '{role_name}' not found.")

    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        role_id=role.id,
        entra_object_id=entra_object_id,
        provisioned_by=provisioned_by,
        provisioned_at=datetime.now(timezone.utc) if provisioned_by else None,
    )
    db.session.add(user)
    db.session.flush()  # Get the user ID for audit logging.

    # Assign default org-wide scope for admin/it_staff/budget_executive.
    if role_name in ("admin", "it_staff", "budget_executive"):
        _add_org_scope(user)

    audit_service.log_change(
        user_id=provisioned_by,
        action_type="CREATE",
        entity_type="auth.user",
        entity_id=user.id,
        new_value={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "role": role_name,
        },
    )
    db.session.commit()

    logger.info("Provisioned user %s with role %s", email, role_name)
    return user


def update_user_role(
    user_id: int,
    new_role_name: str,
    changed_by: int | None = None,
) -> User:
    """
    Change a user's role.

    Args:
        user_id:       The user to update.
        new_role_name: The new role to assign.
        changed_by:    ID of the admin making the change.

    Returns:
        The updated User record.

    Raises:
        ValueError: If the user or role is not found.
    """
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError(f"User ID {user_id} not found.")

    new_role = Role.query.filter_by(role_name=new_role_name).first()
    if new_role is None:
        raise ValueError(f"Role '{new_role_name}' not found.")

    old_role_name = user.role.role_name
    user.role_id = new_role.id
    user.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=changed_by,
        action_type="UPDATE",
        entity_type="auth.user",
        entity_id=user.id,
        previous_value={"role": old_role_name},
        new_value={"role": new_role_name},
    )
    db.session.commit()

    logger.info(
        "Changed role for user %s: %s -> %s",
        user.email,
        old_role_name,
        new_role_name,
    )
    return user


def deactivate_user(user_id: int, changed_by: int | None = None) -> User:
    """Soft-delete a user by setting is_active to False."""
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError(f"User ID {user_id} not found.")

    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=changed_by,
        action_type="UPDATE",
        entity_type="auth.user",
        entity_id=user.id,
        previous_value={"is_active": True},
        new_value={"is_active": False},
    )
    db.session.commit()

    logger.info("Deactivated user %s", user.email)
    return user


def reactivate_user(user_id: int, changed_by: int | None = None) -> User:
    """Re-enable a previously deactivated user."""
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError(f"User ID {user_id} not found.")

    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)

    audit_service.log_change(
        user_id=changed_by,
        action_type="UPDATE",
        entity_type="auth.user",
        entity_id=user.id,
        previous_value={"is_active": False},
        new_value={"is_active": True},
    )
    db.session.commit()

    logger.info("Reactivated user %s", user.email)
    return user


# -- Scope management ------------------------------------------------------


def set_user_scopes(
    user_id: int,
    scopes: list[dict],
    changed_by: int | None = None,
) -> list[UserScope]:
    """
    Replace all scopes for a user with the provided list.

    Args:
        user_id:    The user to update.
        scopes:     List of scope dicts, each containing:
                    ``scope_type`` (organization/department/division),
                    and optionally ``department_id`` or ``division_id``.
        changed_by: ID of the admin making the change.

    Returns:
        The new list of UserScope records.
    """
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError(f"User ID {user_id} not found.")

    # Capture old scopes for audit.
    old_scopes = [
        {
            "scope_type": s.scope_type,
            "department_id": s.department_id,
            "division_id": s.division_id,
        }
        for s in user.scopes
    ]

    # Remove existing scopes.
    UserScope.query.filter_by(user_id=user_id).delete()

    # Add new scopes.
    new_scope_records = []
    for scope_data in scopes:
        scope = UserScope(
            user_id=user_id,
            scope_type=scope_data["scope_type"],
            department_id=scope_data.get("department_id"),
            division_id=scope_data.get("division_id"),
        )
        db.session.add(scope)
        new_scope_records.append(scope)

    audit_service.log_change(
        user_id=changed_by,
        action_type="UPDATE",
        entity_type="auth.user_scope",
        entity_id=user_id,
        previous_value={"scopes": old_scopes},
        new_value={"scopes": scopes},
    )
    db.session.commit()

    logger.info("Updated scopes for user %s", user.email)
    return new_scope_records


def _add_org_scope(user: User) -> None:
    """Add an organization-wide scope for the given user (no commit)."""
    scope = UserScope(
        user_id=user.id,
        scope_type="organization",
    )
    db.session.add(scope)


# -- Role helpers ----------------------------------------------------------


def get_all_roles() -> list[Role]:
    """Return all active roles, ordered by name."""
    return Role.query.filter_by(is_active=True).order_by(Role.role_name).all()


def record_login(user: User) -> None:
    """
    Update the user's last_login timestamp and record first_login_at
    if this is their first login.
    """
    now = datetime.now(timezone.utc)
    if user.first_login_at is None:
        user.first_login_at = now
    user.last_login = now
    db.session.commit()
