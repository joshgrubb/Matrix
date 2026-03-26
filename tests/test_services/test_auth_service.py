"""
Unit tests for the auth service layer (MSAL integration).

Tests every public function in ``app.services.auth_service`` by mocking
the MSAL ``ConfidentialClientApplication`` so no calls are made to
Microsoft Entra ID.  The database is NOT mocked -- ``process_login``
tests use the real SQL Server test instance for user lookup and
auto-provisioning verification.

Sections:
    1.  initiate_auth_flow -- happy path and error handling
    2.  complete_auth_flow -- happy path and error handling
    3.  process_login -- existing user lookup by Entra OID
    4.  process_login -- pre-provisioned user linking by email
    5.  process_login -- auto-provisioning new users
    6.  process_login -- claim validation and edge cases
    7.  process_login -- login timestamp tracking
    8.  clear_session

Mocking strategy (from testing_strategy.md Section 8.2):
    All tests that touch ``initiate_auth_flow`` or ``complete_auth_flow``
    use ``@patch("app.services.auth_service._build_msal_app")`` to
    replace the MSAL app with a mock.  ``process_login`` does NOT need
    the MSAL mock because it operates on the token result dict directly,
    but it DOES use the real database for user lookups.

Config reminder:
    AZURE_SCOPES = ["User.Read"]
    AZURE_REDIRECT_URI = "http://localhost:5000/auth/callback"

Run this file in isolation::

    pytest tests/test_services/test_auth_service.py -v
"""

import time as _time
from unittest.mock import MagicMock, patch

import pytest

from app.models.audit import AuditLog
from app.models.user import User
from app.services import auth_service, user_service


# =====================================================================
# Local helper for generating unique emails
# =====================================================================

_local_counter = int(_time.time() * 10) % 9000


def _unique_email(prefix="auth"):
    """
    Return a unique ``@test.local`` email for test-created users.

    Uses a module-level counter to prevent collisions across tests.
    The ``@test.local`` suffix ensures the conftest cleanup fixture
    deletes these records after each test.
    """
    global _local_counter  # pylint: disable=global-statement
    _local_counter += 1
    return f"_tst_{prefix}_{_local_counter:04d}@test.local"


# =====================================================================
# Shared mock helpers
# =====================================================================


def _make_mock_msal_app(flow_return=None, token_return=None):
    """
    Build a MagicMock that behaves like ``msal.ConfidentialClientApplication``.

    Args:
        flow_return:  The dict returned by ``initiate_auth_code_flow()``.
                      Defaults to a successful flow with a fake auth_uri.
        token_return: The dict returned by ``acquire_token_by_auth_code_flow()``.
                      Defaults to a successful token result with standard
                      id_token_claims.

    Returns:
        A configured MagicMock instance.
    """
    mock_app = MagicMock()

    if flow_return is None:
        flow_return = {
            "auth_uri": "https://login.microsoftonline.com/authorize?fake=1",
            "state": "test-csrf-state",
        }
    mock_app.initiate_auth_code_flow.return_value = flow_return

    if token_return is None:
        token_return = {
            "id_token_claims": {
                "oid": "aaaa-bbbb-cccc-dddd",
                "preferred_username": "testuser@test.local",
                "given_name": "Test",
                "family_name": "User",
            },
            "access_token": "fake-access-token",
        }
    mock_app.acquire_token_by_auth_code_flow.return_value = token_return

    return mock_app


def _make_token_result(
    oid="aaaa-bbbb-cccc-dddd",
    email="testuser@test.local",
    given_name="Test",
    family_name="User",
    **extra_claims,
):
    """
    Build a token result dict matching the structure returned by
    ``complete_auth_flow()``.

    Args:
        oid:         The Entra object ID (``oid`` claim).
        email:       The ``preferred_username`` claim.
        given_name:  The ``given_name`` claim.
        family_name: The ``family_name`` claim.
        **extra_claims: Additional claims to include.

    Returns:
        A dict with ``id_token_claims`` and ``access_token``.
    """
    claims = {
        "oid": oid,
        "preferred_username": email,
        "given_name": given_name,
        "family_name": family_name,
    }
    claims.update(extra_claims)
    return {
        "id_token_claims": claims,
        "access_token": "fake-access-token",
    }


