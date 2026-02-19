"""
Routes for the auth blueprint — login, logout, and OAuth2 callback.

The login flow redirects to Microsoft Entra ID for authentication.
After successful auth, the callback route exchanges the authorization
code for tokens and logs the user in via Flask-Login.
"""

import uuid

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.blueprints.auth import bp
from app.services import audit_service, auth_service


@bp.route("/login")
def login():
    """
    Initiate the OAuth2 login flow.

    If the user is already authenticated, redirect to the dashboard.
    Otherwise, generate a CSRF state token and redirect to the
    Microsoft login page.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    # Generate a random state token to prevent CSRF.
    state = str(uuid.uuid4())
    session["oauth_state"] = state

    # Build the Microsoft authorization URL.
    auth_url = auth_service.get_auth_url(state=state)
    return redirect(auth_url)


@bp.route("/callback")
def callback():
    """
    Handle the OAuth2 redirect from Microsoft Entra ID.

    Validates the state parameter, exchanges the authorization code
    for tokens, processes the login, and redirects to the dashboard.
    """
    # Verify the CSRF state token.
    if request.args.get("state") != session.pop("oauth_state", None):
        flash("Authentication failed: invalid state parameter.", "danger")
        return redirect(url_for("auth.login_page"))

    # Check for errors from Microsoft.
    if "error" in request.args:
        error_desc = request.args.get("error_description", "Unknown error")
        flash(f"Authentication failed: {error_desc}", "danger")
        return redirect(url_for("auth.login_page"))

    # Exchange the authorization code for tokens.
    auth_code = request.args.get("code")
    if not auth_code:
        flash("Authentication failed: no authorization code received.", "danger")
        return redirect(url_for("auth.login_page"))

    try:
        token_result = auth_service.acquire_token_by_code(auth_code)
        user = auth_service.process_login(token_result)
        login_user(user)
        flash(f"Welcome, {user.full_name}!", "success")
        return redirect(url_for("main.dashboard"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("auth.login_page"))


@bp.route("/login-page")
def login_page():
    """Render the login page with a sign-in button."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout():
    """
    Log the user out of the application.

    Clears the Flask session and Flask-Login session, then redirects
    to the login page.
    """
    user_id = current_user.id
    audit_service.log_logout(user_id)
    auth_service.clear_session()
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login_page"))


@bp.route("/unauthorized")
def unauthorized():
    """Render the unauthorized access page."""
    return render_template("auth/unauthorized.html"), 403


@bp.route("/dev-login")
def dev_login():
    """
    Development-only login bypass.

    Logs in as the first active admin user without OAuth2.  This route
    is only available when FLASK_ENV=development.
    """
    if not current_app.debug:
        flash("Development login is only available in debug mode.", "danger")
        return redirect(url_for("auth.login_page"))

    from app.models.user import User  # pylint: disable=import-outside-toplevel

    # Find the first active admin user.
    admin_user = (
        User.query
        .join(User.role)
        .filter(
            User.is_active.is_(True),
            User.role.has(role_name="admin"),
        )
        .first()
    )

    if admin_user is None:
        flash("No admin user found. Create one first.", "warning")
        return redirect(url_for("auth.login_page"))

    login_user(admin_user)
    flash(f"Dev login: signed in as {admin_user.full_name} (admin).", "info")
    return redirect(url_for("main.dashboard"))
