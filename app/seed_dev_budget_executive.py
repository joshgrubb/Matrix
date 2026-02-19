"""
Seed script — create a development budget user for local testing.

Registers a ``flask seed-dev-budget-executive`` CLI command that creates (or
reactivates) a local budget user with organization-wide scope.  This
user is used with the ``/auth/dev-login`` bypass route so you can
develop and test without a working Entra ID app registration.

Usage::

    flask seed-dev-budget-executive                    # Create with defaults
    flask seed-dev-budget-executive --email me@co.gov  # Custom email
    flask seed-dev-budget-executive --first Dev        # Custom first name
    flask seed-dev-budget-executive --last budget       # Custom last name

Prerequisites:
    - The database must exist and the DDL script must have been run.
    - The ``auth.role`` table must contain seed data (at minimum the
      ``budget`` role).  If roles haven't been seeded yet, this script
      will report the issue and exit.
"""

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db
from app.models.user import Role, User, UserScope


# -- Default values for the dev budget user ---------------------------------
_DEFAULT_EMAIL = "dev.budget@localhost"
_DEFAULT_FIRST_NAME = "Dev"
_DEFAULT_LAST_NAME = "Budget"


@click.command("seed-dev-budget-executive")
@click.option(
    "--email",
    default=_DEFAULT_EMAIL,
    show_default=True,
    help="Email address for the dev budget user.",
)
@click.option(
    "--first",
    "first_name",
    default=_DEFAULT_FIRST_NAME,
    show_default=True,
    help="First name for the dev budget user.",
)
@click.option(
    "--last",
    "last_name",
    default=_DEFAULT_LAST_NAME,
    show_default=True,
    help="Last name for the dev budget user.",
)
@with_appcontext
def seed_dev_budget_command(email: str, first_name: str, last_name: str):
    """
    Create a development budget user for local testing.

    If a user with the given email already exists, the script will
    ensure they have the budget role, are active, and have org-wide
    scope — then exit without creating a duplicate.
    """
    click.echo("=" * 60)
    click.echo("  PositionMatrix — Seed Dev budget User")
    click.echo("=" * 60)

    # -- Step 1: Verify the budget role exists -------------------------------
    click.echo("\n[1/3] Looking up budget role...")
    budget_role = Role.query.filter_by(role_name="budget_executive").first()

    if budget_role is None:
        click.secho(
            "      ✗ The 'budget' role was not found in auth.role.",
            fg="red",
        )
        click.echo("        Run the database seed script to populate roles first.")
        raise SystemExit(1)

    click.secho(
        f"      ✓ Found role: {budget_role.role_name} (id={budget_role.id})",
        fg="green",
    )

    # -- Step 2: Create or update the user ---------------------------------
    click.echo("\n[2/3] Creating dev budget user...")
    user = User.query.filter(User.email.ilike(email)).first()

    if user is not None:
        # User already exists — ensure they are budget and active.
        click.echo(f"      User '{email}' already exists (id={user.id}).")
        changed = False

        if user.role_id != budget_role.id:
            user.role_id = budget_role.id
            click.echo("      → Updated role to budget.")
            changed = True

        if not user.is_active:
            user.is_active = True
            click.echo("      → Reactivated user.")
            changed = True

        if changed:
            db.session.commit()
            click.secho("      ✓ Existing user updated.", fg="green")
        else:
            click.secho("      ✓ User is already an active budget.", fg="green")
    else:
        # Create a new user.  entra_object_id is left NULL because this
        # user will only be used with the dev-login bypass route.
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role_id=budget_role.id,
            is_active=True,
            entra_object_id=None,
        )
        db.session.add(user)
        # Flush to get the auto-generated id before adding the scope.
        db.session.flush()
        click.secho(
            f"      ✓ Created user: {first_name} {last_name} "
            f"<{email}> (id={user.id})",
            fg="green",
        )

    # -- Step 3: Ensure organization-wide scope ----------------------------
    click.echo("\n[3/3] Checking organization scope...")
    existing_scope = UserScope.query.filter_by(
        user_id=user.id,
        scope_type="organization",
    ).first()

    if existing_scope is not None:
        click.secho("      ✓ Org-wide scope already exists.", fg="green")
    else:
        org_scope = UserScope(
            user_id=user.id,
            scope_type="organization",
        )
        db.session.add(org_scope)
        click.secho("      ✓ Added organization-wide scope.", fg="green")

    # Commit any pending changes (new user + scope, or just scope).
    db.session.commit()

    # -- Summary -----------------------------------------------------------
    click.echo("\n" + "=" * 60)
    click.secho("  Dev budget user is ready.", fg="green", bold=True)
    click.echo(f"  Email:  {user.email}")
    click.echo(f"  Name:   {user.full_name}")
    click.echo(f"  Role:   {user.role.role_name}")
    click.echo(f"  Active: {user.is_active}")
    click.echo(f"  Scope:  organization")
    click.echo("=" * 60)
    click.echo("\n  → Start the app with FLASK_ENV=development, then visit")
    click.echo("    http://localhost:5000/auth/login-page and click")
    click.echo('    "Dev Login (Debug Only)" to sign in.\n')


def register_seed_commands(app):
    """Register seed-related CLI commands with the Flask application."""
    app.cli.add_command(seed_dev_budget_command)