# =====================================================================
# 1. initiate_auth_flow -- happy path and error handling
# =====================================================================


class TestInitiateAuthFlow:
    """
    Verify that ``initiate_auth_flow()`` correctly delegates to MSAL,
    stores the flow dict in the Flask session, and returns the auth URL.
    """

    @patch("app.services.auth_service._build_msal_app")
    def test_returns_auth_uri(self, mock_build, app):
        """
        The function should return the ``auth_uri`` string from the
        MSAL flow dict.
        """
        expected_uri = "https://login.microsoftonline.com/authorize?test=1"
        mock_app = _make_mock_msal_app(
            flow_return={
                "auth_uri": expected_uri,
                "state": "some-state",
            }
        )
        mock_build.return_value = mock_app

        with app.test_request_context():
            result = auth_service.initiate_auth_flow(state="csrf-token")

        assert result == expected_uri

    @patch("app.services.auth_service._build_msal_app")
    def test_stores_flow_in_session(self, mock_build, app):
        """
        The full MSAL flow dict should be stored in
        ``session["auth_code_flow"]`` so ``complete_auth_flow``
        can retrieve it later.
        """
        flow_dict = {
            "auth_uri": "https://login.microsoftonline.com/authorize",
            "state": "csrf-token",
            "code_verifier": "pkce-verifier",
        }
        mock_app = _make_mock_msal_app(flow_return=flow_dict)
        mock_build.return_value = mock_app

        from flask import session

        with app.test_request_context():
            auth_service.initiate_auth_flow(state="csrf-token")
            assert "auth_code_flow" in session
            assert session["auth_code_flow"] == flow_dict

    @patch("app.services.auth_service._build_msal_app")
    def test_passes_correct_scopes(self, mock_build, app):
        """
        The function should forward ``AZURE_SCOPES`` from the app
        config to MSAL's ``initiate_auth_code_flow()``.
        """
        mock_app = _make_mock_msal_app()
        mock_build.return_value = mock_app

        with app.test_request_context():
            auth_service.initiate_auth_flow()

        # Verify the scopes kwarg matches the config.
        call_kwargs = mock_app.initiate_auth_code_flow.call_args
        assert call_kwargs is not None
        scopes_arg = call_kwargs.kwargs.get(
            "scopes", call_kwargs.args[0] if call_kwargs.args else None
        )
        assert scopes_arg == ["User.Read"]

    @patch("app.services.auth_service._build_msal_app")
    def test_passes_correct_redirect_uri(self, mock_build, app):
        """
        The function should forward ``AZURE_REDIRECT_URI`` from the
        app config to MSAL.
        """
        mock_app = _make_mock_msal_app()
        mock_build.return_value = mock_app

        with app.test_request_context():
            auth_service.initiate_auth_flow()

        call_kwargs = mock_app.initiate_auth_code_flow.call_args
        redirect_uri = call_kwargs.kwargs.get("redirect_uri")
        assert redirect_uri == app.config["AZURE_REDIRECT_URI"]

    @patch("app.services.auth_service._build_msal_app")
    def test_passes_state_parameter(self, mock_build, app):
        """
        The optional ``state`` parameter should be forwarded to MSAL
        for CSRF protection.
        """
        mock_app = _make_mock_msal_app()
        mock_build.return_value = mock_app

        with app.test_request_context():
            auth_service.initiate_auth_flow(state="my-csrf-state")

        call_kwargs = mock_app.initiate_auth_code_flow.call_args
        state_arg = call_kwargs.kwargs.get("state")
        assert state_arg == "my-csrf-state"

    @patch("app.services.auth_service._build_msal_app")
    def test_raises_value_error_on_msal_error(self, mock_build, app):
        """
        If MSAL returns an error dict (e.g., bad authority URL),
        the function should raise ``ValueError`` with the error
        description instead of returning a broken URL.
        """
        mock_app = _make_mock_msal_app(
            flow_return={
                "error": "invalid_client",
                "error_description": "The client ID is not valid.",
            }
        )
        mock_build.return_value = mock_app

        with app.test_request_context():
            with pytest.raises(ValueError, match="Could not start login"):
                auth_service.initiate_auth_flow()

    @patch("app.services.auth_service._build_msal_app")
    def test_error_message_includes_description(self, mock_build, app):
        """
        The ValueError message should include the MSAL error
        description so the developer can diagnose the issue.
        """
        mock_app = _make_mock_msal_app(
            flow_return={
                "error": "invalid_authority",
                "error_description": "Tenant not found.",
            }
        )
        mock_build.return_value = mock_app

        with app.test_request_context():
            with pytest.raises(ValueError, match="Tenant not found"):
                auth_service.initiate_auth_flow()

    @patch("app.services.auth_service._build_msal_app")
    def test_error_without_description_uses_error_code(self, mock_build, app):
        """
        If MSAL returns an error without a description, the
        ValueError should fall back to the error code itself.
        """
        mock_app = _make_mock_msal_app(flow_return={"error": "unknown_failure"})
        mock_build.return_value = mock_app

        with app.test_request_context():
            with pytest.raises(ValueError, match="unknown_failure"):
                auth_service.initiate_auth_flow()


