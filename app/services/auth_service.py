"""
Auth service -- MSAL (Microsoft Authentication Library) integration.

Handles OAuth2/OIDC flow with Entra ID: initiating the authorization
code flow, completing the token exchange, extracting user identity,
and auto-provisioning new users on first login.

MSAL API migration (2026-03-23):
    Replaced the deprecated ``get_authorization_request_url`` /
    ``acquire_token_by_authorization_code`` pair with the modern
    ``initiate_auth_code_flow`` / ``acquire_token_by_auth_code_flow``
    pair.  The new API handles CSRF state validation, nonce
    verification, and optional PKCE internally, removing the need
    for manual state management in the route layer.
"""

import logging

import msal
from flask import current_app, session

from app.services import audit_service, user_service

logger = logging.getLogger(__name__)


def _build_msal_app(cache=None) -> msal.ConfidentialClientApplication:
    """
    Create a configured MSAL ConfidentialClientApplication.

    Args:
        cache: Optional MSAL token cache for session-based caching.

    Returns:
        A configured MSAL application instance.
    """
    return msal.ConfidentialClientApplication(
        client_id=current_app.config["AZURE_CLIENT_ID"],
        client_credential=current_app.config["AZURE_CLIENT_SECRET"],
        authority=current_app.config["AZURE_AUTHORITY"],
        token_cache=cache,
    )


# -- OAuth2 authorization code flow ---------------------------------------


def initiate_auth_flow(state: str | None = None) -> str:
    """
    Start the OAuth2 authorization code flow.

    Creates an auth code flow dict via MSAL's
    ``initiate_auth_code_flow()`` and stores it in the Flask session.
    The flow dict contains MSAL's internal state token, nonce, and
    (when supported) a PKCE code challenge.  It is consumed by
    ``complete_auth_flow()`` after Microsoft redirects back.

    Args:
        state: Optional CSRF state value forwarded to Microsoft.
               MSAL embeds this in the flow dict and validates it
               automatically during ``complete_auth_flow()``.

    Returns:
        The Microsoft authorization URL to redirect the user to.
    """
    app = _build_msal_app()
    flow = app.initiate_auth_code_flow(
        scopes=current_app.config["AZURE_SCOPES"],
        redirect_uri=current_app.config["AZURE_REDIRECT_URI"],
        state=state,
    )

    if "error" in flow:
        # initiate_auth_code_flow can fail if the authority or client
        # config is invalid.  Surface a clear error instead of
        # redirecting to a broken URL.
        error_desc = flow.get("error_description", flow["error"])
        logger.error("Failed to initiate auth flow: %s", error_desc)
        raise ValueError(f"Could not start login: {error_desc}")

    # Store the full flow dict so complete_auth_flow() can retrieve it.
    session["auth_code_flow"] = flow

    return flow["auth_uri"]


def complete_auth_flow(auth_response: dict) -> dict:
    """
    Complete the OAuth2 authorization code flow.

    Retrieves the flow state from the Flask session and passes it
    along with Microsoft's redirect parameters to MSAL's
    ``acquire_token_by_auth_code_flow()``.  MSAL validates the state
    and nonce internally, then exchanges the authorization code for
    access and ID tokens.

    Args:
        auth_response: The query parameters from Microsoft's redirect,
                       typically ``dict(request.args)``.

    Returns:
        The MSAL token response dict containing ``id_token_claims``,
        ``access_token``, etc.

    Raises:
        ValueError: If the flow state is missing from the session or
                    the token exchange fails (including state mismatch,
                    expired code, or invalid grant).
    """
    flow = session.pop("auth_code_flow", None)
    if flow is None:
        raise ValueError(
            "Authentication flow state not found in session. "
            "Please start the login process again."
        )

    app = _build_msal_app()
    result = app.acquire_token_by_auth_code_flow(
        auth_code_flow=flow,
        auth_response=auth_response,
    )

    if "error" in result:
        error_desc = result.get("error_description", result["error"])
        logger.error("Token acquisition failed: %s", error_desc)
        raise ValueError(f"Authentication failed: {error_desc}")

    return result


# -- Login processing ------------------------------------------------------


def process_login(token_result: dict):
    """
    Process a successful OAuth2 token exchange.

    Extracts user identity from the ID token claims, looks up or
    auto-creates the local user record, records the login, and
    returns the User object for Flask-Login.

    Args:
        token_result: The dict returned by ``complete_auth_flow``.

    Returns:
        The User model instance (existing or newly created).

    Raises:
        ValueError: If required claims are missing from the token.
    """
    claims = token_result.get("id_token_claims", {})

    # Extract identity fields from the token claims.
    entra_object_id = claims.get("oid")
    email = claims.get("preferred_username") or claims.get("email")
    first_name = claims.get("given_name", "")
    last_name = claims.get("family_name", "")

    if not entra_object_id or not email:
        raise ValueError("ID token missing required claims (oid, preferred_username).")

    # Look up existing user by Entra object ID.
    user = user_service.get_user_by_entra_id(entra_object_id)

    if user is None:
        # Check if the user was pre-provisioned by email (no Entra ID yet).
        user = user_service.get_user_by_email(email)
        if user is not None:
            # Link the pre-provisioned user to their Entra ID.
            user.entra_object_id = entra_object_id
            logger.info(
                "Linked pre-provisioned user %s to Entra ID %s",
                email,
                entra_object_id,
            )
        else:
            # Auto-create a new user with the default read_only role.
            user = user_service.provision_user(
                email=email,
                first_name=first_name or email.split("@")[0],
                last_name=last_name or "",
                role_name="read_only",
                entra_object_id=entra_object_id,
            )
            logger.info("Auto-provisioned new user: %s", email)

    # Update login timestamps and record the login event.
    user_service.record_login(user)
    audit_service.log_login(user.id)

    # Store minimal user info in the Flask session.
    session["user_id"] = user.id
    session["user_email"] = user.email
    session["user_role"] = user.role.role_name

    return user


def clear_session() -> None:
    """Remove application-specific keys from the Flask session on logout."""
    for key in ("user_id", "user_email", "user_role", "auth_code_flow", "_flashes"):
        session.pop(key, None)
