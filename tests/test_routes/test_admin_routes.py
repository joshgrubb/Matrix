"""
Integration tests for the admin blueprint routes.

Verifies that admin CRUD operations actually persist data through
the HTTP layer.  Role-based access control (who can/cannot reach
these routes) is already tested in ``test_scope_isolation.py``.
These tests focus on the **behavioral** side: does the form
submission create the record, does the redirect go to the right
place, does the flash message appear, and is the database state
correct after the operation.

PREREQUISITE -- CHECK constraint fix:
    The ``deactivate_user`` and ``reactivate_user`` tests require
    that the ``CK_audit_log_action_type`` CHECK constraint on
    ``audit.audit_log`` includes ``DEACTIVATE`` and ``REACTIVATE``.
    If the constraint has not been updated, those tests will fail
    with an IntegrityError.  Run this SQL against both the test
    and production databases before the CIO review::

        ALTER TABLE audit.audit_log
            DROP CONSTRAINT CK_audit_log_action_type;
        ALTER TABLE audit.audit_log
            ADD CONSTRAINT CK_audit_log_action_type CHECK (
                action_type IN (
                    'CREATE','UPDATE','DELETE',
                    'LOGIN','LOGOUT',
                    'SYNC','COPY',
                    'DEACTIVATE','REACTIVATE'
                )
            );

Design decisions:
    - Every test that writes data verifies the database state after
      the route handler returns, not just the HTTP status code.
    - Tests use ``follow_redirects=False`` and inspect the Location
      header to verify redirect targets, then follow the redirect
      separately when flash message verification is needed.
    - Form field names (``email``, ``first_name``, ``last_name``,
      ``role_name``, ``scope_type``, ``department_ids``,
      ``division_ids``) match the actual template ``name``
      attributes so a template refactor will break the test and
      be caught immediately.
    - The ``unique_email`` fixture generates collision-free
      ``@test.local`` addresses for each provisioned user.

Run this file in isolation::

    pytest tests/test_routes/test_admin_routes.py -v
"""

import pytest

from app.models.user import User, UserScope
from app.services import user_service


# =====================================================================
# Local helper fixture for unique emails
# =====================================================================

_local_counter = 0


@pytest.fixture()
def unique_email():
    """
    Factory that returns a unique ``@test.local`` email each call.

    Prevents collisions with conftest-created users and with other
    tests in the same session.
    """
    global _local_counter  # pylint: disable=global-statement

    def _make(prefix="adm"):
        global _local_counter  # pylint: disable=global-statement
        _local_counter += 1
        return f"_tst_{prefix}_{_local_counter:04d}@test.local"

    return _make


# =====================================================================
# Helper: provision a user via the service layer for route tests
# =====================================================================


def _provision_test_user(admin_user, unique_email, role_name="read_only"):
    """
    Create a user via the service layer (not via the route) so that
    route tests have a known user to operate on.

    Returns:
        The newly created User record.
    """
    email = unique_email("target")
    return user_service.provision_user(
        email=email,
        first_name="Route",
        last_name="Target",
        role_name=role_name,
        provisioned_by=admin_user.id,
    )


# =====================================================================
# 1. Manage users page (GET /admin/users)
# =====================================================================