# =====================================================================
# 2. complete_auth_flow -- happy path and error handling
# =====================================================================


class TestCompleteAuthFlow:
    """
    Verify that ``complete_auth_flow()`` retrieves the stored flow
    from the session, exchanges the code via MSAL, and returns the
    token result or raises on failure.
    """

    @patch("app.services.auth_service._build_msal_app")
    def test_returns_token_result_on_success(self, mock_build, app):
        """
        When MSAL returns a successful token result, the function
        should return the full dict including ``id_token_claims``.
        """
        expected_result = {
            "id_token_claims": {
                "oid": "test-oid",
                "preferred_username": "user@test.local",
            },
            "access_token": "real-token",
        }
        mock_app = _make_mock_msal_app(token_return=expected_result)
        mock_build.return_value = mock_app

        from flask import session

        with app.test_request_context():
            # Pre-populate the session with the flow dict.
            session["auth_code_flow"] = {
                "state": "csrf-state",
                "auth_uri": "https://login.microsoftonline.com/authorize",
            }
            result = auth_service.complete_auth_flow(
                auth_response={"code": "auth-code", "state": "csrf-state"}
            )

        assert result == expected_result
        assert "id_token_claims" in result
        assert result["id_token_claims"]["oid"] == "test-oid"

    @patch("app.services.auth_service._build_msal_app")
    def test_passes_flow_and_response_to_msal(self, mock_build, app):
        """
        The function should pass the stored flow dict and the
        auth_response to MSAL's ``acquire_token_by_auth_code_flow``.
        """
        mock_app = _make_mock_msal_app()
        mock_build.return_value = mock_app

        stored_flow = {"state": "stored-state", "nonce": "stored-nonce"}
        auth_response = {"code": "auth-code-123", "state": "stored-state"}

        from flask import session

        with app.test_request_context():
            session["auth_code_flow"] = stored_flow
            auth_service.complete_auth_flow(auth_response=auth_response)

        call_kwargs = mock_app.acquire_token_by_auth_code_flow.call_args
        assert call_kwargs.kwargs["auth_code_flow"] == stored_flow
        assert call_kwargs.kwargs["auth_response"] == auth_response

    @patch("app.services.auth_service._build_msal_app")
    def test_pops_flow_from_session(self, mock_build, app):
        """
        After completing the flow, ``auth_code_flow`` should be
        removed from the session (consumed, not reusable).
        """
        mock_app = _make_mock_msal_app()
        mock_build.return_value = mock_app

        from flask import session

        with app.test_request_context():
            session["auth_code_flow"] = {"state": "s", "nonce": "n"}
            auth_service.complete_auth_flow(auth_response={"code": "c", "state": "s"})
            assert "auth_code_flow" not in session

    def test_raises_value_error_when_session_has_no_flow(self, app):
        """
        If the session does not contain ``auth_code_flow`` (e.g.,
        the user bookmarked the callback URL), the function should
        raise ``ValueError`` with a clear message.
        """
        with app.test_request_context():
            with pytest.raises(ValueError, match="not found in session"):
                auth_service.complete_auth_flow(
                    auth_response={"code": "c", "state": "s"}
                )

    @patch("app.services.auth_service._build_msal_app")
    def test_raises_value_error_on_token_error(self, mock_build, app):
        """
        If MSAL returns an error during token exchange (e.g.,
        expired code, invalid grant), the function should raise
        ``ValueError`` with the error description.
        """
        mock_app = _make_mock_msal_app(
            token_return={
                "error": "invalid_grant",
                "error_description": "The authorization code has expired.",
            }
        )
        mock_build.return_value = mock_app

        from flask import session

        with app.test_request_context():
            session["auth_code_flow"] = {"state": "s"}
            with pytest.raises(ValueError, match="Authentication failed"):
                auth_service.complete_auth_flow(
                    auth_response={"code": "expired-code", "state": "s"}
                )

    @patch("app.services.auth_service._build_msal_app")
    def test_error_message_includes_msal_description(self, mock_build, app):
        """
        The ValueError message should include the specific error
        description from MSAL for diagnostic purposes.
        """
        mock_app = _make_mock_msal_app(
            token_return={
                "error": "invalid_grant",
                "error_description": "Code was already redeemed.",
            }
        )
        mock_build.return_value = mock_app

        from flask import session

        with app.test_request_context():
            session["auth_code_flow"] = {"state": "s"}
            with pytest.raises(ValueError, match="Code was already redeemed"):
                auth_service.complete_auth_flow(
                    auth_response={"code": "c", "state": "s"}
                )

    @patch("app.services.auth_service._build_msal_app")
    def test_error_without_description_uses_error_code(self, mock_build, app):
        """
        If MSAL returns an error without a description field, the
        ValueError should include the raw error code.
        """
        mock_app = _make_mock_msal_app(token_return={"error": "server_error"})
        mock_build.return_value = mock_app

        from flask import session

        with app.test_request_context():
            session["auth_code_flow"] = {"state": "s"}
            with pytest.raises(ValueError, match="server_error"):
                auth_service.complete_auth_flow(
                    auth_response={"code": "c", "state": "s"}
                )


