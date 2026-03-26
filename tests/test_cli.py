"""
Tests for the custom Flask CLI commands in ``app/cli.py``.

Verifies the ``db-check`` and ``hr-sync`` CLI commands that are
registered via ``register_commands()`` in the application factory.

Coverage targets:
    - ``db_check_command``: 0% -> 100% (58 statements)
    - ``hr_sync_command``: 0% -> 100% (7 statements)
    - ``register_commands``: already at 100% (validated here)

Strategy reference: testing_strategy.md Section 7.3 (4 baseline tests).
This file exceeds the baseline with 30+ tests covering every branch:

    db-check command (3-step diagnostic):
        Step 1 -- Basic connectivity:
            - SELECT 1 succeeds and returns 1.
            - SELECT 1 returns an unexpected value.
            - Connection raises an exception (SQL Server down).
        Step 2 -- Database name retrieval:
            - DB_NAME() succeeds and returns the database name.
            - DB_NAME() raises an exception.
        Step 3 -- Schema and table enumeration:
            - All application schemas are found with tables.
            - No application schemas found (empty DDL).
            - Seed data passes the threshold (>=5 roles, >=20 perms).
            - Seed data is incomplete (<5 roles or <20 permissions).
            - Schema query raises an exception.
        Full success path:
            - All three steps pass, "All checks passed" displayed.

    hr-sync command:
        - Successful sync with statistics output.
        - Sync completes with error_message populated.
        - Sync completes without error_message.
        - Service function is called exactly once.
        - Output includes every statistics field.

    Command registration:
        - db-check is registered and discoverable.
        - hr-sync is registered and discoverable.

Design decisions:
    - The ``db-check`` happy path runs against the real test database
      WITHOUT mocking.  This confirms the command works end-to-end
      against the actual SQL Server instance.  Error-path tests mock
      ``db.session.execute`` to simulate failures.
    - The ``hr-sync`` command always mocks ``hr_sync_service`` because
      the real service calls the NeoGov API and creates audit records.
    - All tests use ``app.test_cli_runner()`` which is Flask's
      recommended approach for testing Click commands.  The runner
      invokes commands in an app context automatically.
    - Mock targets use ``app.cli.db`` (the module-level reference
      imported in cli.py) so patches intercept correctly.

Fixture reminder (from conftest.py):
    app:  Session-scoped Flask application (testing config).

Run this file in isolation::

    pytest tests/test_cli.py -v
"""

from unittest.mock import MagicMock, patch


# =====================================================================
# Helper: invoke a CLI command and return the result
# =====================================================================


def _invoke(app, command_name, args=None):
    """
    Invoke a Flask CLI command via the test runner.

    Args:
        app:          The Flask application instance.
        command_name: The command name string (e.g., 'db-check').
        args:         Optional list of additional CLI arguments.

    Returns:
        A ``click.testing.Result`` object with ``exit_code``,
        ``output``, and ``exception`` attributes.
    """
    runner = app.test_cli_runner(mix_stderr=False)
    cli_args = [command_name]
    if args:
        cli_args.extend(args)
    return runner.invoke(args=cli_args)


# =====================================================================
# 1. Command registration verification
# =====================================================================


class TestCommandRegistration:
    """
    Verify that both CLI commands are registered on the Flask app
    and can be discovered by the test runner.
    """

    def test_db_check_command_is_registered(self, app):
        """
        The ``db-check`` command must be registered on the app so
        ``flask db-check`` works from the terminal.
        """
        # Flask stores CLI commands in app.cli.commands (a dict).
        assert "db-check" in app.cli.commands, (
            "db-check command not registered. "
            "Check that register_commands() is called in create_app()."
        )

    def test_hr_sync_command_is_registered(self, app):
        """
        The ``hr-sync`` command must be registered on the app so
        ``flask hr-sync`` works from the terminal.
        """
        assert "hr-sync" in app.cli.commands, (
            "hr-sync command not registered. "
            "Check that register_commands() is called in create_app()."
        )


# =====================================================================
# 2. db-check -- full happy path (real database)
# =====================================================================


