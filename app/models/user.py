"""
Authentication and authorization models — ``auth`` schema.

Authentication is handled entirely by Entra ID (OAuth2/OIDC). No
passwords are stored. These models define application-level roles,
granular permissions, and organizational scope restrictions.

Role = what you can do.  Scope = what you can see.
"""

from flask_login import UserMixin

from app.extensions import db


class Role(db.Model):
    """
    Application role managed by admins within the app.

    Role names are referenced in code (e.g., ``role_name == 'admin'``),
    not by ID. The permission system provides fine-grained access
    control via ``RolePermission``.
    """

    __tablename__ = "role"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    role_name = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    users = db.relationship("User", back_populates="role", lazy="dynamic")
    role_permissions = db.relationship(
        "RolePermission", back_populates="role", lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<Role {self.role_name}>"


class Permission(db.Model):
    """
    Granular permission checked by route decorators.

    Routes check permissions (e.g., ``equipment.create``), not role
    names, via a ``@permission_required`` decorator. This allows role
    definitions to evolve without changing route code.
    """

    __tablename__ = "permission"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    permission_name = db.Column(
        db.String(100), unique=True, nullable=False
    )
    description = db.Column(db.String(200), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    role_permissions = db.relationship(
        "RolePermission", back_populates="permission", lazy="joined"
    )

    def __repr__(self) -> str:
        return f"<Permission {self.permission_name}>"


class RolePermission(db.Model):
    """
    Many-to-many mapping of roles to permissions.

    Example: The ``it_staff`` role has ``equipment.create``,
    ``equipment.edit``, etc.
    """

    __tablename__ = "role_permission"
    __table_args__ = (
        db.UniqueConstraint(
            "role_id",
            "permission_id",
            name="UQ_role_permission_role_permission",
        ),
        {"schema": "auth"},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    role_id = db.Column(
        db.Integer, db.ForeignKey("auth.role.id"), nullable=False
    )
    permission_id = db.Column(
        db.Integer, db.ForeignKey("auth.permission.id"), nullable=False
    )

    # -- Relationships -----------------------------------------------------
    role = db.relationship("Role", back_populates="role_permissions")
    permission = db.relationship(
        "Permission", back_populates="role_permissions"
    )


class User(UserMixin, db.Model):
    """
    Application user record authenticated via Entra ID.

    Can be pre-provisioned by an admin (``entra_object_id`` is NULL
    until first OAuth login) or auto-created on first login with a
    default ``read_only`` role.

    Inherits from ``UserMixin`` to satisfy Flask-Login requirements
    (``is_authenticated``, ``is_active``, ``get_id``).
    """

    # ``user`` is a reserved word in SQL Server; the DDL uses brackets.
    __tablename__ = "user"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entra_object_id = db.Column(db.String(100), nullable=True, unique=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    role_id = db.Column(
        db.Integer, db.ForeignKey("auth.role.id"), nullable=False
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    provisioned_by = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=True
    )
    provisioned_at = db.Column(db.DateTime, nullable=True)
    first_login_at = db.Column(db.DateTime, nullable=True)
    last_login = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, server_default=db.text("SYSUTCDATETIME()")
    )

    # -- Relationships -----------------------------------------------------
    role = db.relationship("Role", back_populates="users")
    scopes = db.relationship(
        "UserScope", back_populates="user", lazy="joined"
    )
    provisioner = db.relationship(
        "User", remote_side=[id], foreign_keys=[provisioned_by]
    )

    # ---- Convenience properties ------------------------------------------

    @property
    def full_name(self) -> str:
        """Return the user's full display name."""
        return f"{self.first_name} {self.last_name}"

    def has_permission(self, permission_name: str) -> bool:
        """
        Check whether the user's role grants a specific permission.

        Args:
            permission_name: Dotted permission string (e.g., 'equipment.create').

        Returns:
            True if the role includes the permission.
        """
        return any(
            rp.permission.permission_name == permission_name
            for rp in self.role.role_permissions
        )

    def has_org_scope(self) -> bool:
        """Return True if any of the user's scopes cover the entire organization."""
        return any(s.scope_type == "organization" for s in self.scopes)

    def scoped_department_ids(self) -> list[int]:
        """
        Return a list of department IDs the user is scoped to.

        Users with organization scope get an empty list (meaning
        'no restriction'). The service layer should check
        ``has_org_scope()`` first.
        """
        return [
            s.department_id
            for s in self.scopes
            if s.scope_type == "department" and s.department_id is not None
        ]

    def scoped_division_ids(self) -> list[int]:
        """
        Return a list of division IDs the user is scoped to.

        Similar to ``scoped_department_ids()`` but for division-level
        scope restrictions.
        """
        return [
            s.division_id
            for s in self.scopes
            if s.scope_type == "division" and s.division_id is not None
        ]

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class UserScope(db.Model):
    """
    Defines which organizational units a user can access.

    ``scope_type`` values:
      - ``organization`` — Sees everything.  For admin, IT staff, budget exec.
      - ``department``   — Sees one department and all its divisions/positions.
      - ``division``     — Sees one division and its positions.

    A user can have multiple scope rows (e.g., manages two divisions).
    Enforced at the service layer so it applies regardless of which
    route or export calls the service.
    """

    __tablename__ = "user_scope"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=False, index=True
    )
    scope_type = db.Column(db.String(20), nullable=False)
    department_id = db.Column(
        db.Integer, db.ForeignKey("org.department.id"), nullable=True
    )
    division_id = db.Column(
        db.Integer, db.ForeignKey("org.division.id"), nullable=True
    )

    # -- Relationships -----------------------------------------------------
    user = db.relationship("User", back_populates="scopes")
    department = db.relationship("Department")
    division = db.relationship("Division")

    def __repr__(self) -> str:
        return f"<UserScope user={self.user_id} type={self.scope_type}>"
