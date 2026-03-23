"""
Routes for the auth blueprint -- login, logout, and OAuth2 callback.

The login flow redirects to Microsoft Entra ID for authentication.
After successful auth, the callback route completes the authorization
code flow and logs the user in via Flask-Login.

MSAL API migration (2026-03-23):
    The login route now calls ``auth_service.initiate_auth_flow()``
    which uses MSAL's ``initiate_auth_code_flow()`` internally.  The
    callback route calls ``auth_service.complete_auth_flow()`` which
    uses ``acquire_token_by_auth_code_flow()``.  MSAL handles CSRF
    state validation, nonce verification, and code exchange in a
    single call, so the manual state/code checks have been removed.

Development-only routes (``/dev-login``, ``/dev-login-picker``) are
available when ``FLASK_ENV=development`` (i.e., ``app.debug`` is True).
These routes bypass OAuth2 and allow one-click login as any seeded
dev user, optionally filtered by role.
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
    Otherwise, start the MSAL auth code flow (which stores the flow
    state in the session) and redirect to the Microsoft login page.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    # Generate a random state token for CSRF protection.
    # MSAL embeds this in the flow dict and validates it automatically
    # when complete_auth_flow() is called in the callback.
    state = str(uuid.uuid4())

    try:
        auth_url = auth_service.initiate_auth_flow(state=state)
        return redirect(auth_url)
    except ValueError as exc:
        # initiate_auth_flow raises ValueError if the MSAL app config
        # is invalid (e.g., bad authority URL or missing client ID).
        flash(str(exc), "danger")
        return redirect(url_for("auth.login_page"))


@bp.route("/callback")
def callback():
    """
    Handle the OAuth2 redirect from Microsoft Entra ID.

    Passes the full query string to ``auth_service.complete_auth_flow()``,
    which retrieves the stored flow state from the session, validates
    the CSRF state parameter, exchanges the authorization code for
    tokens, and returns the token result.  All validation (state
    mismatch, missing code, expired grant) is handled by MSAL internally
    and surfaced as a ``ValueError``.
    """
    # Check for errors from Microsoft before attempting the exchange.
    # Microsoft appends ``error`` and ``error_description`` to the
    # redirect URL when the user cancels or the tenant rejects the
    # request.  Handling this early gives the user a cleaner message
    # than the generic MSAL error path.
    if "error" in request.args:
        error_desc = request.args.get("error_description", "Unknown error")
        flash(f"Authentication failed: {error_desc}", "danger")
        # Pop the flow from the session so it does not linger.
        session.pop("auth_code_flow", None)
        return redirect(url_for("auth.login_page"))

    try:
        token_result = auth_service.complete_auth_flow(dict(request.args))
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


# =========================================================================
# Development-Only Routes
# =========================================================================


@bp.route("/dev-login")
def dev_login():
    """
    Development-only login bypass.

    Logs in as a dev user without OAuth2.  Accepts an optional ``role``
    query parameter to select a user by role name, and an optional
    ``user_id`` parameter to select a specific user by primary key.

    Query Parameters:
        role (str):     Role name to match (e.g., ``admin``, ``manager``).
                        Defaults to ``admin`` for backward compatibility.
        user_id (int):  Specific user ID to log in as.  Takes precedence
                        over ``role`` when both are provided.

    Examples::

        /auth/dev-login                    -> first active admin
        /auth/dev-login?role=manager       -> first active manager
        /auth/dev-login?role=read_only     -> first active read-only user
        /auth/dev-login?user_id=7          -> user with id=7

    This route is only available when ``FLASK_ENV=development``.
    """
    if not current_app.debug or not current_app.config.get("DEV_LOGIN_ENABLED"):
        flash("Development login is only available in debug mode.", "danger")
        return redirect(url_for("auth.login_page"))

    # Import models inside the route to avoid circular imports.
    from app.models.user import User  # pylint: disable=import-outside-toplevel

    # Determine which user to log in as.
    target_user = None
    user_id_param = request.args.get("user_id", type=int)
    role_param = request.args.get("role", "admin").strip().lower()

    if user_id_param is not None:
        # Direct user ID selection -- highest priority.
        target_user = User.query.filter(
            User.id == user_id_param,
            User.is_active == True,  # pylint: disable=singleton-comparison
        ).first()

        if target_user is None:
            flash(
                f"No active user found with ID {user_id_param}.",
                "warning",
            )
            return redirect(url_for("auth.dev_login_picker"))
    else:
        # Role-based selection -- find the first active user with this role.
        target_user = (
            User.query.join(User.role)
            .filter(
                User.is_active == True,  # pylint: disable=singleton-comparison
                User.role.has(role_name=role_param),
            )
            .first()
        )

        if target_user is None:
            flash(
                f"No active user with role '{role_param}' found. "
                f"Run the seed script first: flask seed-dev-{role_param}",
                "warning",
            )
            return redirect(url_for("auth.dev_login_picker"))

    # Log the user in via Flask-Login.
    login_user(target_user)

    # Build a descriptive scope summary for the flash message.
    scope_summary = _describe_user_scopes(target_user)

    flash(
        f"Dev login: signed in as {target_user.full_name} "
        f"({target_user.role_name}). Scope: {scope_summary}",
        "info",
    )
    return redirect(url_for("main.dashboard"))


@bp.route("/dev-login-picker")
def dev_login_picker():
    """
    Development-only login picker page.

    Lists all dev users (identified by ``@localhost`` email addresses)
    with one-click login buttons.  Shows each user's role, scope, and
    department/division assignments for easy test-role selection.

    This route is only available when ``FLASK_ENV=development``.
    """
    if not current_app.debug or not current_app.config.get("DEV_LOGIN_ENABLED"):
        flash("Development login is only available in debug mode.", "danger")
        return redirect(url_for("auth.login_page"))

    # Import models inside the route to avoid circular imports.
    from app.models.user import User  # pylint: disable=import-outside-toplevel

    # Fetch all localhost dev users, grouped by role.
    dev_users = (
        User.query.join(User.role)
        .filter(
            User.email.ilike("%@localhost"),
            User.is_active == True,  # pylint: disable=singleton-comparison
        )
        .order_by(User.role_id, User.last_name)
        .all()
    )

    # Build a list of user dicts with scope descriptions for the template.
    user_cards = []
    for user in dev_users:
        user_cards.append(
            {
                "id": user.id,
                "full_name": user.full_name,
                "email": user.email,
                "role_name": user.role_name,
                "scope_summary": _describe_user_scopes(user),
            }
        )

    return render_template(
        "auth/dev_login.html",
        user_cards=user_cards,
    )


def _describe_user_scopes(user) -> str:
    """
    Build a human-readable summary of a user's organizational scopes.

    Args:
        user: A User model instance with eagerly loaded scopes.

    Returns:
        A string like ``Organization-wide``, ``Dept: Public Works``,
        or ``Div: Roads & Bridges, Water``.
    """
    if user.has_org_scope():
        return "Organization-wide"

    # Collect department and division scope descriptions.
    dept_names = []
    div_names = []

    for scope in user.scopes:
        if scope.scope_type == "department" and scope.department is not None:
            dept_names.append(scope.department.department_name)
        elif scope.scope_type == "division" and scope.division is not None:
            div_names.append(scope.division.division_name)

    parts = []
    if dept_names:
        parts.append(f"Dept: {', '.join(dept_names)}")
    if div_names:
        parts.append(f"Div: {', '.join(div_names)}")

    return "; ".join(parts) if parts else "No scopes assigned"
