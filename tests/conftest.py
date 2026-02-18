"""
Pytest configuration and shared fixtures.

Provides a test application, database session, and test client that
all test modules can use. Uses the ``testing`` configuration which
points to a separate test database.
"""

import pytest

from app import create_app
from app.extensions import db as _db


@pytest.fixture(scope="session")
def app():
    """
    Create a Flask application configured for testing.

    The app is created once per test session. The ``testing`` config
    uses a separate database (PositionMatrix_Test) to avoid polluting
    development data.
    """
    app = create_app("testing")

    # Establish an application context for the entire test session.
    with app.app_context():
        yield app


@pytest.fixture(scope="session")
def database(app):  # pylint: disable=redefined-outer-name
    """
    Provide the SQLAlchemy database instance.

    Since the database schema is managed by the DDL script (not by
    Flask-Migrate), this fixture assumes the test database already
    exists and has the correct schema. Run the DDL script against
    PositionMatrix_Test before running tests.
    """
    yield _db


@pytest.fixture(scope="function")
def db_session(database):  # pylint: disable=redefined-outer-name
    """
    Provide a clean database session for each test function.

    Each test runs within a nested transaction that is rolled back
    after the test completes, keeping the test database clean.
    """
    connection = database.engine.connect()
    transaction = connection.begin()

    # Bind a scoped session to this connection.
    options = {"bind": connection, "binds": {}}
    session = database.create_scoped_session(options=options)
    database.session = session

    yield session

    # Roll back the transaction so the test database stays clean.
    transaction.rollback()
    connection.close()
    session.remove()


@pytest.fixture(scope="function")
def client(app):  # pylint: disable=redefined-outer-name
    """
    Provide a Flask test client for making HTTP requests.

    Usage in tests::

        def test_dashboard(client):
            response = client.get("/")
            assert response.status_code == 200
    """
    with app.test_client() as test_client:
        yield test_client
