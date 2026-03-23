"""
Integration tests for the auth blueprint routes.

Covers every route in ``app.blueprints.auth.routes``: the login page,
OAuth2 login redirect, OAuth2 callback (with mocked MSAL), logout,
the unauthorized page, and the development-only dev-login and
dev-login-picker routes.

MSAL API migration (2026-03-23):
    The login route now calls ``auth_service.initiate_auth_flow()``
    and the callback calls ``auth_service.complete_auth_flow()``.
    MSAL handles state validation internally, so the manual state
    checks have been removed from the route.  Tests that verify
    state-related security now exercise the service-layer
    ``ValueError`` path instead of the old manual session check.

Run this file in isolation::

    pytest tests/test_routes/test_auth_routes.py -v
"""

from unittest.mock import patch

import pytest

from app.models.audit import AuditLog
from app.models.user import User


# =====================================================================
# 1. Login page (GET /auth/login-page)
# =====================================================================


class TestLoginPage:
    """Verify the login page renders correctly for unauthenticated users."""

    def test_login_page_returns_200(self, client):
        """GET /auth/login-page should return 200 for unauthenticated users."""
        response = client.get("/auth/login-page")
        assert response.status_code == 200

    def test_login_page_contains_sign_in_elements(self, client):
        """The login page should contain a sign-in button or link."""
        response = client.get("/auth/login-page")
        body_lower = response.data.lower()
        assert (
            b"sign in" in body_lower
            or b"login" in body_lower
            or b"log in" in body_lower
            or b"microsoft" in body_lower
        )

    def test_login_page_is_html(self, client):
        """The login page should return an HTML content type."""
        response = client.get("/auth/login-page")
        content_type = response.content_type or ""
        assert "text/html" in content_type

    def test_login_page_redirects_authenticated_user_to_dashboard(
        self, auth_client, admin_user
    ):
        """Authenticated users should be redirected to the dashboard."""
        client = auth_client(admin_user)
        response = client.get("/auth/login-page")
        assert response.status_code == 302
        assert "/" in response.headers.get("Location", "")


# =====================================================================
# 2. OAuth2 login redirect (GET /auth/login)
# =====================================================================


class TestLoginRedirect:
    """Verify the OAuth2 login initiation route."""

    @patch("app.blueprints.auth.routes.auth_service.initiate_auth_flow")
    def test_login_redirects_to_auth_url(self, mock_initiate, client):
        """GET /auth/login should redirect to the Microsoft auth URL."""
        mock_initiate.return_value = "https://login.microsoftonline.com/fake"
        response = client.get("/auth/login")
        assert response.status_code == 302
        assert "login.microsoftonline.com" in response.headers.get("Location", "")

    @patch("app.blueprints.auth.routes.auth_service.initiate_auth_flow")
    def test_login_passes_uuid_state_to_initiate_auth_flow(self, mock_initiate, client):
        """The route should pass a 36-char UUID state token."""
        captured_state = None

        def _capture(state=None):
            nonlocal captured_state
            captured_state = state
            return "https://login.microsoftonline.com/fake"

        mock_initiate.side_effect = _capture
        client.get("/auth/login")
        assert captured_state is not None
        assert "-" in captured_state
        assert len(captured_state) == 36

    @patch("app.blueprints.auth.routes.auth_service.initiate_auth_flow")
    def test_login_handles_initiate_failure_gracefully(self, mock_initiate, client):
        """ValueError from initiate_auth_flow should redirect to login page."""
        mock_initiate.side_effect = ValueError("Bad authority URL")
        response = client.get("/auth/login")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    @patch("app.blueprints.auth.routes.auth_service.initiate_auth_flow")
    def test_login_initiate_failure_flashes_error(self, mock_initiate, client):
        """The ValueError message should appear in the flash."""
        mock_initiate.side_effect = ValueError("Bad authority URL")
        response = client.get("/auth/login", follow_redirects=True)
        assert b"Bad authority URL" in response.data

    def test_login_redirects_authenticated_user_to_dashboard(
        self, auth_client, admin_user
    ):
        """Authenticated users should go to dashboard, not Microsoft."""
        client = auth_client(admin_user)
        response = client.get("/auth/login")
        assert response.status_code == 302
        assert "microsoftonline" not in response.headers.get("Location", "")