class TestDbCheckHappyPath:
    """
    Run ``db-check`` against the real SQL Server test database.

    These tests confirm the command works end-to-end without mocks.
    The test database must be set up with the DDL script (schemas,
    tables, seed data) for these tests to pass.
    """

    def test_db_check_exits_zero_on_healthy_database(self, app):
        """
        When the test database is properly configured, db-check
        should exit with code 0 (success).
        """
        result = _invoke(app, "db-check")
        assert result.exit_code == 0, (
            f"db-check exited with code {result.exit_code}. "
            f"Output:\n{result.output}"
        )

    def test_db_check_outputs_connection_string(self, app):
        """
        The output should display the connection string so the
        operator can verify they are pointed at the right server.
        """
        result = _invoke(app, "db-check")
        assert "Connection string:" in result.output

    def test_db_check_outputs_step_1_success(self, app):
        """Step 1 should report a successful SQL Server connection."""
        result = _invoke(app, "db-check")
        assert "Connected to SQL Server successfully" in result.output

    def test_db_check_outputs_step_2_database_name(self, app):
        """
        Step 2 should display the database name.  For the test
        database, this should contain 'PositionMatrix'.
        """
        result = _invoke(app, "db-check")
        assert "Connected to database:" in result.output
        assert "PositionMatrix" in result.output

    def test_db_check_outputs_step_3_schema_summary(self, app):
        """
        Step 3 should list the application schemas and their table
        counts.  The output should mention at least some of the
        expected schemas (org, equip, auth, audit, etc.).
        """
        result = _invoke(app, "db-check")
        # At least two of the known schemas should appear.
        assert "org" in result.output
        assert "auth" in result.output

    def test_db_check_outputs_total_table_count(self, app):
        """
        Step 3 should display a total count of tables across all
        application schemas.
        """
        result = _invoke(app, "db-check")
        assert "Total:" in result.output
        assert "tables across" in result.output

    def test_db_check_outputs_seed_data_counts(self, app):
        """
        Step 3 should report the number of seeded roles and
        permissions so the operator can verify seed data is loaded.
        """
        result = _invoke(app, "db-check")
        assert "Seed data:" in result.output
        assert "roles" in result.output
        assert "permissions" in result.output

    def test_db_check_outputs_seed_data_good_marker(self, app):
        """
        When seed data meets the threshold (>=5 roles, >=20
        permissions), the output should say "Seed data looks good."
        """
        result = _invoke(app, "db-check")
        assert "Seed data looks good" in result.output

    def test_db_check_outputs_all_checks_passed(self, app):
        """
        The final line should confirm all checks passed.  This is
        the overall success indicator.
        """
        result = _invoke(app, "db-check")
        assert "All checks passed" in result.output

    def test_db_check_outputs_database_is_ready(self, app):
        """
        The success banner should include 'Database is ready' so
        it is clear the system is operational.
        """
        result = _invoke(app, "db-check")
        assert "Database is ready" in result.output

    def test_db_check_step_indicators_in_order(self, app):
        """
        The output should contain the three step indicators in the
        correct sequential order.
        """
        result = _invoke(app, "db-check")
        output = result.output
        pos_1 = output.find("[1/3]")
        pos_2 = output.find("[2/3]")
        pos_3 = output.find("[3/3]")
        assert (
            pos_1 < pos_2 < pos_3
        ), "Step indicators are not in sequential order in output"


# =====================================================================
# 3. db-check -- Step 1 failure: connection exception
# =====================================================================


class TestDbCheckStep1ConnectionFailure:
    """
    Verify that db-check handles a complete connection failure
    gracefully and provides troubleshooting guidance.
    """

    @patch("app.cli.db.session.execute")
    def test_connection_failure_does_not_crash(self, mock_execute, app):
        """
        If the database is unreachable, db-check should catch the
        exception and exit without a traceback.
        """
        mock_execute.side_effect = Exception(
            "Cannot open server 'localhost' requested by the login."
        )
        result = _invoke(app, "db-check")
        # Should not raise an unhandled exception.
        assert result.exception is None or result.exit_code == 0

    @patch("app.cli.db.session.execute")
    def test_connection_failure_shows_error_message(self, mock_execute, app):
        """
        The failure output should include the exception text so the
        operator can diagnose the problem.
        """
        mock_execute.side_effect = Exception("Cannot open server 'localhost'")
        result = _invoke(app, "db-check")
        assert "Connection failed" in result.output

    @patch("app.cli.db.session.execute")
    def test_connection_failure_shows_troubleshooting_tips(self, mock_execute, app):
        """
        When Step 1 fails, the command should print troubleshooting
        tips to help the operator fix the issue.
        """
        mock_execute.side_effect = Exception("Connection refused")
        result = _invoke(app, "db-check")
        assert (
            "Troubleshooting" in result.output or "SQL Server running" in result.output
        )

    @patch("app.cli.db.session.execute")
    def test_connection_failure_mentions_odbc_driver(self, mock_execute, app):
        """
        Troubleshooting tips should mention ODBC Driver 18, which
        is a common missing dependency on fresh installs.
        """
        mock_execute.side_effect = Exception("ODBC Driver not found")
        result = _invoke(app, "db-check")
        assert "ODBC Driver 18" in result.output

    @patch("app.cli.db.session.execute")
    def test_connection_failure_does_not_proceed_to_step_2(self, mock_execute, app):
        """
        If Step 1 fails, Steps 2 and 3 should not run.  The command
        should return early after showing the error.
        """
        mock_execute.side_effect = Exception("Connection refused")
        result = _invoke(app, "db-check")
        assert "[2/3]" not in result.output
        assert "[3/3]" not in result.output