# =====================================================================
# 3. process_login -- existing user lookup by Entra OID
# =====================================================================


class TestProcessLoginExistingUser:
    """
    Verify that ``process_login()`` finds existing users by their
    Entra object ID and updates login timestamps.
    """

    def test_existing_user_returned_by_entra_oid(self, app, db_session, admin_user):
        """
        When the ``oid`` claim matches an existing user's
        ``entra_object_id``, that user should be returned.
        """
        # Create a user with a known Entra OID.
        email = _unique_email("existing")
        entra_oid = f"existing-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="Existing",
            last_name="User",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )

        token_result = _make_token_result(oid=entra_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result.id == user.id
        assert result.email == email

    def test_existing_user_last_login_updated(self, app, db_session, admin_user):
        """
        After ``process_login``, the user's ``last_login`` timestamp
        should be set to a non-null value.
        """
        email = _unique_email("login_ts")
        entra_oid = f"login-ts-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="Login",
            last_name="Timestamp",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )
        assert user.last_login is None

        token_result = _make_token_result(oid=entra_oid, email=email)

        with app.test_request_context():
            auth_service.process_login(token_result)

        db_session.refresh(user)
        assert user.last_login is not None

    def test_existing_user_audit_log_created(self, app, db_session, admin_user):
        """
        A LOGIN audit log entry should be created when an existing
        user logs in via ``process_login``.
        """
        email = _unique_email("audit")
        entra_oid = f"audit-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="Audit",
            last_name="Check",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )

        token_result = _make_token_result(oid=entra_oid, email=email)

        with app.test_request_context():
            auth_service.process_login(token_result)

        # Check for the LOGIN audit entry.
        audit_entry = AuditLog.query.filter_by(
            user_id=user.id,
            action_type="LOGIN",
        ).first()
        assert audit_entry is not None

    def test_existing_user_session_populated(self, app, db_session, admin_user):
        """
        After ``process_login``, the Flask session should contain
        ``user_id``, ``user_email``, and ``user_role``.
        """
        email = _unique_email("session")
        entra_oid = f"session-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="Session",
            last_name="Check",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )

        token_result = _make_token_result(oid=entra_oid, email=email)

        from flask import session

        with app.test_request_context():
            auth_service.process_login(token_result)
            assert session.get("user_id") == user.id
            assert session.get("user_email") == email
            assert session.get("user_role") == user.role_name


