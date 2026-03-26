"""
Unit tests for the User, Role, Permission, RolePermission, and
UserScope models in the ``auth`` schema.

Verifies model properties, convenience methods, role checks,
permission lookups, scope helpers, Flask-Login integration, and
the ``__repr__`` outputs that appear in logs and debugger sessions.

These tests exercise the model layer directly against the real
SQL Server test database.  They do not call service functions or
route handlers -- they test the model API that services rely on.

Design decisions:
    - Tests use the ``sample_org`` conftest fixture for scope
      assignment (department and division IDs), and the ``roles``
      session-scoped fixture for looking up seeded role records.
    - The ``create_user`` factory fixture builds users with
      arbitrary role/scope combinations for targeted tests.
    - Permission tests rely on the seeded ``auth.permission`` and
      ``auth.role_permission`` data.  The DDL seeds specific
      permissions (e.g., ``equipment.create``) to specific roles
      (e.g., ``it_staff``, ``admin``).  If the seed data changes,
      these tests must be updated to match.
    - ``__repr__`` assertions check the string contains key
      identifying information, not an exact format, so minor
      formatting changes do not break tests unnecessarily.

Fixture reminder (from conftest.py):
    roles dict keys: admin, it_staff, manager, budget_executive, read_only
    sample_org keys: dept_a, dept_b, div_a1, div_a2, div_b1, div_b2,
                     pos_a1_1 (auth=3), pos_a1_2 (auth=5), pos_a2_1 (auth=2),
                     pos_b1_1 (auth=4), pos_b1_2 (auth=1), pos_b2_1 (auth=6)

Run this file in isolation::

    pytest tests/test_models/test_user_model.py -v
"""

import pytest

from app.models.user import Permission, Role, RolePermission, User, UserScope


# =====================================================================
# 1. User.full_name property
# =====================================================================


class TestUserFullName:
    """Verify the ``full_name`` computed property."""

    def test_full_name_concatenates_first_and_last(self, app, admin_user):
        """full_name should be 'First Last'."""
        assert admin_user.full_name == f"{admin_user.first_name} {admin_user.last_name}"

    def test_full_name_returns_string_type(self, app, admin_user):
        """full_name must return a str, not bytes or None."""
        assert isinstance(admin_user.full_name, str)

    def test_full_name_reflects_changed_first_name(self, app, db_session, admin_user):
        """
        If first_name is updated in-session (before commit), the
        property should immediately reflect the change because it
        reads from the instance attributes, not a cached value.
        """
        admin_user.first_name = "Changed"
        assert admin_user.full_name == f"Changed {admin_user.last_name}"

    def test_full_name_with_single_character_names(self, app, create_user):
        """Single-character names should work without error."""
        user = create_user(first_name="A", last_name="B")
        assert user.full_name == "A B"


# =====================================================================
# 2. User.role_name property
# =====================================================================


class TestUserRoleName:
    """Verify the ``role_name`` shortcut property."""

    def test_admin_user_role_name(self, app, admin_user):
        """An admin user's role_name should be 'admin'."""
        assert admin_user.role_name == "admin"

    def test_manager_user_role_name(self, app, manager_user):
        """A manager user's role_name should be 'manager'."""
        assert manager_user.role_name == "manager"

    def test_it_staff_user_role_name(self, app, it_staff_user):
        """An IT staff user's role_name should be 'it_staff'."""
        assert it_staff_user.role_name == "it_staff"

    def test_budget_user_role_name(self, app, budget_user):
        """A budget executive's role_name should be 'budget_executive'."""
        assert budget_user.role_name == "budget_executive"

    def test_read_only_user_role_name(self, app, read_only_user):
        """A read-only user's role_name should be 'read_only'."""
        assert read_only_user.role_name == "read_only"

    def test_role_name_returns_string(self, app, admin_user):
        """role_name must always return a string."""
        assert isinstance(admin_user.role_name, str)


# =====================================================================
# 3. User.has_role() method
# =====================================================================


class TestUserHasRole:
    """Verify the ``has_role()`` variadic role checker."""

    def test_has_role_single_match(self, app, admin_user):
        """has_role('admin') returns True for an admin user."""
        assert admin_user.has_role("admin") is True

    def test_has_role_single_no_match(self, app, admin_user):
        """has_role('manager') returns False for an admin user."""
        assert admin_user.has_role("manager") is False

    def test_has_role_multiple_with_match(self, app, manager_user):
        """has_role('admin', 'manager') returns True if any match."""
        assert manager_user.has_role("admin", "manager") is True

    def test_has_role_multiple_no_match(self, app, read_only_user):
        """has_role('admin', 'manager') returns False for read_only."""
        assert read_only_user.has_role("admin", "manager") is False

    def test_has_role_with_all_five_roles(self, app, admin_user):
        """Passing all role names should always return True."""
        assert (
            admin_user.has_role(
                "admin", "it_staff", "manager", "budget_executive", "read_only"
            )
            is True
        )

    def test_has_role_empty_args_returns_false(self, app, admin_user):
        """Calling has_role() with no arguments returns False."""
        assert admin_user.has_role() is False

    def test_has_role_nonexistent_role_returns_false(self, app, admin_user):
        """A role name that does not exist returns False."""
        assert admin_user.has_role("superadmin") is False


