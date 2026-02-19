"""
Seed script — create a development read_only user for local testing.

Registers a ``flask seed-dev-read_only`` CLI command that creates (or
reactivates) a local read_only user with organization-wide scope.  This
user is used with the ``/auth/dev-login`` bypass route so you can
develop and test without a working Entra ID app registration.

Usage::

    flask seed-dev-read_only                    # Create with defaults
    flask seed-dev-read_only --email me@co.gov  # Custom email
    flask seed-dev-read_only --first Dev        # Custom first name
    flask seed-dev-read_only --last read_only       # Custom last name

Prerequisites:
    - The database must exist and the DDL script must have been run.
    - The ``auth.role`` table must contain seed data (at minimum the
      ``read_only`` role).  If roles haven't been seeded yet, this script
      will report the issue and exit.
"""

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db
from app.models.user import Role, User, UserScope


# -- Default values for the dev read_only user ---------------------------------
_DEFAULT_EMAIL = "dev.read_only@localhost"
_DEFAULT_FIRST_NAME = "Dev"
_DEFAULT_LAST_NAME = "read_only"


@click.command("seed-dev-read_only")
@click.option(
    "--email",
    default=_DEFAULT_EMAIL,
    show_default=True,
    help="Email address for the dev read_only user.",
)
@click.option(
    "--first",
    "first_name",
    default=_DEFAULT_FIRST_NAME,
    show_default=True,
    help="First name for the dev read_only user.",
)
@click.option(
    "--last",
    "last_name",
    default=_DEFAULT_LAST_NAME,
    show_default=True,
    help="Last name for the dev read_only user.",
)
@with_appcontext
def seed_dev_read_only_command(email: str, first_name: str, last_name: str):
    """
    Create a development read_only user for local testing.

    If a user with the given email already exists, the script will
    ensure they have the read_only role, are active, and have org-wide
    scope — then exit without creating a duplicate.
    """
    click.echo("=" * 60)
    click.echo("  PositionMatrix — Seed Dev read_only User")
    click.echo("=" * 60)

    # -- Step 1: Verify the read_only role exists -------------------------------
    click.echo("\n[1/3] Looking up read_only role...")
    read_only_role = Role.query.filter_by(role_name="read_only").first()

    if read_only_role is None:
        click.secho(
            "      ✗ The 'read_only' role was not found in auth.role.",
            fg="red",
        )
        click.echo("        Run the database seed script to populate roles first.")
        raise SystemExit(1)

    click.secho(
        f"      ✓ Found role: {read_only_role.role_name} (id={read_only_role.id})",
        fg="green",
    )

    # -- Step 2: Create or update the user ---------------------------------
    click.echo("\n[2/3] Creating dev read_only user...")
    user = User.query.filter(User.email.ilike(email)).first()

    if user is not None:
        # User already exists — ensure they are read_only and active.
        click.echo(f"      User '{email}' already exists (id={user.id}).")
        changed = False

        if user.role_id != read_only_role.id:
            user.role_id = read_only_role.id
            click.echo("      → Updated role to read_only.")
            changed = True

        if not user.is_active:
            user.is_active = True
            click.echo("      → Reactivated user.")
            changed = True

        if changed:
            db.session.commit()
            click.secho("      ✓ Existing user updated.", fg="green")
        else:
            click.secho("      ✓ User is already an active read_only.", fg="green")
    else:
        # Create a new user.  entra_object_id is left NULL because this
        # user will only be used with the dev-login bypass route.
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role_id=read_only_role.id,
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
    click.secho("  Dev read_only user is ready.", fg="green", bold=True)
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
    app.cli.add_command(seed_dev_read_only_command)