# =====================================================================
# 4. db-check -- Step 1 failure: unexpected query result
# =====================================================================


class TestDbCheckStep1UnexpectedResult:
    """
    Verify that db-check handles SELECT 1 returning an unexpected
    value (not 1).
    """

    @patch("app.cli.db.session.execute")
    def test_unexpected_result_reports_error(self, mock_execute, app):
        """
        If SELECT 1 returns something other than 1, the command
        should report an unexpected result.
        """
        # Create a mock result where fetchone() returns (99,).
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (99,)
        mock_execute.return_value = mock_result

        result = _invoke(app, "db-check")
        assert "Unexpected result" in result.output

    @patch("app.cli.db.session.execute")
    def test_unexpected_result_does_not_proceed_to_step_2(self, mock_execute, app):
        """
        An unexpected Step 1 result should cause an early return
        before Step 2.
        """
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (0,)
        mock_execute.return_value = mock_result

        result = _invoke(app, "db-check")
        assert "[2/3]" not in result.output


# =====================================================================
# 5. db-check -- Step 2 failure: DB_NAME() exception
# =====================================================================


class TestDbCheckStep2Failure:
    """
    Verify that db-check handles a failure in Step 2 (database name
    retrieval) without crashing.
    """

    @patch("app.cli.db.session.execute")
    def test_step_2_failure_shows_error(self, mock_execute, app):
        """
        If DB_NAME() fails, the command should report the error
        and stop before Step 3.
        """
        # First call (Step 1: SELECT 1) succeeds.
        mock_step_1_result = MagicMock()
        mock_step_1_result.fetchone.return_value = (1,)

        # Second call (Step 2: DB_NAME()) fails.
        mock_execute.side_effect = [
            mock_step_1_result,
            Exception("Could not determine database name: permission denied"),
        ]

        result = _invoke(app, "db-check")
        assert "Could not determine database name" in result.output

    @patch("app.cli.db.session.execute")
    def test_step_2_failure_does_not_proceed_to_step_3(self, mock_execute, app):
        """
        A Step 2 failure should prevent Step 3 from running.
        """
        mock_step_1_result = MagicMock()
        mock_step_1_result.fetchone.return_value = (1,)

        mock_execute.side_effect = [
            mock_step_1_result,
            Exception("DB_NAME() failed"),
        ]

        result = _invoke(app, "db-check")
        assert "[3/3]" not in result.output

    @patch("app.cli.db.session.execute")
    def test_step_1_passes_before_step_2_fails(self, mock_execute, app):
        """
        Step 1 should still show success even when Step 2 fails.
        """
        mock_step_1_result = MagicMock()
        mock_step_1_result.fetchone.return_value = (1,)

        mock_execute.side_effect = [
            mock_step_1_result,
            Exception("DB_NAME() failed"),
        ]

        result = _invoke(app, "db-check")
        assert "Connected to SQL Server successfully" in result.output
        assert "Could not determine database name" in result.output


# =====================================================================
# 6. db-check -- Step 3 failure: no schemas found
# =====================================================================


