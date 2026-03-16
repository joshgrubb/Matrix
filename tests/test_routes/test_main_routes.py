"""
Integration tests for the main blueprint and application-level infrastructure.

Covers:
    - Dashboard page (authenticated access, role-specific content).
    - Health check endpoint (public, JSON response, DB verification).
    - Custom error handlers (403, 404).
    - Authentication boundary (unauthenticated redirects to login).
    - Inactive user rejection.

These tests form the "Config & Infrastructure" base of the test pyramid
described in testing_strategy.md.  They verify that the application
boots correctly, serves its landing page, exposes a monitoring endpoint,
and enforces the authentication boundary at the front door.

Run this file in isolation::

    pytest tests/test_routes/test_main_routes.py -v
"""

import json


# =====================================================================
# 1. Health check endpoint (public, no auth required)
# =====================================================================


class TestHealthCheck:
    """
    The /health endpoint is used by load balancers and monitoring
    tools.  It must be accessible without authentication and must
    report database connectivity status.
    """

    def test_health_check_returns_200(self, client):
        """The health endpoint returns 200 when the app and DB are up."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_check_returns_json(self, client):
        """The health endpoint returns a JSON body, not plain text."""
        response = client.get("/health")
        # Flask returns JSON with a trailing newline; parse it.
        data = json.loads(response.data)
        assert "status" in data
        assert "database" in data

    def test_health_check_reports_healthy_status(self, client):
        """When the DB is reachable, the status field is 'healthy'."""
        response = client.get("/health")
        data = json.loads(response.data)
        assert data["status"] == "healthy"

    def test_health_check_reports_database_connected(self, client):
        """When the DB is reachable, the database field is 'connected'."""
        response = client.get("/health")
        data = json.loads(response.data)
        assert data["database"] == "connected"

    def test_health_check_does_not_require_authentication(self, client):
        """
        The health endpoint must work without any auth header.
        This ensures monitoring tools and load balancers can poll
        it without credentials.
        """
        # The plain ``client`` fixture has no auth header.
        response = client.get("/health")
        # Should NOT redirect to login.
        assert response.status_code == 200


# =====================================================================
# 2. Dashboard (authenticated access)
# =====================================================================


class TestDashboard:
    """
    The dashboard is the landing page after login.  It requires
    authentication and displays role-specific content.
    """

    def test_dashboard_returns_200_for_admin(self, auth_client, admin_user):
        """An authenticated admin can reach the dashboard."""
        client = auth_client(admin_user)
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_returns_200_for_manager(self, auth_client, manager_user):
        """An authenticated manager can reach the dashboard."""
        client = auth_client(manager_user)
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_returns_200_for_read_only(self, auth_client, read_only_user):
        """An authenticated read-only user can reach the dashboard."""
        client = auth_client(read_only_user)
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_returns_200_for_it_staff(self, auth_client, it_staff_user):
        """An authenticated IT staff user can reach the dashboard."""
        client = auth_client(it_staff_user)
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_returns_200_for_budget_executive(self, auth_client, budget_user):
        """An authenticated budget executive can reach the dashboard."""
        client = auth_client(budget_user)
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_contains_app_name(self, auth_client, admin_user):
        """The dashboard page should display the application name."""
        client = auth_client(admin_user)
        response = client.get("/")
        assert b"PositionMatrix" in response.data

    def test_dashboard_greets_user_by_name(self, auth_client, admin_user):
        """
        The dashboard template renders the current user's name
        in the welcome message.
        """
        client = auth_client(admin_user)
        response = client.get("/")
        # The template uses {{ current_user.full_name }}.
        assert admin_user.first_name.encode() in response.data

    def test_dashboard_shows_organization_link(self, auth_client, admin_user):
        """
        All authenticated users should see the Organization card
        with a link to browse departments.
        """
        client = auth_client(admin_user)
        response = client.get("/")
        assert b"departments" in response.data.lower()

    def test_dashboard_shows_admin_section_for_admin(self, auth_client, admin_user):
        """
        Admin users should see the Administration section on the
        dashboard with links to user management and audit logs.
        """
        client = auth_client(admin_user)
        response = client.get("/")
        assert b"Administration" in response.data

    def test_dashboard_hides_admin_section_for_manager(self, auth_client, manager_user):
        """
        Managers should NOT see the Administration section.
        The dashboard template conditionally renders it only for
        admin and IT staff roles.
        """
        client = auth_client(manager_user)
        response = client.get("/")
        assert b"Administration" not in response.data

    def test_dashboard_shows_equipment_wizard_link_for_manager(
        self, auth_client, manager_user
    ):
        """
        Managers should see the Position Equipment card with a
        link to start the wizard.
        """
        client = auth_client(manager_user)
        response = client.get("/")
        # The card links to the requirements blueprint.
        assert b"requirements" in response.data.lower() or (
            b"equipment" in response.data.lower()
        )


# =====================================================================
# 3. Unauthenticated access (redirect to login)
# =====================================================================


class TestUnauthenticatedAccess:
    """
    Verify that unauthenticated users are redirected to the login
    page when they attempt to access protected routes.

    The strategy spec lists this as a required test case:
    ``test_unauthenticated_user_redirected_to_login``.
    """

    def test_dashboard_redirects_to_login(self, client):
        """
        An unauthenticated GET / should redirect to the auth login
        page, not return 200.
        """
        response = client.get("/")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower()

    def test_admin_route_redirects_to_login(self, client):
        """Unauthenticated GET /admin/users redirects to login."""
        response = client.get("/admin/users")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower()

    def test_requirements_route_redirects_to_login(self, client):
        """Unauthenticated GET /requirements/ redirects to login."""
        response = client.get("/requirements/")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower()

    def test_reports_route_redirects_to_login(self, client):
        """Unauthenticated GET /reports/cost-summary redirects to login."""
        response = client.get("/reports/cost-summary")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower()

    def test_organization_route_redirects_to_login(self, client):
        """Unauthenticated GET /org/departments redirects to login."""
        response = client.get("/org/departments")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower()

    def test_redirect_preserves_next_parameter(self, client):
        """
        The login redirect should include a ``next`` parameter so
        the user returns to their original destination after login.
        """
        response = client.get("/")
        location = response.headers.get("Location", "")
        assert "next" in location.lower()


# =====================================================================
# 4. Inactive user rejection
# =====================================================================


class TestInactiveUserAccess:
    """
    Verify that deactivated users cannot access the dashboard.

    The strategy spec lists ``test_deactivated_user_cannot_login``
    as a required test case.
    """

    def test_inactive_user_cannot_access_dashboard(self, auth_client, inactive_user):
        """
        A deactivated user should be treated as unauthenticated
        and redirected away from the dashboard.
        """
        client = auth_client(inactive_user)
        response = client.get("/")
        # Flask-Login should reject inactive users with either
        # a redirect to login (302) or a 403.
        assert response.status_code in (302, 403)


# =====================================================================
# 5. Custom error handlers
# =====================================================================


class TestErrorHandlers:
    """
    Verify that the custom error pages render correctly.

    The strategy spec (Section 7, P3) lists error handler tests.
    We promote them here because they take seconds to write and
    a broken error page during a CIO demo is embarrassing.
    """

    def test_404_returns_custom_page(self, auth_client, admin_user):
        """
        Requesting a URL that does not match any route should
        return 404 with the custom error template, not a bare
        Flask default page.
        """
        client = auth_client(admin_user)
        response = client.get("/this-route-does-not-exist")
        assert response.status_code == 404
        assert b"Page Not Found" in response.data or (b"404" in response.data)

    def test_403_page_contains_access_denied(self, auth_client, manager_user):
        """
        When a user hits a route they lack permission for (e.g.,
        a manager accessing /admin/users), the 403 page should
        render the custom template with an access-denied message.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403
        assert b"Access Denied" in response.data or (b"403" in response.data)

    def test_404_page_includes_return_link(self, auth_client, admin_user):
        """
        The 404 page should include a link back to the dashboard
        so the user is not stranded.
        """
        client = auth_client(admin_user)
        response = client.get("/nonexistent-page")
        assert response.status_code == 404
        # The template includes a "Return to Dashboard" button.
        assert b"dashboard" in response.data.lower() or (b"Return" in response.data)

    def test_403_page_includes_return_link(self, auth_client, read_only_user):
        """
        The 403 page should include a link back to the dashboard
        so the user is not stranded.
        """
        client = auth_client(read_only_user)
        # read_only cannot access the requirements wizard.
        response = client.get("/requirements/")
        assert response.status_code == 403
        assert b"dashboard" in response.data.lower() or (b"Return" in response.data)
