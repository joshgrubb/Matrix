"""
Auth service — MSAL (Microsoft Authentication Library) integration.

Handles OAuth2/OIDC flow with Entra ID: building auth URLs, exchanging
authorization codes for tokens, extracting user identity, and
auto-provisioning new users on first login.
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


def get_auth_url(state: str | None = None) -> str:
    """
    Generate the Microsoft login URL for OAuth2 authorization code flow.

    Args:
        state: Optional CSRF state parameter to include in the redirect.

    Returns:
        The full Microsoft authorization URL the user should be
        redirected to.
    """
    app = _build_msal_app()
    auth_url = app.get_authorization_request_url(
        scopes=current_app.config["AZURE_SCOPES"],
        redirect_uri=current_app.config["AZURE_REDIRECT_URI"],
        state=state,
    )
    return auth_url


def acquire_token_by_code(auth_code: str) -> dict:
    """
    Exchange an authorization code for access and ID tokens.

    Args:
        auth_code: The authorization code from Microsoft's redirect.

    Returns:
        The MSAL token response dict containing ``id_token_claims``,
        ``access_token``, etc.

    Raises:
        ValueError: If the token exchange fails.
    """
    app = _build_msal_app()
    result = app.acquire_token_by_authorization_code(
        code=auth_code,
        scopes=current_app.config["AZURE_SCOPES"],
        redirect_uri=current_app.config["AZURE_REDIRECT_URI"],
    )

    if "error" in result:
        error_desc = result.get("error_description", result["error"])
        logger.error("Token acquisition failed: %s", error_desc)
        raise ValueError(f"Authentication failed: {error_desc}")

    return result


def process_login(token_result: dict):
    """
    Process a successful OAuth2 token exchange.

    Extracts user identity from the ID token claims, looks up or
    auto-creates the local user record, records the login, and
    returns the User object for Flask-Login.

    Args:
        token_result: The dict returned by ``acquire_token_by_code``.

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
        raise ValueError(
            "ID token missing required claims (oid, preferred_username)."
        )

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
    for key in ("user_id", "user_email", "user_role", "_flashes"):
        session.pop(key, None)