# =====================================================================
# 4. User.has_permission() method
# =====================================================================


class TestUserHasPermission:
    """
    Verify ``has_permission()`` checks the user's role's permissions
    via the RolePermission join table.

    The seeded data grants ``equipment.create`` to admin and it_staff
    roles but not to manager, budget_executive, or read_only.
    """

    def test_admin_has_equipment_create(self, app, admin_user):
        """Admin role should have the equipment.create permission."""
        assert admin_user.has_permission("equipment.create") is True

    def test_it_staff_has_equipment_create(self, app, it_staff_user):
        """IT staff role should have the equipment.create permission."""
        assert it_staff_user.has_permission("equipment.create") is True

    def test_manager_lacks_equipment_create(self, app, manager_user):
        """Manager role should NOT have equipment.create."""
        assert manager_user.has_permission("equipment.create") is False

    def test_read_only_lacks_equipment_create(self, app, read_only_user):
        """Read-only role should NOT have equipment.create."""
        assert read_only_user.has_permission("equipment.create") is False

    def test_nonexistent_permission_returns_false(self, app, admin_user):
        """A permission name that does not exist returns False."""
        assert admin_user.has_permission("does.not.exist") is False

    def test_empty_string_permission_returns_false(self, app, admin_user):
        """An empty string permission name returns False."""
        assert admin_user.has_permission("") is False


# =====================================================================
# 5. User.has_org_scope() method
# =====================================================================


class TestUserHasOrgScope:
    """Verify ``has_org_scope()`` detects organization-wide scopes."""

    def test_admin_has_org_scope(self, app, admin_user):
        """Admin users are created with organization scope."""
        assert admin_user.has_org_scope() is True

    def test_it_staff_has_org_scope(self, app, it_staff_user):
        """IT staff users are created with organization scope."""
        assert it_staff_user.has_org_scope() is True

    def test_manager_does_not_have_org_scope(self, app, manager_user):
        """
        The conftest manager_user has division-level scope (div_a1),
        not organization scope.
        """
        assert manager_user.has_org_scope() is False

    def test_read_only_does_not_have_org_scope(self, app, read_only_user):
        """
        The conftest read_only_user has division-level scope,
        not organization scope.
        """
        assert read_only_user.has_org_scope() is False

    def test_department_scoped_user_does_not_have_org_scope(
        self, app, manager_dept_scope_user
    ):
        """A user with department-level scope is not org-wide."""
        assert manager_dept_scope_user.has_org_scope() is False

    def test_user_with_no_scopes_does_not_have_org_scope(self, app, db_session, roles):
        """
        A user with zero scope records should return False,
        not raise an exception.
        """
        # Create a user with no scopes at all.
        import time as _time

        user = User(
            email=f"_tst_noscope_{int(_time.time()*10)%9000}@test.local",
            first_name="No",
            last_name="Scopes",
            role_id=roles["read_only"].id,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        assert user.has_org_scope() is False


# =====================================================================
# 6. User.scoped_department_ids() method
# =====================================================================


class TestUserScopedDepartmentIds:
    """Verify ``scoped_department_ids()`` returns correct IDs."""

    def test_org_scoped_user_returns_empty_list(self, app, admin_user):
        """
        An org-wide user has no department-type scopes, so
        scoped_department_ids() returns an empty list.
        """
        assert admin_user.scoped_department_ids() == []

    def test_department_scoped_user_returns_department_id(
        self, app, manager_dept_scope_user, sample_org
    ):
        """
        A user scoped to dept_a should return [dept_a.id].
        """
        dept_ids = manager_dept_scope_user.scoped_department_ids()
        assert sample_org["dept_a"].id in dept_ids

    def test_department_scoped_user_does_not_include_other_departments(
        self, app, manager_dept_scope_user, sample_org
    ):
        """dept_b should NOT appear in the scoped department IDs."""
        dept_ids = manager_dept_scope_user.scoped_department_ids()
        assert sample_org["dept_b"].id not in dept_ids

    def test_division_scoped_user_returns_empty_department_list(
        self, app, manager_user
    ):
        """
        A user with only division-type scopes should return an
        empty list from scoped_department_ids(), because that
        method only collects department-type scopes.
        """
        assert manager_user.scoped_department_ids() == []

    def test_multi_department_scope_returns_all(self, app, create_user, sample_org):
        """
        A user scoped to two departments should return both IDs.
        """
        user = create_user(
            role_name="manager",
            scopes=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_b"].id,
                },
            ],
        )
        dept_ids = user.scoped_department_ids()
        assert sample_org["dept_a"].id in dept_ids
        assert sample_org["dept_b"].id in dept_ids
        assert len(dept_ids) == 2


