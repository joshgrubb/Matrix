"""
Integration tests for the custom HTTP error handlers.

Verifies that the application-level error handlers registered in
``app.__init__._register_error_handlers`` render the correct custom
error pages for 403 Forbidden, 404 Not Found, and 500 Internal
Server Error responses.

The baseline strategy (Section 6.6, Rank 9) calls for three tests.
This file exceeds the baseline with thorough coverage of:

    - Correct HTTP status codes for each error type.
    - Custom template rendering (not Flask/Werkzeug defaults).
    - Specific page content: error code, heading, description, and
      navigation link back to the dashboard.
    - Correct Content-Type headers (HTML, not JSON or plain text).
    - No Python tracebacks or sensitive stack traces in any response.
    - No sensitive configuration values leaked in error pages.
    - Consistent behavior for both authenticated and unauthenticated
      users (where applicable).
    - The 500 handler's ``db.session.rollback()`` side effect.
    - Multiple trigger paths for each error code to ensure the
      handler is globally registered, not route-specific.

Design decisions:
    - **404 tests** use fabricated URLs that do not match any
      registered route.  Multiple nonsensical paths are tested to
      confirm the handler is route-agnostic.
    - **403 tests** trigger the error by having under-privileged
      users (manager, read-only) request admin-only or
      restricted-role routes.  This proves the decorator pipeline
      (``@role_required``) flows into the custom 403 handler.
    - **500 tests** use ``unittest.mock.patch`` to force an
      unhandled exception inside a route handler.  This is the
      only reliable way to trigger a 500 in the test client
      without modifying application code.  The patch targets the
      service function **where it is imported in the route module**
      (``app.blueprints.reports.routes.cost_service``) so the mock
      intercepts the call correctly.
    - **PROPAGATE_EXCEPTIONS caveat:** Flask sets
      ``PROPAGATE_EXCEPTIONS = True`` when ``TESTING = True``.
      This causes unhandled exceptions to bubble up to the test
      runner instead of being routed through ``@app.errorhandler(500)``.
      The 500 tests use a ``disable_exception_propagation`` fixture
      that temporarily sets ``PROPAGATE_EXCEPTIONS = False`` so the
      custom 500 handler is actually invoked.  This does not affect
      403/404 tests because those use ``abort()`` which raises
      Werkzeug ``HTTPException`` subclasses -- Flask always routes
      those through error handlers regardless of the propagation
      setting.
    - All template assertions reference the actual content strings
      from the ``errors/*.html`` templates (e.g., "Page Not Found",
      "Access Denied", "Something Went Wrong") to catch accidental
      template regressions.
    - The ``base.html`` template is verified indirectly: if the
      error page extends ``base.html``, it will contain the
      PositionMatrix branding and the sidebar structure.

Fixture reminder (from conftest.py):
    client:         Unauthenticated Flask test client.
    auth_client:    Factory returning an authenticated test client.
    admin_user:     User with admin role, organization-wide scope.
    manager_user:   User with manager role, division-scoped (div_a1).
    read_only_user: User with read_only role, division-scoped (div_a1).
    budget_user:    User with budget_executive role, org-wide scope.
    app:            The Flask application instance (session-scoped).

Run this file in isolation::

    pytest tests/test_routes/test_error_handlers.py -v
"""

from unittest.mock import patch

import pytest


# =====================================================================
# Fixture: disable PROPAGATE_EXCEPTIONS for 500 handler tests
# =====================================================================


@pytest.fixture()
def disable_exception_propagation(app):
    """
    Temporarily set PROPAGATE_EXCEPTIONS to False.

    Flask sets ``PROPAGATE_EXCEPTIONS = True`` when ``TESTING = True``,
    which causes unhandled exceptions to bypass the 500 error handler
    and bubble directly to the test runner.  This fixture disables
    that behavior so the custom ``@app.errorhandler(500)`` function
    is actually invoked and returns the ``errors/500.html`` template.

    The original value is restored after the test completes.
    """
    original = app.config.get("PROPAGATE_EXCEPTIONS")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    yield
    # Restore original value (may be None, True, or unset).
    if original is None:
        app.config.pop("PROPAGATE_EXCEPTIONS", None)
    else:
        app.config["PROPAGATE_EXCEPTIONS"] = original


