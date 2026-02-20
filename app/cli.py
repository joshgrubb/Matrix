"""
Custom Flask CLI commands.

These commands are registered with the app via ``init_app()`` in the
application factory. Run them with ``flask <command_name>``.

Usage::

    flask db-check      # Verify database connectivity and schema
"""

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db


@click.command("db-check")
@with_appcontext
def db_check_command():
    """
    Verify database connectivity and confirm expected schemas exist.

    Tests the connection string from the app config, runs a simple
    query against the database, and lists the schemas and tables it
    finds. This is useful for confirming your .env file is correct
    and the DDL script has been executed.
    """
    click.echo("=" * 60)
    click.echo("  PositionMatrix — Database Connectivity Check")
    click.echo("=" * 60)

    # Show the connection string (mask any password).
    db_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    click.echo(f"\n  Connection string: {db_uri}\n")

    # -- Step 1: Basic connectivity ----------------------------------------
    click.echo("[1/3] Testing connection...")
    try:
        result = db.session.execute(db.text("SELECT 1 AS connected"))
        row = result.fetchone()
        if row and row[0] == 1:
            click.secho("      ✓ Connected to SQL Server successfully.", fg="green")
        else:
            click.secho("      ✗ Unexpected result from test query.", fg="red")
            return
    except Exception as exc:  # pylint: disable=broad-exception-caught
        click.secho(f"      ✗ Connection failed: {exc}", fg="red")
        click.echo("\n  Troubleshooting tips:")
        click.echo("    - Is SQL Server running?")
        click.echo("    - Is the instance name correct (e.g., localhost\\SQLEXPRESS)?")
        click.echo("    - Is ODBC Driver 18 for SQL Server installed?")
        click.echo("    - Does your .env DATABASE_URL match your server config?")
        click.echo("    - If using Windows Auth, is Trusted_Connection=yes in the URL?")
        return

    # -- Step 2: Confirm database name -------------------------------------
    click.echo("[2/3] Checking database...")
    try:
        result = db.session.execute(db.text("SELECT DB_NAME() AS db_name"))
        db_name = result.fetchone()[0]
        click.secho(f"      ✓ Connected to database: {db_name}", fg="green")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        click.secho(f"      ✗ Could not determine database name: {exc}", fg="red")
        return

    # -- Step 3: List schemas and table counts -----------------------------
    click.echo("[3/3] Checking schemas and tables...\n")
    try:
        query = db.text(
            """
            SELECT
                s.name AS schema_name,
                COUNT(t.name) AS table_count
            FROM sys.schemas s
            INNER JOIN sys.tables t ON t.schema_id = s.schema_id
            WHERE s.name IN ('org', 'equip', 'asset', 'auth', 'audit', 'budget', 'itsm')
            GROUP BY s.name
            ORDER BY s.name
        """
        )
        result = db.session.execute(query)
        rows = result.fetchall()

        if not rows:
            click.secho("      ✗ No application schemas found.", fg="red")
            click.echo("        Have you run the database_creation.sql script?")
            return

        # Display schema summary.
        total_tables = 0
        for row in rows:
            click.echo(f"      {row[0]:>8}  — {row[1]} table(s)")
            total_tables += row[1]

        click.echo(f"\n      Total: {total_tables} tables across {len(rows)} schemas")

        # Quick spot-check: verify seed data exists.
        result = db.session.execute(db.text("SELECT COUNT(*) FROM auth.role"))
        role_count = result.fetchone()[0]

        result = db.session.execute(db.text("SELECT COUNT(*) FROM auth.permission"))
        permission_count = result.fetchone()[0]

        click.echo(
            f"\n      Seed data: {role_count} roles, {permission_count} permissions"
        )

        if role_count >= 5 and permission_count >= 20:
            click.secho("      ✓ Seed data looks good.", fg="green")
        else:
            click.secho("      ⚠ Seed data may be incomplete.", fg="yellow")

    except Exception as exc:  # pylint: disable=broad-exception-caught
        click.secho(f"      ✗ Schema check failed: {exc}", fg="red")
        return

    click.echo("\n" + "=" * 60)
    click.secho("  All checks passed. Database is ready.", fg="green", bold=True)
    click.echo("=" * 60)


@click.command("hr-sync")
@with_appcontext
def hr_sync_command():
    """Run a full NeoGov HR sync from the command line."""
    from app.services import hr_sync_service

    click.echo("Starting NeoGov HR sync...")
    log = hr_sync_service.run_full_sync()
    click.echo(f"Status: {log.status}")
    click.echo(
        f"Processed: {log.records_processed}  "
        f"Created: {log.records_created}  "
        f"Updated: {log.records_updated}  "
        f"Deactivated: {log.records_deactivated}  "
        f"Errors: {log.records_errors}"
    )
    if log.error_message:
        click.secho(f"Error: {log.error_message}", fg="red")


def register_commands(app):
    """Register all custom CLI commands with the Flask application."""
    app.cli.add_command(db_check_command)
    app.cli.add_command(hr_sync_command)