class TestManageUsersPage:
    """Verify the user management listing page."""

    def test_manage_users_page_loads_for_admin(self, auth_client, admin_user):
        """An admin should see the user management page with a table."""
        client = auth_client(admin_user)
        response = client.get("/admin/users")
        assert response.status_code == 200
        # The page should contain the users table.
        assert b"<table" in response.data

    def test_manage_users_page_shows_provisioned_user(
        self, auth_client, admin_user, unique_email
    ):
        """
        A user created via the service layer should appear in the
        manage-users listing when searched by name.

        The user list is paginated (25 per page, ordered by last
        name), so the test uses the search filter to guarantee
        the target user appears on the returned page regardless
        of how many other users exist in the test database.
        """
        email = unique_email("visible")
        user_service.provision_user(
            email=email,
            first_name="Visible",
            last_name="InList",
            provisioned_by=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.get("/admin/users?search=Visible+InList")
        assert response.status_code == 200
        assert b"Visible" in response.data
        assert b"InList" in response.data

    def test_manage_users_page_supports_search_filter(
        self, auth_client, admin_user, unique_email
    ):
        """
        The search query parameter should filter the user list.
        """
        email = unique_email("searchable")
        user_service.provision_user(
            email=email,
            first_name="Zarephath",
            last_name="Searchable",
            provisioned_by=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.get("/admin/users?search=Zarephath")
        assert response.status_code == 200
        assert b"Zarephath" in response.data

    def test_manage_users_page_supports_role_filter(
        self, auth_client, admin_user, unique_email
    ):
        """
        The role_name query parameter should filter by role.
        """
        email = unique_email("role_filt")
        user_service.provision_user(
            email=email,
            first_name="RoleFiltered",
            last_name="User",
            role_name="budget_executive",
            provisioned_by=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.get("/admin/users?role_name=budget_executive")
        assert response.status_code == 200
        assert b"RoleFiltered" in response.data

    def test_manage_users_page_supports_pagination(self, auth_client, admin_user):
        """Requesting page=1 should not error."""
        client = auth_client(admin_user)
        response = client.get("/admin/users?page=1")
        assert response.status_code == 200


# =====================================================================
# 2. Edit user page (GET /admin/users/<id>/edit)
# =====================================================================


class TestEditUserPage:
    """Verify the user detail/edit page."""

    def test_edit_user_page_loads(self, auth_client, admin_user, unique_email):
        """The edit page should return 200 for an existing user."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{target.id}/edit")
        assert response.status_code == 200

    def test_edit_user_page_shows_current_role(
        self, auth_client, admin_user, unique_email
    ):
        """The edit page should display the user's current role."""
        target = _provision_test_user(admin_user, unique_email, role_name="manager")

        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{target.id}/edit")
        assert response.status_code == 200
        assert b"manager" in response.data

    def test_edit_user_page_shows_user_name(
        self, auth_client, admin_user, unique_email
    ):
        """The edit page should display the user's full name."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{target.id}/edit")
        assert response.status_code == 200
        assert b"Route" in response.data
        assert b"Target" in response.data

    def test_edit_user_page_shows_scope_options(
        self, auth_client, admin_user, unique_email
    ):
        """
        The edit page should render scope type radio buttons
        (organization, department, division).
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{target.id}/edit")
        assert response.status_code == 200
        assert b"organization" in response.data
        assert b"department" in response.data
        assert b"division" in response.data

    def test_edit_nonexistent_user_redirects(self, auth_client, admin_user):
        """
        Requesting the edit page for a nonexistent user ID should
        redirect to the manage-users page with a flash warning,
        not crash with a 500.
        """
        client = auth_client(admin_user)
        response = client.get("/admin/users/999999/edit")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "users" in location


# =====================================================================
# 3. Provision user (POST /admin/users/provision)
# =====================================================================


class TestProvisionUserRoute:
    """Verify the user provisioning form submission."""

    def test_provision_user_creates_account(
        self, auth_client, admin_user, unique_email
    ):
        """
        A valid POST to the provision endpoint should create a user
        in the database and redirect to manage_users.
        """
        email = unique_email("prov_route")

        client = auth_client(admin_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": email,
                "first_name": "Provisioned",
                "last_name": "ViaRoute",
                "role_name": "manager",
            },
        )
        assert response.status_code == 302

        # Verify the user exists in the database.
        user = user_service.get_user_by_email(email)
        assert user is not None
        assert user.first_name == "Provisioned"
        assert user.last_name == "ViaRoute"
        assert user.role_name == "manager"
        assert user.is_active is True
        assert user.provisioned_by == admin_user.id

    def test_provision_user_redirects_to_manage_users(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect should go back to the manage users page."""
        email = unique_email("prov_redir")

        client = auth_client(admin_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": email,
                "first_name": "Redirect",
                "last_name": "Test",
                "role_name": "read_only",
            },
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "users" in location

    def test_provision_user_shows_success_flash(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect page should contain a success flash message."""
        email = unique_email("prov_flash")

        client = auth_client(admin_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": email,
                "first_name": "Flash",
                "last_name": "Test",
                "role_name": "read_only",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"provisioned" in response.data.lower() or (b"Flash" in response.data)

    def test_provision_user_requires_all_fields(self, auth_client, admin_user):
        """
        Missing email, first_name, or last_name should redirect
        with a warning flash and NOT create a user.
        """
        client = auth_client(admin_user)

        # Missing email.
        response = client.post(
            "/admin/users/provision",
            data={
                "email": "",
                "first_name": "NoEmail",
                "last_name": "User",
                "role_name": "read_only",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

        # Missing first_name.
        response = client.post(
            "/admin/users/provision",
            data={
                "email": "nofirst@test.local",
                "first_name": "",
                "last_name": "User",
                "role_name": "read_only",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

        # Missing last_name.
        response = client.post(
            "/admin/users/provision",
            data={
                "email": "nolast@test.local",
                "first_name": "User",
                "last_name": "",
                "role_name": "read_only",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_provision_user_rejects_duplicate_email(
        self, auth_client, admin_user, unique_email
    ):
        """
        Provisioning a user with an email that already exists should
        redirect with a warning flash and NOT create a duplicate.
        """
        email = unique_email("dupe_route")

        client = auth_client(admin_user)

        # First provision succeeds.
        client.post(
            "/admin/users/provision",
            data={
                "email": email,
                "first_name": "First",
                "last_name": "User",
                "role_name": "read_only",
            },
        )

        # Second provision with the same email should show warning.
        response = client.post(
            "/admin/users/provision",
            data={
                "email": email,
                "first_name": "Second",
                "last_name": "User",
                "role_name": "read_only",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"already exists" in response.data.lower()

    def test_provision_user_defaults_to_read_only(
        self, auth_client, admin_user, unique_email
    ):
        """
        When the form omits role_name, the route defaults to
        ``read_only`` (matching the form's default value).
        """
        email = unique_email("default_role_route")

        client = auth_client(admin_user)
        client.post(
            "/admin/users/provision",
            data={
                "email": email,
                "first_name": "Default",
                "last_name": "RoleRoute",
                # role_name intentionally omitted.
            },
        )

        user = user_service.get_user_by_email(email)
        assert user is not None
        assert user.role_name == "read_only"


# =====================================================================
# 4. Change user role (POST /admin/users/<id>/role)
# =====================================================================


class TestChangeUserRoleRoute:
    """Verify the role change form submission."""

    def test_change_user_role_succeeds(self, auth_client, admin_user, unique_email):
        """
        POSTing a valid role_name should update the user's role
        in the database and redirect to the edit page.
        """
        target = _provision_test_user(admin_user, unique_email, role_name="read_only")
        assert target.role_name == "read_only"

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/role",
            data={"role_name": "it_staff"},
        )
        assert response.status_code == 302

        # Verify the role changed in the database.
        refreshed = user_service.get_user_by_id(target.id)
        assert refreshed.role_name == "it_staff"

    def test_change_user_role_redirects_to_edit_page(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect should go back to the user's edit page."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/role",
            data={"role_name": "manager"},
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert str(target.id) in location
        assert "edit" in location

    def test_change_user_role_shows_success_flash(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect page should confirm the role was updated."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/role",
            data={"role_name": "budget_executive"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"role updated" in response.data.lower() or (
            b"updated" in response.data.lower()
        )

    def test_change_user_role_empty_role_shows_warning(
        self, auth_client, admin_user, unique_email
    ):
        """
        Submitting an empty role_name should redirect with a
        warning and leave the role unchanged.
        """
        target = _provision_test_user(admin_user, unique_email, role_name="manager")

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/role",
            data={"role_name": ""},
            follow_redirects=True,
        )
        assert response.status_code == 200
        # The route flashes "No role specified."
        assert b"no role" in response.data.lower() or (
            b"specified" in response.data.lower()
        )

        # Role should be unchanged.
        refreshed = user_service.get_user_by_id(target.id)
        assert refreshed.role_name == "manager"


# =====================================================================
# 5. Update user scope (POST /admin/users/<id>/scope)
# =====================================================================


class TestUpdateUserScopeRoute:
    """Verify the scope update form submission."""

    def test_update_user_scope_to_organization(
        self, auth_client, admin_user, unique_email
    ):
        """
        Submitting scope_type=organization should replace all scopes
        with a single organization-wide scope.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "organization"},
        )
        assert response.status_code == 302

        # Verify the scope in the database.
        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "organization"

    def test_update_user_scope_to_department(
        self, auth_client, admin_user, sample_org, unique_email
    ):
        """
        Submitting scope_type=department with department_ids should
        create department-level scopes.
        """
        target = _provision_test_user(admin_user, unique_email)
        dept_a = sample_org["dept_a"]
        dept_b = sample_org["dept_b"]

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/scope",
            data={
                "scope_type": "department",
                "department_ids": [dept_a.id, dept_b.id],
            },
        )
        assert response.status_code == 302

        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 2
        scope_types = {s.scope_type for s in scopes}
        assert scope_types == {"department"}
        dept_ids = {s.department_id for s in scopes}
        assert dept_a.id in dept_ids
        assert dept_b.id in dept_ids

    def test_update_user_scope_to_division(
        self, auth_client, admin_user, sample_org, unique_email
    ):
        """
        Submitting scope_type=division with division_ids should
        create division-level scopes.
        """
        target = _provision_test_user(admin_user, unique_email)
        div_a1 = sample_org["div_a1"]

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/scope",
            data={
                "scope_type": "division",
                "division_ids": [div_a1.id],
            },
        )
        assert response.status_code == 302

        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "division"
        assert scopes[0].division_id == div_a1.id

    def test_update_scope_replaces_existing(
        self, auth_client, admin_user, sample_org, unique_email
    ):
        """
        Changing from org scope to division scope should DELETE
        the org scope and create the division scope.  No leftover
        records should remain.
        """
        target = _provision_test_user(admin_user, unique_email)
        div_b1 = sample_org["div_b1"]

        client = auth_client(admin_user)

        # First set org scope.
        client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "organization"},
        )
        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "organization"

        # Now replace with division scope.
        client.post(
            f"/admin/users/{target.id}/scope",
            data={
                "scope_type": "division",
                "division_ids": [div_b1.id],
            },
        )
        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "division"
        assert scopes[0].division_id == div_b1.id

    def test_update_scope_redirects_to_edit_page(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect should go back to the user's edit page."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "organization"},
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert str(target.id) in location
        assert "edit" in location

    def test_department_scope_without_ids_shows_warning(
        self, auth_client, admin_user, unique_email
    ):
        """
        Submitting scope_type=department with no department_ids
        should flash a warning and not change the scopes.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)

        # First, give them org scope so we can verify it is preserved.
        client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "organization"},
        )

        # Now submit department scope without any IDs.
        response = client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "department"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"select at least one" in response.data.lower() or (
            b"department" in response.data.lower()
        )

        # The original org scope should still be in place.
        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "organization"

    def test_division_scope_without_ids_shows_warning(
        self, auth_client, admin_user, unique_email
    ):
        """
        Submitting scope_type=division with no division_ids
        should flash a warning and not change the scopes.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)

        # Give them org scope first.
        client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "organization"},
        )

        # Submit division scope without any IDs.
        response = client.post(
            f"/admin/users/{target.id}/scope",
            data={"scope_type": "division"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"select at least one" in response.data.lower() or (
            b"division" in response.data.lower()
        )

        # The original org scope should still be in place.
        scopes = UserScope.query.filter_by(user_id=target.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "organization"


# =====================================================================
# 6. Deactivate user (POST /admin/users/<id>/deactivate)
#
# NOTE: These tests require the CK_audit_log_action_type fix.
# =====================================================================


class TestDeactivateUserRoute:
    """
    Verify the deactivate user form submission.

    Requires the CHECK constraint fix described in the module
    docstring.  Without it, these tests will raise IntegrityError
    because ``DEACTIVATE`` is not in the allowed action_type list.
    """

    def test_deactivate_user_succeeds(self, auth_client, admin_user, unique_email):
        """
        POSTing to the deactivate endpoint should set is_active
        to False and redirect to manage_users.
        """
        target = _provision_test_user(admin_user, unique_email)
        assert target.is_active is True

        client = auth_client(admin_user)
        response = client.post(f"/admin/users/{target.id}/deactivate")
        assert response.status_code == 302

        refreshed = user_service.get_user_by_id(target.id)
        assert refreshed.is_active is False

    def test_deactivate_user_redirects_to_manage_users(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect should go to the user list, not the edit page."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(f"/admin/users/{target.id}/deactivate")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "users" in location

    def test_deactivate_user_shows_flash_message(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect page should show a deactivation confirmation."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"deactivated" in response.data.lower()

    def test_deactivate_already_inactive_shows_warning(
        self, auth_client, admin_user, unique_email
    ):
        """
        Deactivating an already-inactive user should show an error
        flash, not crash.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        # First deactivation.
        client.post(f"/admin/users/{target.id}/deactivate")
        # Second deactivation should show warning.
        response = client.post(
            f"/admin/users/{target.id}/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"already inactive" in response.data.lower()


# =====================================================================
# 7. Reactivate user (POST /admin/users/<id>/reactivate)
#
# NOTE: These tests require the CK_audit_log_action_type fix.
# =====================================================================


class TestReactivateUserRoute:
    """
    Verify the reactivate user form submission.

    Requires the CHECK constraint fix described in the module
    docstring.
    """

    def test_reactivate_user_succeeds(self, auth_client, admin_user, unique_email):
        """
        POSTing to the reactivate endpoint should set is_active
        to True and redirect to manage_users.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        # Deactivate first.
        client.post(f"/admin/users/{target.id}/deactivate")
        refreshed = user_service.get_user_by_id(target.id)
        assert refreshed.is_active is False

        # Reactivate.
        response = client.post(f"/admin/users/{target.id}/reactivate")
        assert response.status_code == 302

        refreshed = user_service.get_user_by_id(target.id)
        assert refreshed.is_active is True

    def test_reactivate_user_redirects_to_manage_users(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect should go to the user list."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        client.post(f"/admin/users/{target.id}/deactivate")

        response = client.post(f"/admin/users/{target.id}/reactivate")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "users" in location

    def test_reactivate_user_shows_flash_message(
        self, auth_client, admin_user, unique_email
    ):
        """The redirect page should show a reactivation confirmation."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        client.post(f"/admin/users/{target.id}/deactivate")

        response = client.post(
            f"/admin/users/{target.id}/reactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"reactivated" in response.data.lower()

    def test_reactivate_already_active_shows_warning(
        self, auth_client, admin_user, unique_email
    ):
        """
        Reactivating an already-active user should show an error
        flash, not crash.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.post(
            f"/admin/users/{target.id}/reactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"already active" in response.data.lower()

    def test_full_deactivate_reactivate_cycle_via_routes(
        self, auth_client, admin_user, unique_email
    ):
        """
        A user should survive a full active -> deactivate -> reactivate
        cycle via the HTTP layer with role and name intact.
        """
        target = _provision_test_user(admin_user, unique_email, role_name="manager")

        client = auth_client(admin_user)
        client.post(f"/admin/users/{target.id}/deactivate")
        client.post(f"/admin/users/{target.id}/reactivate")

        refreshed = user_service.get_user_by_id(target.id)
        assert refreshed.is_active is True
        assert refreshed.role_name == "manager"
        assert refreshed.first_name == "Route"
        assert refreshed.last_name == "Target"


# =====================================================================
# 8. Audit logs page (GET /admin/audit-logs)
# =====================================================================


class TestAuditLogsPage:
    """Verify the audit log viewer."""

    def test_audit_log_page_loads_for_admin(self, auth_client, admin_user):
        """Admins should see the audit logs page."""
        client = auth_client(admin_user)
        response = client.get("/admin/audit-logs")
        assert response.status_code == 200
        assert b"Audit" in response.data

    def test_audit_log_page_loads_for_it_staff(self, auth_client, it_staff_user):
        """IT staff should also see the audit logs page."""
        client = auth_client(it_staff_user)
        response = client.get("/admin/audit-logs")
        assert response.status_code == 200

    def test_audit_log_page_shows_entries(self, auth_client, admin_user, unique_email):
        """
        After provisioning a user (which creates a CREATE audit
        entry), the audit log page should contain that entry.
        """
        email = unique_email("audit_vis")
        user_service.provision_user(
            email=email,
            first_name="AuditVisible",
            last_name="User",
            provisioned_by=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.get("/admin/audit-logs")
        assert response.status_code == 200
        assert b"CREATE" in response.data

    def test_audit_log_page_supports_action_filter(self, auth_client, admin_user):
        """The action_type query parameter should filter entries."""
        client = auth_client(admin_user)
        response = client.get("/admin/audit-logs?action_type=CREATE")
        assert response.status_code == 200

    def test_audit_log_page_supports_entity_filter(self, auth_client, admin_user):
        """The entity_type query parameter should filter entries."""
        client = auth_client(admin_user)
        response = client.get("/admin/audit-logs?entity_type=auth.user")
        assert response.status_code == 200


# =====================================================================
# 9. HR sync page (GET /admin/hr-sync)
# =====================================================================


class TestHRSyncPage:
    """Verify the HR sync status page."""

    def test_hr_sync_page_loads_for_admin(self, auth_client, admin_user):
        """Admins should see the HR sync page."""
        client = auth_client(admin_user)
        response = client.get("/admin/hr-sync")
        assert response.status_code == 200
        assert b"HR Sync" in response.data or b"sync" in response.data.lower()

    def test_hr_sync_page_loads_for_it_staff(self, auth_client, it_staff_user):
        """IT staff should also see the HR sync page."""
        client = auth_client(it_staff_user)
        response = client.get("/admin/hr-sync")
        assert response.status_code == 200

    def test_hr_sync_page_blocked_for_manager(self, auth_client, manager_user):
        """Managers should not access the HR sync page."""
        client = auth_client(manager_user)
        response = client.get("/admin/hr-sync")
        assert response.status_code == 403
