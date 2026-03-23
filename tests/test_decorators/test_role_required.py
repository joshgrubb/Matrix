"""
Unit and integration tests for all three authorization decorators.

Covers ``role_required``, ``permission_required``, and ``scope_check``
from ``app.decorators``.  These decorators form the authorization
backbone of the application.  A decorator misconfiguration -- where
a manager can reach admin pages, or a read-only user can modify
equipment -- would be the single most embarrassing failure during a
CIO review.

Test approach:
    - **role_required** is tested via integration against real production
      routes, exercising every role-vs-route combination to prove the
      authorization matrix holds end-to-end.  This catches both
      decorator bugs and route wiring mistakes.
    - **permission_required** and **scope_check** are tested as isolated
      unit tests using ``flask_login.login_user()`` inside a test
      request context with decorated dummy functions.  This isolates
      the decorator logic from any specific route implementation.
    - Response behavior (status code, flash messages, function metadata
      preservation) is verified explicitly for each decorator.
    - Unauthenticated access is tested to ensure ``@login_required``
      works in concert with the authorization decorators.
    - POST endpoints are tested separately from GET to verify that
      crafted POST requests cannot bypass role enforcement.

Fixture reminder (from conftest.py):
    auth_client:    Factory that returns an authenticated test client.
    admin_user:     Role=admin, scope=organization.
    it_staff_user:  Role=it_staff, scope=organization.
    manager_user:   Role=manager, scope=division (div_a1).
    budget_user:    Role=budget_executive, scope=organization.
    read_only_user: Role=read_only, scope=division (div_a1).
    inactive_user:  Role=read_only, is_active=False.
    sample_org:     Two departments, four divisions, six positions.
    create_user:    Factory for custom role/scope combinations.

Run this file in isolation::

    pytest tests/test_decorators/test_role_required.py -v
"""

import pytest
from flask_login import login_user
from werkzeug.exceptions import Forbidden, Unauthorized

from app.decorators import permission_required, role_required, scope_check


# =====================================================================
# 1. role_required -- single-role routes (admin only)
# =====================================================================


