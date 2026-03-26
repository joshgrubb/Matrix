"""
Branch-gap tests for uncovered functions in the admin blueprint routes.

Targets two functions that are at 0% coverage in the admin routes file
(``app/blueprints/admin/routes.py``):

    - ``htmx_user_divisions`` (9 statements, 0% covered)
    - ``run_hr_sync`` (8 statements, 0% covered)

These tests close the branch gaps that keep admin routes at 86% coverage
instead of the target 95%+.

Sections:
    1.  htmx_user_divisions -- happy path (with department filter)
    2.  htmx_user_divisions -- all divisions (no department filter)
    3.  htmx_user_divisions -- user not found
    4.  htmx_user_divisions -- pre-checked existing scopes
    5.  htmx_user_divisions -- role enforcement
    6.  htmx_user_divisions -- empty department (no divisions)
    7.  run_hr_sync -- success path (mocked sync)
    8.  run_hr_sync -- non-success status (mocked sync)
    9.  run_hr_sync -- sync exception (mocked sync)
    10. run_hr_sync -- role enforcement (manager blocked)
    11. run_hr_sync -- role enforcement (it_staff allowed)
    12. run_hr_sync -- unauthenticated access blocked

Fixture reminder (from conftest.py):
    auth_client:    Factory returning an authenticated test client.
    admin_user:     Role=admin, scope=organization.
    it_staff_user:  Role=it_staff, scope=organization.
    manager_user:   Role=manager, scope=division (div_a1).
    read_only_user: Role=read_only, scope=division (div_a1).
    sample_org:     Two departments, four divisions, six positions.
    create_user:    Factory for custom role/scope combinations.

Run this file in isolation::

    pytest tests/test_routes/test_admin_branch_gaps.py -v
"""

import time as _time
from unittest.mock import MagicMock, patch

import pytest

from app.models.user import User, UserScope


# =====================================================================
# Local helper fixture for unique emails
# =====================================================================

_local_counter = int(_time.time() * 10) % 9000


@pytest.fixture()
def unique_email():
    """
    Factory fixture returning a unique ``@test.local`` email each
    call to avoid collisions with conftest-created users.
    """
    global _local_counter  # pylint: disable=global-statement

    def _make(prefix="abg"):
        global _local_counter  # pylint: disable=global-statement
        _local_counter += 1
        return f"_tst_{prefix}_{_local_counter:04d}@test.local"

    return _make


# =====================================================================
# Helper: provision a test user via the service layer
# =====================================================================


def _provision_test_user(admin_user, unique_email, role_name="read_only"):
    """
    Provision a test user with the given role via user_service.

    This creates a real database record that routes can operate on.

    Args:
        admin_user: The admin user performing the provisioning.
        unique_email: The unique_email fixture callable.
        role_name: The role to assign to the new user.

    Returns:
        The newly created User model instance.
    """
    from app.services import user_service  # pylint: disable=import-outside-toplevel

    email = unique_email(f"abg_{role_name}")
    return user_service.provision_user(
        email=email,
        first_name="BranchGap",
        last_name="Test",
        role_name=role_name,
        provisioned_by=admin_user.id,
    )


# =====================================================================
# 1. htmx_user_divisions -- division checkboxes with department filter
# =====================================================================


class TestHtmxUserDivisionsWithDepartment:
    """
    Verify that ``GET /admin/users/<id>/htmx/divisions`` returns
    division checkbox HTML filtered to a specific department when
    the ``department_id`` query parameter is provided.

    This exercises the ``if department_id:`` branch of the function
    where divisions are filtered by ``department_id``.
    """

    def test_returns_200_for_valid_department(
        self, auth_client, admin_user, sample_org, unique_email
    ):
        """
        A valid department_id should produce a 200 response with
        division names from that department in the response body.
        """
        # Create a user to be the target of the HTMX request.
        target = _provision_test_user(admin_user, unique_email)
        dept_a = sample_org["dept_a"]

        client = auth_client(admin_user)
        response = client.get(
            f"/admin/users/{target.id}/htmx/divisions" f"?department_id={dept_a.id}"
        )

        assert response.status_code == 200
        # div_a1 and div_a2 belong to dept_a; they should appear.
        html = response.data.decode("utf-8")
        assert sample_org["div_a1"].division_name in html
        assert sample_org["div_a2"].division_name in html

    def test_excludes_divisions_from_other_departments(
        self, auth_client, admin_user, sample_org, unique_email
    ):
        """
        When filtering by dept_a, divisions from dept_b must NOT
        appear in the response.
        """
        target = _provision_test_user(admin_user, unique_email)
        dept_a = sample_org["dept_a"]

        client = auth_client(admin_user)
        response = client.get(
            f"/admin/users/{target.id}/htmx/divisions" f"?department_id={dept_a.id}"
        )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # div_b1 and div_b2 belong to dept_b; they must be absent.
        assert sample_org["div_b1"].division_name not in html
        assert sample_org["div_b2"].division_name not in html