# =====================================================================
# 7. User.scoped_division_ids() method
# =====================================================================


class TestUserScopedDivisionIds:
    """Verify ``scoped_division_ids()`` returns correct IDs."""

    def test_org_scoped_user_returns_empty_list(self, app, admin_user):
        """An org-wide user has no division-type scopes."""
        assert admin_user.scoped_division_ids() == []

    def test_division_scoped_user_returns_division_id(
        self, app, manager_user, sample_org
    ):
        """
        The conftest manager_user is scoped to div_a1, so
        scoped_division_ids() should contain div_a1.id.
        """
        div_ids = manager_user.scoped_division_ids()
        assert sample_org["div_a1"].id in div_ids

    def test_division_scoped_user_does_not_include_other_divisions(
        self, app, manager_user, sample_org
    ):
        """Divisions outside the user's scope should not appear."""
        div_ids = manager_user.scoped_division_ids()
        assert sample_org["div_a2"].id not in div_ids
        assert sample_org["div_b1"].id not in div_ids

    def test_department_scoped_user_returns_empty_division_list(
        self, app, manager_dept_scope_user
    ):
        """
        A user with only department-type scopes should return an
        empty list from scoped_division_ids().
        """
        assert manager_dept_scope_user.scoped_division_ids() == []

    def test_multi_division_scope_returns_all(self, app, create_user, sample_org):
        """A user scoped to two divisions should return both IDs."""
        user = create_user(
            role_name="manager",
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
            ],
        )
        div_ids = user.scoped_division_ids()
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_b1"].id in div_ids
        assert len(div_ids) == 2


# =====================================================================
# 8. Flask-Login integration (UserMixin)
# =====================================================================


class TestFlaskLoginIntegration:
    """
    Verify that the User model satisfies Flask-Login's UserMixin
    contract: is_authenticated, is_active, get_id.
    """

    def test_is_authenticated_returns_true_for_active_user(self, app, admin_user):
        """An active user's is_authenticated should be True."""
        assert admin_user.is_authenticated is True

    def test_is_active_returns_true_for_active_user(self, app, admin_user):
        """An active user's is_active should be True."""
        assert admin_user.is_active is True

    def test_is_active_returns_false_for_inactive_user(self, app, inactive_user):
        """A deactivated user's is_active should be False."""
        assert inactive_user.is_active is False

    def test_get_id_returns_string_of_primary_key(self, app, admin_user):
        """get_id() must return the user's PK as a string."""
        result = admin_user.get_id()
        assert result == str(admin_user.id)
        assert isinstance(result, str)


# =====================================================================
# 9. User.__repr__
# =====================================================================


class TestUserRepr:
    """Verify the ``__repr__`` output for logging and debugging."""

    def test_repr_contains_email(self, app, admin_user):
        """The repr string should include the user's email."""
        assert admin_user.email in repr(admin_user)

    def test_repr_contains_role_name(self, app, admin_user):
        """The repr string should include the role name."""
        assert "admin" in repr(admin_user)

    def test_repr_starts_with_class_name(self, app, admin_user):
        """The repr should start with '<User'."""
        assert repr(admin_user).startswith("<User")


# =====================================================================
# 10. User.role relationship
# =====================================================================


class TestUserRoleRelationship:
    """Verify the User -> Role SQLAlchemy relationship."""

    def test_role_is_loaded(self, app, admin_user):
        """The role relationship should be populated, not None."""
        assert admin_user.role is not None

    def test_role_is_role_instance(self, app, admin_user):
        """The related object should be a Role model instance."""
        assert isinstance(admin_user.role, Role)

    def test_role_has_correct_name(self, app, admin_user):
        """The related role should match the assigned role."""
        assert admin_user.role.role_name == "admin"


# =====================================================================
# 11. User.scopes relationship
# =====================================================================