class TestDbCheckStep3NoSchemas:
    """
    Verify that db-check reports when no application schemas exist,
    indicating the DDL script has not been run.
    """

    @patch("app.cli.db.session.execute")
    def test_no_schemas_reports_error(self, mock_execute, app):
        """
        When the schema query returns zero rows, the command should
        report that no application schemas were found.
        """
        # Step 1 succeeds.
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        # Step 2 succeeds.
        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        # Step 3: schema query returns empty list.
        mock_step_3 = MagicMock()
        mock_step_3.fetchall.return_value = []

        mock_execute.side_effect = [mock_step_1, mock_step_2, mock_step_3]

        result = _invoke(app, "db-check")
        assert "No application schemas found" in result.output

    @patch("app.cli.db.session.execute")
    def test_no_schemas_suggests_running_ddl(self, mock_execute, app):
        """
        The error message should suggest running the DDL script to
        create the schemas.
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_step_3 = MagicMock()
        mock_step_3.fetchall.return_value = []

        mock_execute.side_effect = [mock_step_1, mock_step_2, mock_step_3]

        result = _invoke(app, "db-check")
        assert "database_creation.sql" in result.output

    @patch("app.cli.db.session.execute")
    def test_no_schemas_does_not_show_all_checks_passed(self, mock_execute, app):
        """
        The success banner should NOT appear when schemas are missing.
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_step_3 = MagicMock()
        mock_step_3.fetchall.return_value = []

        mock_execute.side_effect = [mock_step_1, mock_step_2, mock_step_3]

        result = _invoke(app, "db-check")
        assert "All checks passed" not in result.output


# =====================================================================
# 7. db-check -- Step 3: incomplete seed data
# =====================================================================


class TestDbCheckStep3IncompleteSeedData:
    """
    Verify that db-check warns when seed data is below the expected
    thresholds (fewer than 5 roles or fewer than 20 permissions).
    """

    @patch("app.cli.db.session.execute")
    def test_low_role_count_shows_warning(self, mock_execute, app):
        """
        If fewer than 5 roles are seeded, the output should warn
        that seed data may be incomplete.
        """
        # Step 1 succeeds.
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        # Step 2 succeeds.
        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        # Step 3: schema query returns valid schemas.
        mock_step_3_schemas = MagicMock()
        mock_step_3_schemas.fetchall.return_value = [
            ("auth", 3),
            ("org", 4),
        ]

        # Role count query returns low count.
        mock_role_count = MagicMock()
        mock_role_count.fetchone.return_value = (2,)

        # Permission count query returns acceptable count.
        mock_perm_count = MagicMock()
        mock_perm_count.fetchone.return_value = (25,)

        mock_execute.side_effect = [
            mock_step_1,
            mock_step_2,
            mock_step_3_schemas,
            mock_role_count,
            mock_perm_count,
        ]

        result = _invoke(app, "db-check")
        assert "Seed data may be incomplete" in result.output

    @patch("app.cli.db.session.execute")
    def test_low_permission_count_shows_warning(self, mock_execute, app):
        """
        If fewer than 20 permissions are seeded, the output should
        warn that seed data may be incomplete.
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_step_3_schemas = MagicMock()
        mock_step_3_schemas.fetchall.return_value = [
            ("auth", 3),
            ("org", 4),
        ]

        # Roles pass the threshold, but permissions do not.
        mock_role_count = MagicMock()
        mock_role_count.fetchone.return_value = (5,)

        mock_perm_count = MagicMock()
        mock_perm_count.fetchone.return_value = (10,)

        mock_execute.side_effect = [
            mock_step_1,
            mock_step_2,
            mock_step_3_schemas,
            mock_role_count,
            mock_perm_count,
        ]

        result = _invoke(app, "db-check")
        assert "Seed data may be incomplete" in result.output


# =====================================================================
# 8. db-check -- Step 3 failure: schema query exception
# =====================================================================


class TestDbCheckStep3SchemaQueryException:
    """
    Verify that db-check handles an exception during the Step 3
    schema enumeration query.
    """

    @patch("app.cli.db.session.execute")
    def test_schema_query_exception_shows_error(self, mock_execute, app):
        """
        If the schema query throws an exception, the command should
        report the failure gracefully.
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_execute.side_effect = [
            mock_step_1,
            mock_step_2,
            Exception("Permission denied on sys.schemas"),
        ]

        result = _invoke(app, "db-check")
        assert "Schema check failed" in result.output

    @patch("app.cli.db.session.execute")
    def test_schema_query_exception_does_not_show_success(self, mock_execute, app):
        """
        The 'All checks passed' banner should NOT appear when
        the schema query fails.
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_execute.side_effect = [
            mock_step_1,
            mock_step_2,
            Exception("Schema query failed"),
        ]

        result = _invoke(app, "db-check")
        assert "All checks passed" not in result.output


# =====================================================================
# 9. db-check -- Step 3: good seed data with schema listing
# =====================================================================


class TestDbCheckStep3GoodSeedData:
    """
    Verify that db-check correctly identifies healthy seed data
    and displays the success banner when all checks pass.
    """

    @patch("app.cli.db.session.execute")
    def test_good_seed_data_shows_success_marker(self, mock_execute, app):
        """
        When roles >= 5 and permissions >= 20, the output should
        include 'Seed data looks good.'
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_step_3_schemas = MagicMock()
        mock_step_3_schemas.fetchall.return_value = [
            ("asset", 5),
            ("audit", 2),
            ("auth", 4),
            ("budget", 3),
            ("equip", 4),
            ("itsm", 3),
            ("org", 4),
        ]

        mock_role_count = MagicMock()
        mock_role_count.fetchone.return_value = (5,)

        mock_perm_count = MagicMock()
        mock_perm_count.fetchone.return_value = (24,)

        mock_execute.side_effect = [
            mock_step_1,
            mock_step_2,
            mock_step_3_schemas,
            mock_role_count,
            mock_perm_count,
        ]

        result = _invoke(app, "db-check")
        assert "Seed data looks good" in result.output
        assert "All checks passed" in result.output

    @patch("app.cli.db.session.execute")
    def test_schema_listing_shows_each_schema_name(self, mock_execute, app):
        """
        Each schema returned by the query should appear in the
        output alongside its table count.
        """
        mock_step_1 = MagicMock()
        mock_step_1.fetchone.return_value = (1,)

        mock_step_2 = MagicMock()
        mock_step_2.fetchone.return_value = ("PositionMatrixTest",)

        mock_step_3_schemas = MagicMock()
        mock_step_3_schemas.fetchall.return_value = [
            ("auth", 4),
            ("org", 5),
        ]

        mock_role_count = MagicMock()
        mock_role_count.fetchone.return_value = (5,)

        mock_perm_count = MagicMock()
        mock_perm_count.fetchone.return_value = (24,)

        mock_execute.side_effect = [
            mock_step_1,
            mock_step_2,
            mock_step_3_schemas,
            mock_role_count,
            mock_perm_count,
        ]

        result = _invoke(app, "db-check")
        assert "auth" in result.output
        assert "org" in result.output


