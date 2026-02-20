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
    permission_name = db.Column(db.String(100), unique=True, nullable=False)
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
    role_id = db.Column(db.Integer, db.ForeignKey("auth.role.id"), nullable=False)
    permission_id = db.Column(
        db.Integer, db.ForeignKey("auth.permission.id"), nullable=False
    )

    # -- Relationships -----------------------------------------------------
    role = db.relationship("Role", back_populates="role_permissions")
    permission = db.relationship("Permission", back_populates="role_permissions")


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
    role_id = db.Column(db.Integer, db.ForeignKey("auth.role.id"), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    provisioned_by = db.Column(db.Integer, db.ForeignKey("auth.user.id"), nullable=True)
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
    scopes = db.relationship("UserScope", back_populates="user", lazy="joined")
    provisioner = db.relationship(
        "User", remote_side=[id], foreign_keys=[provisioned_by]
    )

    # ---- Convenience properties ------------------------------------------

    @property
    def full_name(self) -> str:
        """Return the user's full display name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def role_name(self) -> str:
        """Shortcut to the user's role name string."""
        return self.role.role_name if self.role else "unknown"

    # ---- Role checks -----------------------------------------------------

    def has_role(self, *role_names: str) -> bool:
        """Check if the user has any of the given role names."""
        return self.role_name in role_names

    def has_permission(self, permission_name: str) -> bool:
        """Check if the user's role grants a specific permission."""
        if not self.role:
            return False
        return any(
            rp.permission.permission_name == permission_name
            for rp in self.role.role_permissions
        )

    # ---- Scope checks ----------------------------------------------------

    def has_org_scope(self) -> bool:
        """Return True if the user has organization-wide scope."""
        return any(s.scope_type == "organization" for s in self.scopes)

    def scoped_department_ids(self) -> list[int]:
        """
        Return a list of department IDs the user has explicit access to.

        Collects IDs from all ``department``-type scopes.  Does **not**
        include departments implied by division-level scopes — callers
        in ``organization_service`` handle that roll-up themselves.

        Returns:
            A list of ``org.department.id`` values, possibly empty.
        """
        return [
            s.department_id
            for s in self.scopes
            if s.scope_type == "department" and s.department_id is not None
        ]

    def scoped_division_ids(self) -> list[int]:
        """
        Return a list of division IDs the user has explicit access to.

        Collects IDs from all ``division``-type scopes.

        Returns:
            A list of ``org.division.id`` values, possibly empty.
        """
        return [
            s.division_id
            for s in self.scopes
            if s.scope_type == "division" and s.division_id is not None
        ]

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role_name}>"


class UserScope(db.Model):
    """
    Organizational scope restricting what data a user can access.

    ``scope_type`` values:
      - ``organization``: User can see everything.
      - ``department``: User is restricted to a specific department.
      - ``division``: User is restricted to a specific division.

    A user can have multiple scopes (e.g., access to two departments).
    Admin and IT staff roles are typically given organization scope.
    """

    __tablename__ = "user_scope"
    __table_args__ = {"schema": "auth"}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("auth.user.id"), nullable=False, index=True
    )
    scope_type = db.Column(db.String(20), nullable=False)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("org.department.id"),
        nullable=True,
    )
    division_id = db.Column(
        db.Integer,
        db.ForeignKey("org.division.id"),
        nullable=True,
    )

    # -- Relationships -----------------------------------------------------
    user = db.relationship("User", back_populates="scopes")
    department = db.relationship("Department")
    division = db.relationship("Division")

    def __repr__(self) -> str:
        return f"<UserScope user={self.user_id} " f"type={self.scope_type}>"