class TestUserScopesRelationship:
    """Verify the User -> UserScope relationship (eager loaded)."""

    def test_scopes_is_list(self, app, admin_user):
        """The scopes relationship should return a list."""
        assert isinstance(admin_user.scopes, list)

    def test_admin_has_at_least_one_scope(self, app, admin_user):
        """An admin user should have at least one scope record."""
        assert len(admin_user.scopes) >= 1

    def test_scope_entries_are_user_scope_instances(self, app, admin_user):
        """Each entry in the scopes list should be a UserScope."""
        for scope in admin_user.scopes:
            assert isinstance(scope, UserScope)

    def test_manager_has_division_scope(self, app, manager_user):
        """The conftest manager should have a division-type scope."""
        scope_types = [s.scope_type for s in manager_user.scopes]
        assert "division" in scope_types


# =====================================================================
# 12. Role model
# =====================================================================


class TestRoleModel:
    """Verify the Role model's seeded data and relationships."""

    def test_seeded_roles_exist(self, app, roles):
        """All five expected roles should be present in the DB."""
        expected = {"admin", "it_staff", "manager", "budget_executive", "read_only"}
        assert set(roles.keys()) == expected

    def test_role_has_role_name(self, app, roles):
        """Each seeded role should have a non-empty role_name."""
        for name, role in roles.items():
            assert role.role_name == name
            assert len(role.role_name) > 0

    def test_role_repr_contains_name(self, app, roles):
        """The Role repr should include the role_name."""
        admin_role = roles["admin"]
        assert "admin" in repr(admin_role)

    def test_admin_role_has_permissions(self, app, roles):
        """The admin role should have at least one permission."""
        admin_role = roles["admin"]
        assert len(admin_role.role_permissions) > 0

    def test_role_permissions_are_role_permission_instances(self, app, roles):
        """Each entry in role_permissions should be a RolePermission."""
        admin_role = roles["admin"]
        for rp in admin_role.role_permissions:
            assert isinstance(rp, RolePermission)

    def test_role_permission_has_permission_relationship(self, app, roles, db_session):
        """
        Each RolePermission should have a loaded Permission
        with a non-empty permission_name.

        This test re-queries the Role via the function-scoped
        db_session because the session-scoped ``roles`` fixture's
        RolePermission objects are detached from the active session.
        Accessing ``rp.permission`` triggers a lazy load that
        requires an active session binding.
        """
        # Re-query within the active session so lazy loads work.
        admin_role = db_session.get(Role, roles["admin"].id)
        for rp in admin_role.role_permissions:
            assert rp.permission is not None
            assert isinstance(rp.permission, Permission)
            assert len(rp.permission.permission_name) > 0


# =====================================================================
# 13. Permission model
# =====================================================================


class TestPermissionModel:
    """Verify the Permission model's seeded data."""

    def test_equipment_create_permission_exists(self, app):
        """The equipment.create permission should be seeded."""
        perm = Permission.query.filter_by(permission_name="equipment.create").first()
        assert perm is not None

    def test_permission_repr_contains_name(self, app):
        """The Permission repr should include the permission_name."""
        perm = Permission.query.first()
        assert perm.permission_name in repr(perm)


# =====================================================================
# 14. UserScope model
# =====================================================================


class TestUserScopeModel:
    """Verify the UserScope model directly."""

    def test_user_scope_repr_contains_user_id(self, app, admin_user):
        """The UserScope repr should include the user_id."""
        scope = admin_user.scopes[0]
        assert str(admin_user.id) in repr(scope)

    def test_user_scope_repr_contains_scope_type(self, app, admin_user):
        """The UserScope repr should include the scope_type."""
        scope = admin_user.scopes[0]
        assert "organization" in repr(scope)

    def test_division_scope_has_division_relationship(
        self, app, manager_user, sample_org
    ):
        """
        A division-type scope should have a loaded division
        relationship pointing to the correct Division record.
        """
        div_scopes = [s for s in manager_user.scopes if s.scope_type == "division"]
        assert len(div_scopes) >= 1
        scope = div_scopes[0]
        assert scope.division is not None
        assert scope.division.id == sample_org["div_a1"].id

    def test_department_scope_has_department_relationship(
        self, app, manager_dept_scope_user, sample_org
    ):
        """
        A department-type scope should have a loaded department
        relationship pointing to the correct Department record.
        """
        dept_scopes = [
            s for s in manager_dept_scope_user.scopes if s.scope_type == "department"
        ]
        assert len(dept_scopes) >= 1
        scope = dept_scopes[0]
        assert scope.department is not None
        assert scope.department.id == sample_org["dept_a"].id

    def test_org_scope_has_null_department_and_division(self, app, admin_user):
        """
        An organization-type scope should have department_id and
        division_id both set to None.
        """
        org_scopes = [s for s in admin_user.scopes if s.scope_type == "organization"]
        assert len(org_scopes) >= 1
        scope = org_scopes[0]
        assert scope.department_id is None
        assert scope.division_id is None