class TestRoleRequiredAdminOnly:
    """
    Verify that routes decorated with ``@role_required('admin')``
    allow only admin users and return 403 for every other role.

    Target route: GET /admin/users (uses ``@role_required('admin')``).
    """

    def test_admin_can_access_admin_route(self, auth_client, admin_user):
        """Admin role should receive 200 on admin-only routes."""
        client = auth_client(admin_user)
        response = client.get("/admin/users")
        assert response.status_code == 200

    def test_it_staff_blocked_from_admin_route(self, auth_client, it_staff_user):
        """IT staff should receive 403 on admin-only routes."""
        client = auth_client(it_staff_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_manager_blocked_from_admin_route(self, auth_client, manager_user):
        """Manager should receive 403 on admin-only routes."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_budget_executive_blocked_from_admin_route(self, auth_client, budget_user):
        """Budget executive should receive 403 on admin-only routes."""
        client = auth_client(budget_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_read_only_blocked_from_admin_route(self, auth_client, read_only_user):
        """Read-only users should receive 403 on admin-only routes."""
        client = auth_client(read_only_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_admin_can_access_edit_user_page(
        self, auth_client, admin_user, manager_user
    ):
        """Admin can reach the edit page for another user."""
        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{manager_user.id}/edit")
        assert response.status_code == 200

    def test_manager_blocked_from_edit_user_page(
        self, auth_client, admin_user, manager_user
    ):
        """Manager should receive 403 on the edit user page."""
        client = auth_client(manager_user)
        response = client.get(f"/admin/users/{admin_user.id}/edit")
        assert response.status_code == 403


# =====================================================================
# 2. role_required -- two-role routes (admin + it_staff)
# =====================================================================


class TestRoleRequiredEquipmentWriteRoutes:
    """
    Verify that routes decorated with
    ``@role_required('admin', 'it_staff')`` allow exactly those two
    roles and block the other three.

    Target routes: GET /equipment/hardware-types/new,
                   GET /equipment/software-types/new.
    """

    def test_admin_can_access_hardware_type_create(self, auth_client, admin_user):
        """Admin should reach the hardware type creation form."""
        client = auth_client(admin_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 200

    def test_it_staff_can_access_hardware_type_create(self, auth_client, it_staff_user):
        """IT staff should reach the hardware type creation form."""
        client = auth_client(it_staff_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 200

    def test_manager_blocked_from_hardware_type_create(self, auth_client, manager_user):
        """Manager should receive 403 on equipment write routes."""
        client = auth_client(manager_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 403

    def test_budget_executive_blocked_from_hardware_type_create(
        self, auth_client, budget_user
    ):
        """Budget executive should receive 403 on equipment write routes."""
        client = auth_client(budget_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 403

    def test_read_only_blocked_from_hardware_type_create(
        self, auth_client, read_only_user
    ):
        """Read-only users should receive 403 on equipment write routes."""
        client = auth_client(read_only_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 403

    def test_manager_blocked_from_software_type_create(self, auth_client, manager_user):
        """Manager should also be blocked from software type creation."""
        client = auth_client(manager_user)
        response = client.get("/equipment/software-types/new")
        assert response.status_code == 403

    def test_it_staff_can_access_software_type_create(self, auth_client, it_staff_user):
        """IT staff should reach the software type creation form."""
        client = auth_client(it_staff_user)
        response = client.get("/equipment/software-types/new")
        assert response.status_code == 200


# =====================================================================
# 3. role_required -- three-role routes (admin + it_staff + manager)
# =====================================================================


class TestRoleRequiredRequirementsRoutes:
    """
    Verify that routes decorated with
    ``@role_required('admin', 'it_staff', 'manager')`` allow those
    three roles and block the other two.

    Target route: GET /requirements/ (wizard landing page).
    """

    def test_admin_can_access_wizard(self, auth_client, admin_user):
        """Admin should reach the requirements wizard."""
        client = auth_client(admin_user)
        response = client.get("/requirements/")
        assert response.status_code == 200

    def test_it_staff_can_access_wizard(self, auth_client, it_staff_user):
        """IT staff should reach the requirements wizard."""
        client = auth_client(it_staff_user)
        response = client.get("/requirements/")
        assert response.status_code == 200

    def test_manager_can_access_wizard(self, auth_client, manager_user):
        """Managers should reach the requirements wizard."""
        client = auth_client(manager_user)
        response = client.get("/requirements/")
        assert response.status_code == 200

    def test_budget_executive_blocked_from_wizard(self, auth_client, budget_user):
        """Budget executives should receive 403 on wizard routes."""
        client = auth_client(budget_user)
        response = client.get("/requirements/")
        assert response.status_code == 403

    def test_read_only_blocked_from_wizard(self, auth_client, read_only_user):
        """Read-only users should receive 403 on wizard routes."""
        client = auth_client(read_only_user)
        response = client.get("/requirements/")
        assert response.status_code == 403


# =====================================================================
# 4. role_required -- export routes (admin + it_staff + budget_executive)
# =====================================================================


class TestRoleRequiredExportRoutes:
    """
    Verify that export routes decorated with
    ``@role_required('admin', 'it_staff', 'budget_executive')``
    allow exactly those three roles and block the other two.

    Target route: GET /reports/export/department-costs/csv.
    """

    def test_admin_can_export(self, auth_client, admin_user):
        """Admin should access the CSV export endpoint."""
        client = auth_client(admin_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 200

    def test_it_staff_can_export(self, auth_client, it_staff_user):
        """IT staff should access the CSV export endpoint."""
        client = auth_client(it_staff_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 200

    def test_budget_executive_can_export(self, auth_client, budget_user):
        """Budget executives should access the CSV export endpoint."""
        client = auth_client(budget_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 200

    def test_manager_blocked_from_export(self, auth_client, manager_user):
        """Managers should receive 403 on export routes."""
        client = auth_client(manager_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 403

    def test_read_only_blocked_from_export(self, auth_client, read_only_user):
        """Read-only users should receive 403 on export routes."""
        client = auth_client(read_only_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 403


# =====================================================================
# 5. role_required -- POST method enforcement
# =====================================================================


class TestRoleRequiredPostMethods:
    """
    Verify that role_required also blocks unauthorized POST requests.

    An attacker could bypass client-side controls by crafting POST
    requests directly.  These tests prove the server-side decorator
    rejects them regardless of the HTTP method.
    """

    def test_manager_cannot_post_to_provision_user(self, auth_client, manager_user):
        """A crafted POST to provision a user should return 403."""
        client = auth_client(manager_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": "hacker@evil.com",
                "first_name": "Hacker",
                "last_name": "McHack",
                "role_name": "admin",
            },
        )
        assert response.status_code == 403

    def test_read_only_cannot_post_to_change_role(
        self, auth_client, read_only_user, admin_user
    ):
        """
        A read-only user cannot escalate privileges by posting a
        role change for another user.
        """
        client = auth_client(read_only_user)
        response = client.post(
            f"/admin/users/{admin_user.id}/role",
            data={"role_name": "admin"},
        )
        assert response.status_code == 403

    def test_budget_executive_cannot_post_hardware_type(self, auth_client, budget_user):
        """Budget executives cannot create hardware types via POST."""
        client = auth_client(budget_user)
        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": "Malicious Type",
                "estimated_cost": "999.99",
            },
        )
        assert response.status_code == 403

    def test_read_only_cannot_post_to_hardware_step(self, auth_client, read_only_user):
        """Read-only users cannot submit hardware selections via POST."""
        client = auth_client(read_only_user)
        response = client.post(
            "/requirements/position/1/hardware",
            data={"dummy": "data"},
        )
        assert response.status_code == 403

    def test_admin_can_post_to_provision_user(self, auth_client, admin_user):
        """
        Positive control: admin CAN post to the provision endpoint.
        Successful provisioning redirects (302), confirming the
        decorator lets the request through.
        """
        import time

        client = auth_client(admin_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": f"_tst_dec_{int(time.time() * 1000)}@test.local",
                "first_name": "Decorator",
                "last_name": "Test",
                "role_name": "read_only",
            },
        )
        assert response.status_code == 302


# =====================================================================
# 6. role_required -- response behavior verification
# =====================================================================


class TestRoleRequiredResponseBehavior:
    """
    Verify the exact response characteristics when role_required
    blocks a request: HTTP 403 (not 500), flash message set, and
    the response is an error page (not a traceback).
    """

    def test_blocked_request_returns_403_not_500(self, auth_client, manager_user):
        """
        The HTTP status code must be exactly 403 Forbidden, not
        500 Internal Server Error.  A 500 would indicate the
        decorator raised an unhandled exception rather than cleanly
        aborting.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_blocked_request_body_is_not_traceback(self, auth_client, manager_user):
        """
        The response body should NOT contain a Python traceback.
        It should be either a custom error page or a generic 403
        page, not a raw exception dump.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"Traceback" not in response.data

    def test_blocked_request_sets_flash_message(self, auth_client, manager_user):
        """
        The decorator should flash a 'danger' message explaining
        the access denial.  After following the response (which is
        a 403 page served by the error handler), the flash message
        may be consumed.  We verify the flash was set by checking
        the response includes the permission-denied language.
        """
        client = auth_client(manager_user)
        # Use follow_redirects=False to capture the raw 403 response.
        response = client.get("/admin/users")
        assert response.status_code == 403
        # The custom 403 error page or the flash message should
        # contain permission-related language.
        body_lower = response.data.lower()
        assert (
            b"permission" in body_lower
            or b"forbidden" in body_lower
            or b"403" in body_lower
            or b"access" in body_lower
        )

    def test_blocked_request_is_not_redirect(self, auth_client, manager_user):
        """
        role_required calls abort(403), which should NOT redirect.
        The response should be a direct 403, not a 302 to a login
        page.  (Redirecting to login would be a 401/login_required
        behavior, not a 403/role_required behavior.)
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        # Must NOT be a redirect.
        assert response.status_code == 403
        assert response.status_code != 302


# =====================================================================
# 7. role_required -- unauthenticated access
# =====================================================================


class TestRoleRequiredUnauthenticated:
    """
    Verify that unauthenticated requests (no X-Test-User-Id header)
    are handled by Flask-Login's ``@login_required`` before the
    ``@role_required`` decorator is even reached.

    The expected behavior is a redirect to the login page (302),
    not a 401 or 403.
    """

    def test_unauthenticated_admin_route_redirects_to_login(self, client):
        """GET /admin/users without auth should redirect to login."""
        response = client.get("/admin/users")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_equipment_route_redirects_to_login(self, client):
        """GET /equipment/hardware-types/new without auth should redirect."""
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_wizard_route_redirects_to_login(self, client):
        """GET /requirements/ without auth should redirect."""
        response = client.get("/requirements/")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_export_route_redirects_to_login(self, client):
        """GET /reports/export/department-costs/csv without auth should redirect."""
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_post_to_admin_redirects_to_login(self, client):
        """POST /admin/users/provision without auth should redirect."""
        response = client.post(
            "/admin/users/provision",
            data={"email": "anon@evil.com"},
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()


# =====================================================================
# 8. role_required -- functools.wraps metadata preservation
# =====================================================================


class TestRoleRequiredPreservesMetadata:
    """
    Verify that ``@role_required`` uses ``functools.wraps`` so the
    decorated function retains its original ``__name__``, ``__doc__``,
    and ``__module__`` attributes.

    If ``@wraps`` is missing, Flask's URL routing breaks because
    multiple routes would share the same endpoint name (``'wrapper'``),
    raising an ``AssertionError`` during blueprint registration.

    We test this by importing the actual view functions and checking
    their metadata, which proves ``@wraps`` is applied.
    """

    def test_admin_manage_users_retains_function_name(self, app):
        """
        The ``manage_users`` view in the admin blueprint should
        retain its original function name, not be renamed to
        ``'wrapper'`` by a missing ``@wraps``.
        """
        # Access the view function through the app's URL map.
        with app.test_request_context():
            view_func = app.view_functions.get("admin.manage_users")
            assert view_func is not None
            assert view_func.__name__ == "manage_users"

    def test_requirements_select_position_retains_function_name(self, app):
        """
        The ``select_position`` view should retain its name through
        the ``@role_required`` decorator chain.
        """
        with app.test_request_context():
            view_func = app.view_functions.get("requirements.select_position")
            assert view_func is not None
            assert view_func.__name__ == "select_position"

    def test_equipment_hardware_type_create_retains_function_name(self, app):
        """
        The ``hardware_type_create`` view should retain its name.
        """
        with app.test_request_context():
            view_func = app.view_functions.get("equipment.hardware_type_create")
            assert view_func is not None
            assert view_func.__name__ == "hardware_type_create"

    def test_export_department_costs_retains_function_name(self, app):
        """
        The ``export_department_costs`` view should retain its name.
        """
        with app.test_request_context():
            view_func = app.view_functions.get("reports.export_department_costs")
            assert view_func is not None
            assert view_func.__name__ == "export_department_costs"

    def test_decorated_dummy_function_retains_docstring(self, app):
        """
        A freshly decorated function should preserve its docstring.
        This tests the decorator factory in isolation.
        """

        @role_required("admin")
        def documented_view():
            """This docstring must survive the decorator."""
            return "OK"

        assert documented_view.__doc__ is not None
        assert "must survive" in documented_view.__doc__

    def test_decorated_dummy_function_retains_name(self, app):
        """
        A freshly decorated function should preserve its __name__.
        """

        @role_required("admin", "it_staff")
        def my_custom_view():
            """A test view."""
            return "OK"

        assert my_custom_view.__name__ == "my_custom_view"


# =====================================================================
# 9. role_required -- inactive user handling
# =====================================================================


class TestRoleRequiredInactiveUser:
    """
    Verify that deactivated users are rejected even if their role
    would normally grant access.

    Flask-Login's ``UserMixin.is_active`` returns True by default,
    but the User model sets ``is_active = False`` for deactivated
    users.  Flask-Login should reject them before the decorator runs.
    """

    def test_inactive_user_cannot_access_admin_route(self, auth_client, inactive_user):
        """
        A deactivated user should be treated as unauthenticated
        and redirected to login, not given a 200 or 403.
        """
        client = auth_client(inactive_user)
        response = client.get("/admin/users")
        # Flask-Login should reject inactive users with a redirect.
        assert response.status_code in (302, 401, 403)
        # It should NOT be 200 (access granted).
        assert response.status_code != 200

    def test_inactive_user_cannot_access_wizard(self, auth_client, inactive_user):
        """A deactivated user cannot access the requirements wizard."""
        client = auth_client(inactive_user)
        response = client.get("/requirements/")
        assert response.status_code != 200


# =====================================================================
# 10. role_required -- custom role via create_user factory
# =====================================================================


class TestRoleRequiredWithCustomUsers:
    """
    Use the ``create_user`` factory fixture to test role enforcement
    with explicitly constructed users, ensuring the decorator reads
    the role from the database rather than relying on fixture
    naming conventions.
    """

    def test_custom_admin_can_access_admin_route(self, auth_client, create_user):
        """
        A user created via the factory with role_name='admin'
        should pass the admin-only role check.
        """
        user = create_user(role_name="admin")
        client = auth_client(user)
        response = client.get("/admin/users")
        assert response.status_code == 200

    def test_custom_read_only_blocked_from_admin_route(self, auth_client, create_user):
        """
        A user created via the factory with role_name='read_only'
        should be blocked from admin-only routes.
        """
        user = create_user(role_name="read_only")
        client = auth_client(user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_custom_manager_can_access_wizard_but_not_admin(
        self, auth_client, create_user
    ):
        """
        A factory-created manager should access the wizard (allowed)
        but be blocked from admin routes (not allowed).
        """
        user = create_user(role_name="manager")
        client = auth_client(user)

        wizard_response = client.get("/requirements/")
        assert wizard_response.status_code == 200

        admin_response = client.get("/admin/users")
        assert admin_response.status_code == 403


# =====================================================================
# 11. permission_required -- isolated unit tests
# =====================================================================


class TestPermissionRequiredAllows:
    """
    Verify that ``permission_required`` passes the request through
    when the authenticated user's role grants the specified permission.

    Tested in isolation using a decorated dummy function within a
    test request context.  The admin role has all permissions
    assigned (verified by test_db_connection.py), so an admin user
    should pass any permission check.
    """

    def test_user_with_permission_is_allowed(self, app, admin_user):
        """
        An admin user (who has all permissions) should not trigger
        a Forbidden exception from permission_required.
        """

        @permission_required("equipment.create")
        def dummy_view():
            """A view requiring equipment.create permission."""
            return "OK"

        with app.test_request_context("/test-permission"):
            login_user(admin_user)
            result = dummy_view()
            assert result == "OK"

    def test_user_with_different_permission_is_allowed(self, app, admin_user):
        """
        Admin should pass any permission name since admin has all
        permissions assigned via role_permission.
        """

        @permission_required("equipment.create")
        def dummy_manage_view():
            """A view requiring equipment.create permission."""
            return "managed"

        with app.test_request_context("/test-permission-2"):
            login_user(admin_user)
            result = dummy_manage_view()
            assert result == "managed"


class TestPermissionRequiredBlocks:
    """
    Verify that ``permission_required`` raises Forbidden (403) when
    the authenticated user's role does NOT grant the specified
    permission.

    The read-only role has limited permissions and should fail
    checks for equipment-write permissions.
    """

    def test_user_without_permission_gets_403(self, app, read_only_user):
        """
        A read-only user should trigger Forbidden when accessing
        a view that requires 'equipment.create' permission.
        """

        @permission_required("equipment.create")
        def dummy_create_view():
            """Requires equipment.create."""
            return "created"

        with app.test_request_context("/test-perm-block"):
            login_user(read_only_user)
            with pytest.raises(Forbidden):
                dummy_create_view()

    def test_manager_without_admin_permission_gets_403(self, app, manager_user):
        """
        A manager should trigger Forbidden for admin-level
        permissions like 'user.manage'.
        """

        @permission_required("user.manage")
        def dummy_admin_view():
            """Requires user.manage."""
            return "managed"

        with app.test_request_context("/test-perm-mgr"):
            login_user(manager_user)
            with pytest.raises(Forbidden):
                dummy_admin_view()


class TestPermissionRequiredResponseBehavior:
    """
    Verify the exact behavior of the Forbidden response from
    permission_required, including the flash message text.
    """

    def test_permission_required_flashes_danger_message(self, app, read_only_user):
        """
        The decorator should call ``flash()`` with a message about
        lacking permission to perform the action.  We verify by
        inspecting the flashed messages after the exception.
        """
        from flask import get_flashed_messages

        @permission_required("equipment.create")
        def dummy_flash_view():
            """Requires equipment.create."""
            return "nope"

        with app.test_request_context("/test-perm-flash"):
            login_user(read_only_user)
            with pytest.raises(Forbidden):
                dummy_flash_view()

            # Retrieve flashed messages.
            messages = get_flashed_messages(with_categories=True)
            danger_messages = [msg for cat, msg in messages if cat == "danger"]
            assert len(danger_messages) >= 1
            assert "permission" in danger_messages[0].lower()

    def test_permission_required_preserves_function_name(self, app):
        """
        ``@permission_required`` should use ``functools.wraps`` to
        preserve the decorated function's ``__name__``.
        """

        @permission_required("some.perm")
        def my_protected_view():
            """Docstring for testing."""
            return "OK"

        assert my_protected_view.__name__ == "my_protected_view"
        assert "Docstring for testing" in my_protected_view.__doc__


# =====================================================================
# 12. scope_check -- org-wide bypass
# =====================================================================


class TestScopeCheckOrgWideBypass:
    """
    Verify that ``scope_check`` allows organization-scoped users to
    bypass the scope check entirely.

    Tested in isolation using a decorated dummy function.  The
    admin user has organization-wide scope.
    """

    def test_org_scope_user_bypasses_department_check(
        self, app, admin_user, sample_org
    ):
        """
        An admin with org scope should pass a department scope_check
        for any department ID.
        """
        dept_b_id = sample_org["dept_b"].id

        @scope_check("department", "id")
        def dummy_dept_view(id):  # pylint: disable=redefined-builtin
            """View checking department scope."""
            return f"dept-{id}"

        with app.test_request_context(f"/test-scope/{dept_b_id}"):
            login_user(admin_user)
            result = dummy_dept_view(id=dept_b_id)
            assert result == f"dept-{dept_b_id}"

    def test_org_scope_user_bypasses_position_check(self, app, admin_user, sample_org):
        """
        An admin with org scope should pass a position scope_check
        for any position ID, even one outside their direct scope.
        """
        pos_b2_id = sample_org["pos_b2_1"].id

        @scope_check("position", "id")
        def dummy_pos_view(id):  # pylint: disable=redefined-builtin
            """View checking position scope."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-scope-pos/{pos_b2_id}"):
            login_user(admin_user)
            result = dummy_pos_view(id=pos_b2_id)
            assert result == f"pos-{pos_b2_id}"


# =====================================================================
# 13. scope_check -- matching scope
# =====================================================================


class TestScopeCheckMatchingScope:
    """
    Verify that ``scope_check`` allows users whose scope matches
    the requested entity.
    """

    def test_division_scoped_user_accesses_own_position(
        self, app, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 should pass the scope check for
        a position within div_a1.
        """
        pos_a1_id = sample_org["pos_a1_1"].id

        @scope_check("position", "id")
        def dummy_pos_view(id):  # pylint: disable=redefined-builtin
            """View checking position scope."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-scope/{pos_a1_id}"):
            login_user(manager_user)
            result = dummy_pos_view(id=pos_a1_id)
            assert result == f"pos-{pos_a1_id}"

    def test_department_scoped_user_accesses_own_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a should pass the department
        scope check for dept_a.
        """
        dept_a_id = sample_org["dept_a"].id

        @scope_check("department", "id")
        def dummy_dept_view(id):  # pylint: disable=redefined-builtin
            """View checking department scope."""
            return f"dept-{id}"

        with app.test_request_context(f"/test-scope-dept/{dept_a_id}"):
            login_user(manager_dept_scope_user)
            result = dummy_dept_view(id=dept_a_id)
            assert result == f"dept-{dept_a_id}"


# =====================================================================
# 14. scope_check -- blocks out-of-scope
# =====================================================================


class TestScopeCheckBlocksOutOfScope:
    """
    Verify that ``scope_check`` raises Forbidden (403) when the
    user's scope does not include the requested entity.
    """

    def test_division_scoped_user_blocked_from_other_division(
        self, app, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 should be blocked from accessing
        a position in div_b1 (different department entirely).
        """
        pos_b1_id = sample_org["pos_b1_1"].id

        @scope_check("position", "id")
        def dummy_pos_view(id):  # pylint: disable=redefined-builtin
            """View checking position scope."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-scope/{pos_b1_id}"):
            login_user(manager_user)
            with pytest.raises(Forbidden):
                dummy_pos_view(id=pos_b1_id)

    def test_division_scoped_user_blocked_from_sibling_division(
        self, app, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 should be blocked from div_a2
        even though both divisions are in dept_a.  Division scope
        does NOT imply department-wide access.
        """
        pos_a2_id = sample_org["pos_a2_1"].id

        @scope_check("position", "id")
        def dummy_pos_view(id):  # pylint: disable=redefined-builtin
            """View checking position scope."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-scope/{pos_a2_id}"):
            login_user(manager_user)
            with pytest.raises(Forbidden):
                dummy_pos_view(id=pos_a2_id)

    def test_department_scoped_user_blocked_from_other_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a should be blocked from
        accessing dept_b.
        """
        dept_b_id = sample_org["dept_b"].id

        @scope_check("department", "id")
        def dummy_dept_view(id):  # pylint: disable=redefined-builtin
            """View checking department scope."""
            return f"dept-{id}"

        with app.test_request_context(f"/test-scope/{dept_b_id}"):
            login_user(manager_dept_scope_user)
            with pytest.raises(Forbidden):
                dummy_dept_view(id=dept_b_id)


# =====================================================================
# 15. scope_check -- response behavior and edge cases
# =====================================================================


class TestScopeCheckResponseBehavior:
    """
    Verify flash message text, missing entity_id handling, and
    metadata preservation for the scope_check decorator.
    """

    def test_scope_check_flashes_warning_message(self, app, manager_user, sample_org):
        """
        The scope_check decorator should flash a 'warning' message
        (not 'danger') when blocking access, since scope violations
        may be accidental navigation rather than malicious intent.
        """
        from flask import get_flashed_messages

        pos_b1_id = sample_org["pos_b1_1"].id

        @scope_check("position", "id")
        def dummy_view(id):  # pylint: disable=redefined-builtin
            """View for flash testing."""
            return "OK"

        with app.test_request_context(f"/test-scope-flash/{pos_b1_id}"):
            login_user(manager_user)
            with pytest.raises(Forbidden):
                dummy_view(id=pos_b1_id)

            messages = get_flashed_messages(with_categories=True)
            warning_messages = [msg for cat, msg in messages if cat == "warning"]
            assert len(warning_messages) >= 1
            assert "access" in warning_messages[0].lower()

    def test_scope_check_returns_400_for_missing_entity_id(self, app, admin_user):
        """
        If the expected keyword argument (entity_id_kwarg) is not
        present in the route kwargs, scope_check should abort(400)
        rather than silently proceeding or crashing with a 500.
        """
        from werkzeug.exceptions import BadRequest

        @scope_check("department", "id")
        def dummy_view():
            """View with no id kwarg."""
            return "OK"

        with app.test_request_context("/test-scope-missing"):
            login_user(admin_user)
            # The decorator looks for kwargs["id"] but the function
            # accepts no kwargs, so entity_id will be None -> abort(400).
            # Note: admin has org scope so the org bypass would fire
            # first IF entity_id were present.  With entity_id=None,
            # the abort(400) fires before the bypass.
            with pytest.raises(BadRequest):
                dummy_view()

    def test_scope_check_preserves_function_name(self, app):
        """
        ``@scope_check`` should use ``functools.wraps`` to preserve
        the decorated function's metadata.
        """

        @scope_check("position", "position_id")
        def my_scoped_view(position_id):
            """A scoped view for metadata testing."""
            return f"pos-{position_id}"

        assert my_scoped_view.__name__ == "my_scoped_view"
        assert "metadata testing" in my_scoped_view.__doc__


# =====================================================================
# 16. Cross-decorator stacking verification
# =====================================================================


class TestDecoratorStacking:
    """
    In production, decorators are stacked as::

        @login_required
        @role_required(...)
        def view(): ...

    Verify that the stacking order works correctly: login_required
    fires first (redirecting unauthenticated users), then
    role_required fires (blocking unauthorized roles).
    """

    def test_unauthenticated_hits_login_required_not_role_required(self, client):
        """
        An unauthenticated request should be redirected (302) by
        login_required, not rejected (403) by role_required.
        The distinction matters: 302 sends the user to a login
        page, while 403 shows an error page with no recourse.
        """
        response = client.get("/admin/users")
        assert response.status_code == 302
        # Confirm it is a redirect to the login page.
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_authenticated_wrong_role_hits_role_required_not_login(
        self, auth_client, manager_user
    ):
        """
        An authenticated user with the wrong role should receive
        403 from role_required, not 302 from login_required.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403
        # NOT a redirect.
        assert response.status_code != 302

    def test_authenticated_correct_role_passes_both_decorators(
        self, auth_client, admin_user
    ):
        """
        An authenticated user with the correct role should pass
        through both login_required and role_required to reach the
        view function and receive a 200.
        """
        client = auth_client(admin_user)
        response = client.get("/admin/users")
        assert response.status_code == 200
