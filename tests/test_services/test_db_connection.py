"""
Database connectivity and seed data verification tests.

These tests confirm that:
  - The application can connect to SQL Server.
  - The expected schemas and tables exist.
  - Seed data (roles, permissions, conditions) was loaded by the DDL script.

Run from your project root with::

    pytest tests/test_services/test_db_connection.py -v
"""

from app.extensions import db
from app.models.user import Role, Permission


class TestDatabaseConnectivity:
    """Verify that the app can talk to SQL Server."""

    def test_basic_connection(self, app):
        """
        Execute a simple SELECT 1 query to confirm the database
        is reachable and the connection string is correct.
        """
        with app.app_context():
            result = db.session.execute(db.text("SELECT 1 AS connected"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1

    def test_database_name(self, app):
        """Confirm we're connected to the expected database."""
        with app.app_context():
            result = db.session.execute(db.text("SELECT DB_NAME()"))
            db_name = result.fetchone()[0]
            # Should be PositionMatrix or PositionMatrix_Test.
            assert "PositionMatrix" in db_name


class TestSchemaExists:
    """Verify that all expected schemas were created by the DDL script."""

    def test_application_schemas_exist(self, app):
        """
        All seven application schemas should be present in the database.
        """
        expected_schemas = {"org", "equip", "asset", "auth", "audit", "budget", "itsm"}

        with app.app_context():
            result = db.session.execute(
                db.text(
                    """
                SELECT s.name
                FROM sys.schemas s
                WHERE s.name IN ('org', 'equip', 'asset', 'auth', 'audit', 'budget', 'itsm')
            """
                )
            )
            found_schemas = {row[0] for row in result.fetchall()}

        assert (
            found_schemas == expected_schemas
        ), f"Missing schemas: {expected_schemas - found_schemas}"


class TestSeedData:
    """Verify that the DDL script's INSERT statements loaded seed data."""

    def test_roles_are_seeded(self, app):
        """
        The five application roles should exist in auth.role.
        """
        expected_roles = {
            "admin",
            "it_staff",
            "manager",
            "budget_executive",
            "read_only",
        }

        with app.app_context():
            roles = db.session.execute(db.select(Role.role_name)).scalars().all()

        assert set(roles) == expected_roles

    def test_permissions_are_seeded(self, app):
        """
        There should be at least 20 permissions loaded from the DDL.
        """
        with app.app_context():
            count = db.session.execute(db.select(db.func.count(Permission.id))).scalar()

        assert count >= 20, f"Expected ≥20 permissions, found {count}"

    def test_admin_has_all_permissions(self, app):
        """
        The admin role should have every permission assigned via
        role_permission mappings.
        """
        with app.app_context():
            # Count total permissions.
            total_permissions = db.session.execute(
                db.select(db.func.count(Permission.id))
            ).scalar()

            # Count permissions assigned to the admin role.
            admin_permission_count = db.session.execute(
                db.text(
                    """
                SELECT COUNT(*)
                FROM auth.role_permission rp
                INNER JOIN auth.role r ON r.id = rp.role_id
                WHERE r.role_name = 'admin'
            """
                )
            ).scalar()

        assert admin_permission_count == total_permissions, (
            f"Admin has {admin_permission_count} permissions but "
            f"there are {total_permissions} total"
        )

    def test_asset_conditions_are_seeded(self, app):
        """The seven asset condition records should be present."""
        with app.app_context():
            count = db.session.execute(
                db.text("SELECT COUNT(*) FROM asset.condition")
            ).scalar()

        assert count == 7