# Patch target: the cost_service module reference as imported
# inside the reports blueprint routes module.  Patching here
# ensures the mock intercepts calls made by the route handler.
_COST_SVC_PATCH = (
    "app.blueprints.reports.routes.cost_service.get_department_cost_breakdown"
)


# =====================================================================
# 1. 404 Not Found -- authenticated user
# =====================================================================


class TestNotFoundAuthenticated:
    """
    Verify that authenticated users who request a nonexistent URL
    receive the custom 404 error page, not a bare Werkzeug default.
    """

    def test_nonexistent_url_returns_404_status(self, auth_client, admin_user):
        """A fabricated URL must return HTTP 404."""
        client = auth_client(admin_user)
        response = client.get("/this-route-definitely-does-not-exist")
        assert response.status_code == 404

    def test_404_page_renders_custom_template(self, auth_client, admin_user):
        """
        The response body must contain the custom heading from
        ``errors/404.html``, not the Werkzeug default "Not Found".
        """
        client = auth_client(admin_user)
        response = client.get("/not-a-real-page")
        assert b"Page Not Found" in response.data

    def test_404_page_displays_error_code(self, auth_client, admin_user):
        """
        The custom template includes a large ``pm-error-code`` div
        displaying "404".  Verify the numeric code is present.
        """
        client = auth_client(admin_user)
        response = client.get("/bogus-path-12345")
        assert b"404" in response.data

    def test_404_page_displays_descriptive_message(self, auth_client, admin_user):
        """
        The template includes a user-friendly description telling
        the user the page does not exist or has been moved.
        """
        client = auth_client(admin_user)
        response = client.get("/no-such-route")
        body = response.data.decode("utf-8")
        # The template text: "The page you're looking for doesn't
        # exist or has been moved."
        assert "exist" in body.lower() or "moved" in body.lower()

    def test_404_page_includes_return_to_dashboard_link(self, auth_client, admin_user):
        """
        The error page must include a "Return to Dashboard" link
        so the user is not stranded on a dead-end page.
        """
        client = auth_client(admin_user)
        response = client.get("/nonexistent-page")
        body = response.data.decode("utf-8")
        assert "Return to Dashboard" in body

    def test_404_page_dashboard_link_targets_root(self, auth_client, admin_user):
        """
        The "Return to Dashboard" anchor must point to the main
        dashboard URL (generated by ``url_for('main.dashboard')``).
        """
        client = auth_client(admin_user)
        response = client.get("/fake-url")
        body = response.data.decode("utf-8")
        # The url_for('main.dashboard') resolves to "/".
        assert 'href="/"' in body

    def test_404_page_content_type_is_html(self, auth_client, admin_user):
        """
        The Content-Type header must indicate HTML, not JSON or
        plain text.  A JSON 404 would confuse browser users.
        """
        client = auth_client(admin_user)
        response = client.get("/missing-page")
        assert "text/html" in response.content_type

    def test_404_page_does_not_contain_traceback(self, auth_client, admin_user):
        """
        The response body must never contain a Python traceback.
        Even though 404 is unlikely to produce one, this guards
        against a misconfigured error handler that re-raises.
        """
        client = auth_client(admin_user)
        response = client.get("/doesnotexist")
        assert b"Traceback" not in response.data

    def test_404_page_does_not_leak_config_values(self, auth_client, admin_user):
        """
        The error page must not expose sensitive configuration
        like SECRET_KEY, database connection strings, or Azure
        credentials.
        """
        client = auth_client(admin_user)
        response = client.get("/secret-leak-test")
        body_lower = response.data.lower()
        assert b"secret_key" not in body_lower
        assert b"connection_string" not in body_lower
        assert b"client_secret" not in body_lower

    def test_404_extends_base_template(self, auth_client, admin_user):
        """
        The 404 page extends ``base.html``, which includes the
        PositionMatrix branding.  Verify the page title contains
        the application name.
        """
        client = auth_client(admin_user)
        response = client.get("/imaginary-route")
        body = response.data.decode("utf-8")
        assert "PositionMatrix" in body

    def test_404_on_nested_nonexistent_path(self, auth_client, admin_user):
        """
        A deeply nested path that does not exist should still
        trigger the 404 handler, not a 500 or a different error.
        """
        client = auth_client(admin_user)
        response = client.get("/admin/users/99999/nonexistent/subpage")
        assert response.status_code == 404

    def test_404_on_post_to_nonexistent_url(self, auth_client, admin_user):
        """
        A POST to a nonexistent URL should also return 404, not
        405 Method Not Allowed.  Flask's default router returns
        404 when no route matches regardless of method.
        """
        client = auth_client(admin_user)
        response = client.post("/not-a-real-endpoint", data={"key": "val"})
        assert response.status_code == 404