# =====================================================================
# Patch target for hr-sync tests.
#
# ``hr_sync_command`` does a lazy import inside the function body:
#     from app.services import hr_sync_service
#     log = hr_sync_service.run_full_sync()
#
# Because the import resolves at call time, ``app.cli.hr_sync_service``
# does NOT exist as a module-level attribute.  We must patch the
# function on the actual module that the lazy import resolves to:
#     app.services.hr_sync_service.run_full_sync
# =====================================================================

_HR_SYNC_PATCH = "app.services.hr_sync_service.run_full_sync"


def _make_sync_log(
    status="completed",
    processed=0,
    created=0,
    updated=0,
    deactivated=0,
    errors=0,
    error_message=None,
):
    """
    Build a MagicMock that mimics an ``HRSyncLog`` model instance.

    Centralizes mock construction so every hr-sync test uses
    consistent attribute names matching the real model.

    Args:
        status:        Sync status string ('completed' or 'failed').
        processed:     Total records processed.
        created:       Records created.
        updated:       Records updated.
        deactivated:   Records deactivated.
        errors:        Records with errors.
        error_message: Optional error description string.

    Returns:
        A MagicMock with the expected HRSyncLog attributes.
    """
    mock_log = MagicMock()
    mock_log.status = status
    mock_log.records_processed = processed
    mock_log.records_created = created
    mock_log.records_updated = updated
    mock_log.records_deactivated = deactivated
    mock_log.records_errors = errors
    mock_log.error_message = error_message
    return mock_log


# =====================================================================
# 10. hr-sync -- successful sync (mocked service)
# =====================================================================


