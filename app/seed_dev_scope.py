"""
Seed script — assign organizational scopes to dev users for testing.

Registers a ``flask seed-dev-scope`` CLI command that replaces a dev
user's scopes with the specified scope type (organization, department,
or division).  This lets you test scoped views and access restrictions
without modifying the seed-user scripts themselves.

Usage::

    # Give a user org-wide access (default for admin/it_staff).
    flask seed-dev-scope --email dev.manager@localhost --scope organization

    # Restrict a user to a single department by ID.
    flask seed-dev-scope --email dev.manager@localhost \\
        --scope department --department-id 3

    # Restrict a user to specific divisions by ID (repeatable).
    flask seed-dev-scope --email dev.read_only@localhost \\
        --scope division --division-id 5 --division-id 8

    # List all dev users and their current scopes (read-only).
    flask seed-dev-scope --list

Prerequisites:
    - The database must exist and the DDL script must have been run.
    - The target dev user must already exist (run the appropriate
      ``seed-dev-*`` command first).
    - For department or division scopes, the referenced IDs must
      exist in the ``org.department`` / ``org.division`` tables.
"""

import click
from flask.cli import with_appcontext

from app.extensions import db
from app.models.organization import Department, Division
from app.models.user import User, UserScope


@click.command("seed-dev-scope")
@click.option(
    "--email",
    default=None,
    help="Email address of the dev user to update.",
)
@click.option(
    "--scope",
    "scope_type",
    type=click.Choice(["organization", "department", "division"], case_sensitive=False),
    default=None,
    help="Scope type to assign: organization, department, or division.",
)
@click.option(
    "--department-id",
    "department_ids",
    multiple=True,
    type=int,
    help="Department ID(s) for department scope (repeatable).",
)
@click.option(
    "--division-id",
    "division_ids",
    multiple=True,
    type=int,
    help="Division ID(s) for division scope (repeatable).",
)
@click.option(
    "--list",
    "list_users",
    is_flag=True,
    default=False,
    help="List all dev users and their current scopes, then exit.",
)
@with_appcontext
def seed_dev_scope_command(
    email: str | None,
    scope_type: str | None,
    department_ids: tuple[int, ...],
    division_ids: tuple[int, ...],
    list_users: bool,
):
    """
    Assign organizational scopes to a dev user for testing.

    Replaces all existing scopes for the target user with the
    specified scope configuration.  Use ``--list`` to view the
    current scope assignments for all dev users.
    """
    click.echo("=" * 60)
    click.echo("  PositionMatrix — Dev User Scope Manager")
    click.echo("=" * 60)

    # -- List mode: show all dev users and exit. ---------------------------
    if list_users:
        _list_dev_users()
        return

    # -- Validation --------------------------------------------------------
    if email is None:
        click.secho(
            "\n  ✗ --email is required (or use --list to view users).",
            fg="red",
        )
        raise SystemExit(1)

    if scope_type is None:
        click.secho(
            "\n  ✗ --scope is required (organization, department, or division).",
            fg="red",
        )
        raise SystemExit(1)

    # -- Step 1: Look up the target user. ----------------------------------
    click.echo(f"\n[1/3] Looking up user: {email}")
    user = User.query.filter(User.email.ilike(email)).first()

    if user is None:
        click.secho(f"      ✗ No user found with email '{email}'.", fg="red")
        click.echo("        Run the seed script first, e.g.:")
        click.echo("          flask seed-dev-admin")
        click.echo("          flask seed-dev-manager")
        raise SystemExit(1)

    click.secho(
        f"      ✓ Found: {user.full_name} " f"(id={user.id}, role={user.role_name})",
        fg="green",
    )

    # -- Step 2: Validate the scope references. ----------------------------
    click.echo(f"\n[2/3] Validating scope: {scope_type}")
    new_scopes = []

    if scope_type == "organization":
        # Organization-wide — no extra IDs needed.
        new_scopes.append(
            UserScope(
                user_id=user.id,
                scope_type="organization",
            )
        )
        click.secho("      ✓ Will assign organization-wide scope.", fg="green")

    elif scope_type == "department":
        if not department_ids:
            click.secho(
                "      ✗ --department-id is required for department scope.",
                fg="red",
            )
            raise SystemExit(1)

        for dept_id in department_ids:
            dept = db.session.get(Department, dept_id)
            if dept is None:
                click.secho(
                    f"      ✗ Department ID {dept_id} not found in org.department.",
                    fg="red",
                )
                raise SystemExit(1)

            new_scopes.append(
                UserScope(
                    user_id=user.id,
                    scope_type="department",
                    department_id=dept.id,
                )
            )
            click.secho(
                f"      ✓ Department: {dept.department_name} (id={dept.id})",
                fg="green",
            )

    elif scope_type == "division":
        if not division_ids:
            click.secho(
                "      ✗ --division-id is required for division scope.",
                fg="red",
            )
            raise SystemExit(1)

        for div_id in division_ids:
            div = db.session.get(Division, div_id)
            if div is None:
                click.secho(
                    f"      ✗ Division ID {div_id} not found in org.division.",
                    fg="red",
                )
                raise SystemExit(1)

            new_scopes.append(
                UserScope(
                    user_id=user.id,
                    scope_type="division",
                    division_id=div.id,
                )
            )
            click.secho(
                f"      ✓ Division: {div.division_name} "
                f"(id={div.id}, dept={div.department.department_name})",
                fg="green",
            )

    # -- Step 3: Replace existing scopes. ----------------------------------
    click.echo("\n[3/3] Replacing scopes...")

    # Log the old scopes for visibility.
    old_scopes = UserScope.query.filter_by(user_id=user.id).all()
    if old_scopes:
        click.echo(f"      Removing {len(old_scopes)} existing scope(s):")
        for old in old_scopes:
            click.echo(f"        - {_describe_scope(old)}")
    else:
        click.echo("      No existing scopes to remove.")

    # Delete old scopes and add new ones.
    UserScope.query.filter_by(user_id=user.id).delete()
    for scope in new_scopes:
        db.session.add(scope)
    db.session.commit()

    click.secho(
        f"      ✓ Assigned {len(new_scopes)} new scope(s).",
        fg="green",
    )

    # -- Summary -----------------------------------------------------------
    click.echo("\n" + "=" * 60)
    click.secho("  Scope update complete.", fg="green", bold=True)
    click.echo(f"  User:   {user.full_name} <{user.email}>")
    click.echo(f"  Role:   {user.role_name}")
    click.echo(f"  Scopes: {len(new_scopes)}")
    for scope in new_scopes:
        click.echo(f"    → {_describe_scope(scope)}")
    click.echo("=" * 60)
    click.echo("\n  → Sign in via the dev login picker to test this scope.\n")