# =====================================================================
# 2. 404 Not Found -- unauthenticated user
# =====================================================================


class TestNotFoundUnauthenticated:
    """
    Verify 404 behavior for unauthenticated requests.

    Some routes redirect unauthenticated users to login via
    ``@login_required``.  But a truly nonexistent URL should still
    produce a 404, not a redirect, because ``@login_required`` is
    only applied to routes that exist.
    """

    def test_unauthenticated_nonexistent_url_returns_404(self, client):
        """
        An unauthenticated request to a nonexistent URL should
        return 404.  The ``@login_required`` decorator is never
        reached because no route matches.
        """
        response = client.get("/absolutely-no-route-here")
        assert response.status_code == 404

    def test_unauthenticated_404_renders_custom_template(self, client):
        """
        Even without authentication, the custom 404 template
        should render (it does not require ``current_user``).
        """
        response = client.get("/no-such-page")
        assert b"Page Not Found" in response.data

    def test_unauthenticated_404_includes_dashboard_link(self, client):
        """
        The "Return to Dashboard" link should still be present.
        If the user is not logged in, clicking it will redirect
        to login, which is the correct behavior.
        """
        response = client.get("/phantom-url")
        body = response.data.decode("utf-8")
        assert "Return to Dashboard" in body


# =====================================================================
# 3. 403 Forbidden -- role-based denial
# =====================================================================


class TestForbiddenRoleDenial:
    """
    Verify that the custom 403 error page renders when a user
    requests a route they lack the required role for.

    These tests trigger 403 through the ``@role_required`` decorator
    pipeline, which calls ``abort(403)``.  The application-level
    403 error handler should intercept the abort and render the
    custom template.
    """

    def test_manager_admin_route_returns_403_status(self, auth_client, manager_user):
        """
        A manager requesting /admin/users must receive HTTP 403.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_403_page_renders_custom_template(self, auth_client, manager_user):
        """
        The response body must contain the custom heading from
        ``errors/403.html``, not the Werkzeug default "Forbidden".
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"Access Denied" in response.data

    def test_403_page_displays_error_code(self, auth_client, manager_user):
        """The template must display the numeric 403 error code."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"403" in response.data

    def test_403_page_displays_permission_message(self, auth_client, manager_user):
        """
        The template includes a description telling the user they
        lack permission and should contact their administrator.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        body = response.data.decode("utf-8")
        assert "permission" in body.lower()

    def test_403_page_includes_return_to_dashboard_link(
        self, auth_client, manager_user
    ):
        """
        The 403 page must include a "Return to Dashboard" link
        so the user can navigate away from the dead-end.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        body = response.data.decode("utf-8")
        assert "Return to Dashboard" in body

    def test_403_page_dashboard_link_targets_root(self, auth_client, manager_user):
        """The dashboard link must point to '/'."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        body = response.data.decode("utf-8")
        assert 'href="/"' in body

    def test_403_page_content_type_is_html(self, auth_client, manager_user):
        """The Content-Type must be text/html."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert "text/html" in response.content_type

    def test_403_page_does_not_contain_traceback(self, auth_client, manager_user):
        """The 403 page must never expose a Python traceback."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"Traceback" not in response.data

    def test_403_page_does_not_leak_config_values(self, auth_client, manager_user):
        """The 403 page must not expose sensitive configuration."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        body_lower = response.data.lower()
        assert b"secret_key" not in body_lower
        assert b"connection_string" not in body_lower

    def test_403_extends_base_template(self, auth_client, manager_user):
        """
        The 403 page extends ``base.html`` and includes the
        PositionMatrix branding in the page title.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        body = response.data.decode("utf-8")
        assert "PositionMatrix" in body

    def test_403_is_not_a_redirect(self, auth_client, manager_user):
        """
        The response must be a direct 403, not a 302 redirect to
        a login page.  Redirecting to login would indicate that
        ``@login_required`` handled the denial instead of
        ``@role_required``, which would be a decorator-ordering bug.
        """
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403
        assert response.status_code != 302