# =====================================================================
# 4. process_login -- pre-provisioned user linking by email
# =====================================================================


class TestProcessLoginPreProvisionedUser:
    """
    Verify that ``process_login()`` links a pre-provisioned user
    (created by admin with no Entra OID) to their Entra ID on
    first OAuth login.
    """

    def test_pre_provisioned_user_linked_by_email(self, app, db_session, admin_user):
        """
        A user who was pre-provisioned by email (entra_object_id=None)
        should be found by email and have their entra_object_id set.
        """
        email = _unique_email("preprov")
        user = user_service.provision_user(
            email=email,
            first_name="Pre",
            last_name="Provisioned",
            provisioned_by=admin_user.id,
            # entra_object_id intentionally omitted (None).
        )
        assert user.entra_object_id is None

        new_oid = f"newly-linked-oid-{_local_counter}"
        token_result = _make_token_result(oid=new_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        # Should return the same user, now linked.
        assert result.id == user.id
        db_session.refresh(user)
        assert user.entra_object_id == new_oid

    def test_pre_provisioned_user_retains_original_role(
        self, app, db_session, admin_user
    ):
        """
        When linking a pre-provisioned user, the role assigned by
        the admin should be preserved (not overwritten to read_only).
        """
        email = _unique_email("preprov_role")
        user = user_service.provision_user(
            email=email,
            first_name="Role",
            last_name="Kept",
            role_name="manager",
            provisioned_by=admin_user.id,
        )

        new_oid = f"role-kept-oid-{_local_counter}"
        token_result = _make_token_result(oid=new_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result.role_name == "manager"


# =====================================================================
# 5. process_login -- auto-provisioning new users
# =====================================================================


class TestProcessLoginAutoProvision:
    """
    Verify that ``process_login()`` auto-creates a new user when
    neither the Entra OID nor the email matches an existing record.
    """

    def test_new_user_auto_provisioned(self, app, db_session):
        """
        When no existing user matches the OID or email, a new User
        record should be created in the database.
        """
        email = _unique_email("auto")
        new_oid = f"auto-oid-{_local_counter}"
        token_result = _make_token_result(
            oid=new_oid,
            email=email,
            given_name="AutoFirst",
            family_name="AutoLast",
        )

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result is not None
        assert result.id is not None
        assert result.email == email
        assert result.first_name == "AutoFirst"
        assert result.last_name == "AutoLast"
        assert result.entra_object_id == new_oid

    def test_auto_provisioned_user_gets_read_only_role(self, app, db_session):
        """
        Auto-provisioned users should receive the ``read_only`` role
        by default (least privilege).
        """
        email = _unique_email("auto_role")
        new_oid = f"auto-role-oid-{_local_counter}"
        token_result = _make_token_result(oid=new_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result.role_name == "read_only"

    def test_auto_provisioned_user_is_active(self, app, db_session):
        """Auto-provisioned users should be active by default."""
        email = _unique_email("auto_active")
        new_oid = f"auto-active-oid-{_local_counter}"
        token_result = _make_token_result(oid=new_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result.is_active is True

    def test_auto_provisioned_user_login_timestamp_set(self, app, db_session):
        """
        An auto-provisioned user's ``last_login`` should be set
        immediately since the provisioning IS a login.
        """
        email = _unique_email("auto_ts")
        new_oid = f"auto-ts-oid-{_local_counter}"
        token_result = _make_token_result(oid=new_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result.last_login is not None

    def test_auto_provisioned_user_persisted_in_database(self, app, db_session):
        """
        The auto-provisioned user should be queryable from the
        database after ``process_login`` returns.
        """
        email = _unique_email("auto_db")
        new_oid = f"auto-db-oid-{_local_counter}"
        token_result = _make_token_result(oid=new_oid, email=email)

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        # Re-query from the database to confirm persistence.
        db_user = user_service.get_user_by_entra_id(new_oid)
        assert db_user is not None
        assert db_user.id == result.id

    def test_auto_provision_uses_email_prefix_when_given_name_missing(
        self, app, db_session
    ):
        """
        If the token claims have no ``given_name``, the service
        should fall back to using the email prefix (before the @)
        as the first name.
        """
        email = _unique_email("noname")
        new_oid = f"noname-oid-{_local_counter}"
        token_result = _make_token_result(
            oid=new_oid,
            email=email,
            given_name="",
            family_name="",
        )

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        # The code does: first_name or email.split("@")[0]
        # When given_name is "", the or expression triggers.
        expected_prefix = email.split("@")[0]
        assert result.first_name == expected_prefix


# =====================================================================
# 6. process_login -- claim validation and edge cases
# =====================================================================


class TestProcessLoginClaimValidation:
    """
    Verify that ``process_login()`` raises appropriate errors when
    required claims are missing or invalid.
    """

    def test_missing_oid_claim_raises_value_error(self, app):
        """
        A token result with no ``oid`` claim should raise
        ``ValueError`` with a message about missing claims.
        """
        token_result = {
            "id_token_claims": {
                # "oid" is intentionally missing.
                "preferred_username": "user@test.local",
                "given_name": "No",
                "family_name": "OID",
            }
        }

        with app.test_request_context():
            with pytest.raises(ValueError, match="missing required claims"):
                auth_service.process_login(token_result)

    def test_missing_email_claim_raises_value_error(self, app):
        """
        A token result with no ``preferred_username`` and no ``email``
        claim should raise ``ValueError``.
        """
        token_result = {
            "id_token_claims": {
                "oid": "some-oid",
                # Neither preferred_username nor email is present.
                "given_name": "No",
                "family_name": "Email",
            }
        }

        with app.test_request_context():
            with pytest.raises(ValueError, match="missing required claims"):
                auth_service.process_login(token_result)

    def test_missing_both_oid_and_email_raises_value_error(self, app):
        """
        A token result missing both ``oid`` and email fields should
        raise ``ValueError``.
        """
        token_result = {"id_token_claims": {"given_name": "Ghost"}}

        with app.test_request_context():
            with pytest.raises(ValueError, match="missing required claims"):
                auth_service.process_login(token_result)

    def test_empty_id_token_claims_raises_value_error(self, app):
        """
        A token result with an empty ``id_token_claims`` dict should
        raise ``ValueError``.
        """
        token_result = {"id_token_claims": {}}

        with app.test_request_context():
            with pytest.raises(ValueError, match="missing required claims"):
                auth_service.process_login(token_result)

    def test_missing_id_token_claims_key_raises_value_error(self, app):
        """
        A token result with no ``id_token_claims`` key at all should
        raise ``ValueError`` because ``oid`` will be None.
        """
        token_result = {"access_token": "some-token"}

        with app.test_request_context():
            with pytest.raises(ValueError, match="missing required claims"):
                auth_service.process_login(token_result)

    def test_email_claim_falls_back_to_email_field(self, app, db_session):
        """
        If ``preferred_username`` is absent but ``email`` is present,
        the service should use ``email`` as the fallback.
        """
        email = _unique_email("fallback")
        new_oid = f"fallback-oid-{_local_counter}"
        token_result = {
            "id_token_claims": {
                "oid": new_oid,
                # "preferred_username" is absent.
                "email": email,
                "given_name": "Fallback",
                "family_name": "User",
            }
        }

        with app.test_request_context():
            result = auth_service.process_login(token_result)

        assert result.email == email


# =====================================================================
# 7. process_login -- login timestamp tracking
# =====================================================================


class TestProcessLoginTimestamps:
    """
    Verify that ``process_login()`` correctly sets first_login_at
    and last_login through the ``user_service.record_login()`` call.
    """

    def test_first_login_at_set_on_first_login(self, app, db_session, admin_user):
        """
        The first call to ``process_login`` for a user should set
        ``first_login_at``.
        """
        email = _unique_email("first_login")
        entra_oid = f"first-login-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="First",
            last_name="Login",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )
        assert user.first_login_at is None

        token_result = _make_token_result(oid=entra_oid, email=email)

        with app.test_request_context():
            auth_service.process_login(token_result)

        db_session.refresh(user)
        assert user.first_login_at is not None

    def test_first_login_at_not_overwritten_on_subsequent_login(
        self, app, db_session, admin_user
    ):
        """
        The second call to ``process_login`` should NOT overwrite
        ``first_login_at`` -- it should remain at the original value.
        """
        email = _unique_email("repeat_login")
        entra_oid = f"repeat-login-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="Repeat",
            last_name="Login",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )

        token_result = _make_token_result(oid=entra_oid, email=email)

        # First login.
        with app.test_request_context():
            auth_service.process_login(token_result)

        db_session.refresh(user)
        first_login_time = user.first_login_at
        assert first_login_time is not None

        # Second login.
        with app.test_request_context():
            auth_service.process_login(token_result)

        db_session.refresh(user)
        assert user.first_login_at == first_login_time

    def test_last_login_updated_on_each_login(self, app, db_session, admin_user):
        """
        Each call to ``process_login`` should update ``last_login``
        to a new (more recent) timestamp.
        """
        email = _unique_email("multi_login")
        entra_oid = f"multi-login-oid-{_local_counter}"
        user = user_service.provision_user(
            email=email,
            first_name="Multi",
            last_name="Login",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )

        token_result = _make_token_result(oid=entra_oid, email=email)

        # First login.
        with app.test_request_context():
            auth_service.process_login(token_result)

        db_session.refresh(user)
        first_last_login = user.last_login
        assert first_last_login is not None

        # Second login (should be >= first).
        with app.test_request_context():
            auth_service.process_login(token_result)

        db_session.refresh(user)
        assert user.last_login >= first_last_login


# =====================================================================
# 8. clear_session
# =====================================================================


class TestClearSession:
    """
    Verify that ``clear_session()`` removes all application-specific
    keys from the Flask session without touching other session data.
    """

    def test_removes_auth_code_flow_key(self, app):
        """``auth_code_flow`` should be removed from the session."""
        from flask import session

        with app.test_request_context():
            session["auth_code_flow"] = {"state": "s"}
            auth_service.clear_session()
            assert "auth_code_flow" not in session

    def test_removes_user_id_key(self, app):
        """``user_id`` should be removed from the session."""
        from flask import session

        with app.test_request_context():
            session["user_id"] = 42
            auth_service.clear_session()
            assert "user_id" not in session

    def test_removes_user_email_key(self, app):
        """``user_email`` should be removed from the session."""
        from flask import session

        with app.test_request_context():
            session["user_email"] = "test@test.local"
            auth_service.clear_session()
            assert "user_email" not in session

    def test_removes_user_role_key(self, app):
        """``user_role`` should be removed from the session."""
        from flask import session

        with app.test_request_context():
            session["user_role"] = "admin"
            auth_service.clear_session()
            assert "user_role" not in session

    def test_removes_flashes_key(self, app):
        """``_flashes`` should be removed from the session."""
        from flask import session

        with app.test_request_context():
            session["_flashes"] = [("success", "Test")]
            auth_service.clear_session()
            assert "_flashes" not in session

    def test_preserves_unrelated_session_keys(self, app):
        """
        Keys not managed by the auth service should survive
        ``clear_session()`` -- only application-specific keys
        are removed.
        """
        from flask import session

        with app.test_request_context():
            session["unrelated_key"] = "keep me"
            session["user_id"] = 42
            auth_service.clear_session()
            assert session.get("unrelated_key") == "keep me"

    def test_clear_session_does_not_crash_on_empty_session(self, app):
        """
        Calling ``clear_session()`` when none of the managed keys
        exist should not raise any errors.
        """
        from flask import session

        with app.test_request_context():
            # Session is empty -- should be a no-op.
            auth_service.clear_session()
            # If we get here without an exception, the test passes.