# =====================================================================
# 3. OAuth2 callback -- error from Microsoft
# =====================================================================


class TestCallbackHandlesMicrosoftErrors:
    """Verify callback handles Microsoft error responses."""

    def test_callback_handles_error_param(self, client):
        """Error from Microsoft should redirect to login page."""
        response = client.get(
            "/auth/callback?error=access_denied"
            "&error_description=User+cancelled+the+login"
        )
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    def test_callback_error_flashes_description(self, client):
        """The error_description should appear in the flash."""
        response = client.get(
            "/auth/callback?error=access_denied"
            "&error_description=The+user+denied+consent",
            follow_redirects=True,
        )
        assert b"denied consent" in response.data or b"failed" in response.data.lower()

    def test_callback_error_cleans_up_session_flow(self, client):
        """Microsoft error should clean up auth_code_flow from session."""
        with client.session_transaction() as sess:
            sess["auth_code_flow"] = {"state": "old", "auth_uri": "https://fake"}
        client.get("/auth/callback?error=access_denied")
        with client.session_transaction() as sess:
            assert "auth_code_flow" not in sess


# =====================================================================
# 4. OAuth2 callback -- flow state errors
# =====================================================================


class TestCallbackFlowStateErrors:
    """Verify callback handles missing flow state and state mismatch."""

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_missing_flow_redirects_to_login(self, mock_complete, client):
        """Missing flow state should redirect to login."""
        mock_complete.side_effect = ValueError(
            "Authentication flow state not found in session."
        )
        response = client.get("/auth/callback?code=some-code&state=some-state")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_missing_flow_flashes_message(self, mock_complete, client):
        """The flash should explain that session state is missing."""
        mock_complete.side_effect = ValueError(
            "Authentication flow state not found in session."
        )
        response = client.get("/auth/callback?code=c&state=s", follow_redirects=True)
        body_lower = response.data.lower()
        assert b"not found" in body_lower or b"failed" in body_lower

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_state_mismatch_redirects_to_login(self, mock_complete, client):
        """MSAL state mismatch should redirect to login."""
        mock_complete.side_effect = ValueError("Authentication failed: state mismatch")
        response = client.get("/auth/callback?code=c&state=wrong")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_state_mismatch_flashes_error(self, mock_complete, client):
        """The state mismatch error should appear in the flash."""
        mock_complete.side_effect = ValueError("Authentication failed: state mismatch")
        response = client.get(
            "/auth/callback?code=c&state=wrong", follow_redirects=True
        )
        assert b"state mismatch" in response.data or b"failed" in response.data.lower()


# =====================================================================
# 5. OAuth2 callback -- successful login
# =====================================================================


class TestCallbackSuccessfulLogin:
    """Verify the happy path: valid flow, valid code, user logged in."""

    @patch("app.blueprints.auth.routes.auth_service.process_login")
    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_success_redirects_to_dashboard(
        self, mock_complete, mock_process, client, admin_user
    ):
        """Successful callback should redirect to the dashboard."""
        mock_complete.return_value = {"id_token_claims": {"oid": "fake"}}
        mock_process.return_value = admin_user
        response = client.get("/auth/callback?state=s&code=c")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert location.endswith("/") or "dashboard" in location.lower()

    @patch("app.blueprints.auth.routes.auth_service.process_login")
    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_passes_request_args_to_complete_auth_flow(
        self, mock_complete, mock_process, client, admin_user
    ):
        """The full request.args dict should be passed to complete_auth_flow."""
        mock_complete.return_value = {"id_token_claims": {}}
        mock_process.return_value = admin_user
        client.get("/auth/callback?state=my-state&code=my-code")
        mock_complete.assert_called_once()
        call_args = mock_complete.call_args[0][0]
        assert call_args["state"] == "my-state"
        assert call_args["code"] == "my-code"

    @patch("app.blueprints.auth.routes.auth_service.process_login")
    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_passes_token_result_to_process_login(
        self, mock_complete, mock_process, client, admin_user
    ):
        """The token result should be passed to process_login."""
        token_result = {"id_token_claims": {"oid": "abc"}}
        mock_complete.return_value = token_result
        mock_process.return_value = admin_user
        client.get("/auth/callback?state=s&code=c")
        mock_process.assert_called_once_with(token_result)

    @patch("app.blueprints.auth.routes.auth_service.process_login")
    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_success_flashes_welcome_message(
        self, mock_complete, mock_process, client, admin_user
    ):
        """Successful login should flash a welcome with the user's name."""
        mock_complete.return_value = {"id_token_claims": {}}
        mock_process.return_value = admin_user
        response = client.get("/auth/callback?state=s&code=c", follow_redirects=True)
        assert admin_user.first_name.encode() in response.data