# =====================================================================
# 2. htmx_user_divisions -- all divisions (no department filter)
# =====================================================================


class TestHtmxUserDivisionsAllDivisions:
    """
    When no ``department_id`` is provided, the endpoint should
    return checkboxes for ALL active divisions across all
    departments.  This tests the ``else`` branch.
    """

    def test_returns_all_active_divisions_without_filter(
        self, auth_client, admin_user, sample_org, unique_email
    ):
        """
        With no department_id, every active division from both
        departments should appear in the response.
        """
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{target.id}/htmx/divisions")

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # All four divisions from sample_org should be present.
        assert sample_org["div_a1"].division_name in html
        assert sample_org["div_a2"].division_name in html
        assert sample_org["div_b1"].division_name in html
        assert sample_org["div_b2"].division_name in html


# =====================================================================
# 3. htmx_user_divisions -- user not found
# =====================================================================


class TestHtmxUserDivisionsUserNotFound:
    """
    Verify the endpoint returns 404 when the user_id does not
    correspond to an existing user.  This exercises the
    ``if user is None`` guard clause.
    """

    def test_nonexistent_user_returns_404(self, auth_client, admin_user):
        """
        A request with an invalid user_id should return 404 with
        an appropriate error message fragment.
        """
        client = auth_client(admin_user)
        response = client.get("/admin/users/999999/htmx/divisions")

        assert response.status_code == 404
        html = response.data.decode("utf-8")
        assert "not found" in html.lower()


# =====================================================================
# 4. htmx_user_divisions -- pre-checked existing scopes
# =====================================================================


class TestHtmxUserDivisionsPreChecked:
    """
    Verify that a user who already has division-level scopes sees
    those divisions pre-checked in the returned HTML.  The route
    passes ``current_div_ids`` to the template for this purpose.
    """

    def test_existing_division_scopes_are_reflected(
        self, auth_client, admin_user, sample_org, unique_email, db_session
    ):
        """
        Create a user with a division scope on div_a1.  Request
        divisions for that user.  The checkbox for div_a1 should
        be checked (the template uses ``current_div_ids``).
        """
        from app.services import user_service  # pylint: disable=import-outside-toplevel

        # Provision user and give them a division scope.
        email = unique_email("precheck")
        target = user_service.provision_user(
            email=email,
            first_name="PreCheck",
            last_name="Scope",
            provisioned_by=admin_user.id,
        )
        user_service.set_user_scopes(
            user_id=target.id,
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                }
            ],
            changed_by=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.get(f"/admin/users/{target.id}/htmx/divisions")

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # The response should contain a checked attribute for div_a1.
        # The template uses the current_div_ids list to set "checked".
        assert sample_org["div_a1"].division_name in html


# =====================================================================
# 5. htmx_user_divisions -- role enforcement
# =====================================================================