# =====================================================================
# 4. 403 Forbidden -- multiple roles denied
# =====================================================================


class TestForbiddenMultipleRoles:
    """
    Verify that the 403 handler fires for every under-privileged
    role, not just managers.  This catches bugs where a decorator
    allows an unintended role through.
    """

    def test_read_only_user_denied_admin_route(self, auth_client, read_only_user):
        """Read-only users must receive 403 on admin routes."""
        client = auth_client(read_only_user)
        response = client.get("/admin/users")
        assert response.status_code == 403
        assert b"Access Denied" in response.data

    def test_read_only_user_denied_requirements_wizard(
        self, auth_client, read_only_user
    ):
        """
        Read-only users cannot access the requirements wizard.
        The wizard requires manager, admin, or IT staff roles.
        """
        client = auth_client(read_only_user)
        response = client.get("/requirements/")
        assert response.status_code == 403
        assert b"Access Denied" in response.data

    def test_budget_user_denied_requirements_wizard(self, auth_client, budget_user):
        """
        Budget executives cannot access the requirements wizard.
        They can view reports but not edit requirements.
        """
        client = auth_client(budget_user)
        response = client.get("/requirements/")
        assert response.status_code == 403

    def test_manager_denied_export_route(self, auth_client, manager_user):
        """
        Managers cannot export cost data.  Exports are restricted
        to admin, IT staff, and budget executive roles.
        """
        client = auth_client(manager_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 403
        assert b"Access Denied" in response.data

    def test_read_only_denied_export_route(self, auth_client, read_only_user):
        """Read-only users cannot export cost data."""
        client = auth_client(read_only_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 403


# =====================================================================
# 5. 403 Forbidden -- POST method denial
# =====================================================================


class TestForbiddenPostMethod:
    """
    Verify that the 403 handler fires on POST requests to
    restricted routes, not just GET requests.  A decorator that
    only checks GET but allows POST would be a critical security
    flaw.
    """

    def test_manager_cannot_post_to_provision_user(self, auth_client, manager_user):
        """
        A manager POSTing to the user provisioning endpoint must
        receive 403 with the custom error page.
        """
        client = auth_client(manager_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": "should_not_work@test.local",
                "first_name": "Blocked",
                "last_name": "User",
                "role_name": "read_only",
            },
        )
        assert response.status_code == 403
        assert b"Access Denied" in response.data

    def test_read_only_cannot_post_to_hardware_step(self, auth_client, read_only_user):
        """
        A read-only user POSTing to a hardware selection endpoint
        must receive 403.
        """
        client = auth_client(read_only_user)
        response = client.post(
            "/requirements/position/1/hardware",
            data={"dummy": "data"},
        )
        assert response.status_code == 403


# =====================================================================
# 6. 500 Internal Server Error
# =====================================================================


@pytest.mark.usefixtures("disable_exception_propagation")
class TestInternalServerError:
    """
    Verify that unhandled exceptions in route handlers produce the
    custom 500 error page, not a raw Werkzeug traceback.

    The 500 handler is tested by patching a service function that a
    known route calls, forcing it to raise an unhandled exception.
    This simulates a real server error without modifying application
    code.

    The 500 handler in ``_register_error_handlers`` also calls
    ``db.session.rollback()`` to prevent a broken transaction from
    poisoning subsequent requests.  This side effect is verified
    via a mock.

    The ``disable_exception_propagation`` fixture (applied at class
    level) sets ``PROPAGATE_EXCEPTIONS = False`` so Flask routes the
    exception through ``@app.errorhandler(500)`` instead of
    re-raising it to the test runner.
    """

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated database failure"),
    )
    def test_unhandled_exception_returns_500_status(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        An unhandled RuntimeError inside a route handler must
        result in HTTP 500, not a different status code.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 500

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_renders_custom_template(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The response body must contain the custom heading from
        ``errors/500.html``, not the Werkzeug default traceback.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"Something Went Wrong" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_displays_error_code(self, mock_cost_svc, auth_client, admin_user):
        """The template must display the numeric 500 error code."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"500" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_displays_user_friendly_message(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The template includes a user-friendly description telling
        the user an unexpected error occurred and has been logged.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        body = response.data.decode("utf-8")
        assert "unexpected error" in body.lower() or "error" in body.lower()

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_includes_return_to_dashboard_link(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The 500 page must include a "Return to Dashboard" link
        so the user can recover from the error state.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        body = response.data.decode("utf-8")
        assert "Return to Dashboard" in body

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_dashboard_link_targets_root(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """The dashboard link on the 500 page must point to '/'."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        body = response.data.decode("utf-8")
        assert 'href="/"' in body

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_content_type_is_html(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """The Content-Type must be text/html, not a JSON error."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert "text/html" in response.content_type

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_does_not_contain_traceback(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The 500 page must NEVER contain a Python traceback.
        Exposing tracebacks in production is a security risk
        (leaking file paths, library versions, variable contents)
        and unprofessional during a CIO demo.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"Traceback" not in response.data
        assert b"RuntimeError" not in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_does_not_leak_exception_message(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The specific exception message ("Simulated crash") must
        not appear in the response body.  Exception details belong
        in server logs, not in the user-facing page.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"Simulated crash" not in response.data
        assert b"Simulated database failure" not in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_page_does_not_leak_config_values(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The 500 page must not expose sensitive configuration
        values like SECRET_KEY, database URIs, or Azure secrets.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        body_lower = response.data.lower()
        assert b"secret_key" not in body_lower
        assert b"connection_string" not in body_lower
        assert b"client_secret" not in body_lower

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_extends_base_template(self, mock_cost_svc, auth_client, admin_user):
        """The 500 page extends ``base.html`` with PositionMatrix branding."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        body = response.data.decode("utf-8")
        assert "PositionMatrix" in body

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Simulated crash"),
    )
    def test_500_handler_calls_db_session_rollback(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        The 500 handler calls ``db.session.rollback()`` to clean
        up any broken transaction state.  Without this, subsequent
        database operations in the same process could fail with
        "transaction is aborted" errors.

        We verify this by patching ``db.session.rollback`` and
        asserting it was called.
        """
        with patch("app.extensions.db.session.rollback") as mock_rollback:
            client = auth_client(admin_user)
            response = client.get("/reports/cost-summary")
            assert response.status_code == 500
            mock_rollback.assert_called()


# =====================================================================
# 7. 500 triggered by different exception types
# =====================================================================


@pytest.mark.usefixtures("disable_exception_propagation")
class TestInternalServerErrorVariousExceptions:
    """
    Verify that the 500 handler catches different exception types,
    not just RuntimeError.  This proves the handler is a true
    catch-all registered via ``@app.errorhandler(500)``.
    """

    @patch(
        _COST_SVC_PATCH,
        side_effect=TypeError("NoneType is not iterable"),
    )
    def test_type_error_returns_500(self, mock_cost_svc, auth_client, admin_user):
        """A TypeError must be caught by the 500 handler."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 500
        assert b"Something Went Wrong" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=KeyError("missing_key"),
    )
    def test_key_error_returns_500(self, mock_cost_svc, auth_client, admin_user):
        """A KeyError must be caught by the 500 handler."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 500
        assert b"Something Went Wrong" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=AttributeError("object has no attribute 'foo'"),
    )
    def test_attribute_error_returns_500(self, mock_cost_svc, auth_client, admin_user):
        """An AttributeError must be caught by the 500 handler."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 500
        assert b"Something Went Wrong" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=ValueError("invalid literal for int()"),
    )
    def test_value_error_returns_500(self, mock_cost_svc, auth_client, admin_user):
        """A ValueError must be caught by the 500 handler."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 500
        assert b"Something Went Wrong" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=TypeError("NoneType is not iterable"),
    )
    def test_exception_message_never_in_response_body(
        self, mock_cost_svc, auth_client, admin_user
    ):
        """
        Regardless of exception type, the internal message must
        never appear in the response body.
        """
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"NoneType is not iterable" not in response.data


# =====================================================================
# 8. Error page title verification
# =====================================================================


class TestErrorPageTitles:
    """
    Verify that each error page sets a meaningful ``<title>`` tag.

    The browser tab title is one of the first things a user (or CIO)
    notices.  A generic "Error" or blank title looks unprofessional.
    """

    def test_404_page_title_contains_404(self, auth_client, admin_user):
        """The 404 page ``<title>`` must contain '404'."""
        client = auth_client(admin_user)
        response = client.get("/no-such-page")
        body = response.data.decode("utf-8")
        # Template: {% block title %}404 Not Found - PositionMatrix{% endblock %}
        assert "404" in body
        # Verify it appears in the <title> tag specifically.
        assert "<title>" in body.lower()
        title_start = body.lower().index("<title>")
        title_end = body.lower().index("</title>")
        title_text = body[title_start:title_end]
        assert "404" in title_text

    def test_403_page_title_contains_403(self, auth_client, manager_user):
        """The 403 page ``<title>`` must contain '403'."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        body = response.data.decode("utf-8")
        title_start = body.lower().index("<title>")
        title_end = body.lower().index("</title>")
        title_text = body[title_start:title_end]
        assert "403" in title_text

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Boom"),
    )
    def test_500_page_title_contains_500(
        self,
        mock_cost_svc,
        auth_client,
        admin_user,
        disable_exception_propagation,
    ):
        """The 500 page ``<title>`` must contain '500'."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        body = response.data.decode("utf-8")
        title_start = body.lower().index("<title>")
        title_end = body.lower().index("</title>")
        title_text = body[title_start:title_end]
        assert "500" in title_text

    def test_404_page_title_contains_application_name(self, auth_client, admin_user):
        """The 404 page title must include 'PositionMatrix'."""
        client = auth_client(admin_user)
        response = client.get("/nope")
        body = response.data.decode("utf-8")
        title_start = body.lower().index("<title>")
        title_end = body.lower().index("</title>")
        title_text = body[title_start:title_end]
        assert "positionmatrix" in title_text.lower()


# =====================================================================
# 9. Error handler response body size
# =====================================================================


class TestErrorPageBodySize:
    """
    Verify that error pages return a non-trivial response body.

    An empty or near-empty response (< 100 bytes) indicates the
    template failed to render or was not found.  A bare Werkzeug
    default page is typically ~200 bytes, while the custom templates
    with base.html structure should be several KB.
    """

    def test_404_response_body_is_substantial(self, auth_client, admin_user):
        """The 404 page must have a body larger than 500 bytes."""
        client = auth_client(admin_user)
        response = client.get("/fake")
        assert len(response.data) > 500, (
            f"404 body is only {len(response.data)} bytes -- "
            f"likely a bare default, not the custom template"
        )

    def test_403_response_body_is_substantial(self, auth_client, manager_user):
        """The 403 page must have a body larger than 500 bytes."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert len(response.data) > 500, (
            f"403 body is only {len(response.data)} bytes -- "
            f"likely a bare default, not the custom template"
        )

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Crash"),
    )
    def test_500_response_body_is_substantial(
        self,
        mock_cost_svc,
        auth_client,
        admin_user,
        disable_exception_propagation,
    ):
        """The 500 page must have a body larger than 500 bytes."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert len(response.data) > 500, (
            f"500 body is only {len(response.data)} bytes -- "
            f"likely a bare default, not the custom template"
        )


# =====================================================================
# 10. Error handler icon verification
# =====================================================================


class TestErrorPageIcons:
    """
    Verify that the Bootstrap Icons referenced in the error
    templates are present in the response body.

    Each error page includes a ``<i class="bi bi-house-door"></i>``
    icon in the "Return to Dashboard" button.  If the icon class
    is missing, the button looks broken.
    """

    def test_404_page_includes_house_icon(self, auth_client, admin_user):
        """The 404 page dashboard button must include the house icon."""
        client = auth_client(admin_user)
        response = client.get("/no-such-page")
        assert b"bi-house-door" in response.data

    def test_403_page_includes_house_icon(self, auth_client, manager_user):
        """The 403 page dashboard button must include the house icon."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"bi-house-door" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Crash"),
    )
    def test_500_page_includes_house_icon(
        self,
        mock_cost_svc,
        auth_client,
        admin_user,
        disable_exception_propagation,
    ):
        """The 500 page dashboard button must include the house icon."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"bi-house-door" in response.data


# =====================================================================
# 11. Error handler CSS class verification
# =====================================================================


class TestErrorPageCssStructure:
    """
    Verify that the custom error pages use the expected CSS
    classes from the design system.

    Each error template wraps its content in a ``div`` with class
    ``pm-error-page`` and displays the numeric code in a ``div``
    with class ``pm-error-code``.  If these classes are missing,
    the error page will render without the intended styling.
    """

    def test_404_page_has_error_page_class(self, auth_client, admin_user):
        """The 404 template must include the ``pm-error-page`` wrapper."""
        client = auth_client(admin_user)
        response = client.get("/fake-url")
        assert b"pm-error-page" in response.data

    def test_404_page_has_error_code_class(self, auth_client, admin_user):
        """The 404 template must include the ``pm-error-code`` element."""
        client = auth_client(admin_user)
        response = client.get("/fake-url")
        assert b"pm-error-code" in response.data

    def test_403_page_has_error_page_class(self, auth_client, manager_user):
        """The 403 template must include the ``pm-error-page`` wrapper."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"pm-error-page" in response.data

    def test_403_page_has_error_code_class(self, auth_client, manager_user):
        """The 403 template must include the ``pm-error-code`` element."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"pm-error-code" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Crash"),
    )
    def test_500_page_has_error_page_class(
        self,
        mock_cost_svc,
        auth_client,
        admin_user,
        disable_exception_propagation,
    ):
        """The 500 template must include the ``pm-error-page`` wrapper."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"pm-error-page" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Crash"),
    )
    def test_500_page_has_error_code_class(
        self,
        mock_cost_svc,
        auth_client,
        admin_user,
        disable_exception_propagation,
    ):
        """The 500 template must include the ``pm-error-code`` element."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"pm-error-code" in response.data


# =====================================================================
# 12. Error handler button styling verification
# =====================================================================


class TestErrorPageButtonStyling:
    """
    Verify that the "Return to Dashboard" button uses the correct
    design-system CSS classes.

    Each error template uses ``pm-btn pm-btn-primary`` on the
    anchor element.  If these are missing, the button renders as
    a plain unstyled link, which looks broken during a demo.
    """

    def test_404_button_has_primary_class(self, auth_client, admin_user):
        """The 404 dashboard button must use ``pm-btn-primary``."""
        client = auth_client(admin_user)
        response = client.get("/nonexistent")
        assert b"pm-btn-primary" in response.data

    def test_403_button_has_primary_class(self, auth_client, manager_user):
        """The 403 dashboard button must use ``pm-btn-primary``."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert b"pm-btn-primary" in response.data

    @patch(
        _COST_SVC_PATCH,
        side_effect=RuntimeError("Crash"),
    )
    def test_500_button_has_primary_class(
        self,
        mock_cost_svc,
        auth_client,
        admin_user,
        disable_exception_propagation,
    ):
        """The 500 dashboard button must use ``pm-btn-primary``."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert b"pm-btn-primary" in response.data