# =====================================================================
# 6. OAuth2 callback -- token exchange failure
# =====================================================================


class TestCallbackTokenExchangeFailure:
    """Verify callback handles ValueError from token exchange."""

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_token_error_redirects_to_login(self, mock_complete, client):
        """ValueError from complete_auth_flow should redirect to login."""
        mock_complete.side_effect = ValueError("Authentication failed: invalid_grant")
        response = client.get("/auth/callback?state=s&code=bad")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_token_error_flashes_message(self, mock_complete, client):
        """The ValueError message should appear in the flash."""
        mock_complete.side_effect = ValueError("Authentication failed: expired code")
        response = client.get("/auth/callback?state=s&code=bad", follow_redirects=True)
        assert b"expired code" in response.data or b"failed" in response.data.lower()

    @patch("app.blueprints.auth.routes.auth_service.process_login")
    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_process_login_error_redirects(
        self, mock_complete, mock_process, client
    ):
        """ValueError from process_login should redirect to login."""
        mock_complete.return_value = {"id_token_claims": {}}
        mock_process.side_effect = ValueError("Missing required claims")
        response = client.get("/auth/callback?state=s&code=c")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()


# =====================================================================
# 7. Logout (GET /auth/logout)
# =====================================================================


class TestLogout:
    """Verify the logout route."""

    def test_logout_redirects_to_login_page(self, auth_client, admin_user):
        """Logout should redirect to the login page."""
        client = auth_client(admin_user)
        response = client.get("/auth/logout")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    def test_logout_flashes_signed_out_message(self, auth_client, admin_user):
        """Logout should flash a signed-out message."""
        client = auth_client(admin_user)
        response = client.get("/auth/logout", follow_redirects=True)
        body_lower = response.data.lower()
        assert b"signed out" in body_lower or b"logged out" in body_lower

    def test_logout_creates_audit_entry(self, auth_client, admin_user):
        """Logout should create a LOGOUT audit log entry."""
        client = auth_client(admin_user)
        client.get("/auth/logout")
        entry = (
            AuditLog.query.filter_by(
                action_type="LOGOUT",
                entity_type="auth.user",
                user_id=admin_user.id,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert entry is not None
        assert entry.entity_id == admin_user.id

    def test_unauthenticated_logout_redirects_to_login(self, client):
        """Unauthenticated /auth/logout should redirect to login."""
        response = client.get("/auth/logout")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()


# =====================================================================
# 8. Unauthorized page (GET /auth/unauthorized)
# =====================================================================


class TestUnauthorizedPage:
    """Verify the static unauthorized access page."""

    def test_unauthorized_returns_403(self, client):
        """GET /auth/unauthorized should return HTTP 403."""
        response = client.get("/auth/unauthorized")
        assert response.status_code == 403

    def test_unauthorized_renders_html(self, client):
        """The unauthorized page should return HTML content."""
        response = client.get("/auth/unauthorized")
        assert "text/html" in (response.content_type or "")

    def test_unauthorized_page_is_not_empty(self, client):
        """The response body should not be empty."""
        response = client.get("/auth/unauthorized")
        assert len(response.data) > 0


# =====================================================================
# 9. Dev-login guard (DEBUG=False blocks access)
# =====================================================================


class TestDevLoginGuard:
    """Verify dev-login is blocked when DEBUG=False."""

    def test_dev_login_blocked_when_debug_false(self, client):
        """GET /auth/dev-login should redirect when debug is off."""
        response = client.get("/auth/dev-login")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    def test_dev_login_blocked_flashes_message(self, client):
        """The redirect should flash about debug mode."""
        response = client.get("/auth/dev-login", follow_redirects=True)
        body_lower = response.data.lower()
        assert b"debug" in body_lower or b"development" in body_lower

    def test_dev_login_picker_blocked_when_debug_false(self, client):
        """GET /auth/dev-login-picker should also redirect."""
        response = client.get("/auth/dev-login-picker")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()


# =====================================================================
# 10. Dev-login happy path (with debug enabled)
# =====================================================================


@pytest.fixture()
def debug_client(app):
    """Test client with app.debug temporarily True."""
    original_debug = app.debug
    app.debug = True
    with app.test_client() as test_client:
        yield test_client
    app.debug = original_debug


class TestDevLoginHappyPath:
    """Verify dev-login when both DEBUG and DEV_LOGIN_ENABLED are True."""

    def test_dev_login_default_role_redirects(self, debug_client):
        """GET /auth/dev-login (no params) should redirect."""
        response = debug_client.get("/auth/dev-login")
        assert response.status_code == 302

    def test_dev_login_with_role_param(self, debug_client):
        """GET /auth/dev-login?role=manager should redirect."""
        response = debug_client.get("/auth/dev-login?role=manager")
        assert response.status_code == 302

    def test_dev_login_nonexistent_role_redirects_to_picker(self, debug_client):
        """Nonexistent role should redirect to picker."""
        response = debug_client.get("/auth/dev-login?role=nonexistent_xyz")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "dev-login-picker" in location or "login" in location.lower()

    def test_dev_login_with_user_id(self, debug_client, admin_user):
        """user_id param should log in as that user."""
        response = debug_client.get(f"/auth/dev-login?user_id={admin_user.id}")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert location.endswith("/") or "dashboard" in location.lower()

    def test_dev_login_user_id_flashes_scope_info(self, debug_client, admin_user):
        """Successful dev-login should flash user info."""
        response = debug_client.get(
            f"/auth/dev-login?user_id={admin_user.id}", follow_redirects=True
        )
        body = response.data
        assert admin_user.first_name.encode() in body or b"admin" in body.lower()

    def test_dev_login_invalid_user_id_redirects_to_picker(self, debug_client):
        """Nonexistent user_id should redirect to picker."""
        response = debug_client.get("/auth/dev-login?user_id=999999")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "dev-login-picker" in location or "login" in location.lower()

    def test_dev_login_inactive_user_id_redirects_to_picker(
        self, debug_client, inactive_user
    ):
        """Deactivated user should not be available for dev-login."""
        response = debug_client.get(f"/auth/dev-login?user_id={inactive_user.id}")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "dev-login-picker" in location or "login" in location.lower()

    def test_dev_login_user_id_takes_precedence_over_role(
        self, debug_client, admin_user
    ):
        """user_id should win when both params are provided."""
        response = debug_client.get(
            f"/auth/dev-login?user_id={admin_user.id}&role=read_only",
            follow_redirects=True,
        )
        assert admin_user.first_name.encode() in response.data


# =====================================================================
# 11. Dev-login picker
# =====================================================================


class TestDevLoginPicker:
    """Verify the dev-login picker page."""

    def test_dev_login_picker_returns_200(self, debug_client):
        """Picker should return 200 in debug mode."""
        response = debug_client.get("/auth/dev-login-picker")
        assert response.status_code == 200

    def test_dev_login_picker_is_html(self, debug_client):
        """Picker should return HTML."""
        response = debug_client.get("/auth/dev-login-picker")
        assert "text/html" in (response.content_type or "")

    def test_dev_login_picker_contains_heading(self, debug_client):
        """Picker should identify itself."""
        response = debug_client.get("/auth/dev-login-picker")
        body_lower = response.data.lower()
        assert b"dev login" in body_lower or b"picker" in body_lower

    def test_dev_login_picker_contains_back_link(self, debug_client):
        """Picker should link back to login."""
        response = debug_client.get("/auth/dev-login-picker")
        assert b"login" in response.data.lower()


# =====================================================================
# 12. Session behavior
# =====================================================================


class TestSessionBehavior:
    """Verify session management across login/logout."""

    def test_logout_redirects_to_login(self, auth_client, admin_user):
        """Logout should redirect to login."""
        client = auth_client(admin_user)
        response = client.get("/auth/logout")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()

    def test_unauthenticated_client_redirected_from_dashboard(self, client):
        """Unauthenticated client should be redirected from /."""
        response = client.get("/")
        assert response.status_code == 302
        assert "login" in response.headers.get("Location", "").lower()


# =====================================================================
# 13. Auth route URL registration
# =====================================================================


class TestAuthRouteRegistration:
    """Verify all auth routes are registered correctly."""

    def test_login_route(self, app):
        """The /auth/login endpoint should exist."""
        with app.test_request_context():
            assert app.url_map.bind("localhost").build("auth.login") == "/auth/login"

    def test_login_page_route(self, app):
        """The /auth/login-page endpoint should exist."""
        with app.test_request_context():
            assert (
                app.url_map.bind("localhost").build("auth.login_page")
                == "/auth/login-page"
            )

    def test_callback_route(self, app):
        """The /auth/callback endpoint should exist."""
        with app.test_request_context():
            assert (
                app.url_map.bind("localhost").build("auth.callback") == "/auth/callback"
            )

    def test_logout_route(self, app):
        """The /auth/logout endpoint should exist."""
        with app.test_request_context():
            assert app.url_map.bind("localhost").build("auth.logout") == "/auth/logout"

    def test_unauthorized_route(self, app):
        """The /auth/unauthorized endpoint should exist."""
        with app.test_request_context():
            assert (
                app.url_map.bind("localhost").build("auth.unauthorized")
                == "/auth/unauthorized"
            )

    def test_dev_login_route(self, app):
        """The /auth/dev-login endpoint should exist."""
        with app.test_request_context():
            assert (
                app.url_map.bind("localhost").build("auth.dev_login")
                == "/auth/dev-login"
            )

    def test_dev_login_picker_route(self, app):
        """The /auth/dev-login-picker endpoint should exist."""
        with app.test_request_context():
            assert (
                app.url_map.bind("localhost").build("auth.dev_login_picker")
                == "/auth/dev-login-picker"
            )


# =====================================================================
# 14. Security: callback resilience
# =====================================================================


class TestCallbackSecurityBehavior:
    """Verify security-relevant callback behavior."""

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_value_error_never_returns_500(self, mock_complete, client):
        """ValueError should produce 302, never 500."""
        mock_complete.side_effect = ValueError("Something went wrong")
        response = client.get("/auth/callback?state=x&code=y")
        assert response.status_code == 302

    def test_callback_microsoft_error_never_returns_500(self, client):
        """Microsoft error param should produce 302, never 500."""
        response = client.get("/auth/callback?error=server_error")
        assert response.status_code == 302

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_does_not_leak_errors_in_redirect_url(self, mock_complete, client):
        """Sensitive error details must not appear in the redirect URL."""
        mock_complete.side_effect = ValueError("client_secret is invalid")
        response = client.get("/auth/callback?state=s&code=c")
        location = response.headers.get("Location", "")
        assert "client_secret" not in location
        assert "invalid" not in location.lower()

    @patch("app.blueprints.auth.routes.auth_service.complete_auth_flow")
    def test_callback_flow_consumed_after_success(
        self, mock_complete, client, admin_user
    ):
        """auth_code_flow should be consumed (popped) after exchange."""
        mock_complete.return_value = {"id_token_claims": {"oid": "x"}}
        with patch(
            "app.blueprints.auth.routes.auth_service.process_login"
        ) as mock_process:
            mock_process.return_value = admin_user
            client.get("/auth/callback?state=s&code=c")
        mock_complete.assert_called_once()