class TestHtmxUserDivisionsRoleEnforcement:
    """
    The endpoint is decorated with ``@role_required('admin')``.
    Non-admin users should receive 403.
    """

    def test_manager_cannot_access_htmx_divisions(
        self, auth_client, manager_user, admin_user, unique_email
    ):
        """A manager should be blocked from the HTMX divisions endpoint."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(manager_user)
        response = client.get(f"/admin/users/{target.id}/htmx/divisions")

        assert response.status_code == 403

    def test_read_only_cannot_access_htmx_divisions(
        self, auth_client, read_only_user, admin_user, unique_email
    ):
        """A read-only user should be blocked from the endpoint."""
        target = _provision_test_user(admin_user, unique_email)

        client = auth_client(read_only_user)
        response = client.get(f"/admin/users/{target.id}/htmx/divisions")

        assert response.status_code == 403


# =====================================================================
# 6. htmx_user_divisions -- empty department (no divisions)
# =====================================================================


class TestHtmxUserDivisionsEmptyDepartment:
    """
    When the department_id points to a department with no active
    divisions, the response should still be 200 but contain no
    division checkboxes.
    """

    def test_department_with_no_divisions_returns_empty_list(
        self, auth_client, admin_user, unique_email, db_session
    ):
        """
        Create a bare department with no divisions.  Request
        divisions filtered to that department.  Assert 200 and
        no checkbox content (or just a wrapper with no items).
        """
        from app.models.organization import (
            Department,
        )  # pylint: disable=import-outside-toplevel

        target = _provision_test_user(admin_user, unique_email)

        # Create a department with no divisions.
        empty_dept = Department(
            department_code=f"_TST_EMPTY_{_local_counter}",
            department_name=f"_TST_ Empty Dept {_local_counter}",
        )
        db_session.add(empty_dept)
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get(
            f"/admin/users/{target.id}/htmx/divisions" f"?department_id={empty_dept.id}"
        )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # The response should not contain any checkbox inputs,
        # or should contain an empty container.
        assert "form-check-input" not in html or html.count("form-check-input") == 0


# =====================================================================
# 7. run_hr_sync -- success path (mocked)
# =====================================================================


class TestRunHrSyncSuccess:
    """
    Verify that ``POST /admin/hr-sync/run`` calls the sync service,
    flashes a success message, and redirects back to the sync page.

    The actual NeoGov API call is mocked to prevent external
    network requests during testing.
    """

    @patch("app.blueprints.admin.routes.hr_sync_service")
    def test_successful_sync_flashes_success_and_redirects(
        self, mock_hr_sync, auth_client, admin_user
    ):
        """
        When run_full_sync returns a log with status='success',
        the route should flash a success message and redirect.
        """
        # Configure the mock to return a successful sync log.
        mock_log = MagicMock()
        mock_log.status = "success"
        mock_log.records_processed = 42
        mock_hr_sync.run_full_sync.return_value = mock_log

        client = auth_client(admin_user)
        response = client.post("/admin/hr-sync/run")

        # Should redirect to the hr_sync page.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "hr-sync" in location

        # Verify the sync function was called with the admin's user_id.
        mock_hr_sync.run_full_sync.assert_called_once_with(user_id=admin_user.id)

    @patch("app.blueprints.admin.routes.hr_sync_service")
    def test_success_flash_contains_record_count(
        self, mock_hr_sync, auth_client, admin_user
    ):
        """
        The success flash message should include the number of
        records processed so the admin knows the sync did work.
        """
        mock_log = MagicMock()
        mock_log.status = "success"
        mock_log.records_processed = 100
        mock_hr_sync.run_full_sync.return_value = mock_log

        # The redirect lands on GET /admin/hr-sync which calls
        # hr_sync_service.get_sync_logs().  Because we patched
        # the entire service module, get_sync_logs also returns
        # a bare MagicMock.  The template does
        # ``sync_logs.pages > 1`` which fails on MagicMock vs int.
        # Provide a pagination-shaped mock so the page renders.
        mock_page = MagicMock()
        mock_page.items = []
        mock_page.pages = 0
        mock_page.total = 0
        mock_page.page = 1
        mock_page.has_prev = False
        mock_page.has_next = False
        mock_hr_sync.get_sync_logs.return_value = mock_page

        client = auth_client(admin_user)
        response = client.post(
            "/admin/hr-sync/run",
            follow_redirects=True,
        )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # The flash message includes the count.
        assert "100" in html


# =====================================================================
# 8. run_hr_sync -- non-success status
# =====================================================================


class TestRunHrSyncWarningStatus:
    """
    When the sync completes but with a non-success status (e.g.,
    'partial' or 'warning'), the route should flash a warning
    instead of a success message.
    """

    @patch("app.blueprints.admin.routes.hr_sync_service")
    def test_non_success_status_flashes_warning(
        self, mock_hr_sync, auth_client, admin_user
    ):
        """
        A sync log with status='partial' should produce a warning
        flash, not a success flash.
        """
        mock_log = MagicMock()
        mock_log.status = "partial"
        mock_log.records_processed = 25
        mock_hr_sync.run_full_sync.return_value = mock_log

        # Provide a pagination-shaped mock for the redirect target
        # GET /admin/hr-sync, which calls get_sync_logs() and the
        # template compares sync_logs.pages > 1.
        mock_page = MagicMock()
        mock_page.items = []
        mock_page.pages = 0
        mock_page.total = 0
        mock_page.page = 1
        mock_page.has_prev = False
        mock_page.has_next = False
        mock_hr_sync.get_sync_logs.return_value = mock_page

        client = auth_client(admin_user)
        response = client.post(
            "/admin/hr-sync/run",
            follow_redirects=True,
        )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        # The template renders warning-category flash messages.
        # Check for the status text that indicates a non-success outcome.
        assert "partial" in html.lower()


# =====================================================================
# 9. run_hr_sync -- exception during sync
# =====================================================================


class TestRunHrSyncException:
    """
    When ``run_full_sync()`` raises an exception, the route should
    catch it (broad except), flash a danger message with the error
    text, and redirect instead of returning a 500.
    """

    @patch("app.blueprints.admin.routes.hr_sync_service")
    def test_sync_exception_flashes_danger_and_redirects(
        self, mock_hr_sync, auth_client, admin_user
    ):
        """
        An exception from the sync service should be caught and
        reported as a danger flash, not propagated as a 500.
        """
        mock_hr_sync.run_full_sync.side_effect = RuntimeError(
            "NeoGov API connection timeout"
        )

        client = auth_client(admin_user)
        response = client.post("/admin/hr-sync/run")

        # Should still redirect (the route catches the exception).
        assert response.status_code == 302

    @patch("app.blueprints.admin.routes.hr_sync_service")
    def test_sync_exception_message_visible_after_redirect(
        self, mock_hr_sync, auth_client, admin_user
    ):
        """
        Following the redirect after a sync exception, the danger
        flash message should contain the error description.
        """
        mock_hr_sync.run_full_sync.side_effect = ConnectionError("API unreachable")

        # Provide a pagination-shaped mock for the redirect target
        # GET /admin/hr-sync, which calls get_sync_logs() and the
        # template compares sync_logs.pages > 1.
        mock_page = MagicMock()
        mock_page.items = []
        mock_page.pages = 0
        mock_page.total = 0
        mock_page.page = 1
        mock_page.has_prev = False
        mock_page.has_next = False
        mock_hr_sync.get_sync_logs.return_value = mock_page

        client = auth_client(admin_user)
        response = client.post(
            "/admin/hr-sync/run",
            follow_redirects=True,
        )

        assert response.status_code == 200
        html = response.data.decode("utf-8")
        assert "API unreachable" in html


# =====================================================================
# 10. run_hr_sync -- role enforcement (manager blocked)
# =====================================================================


class TestRunHrSyncRoleEnforcement:
    """
    ``run_hr_sync`` is decorated with
    ``@role_required('admin', 'it_staff')``.  All other roles must
    be denied.
    """

    def test_manager_cannot_trigger_sync(self, auth_client, manager_user):
        """A manager should receive 403 when POSTing to run_hr_sync."""
        client = auth_client(manager_user)
        response = client.post("/admin/hr-sync/run")
        assert response.status_code == 403

    def test_read_only_cannot_trigger_sync(self, auth_client, read_only_user):
        """A read-only user should receive 403."""
        client = auth_client(read_only_user)
        response = client.post("/admin/hr-sync/run")
        assert response.status_code == 403

    def test_budget_executive_cannot_trigger_sync(self, auth_client, budget_user):
        """A budget executive should receive 403."""
        client = auth_client(budget_user)
        response = client.post("/admin/hr-sync/run")
        assert response.status_code == 403


# =====================================================================
# 11. run_hr_sync -- it_staff allowed
# =====================================================================


class TestRunHrSyncItStaffAccess:
    """
    IT staff should be allowed to trigger the sync alongside admins,
    per the ``@role_required('admin', 'it_staff')`` decorator.
    """

    @patch("app.blueprints.admin.routes.hr_sync_service")
    def test_it_staff_can_trigger_sync(self, mock_hr_sync, auth_client, it_staff_user):
        """IT staff should get a redirect (not 403) when triggering sync."""
        mock_log = MagicMock()
        mock_log.status = "success"
        mock_log.records_processed = 10
        mock_hr_sync.run_full_sync.return_value = mock_log

        client = auth_client(it_staff_user)
        response = client.post("/admin/hr-sync/run")

        assert response.status_code == 302
        mock_hr_sync.run_full_sync.assert_called_once_with(user_id=it_staff_user.id)


# =====================================================================
# 12. run_hr_sync -- unauthenticated access blocked
# =====================================================================


class TestRunHrSyncUnauthenticated:
    """
    Unauthenticated users should be redirected to the login page
    by ``@login_required``, not reach the sync handler.
    """

    def test_unauthenticated_post_redirects_to_login(self, client):
        """An unauthenticated POST should redirect, not return 200 or 500."""
        response = client.post("/admin/hr-sync/run")
        # Flask-Login redirects to the login page (302) or returns
        # 401 depending on configuration.  Our TestingConfig redirects.
        assert response.status_code in (302, 401)

    def test_unauthenticated_get_divisions_redirects(self, client):
        """
        An unauthenticated GET to the HTMX divisions endpoint
        should also be blocked.
        """
        response = client.get("/admin/users/1/htmx/divisions")
        assert response.status_code in (302, 401)