class TestHrSyncCommandSuccess:
    """
    Verify that ``hr-sync`` calls the sync service, outputs the
    statistics, and handles both success and error scenarios.

    The ``run_full_sync`` function is always mocked because the real
    implementation calls the NeoGov API and writes audit records.
    """

    @patch(_HR_SYNC_PATCH)
    def test_hr_sync_exits_zero_on_success(self, mock_run, app):
        """
        A successful sync should result in exit code 0.
        """
        mock_run.return_value = _make_sync_log(
            status="completed",
            processed=150,
            created=10,
            updated=30,
            deactivated=5,
        )

        result = _invoke(app, "hr-sync")
        assert result.exit_code == 0, (
            f"hr-sync exited with code {result.exit_code}. " f"Output:\n{result.output}"
        )

    @patch(_HR_SYNC_PATCH)
    def test_hr_sync_outputs_starting_message(self, mock_run, app):
        """
        The command should announce that the sync is starting so
        the operator knows it is running.
        """
        mock_run.return_value = _make_sync_log()

        result = _invoke(app, "hr-sync")
        assert "Starting NeoGov HR sync" in result.output

    @patch(_HR_SYNC_PATCH)
    def test_hr_sync_outputs_status(self, mock_run, app):
        """
        The output should display the sync status returned by the
        service (e.g., 'completed', 'failed').
        """
        mock_run.return_value = _make_sync_log(
            status="completed",
            processed=42,
            created=5,
            updated=10,
            deactivated=2,
        )

        result = _invoke(app, "hr-sync")
        assert "Status: completed" in result.output

    @patch(_HR_SYNC_PATCH)
    def test_hr_sync_outputs_all_statistics(self, mock_run, app):
        """
        The output should include all five statistics fields:
        Processed, Created, Updated, Deactivated, and Errors.
        """
        mock_run.return_value = _make_sync_log(
            status="completed",
            processed=100,
            created=15,
            updated=20,
            deactivated=3,
            errors=2,
        )

        result = _invoke(app, "hr-sync")
        assert "Processed: 100" in result.output
        assert "Created: 15" in result.output
        assert "Updated: 20" in result.output
        assert "Deactivated: 3" in result.output
        assert "Errors: 2" in result.output

    @patch(_HR_SYNC_PATCH)
    def test_hr_sync_calls_run_full_sync_once(self, mock_run, app):
        """
        The command should call ``run_full_sync()`` exactly once
        per invocation.
        """
        mock_run.return_value = _make_sync_log()

        _invoke(app, "hr-sync")
        mock_run.assert_called_once()


# =====================================================================
# 11. hr-sync -- sync with error message
# =====================================================================


class TestHrSyncCommandWithError:
    """
    Verify that when the sync log contains an error_message, the
    command displays it prominently.
    """

    @patch(_HR_SYNC_PATCH)
    def test_error_message_is_displayed(self, mock_run, app):
        """
        If ``log.error_message`` is not None, the command should
        output the error text.
        """
        mock_run.return_value = _make_sync_log(
            status="failed",
            processed=50,
            created=3,
            errors=5,
            error_message="NeoGov API returned 503 Service Unavailable",
        )

        result = _invoke(app, "hr-sync")
        assert "Error:" in result.output
        assert "NeoGov API returned 503" in result.output

    @patch(_HR_SYNC_PATCH)
    def test_error_message_does_not_appear_when_none(self, mock_run, app):
        """
        If ``log.error_message`` is None (success), the output
        should NOT contain the 'Error:' prefix line.
        """
        mock_run.return_value = _make_sync_log(
            status="completed",
            processed=42,
            created=5,
            updated=10,
            deactivated=2,
        )

        result = _invoke(app, "hr-sync")
        assert "Error:" not in result.output

    @patch(_HR_SYNC_PATCH)
    def test_failed_status_still_shows_statistics(self, mock_run, app):
        """
        Even when the sync fails, the partial statistics should be
        displayed so the operator knows how far the sync got.
        """
        mock_run.return_value = _make_sync_log(
            status="failed",
            processed=75,
            created=8,
            updated=12,
            errors=3,
            error_message="Timeout after 30 seconds",
        )

        result = _invoke(app, "hr-sync")
        assert "Status: failed" in result.output
        assert "Processed: 75" in result.output
        assert "Created: 8" in result.output
        assert "Errors: 3" in result.output

    @patch(_HR_SYNC_PATCH)
    def test_zero_records_sync_outputs_zeros(self, mock_run, app):
        """
        A sync that processes zero records (empty NeoGov response)
        should output zeros without crashing.
        """
        mock_run.return_value = _make_sync_log()

        result = _invoke(app, "hr-sync")
        assert "Processed: 0" in result.output
        assert "Created: 0" in result.output
        assert "Updated: 0" in result.output
        assert "Deactivated: 0" in result.output
        assert "Errors: 0" in result.output