def _list_dev_users():
    """
    Print a table of all dev users (``@localhost``) and their scopes.

    Called when the ``--list`` flag is provided.
    """
    dev_users = (
        User.query.filter(User.email.ilike("%@localhost"))
        .order_by(User.role_id, User.email)
        .all()
    )

    if not dev_users:
        click.echo("\n  No dev users found (@localhost emails).")
        click.echo("  Run one of the seed scripts first:")
        click.echo("    flask seed-dev-admin")
        click.echo("    flask seed-dev-manager")
        click.echo("    flask seed-dev-it_staff")
        click.echo("    flask seed-dev-read_only")
        click.echo("    flask seed-dev-budget-executive")
        return

    click.echo(f"\n  Found {len(dev_users)} dev user(s):\n")
    click.echo(f"  {'ID':<5} {'Email':<30} {'Role':<18} " f"{'Active':<8} Scopes")
    click.echo("  " + "-" * 85)

    for user in dev_users:
        # Build a compact scope description.
        scope_parts = []
        for scope in user.scopes:
            scope_parts.append(_describe_scope(scope))
        scope_str = ", ".join(scope_parts) if scope_parts else "(none)"

        active_str = "Yes" if user.is_active else "No"
        click.echo(
            f"  {user.id:<5} {user.email:<30} {user.role_name:<18} "
            f"{active_str:<8} {scope_str}"
        )

    click.echo()


def _describe_scope(scope: UserScope) -> str:
    """
    Return a human-readable label for a single UserScope record.

    Args:
        scope: A UserScope model instance.

    Returns:
        A string like ``organization``, ``dept:Public Works (3)``,
        or ``div:Roads & Bridges (5)``.
    """
    if scope.scope_type == "organization":
        return "organization"

    if scope.scope_type == "department":
        name = scope.department.department_name if scope.department is not None else "?"
        return f"dept:{name} ({scope.department_id})"

    if scope.scope_type == "division":
        name = scope.division.division_name if scope.division is not None else "?"
        return f"div:{name} ({scope.division_id})"

    return f"{scope.scope_type}(?)"


def register_seed_commands(app):
    """Register the seed-dev-scope CLI command with the Flask application."""
    app.cli.add_command(seed_dev_scope_command)
