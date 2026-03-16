"""
Pytest configuration and shared fixtures for the PositionMatrix test suite.

Provides:
    - Application and database lifecycle fixtures (session-scoped).
    - Per-test cleanup to keep the test database clean (function-scoped).
    - Authenticated test client factory for route-level testing.
    - Role-specific user fixtures (admin, manager, IT staff, etc.).
    - Organizational structure fixtures (departments, divisions, positions).
    - Equipment catalog fixtures (hardware types/items, software types/items).

Design decisions:
    1.  **Real SQL Server, not SQLite.**  The application relies on SQL
        Server-specific features (schemas, SYSUTCDATETIME(), sys.schemas).
        Using SQLite would silently pass tests that fail in production.
    2.  **Commit-and-cleanup.**  Test data is committed to the real
        database so that Flask route handlers (which use their own
        session per request) can see it.  An ``autouse`` fixture
        deletes all test data after each test.  This is the standard
        pattern for testing Flask + SQLAlchemy against a real RDBMS.
    3.  **Flask-Login session injection.**  Authentication is handled by
        setting ``_user_id`` directly in the Flask session via
        ``session_transaction()``.
    4.  **Unique test codes.**  All fixture-created records use codes
        prefixed with ``_TST_`` and emails ending in ``@test.local``
        to enable reliable cleanup.

Run the full suite::

    pytest -v

Run with coverage::

    pytest --cov=app --cov-report=term-missing
"""

from decimal import Decimal

import pytest
from sqlalchemy import text

from app import create_app
from app.extensions import db as _db
from app.models.equipment import (
    Hardware,
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareType,
)
from app.models.organization import Department, Division, Position
from app.models.requirement import PositionHardware, PositionSoftware
from app.models.user import Role, User, UserScope


# =====================================================================
# Counter for generating unique codes within a test session.
# =====================================================================

_unique_counter = 0


def _next_unique_code(prefix: str) -> str:
    """
    Generate a unique code string for test data.

    Each call increments a module-level counter and returns a string
    like ``_TST_DEPT_0001``.  This prevents unique-constraint violations
    when multiple fixtures or tests create records in the same session.

    Args:
        prefix: A short label for the entity type.

    Returns:
        A unique string safe for use in ``_code`` columns.
    """
    global _unique_counter  # pylint: disable=global-statement
    _unique_counter += 1
    return f"_TST_{prefix}_{_unique_counter:04d}"


# =====================================================================
# Application and database lifecycle (session-scoped)
# =====================================================================


@pytest.fixture(scope="session")
def app():
    """
    Create a Flask application configured for testing.

    The app is created once per test session.  The ``testing`` config
    uses a separate database (PositionMatrix_Test) to avoid polluting
    development data.

    Registers a ``request_loader`` on Flask-Login that authenticates
    via the ``X-Test-User-Id`` header.  This allows the ``auth_client``
    fixture to authenticate without touching the Flask session, which
    avoids SQLAlchemy session lifecycle conflicts in Flask-SQLAlchemy 3.x.
    """
    application = create_app("testing")

    with application.app_context():
        # Register a request_loader for header-based test authentication.
        # Flask-Login tries session-based auth first (user_loader), then
        # falls back to request_loader.  Since test clients never set
        # _user_id in the session cookie, user_loader returns None, and
        # this request_loader kicks in to authenticate via the header.
        from app.extensions import (
            login_manager as _lm,
        )  # pylint: disable=import-outside-toplevel

        @_lm.request_loader
        def _load_test_user_from_header(req):
            """Authenticate test requests via the X-Test-User-Id header."""
            user_id = req.headers.get("X-Test-User-Id")
            if user_id:
                return _db.session.get(User, int(user_id))
            return None

        yield application


@pytest.fixture(scope="session")
def database(app):  # pylint: disable=redefined-outer-name
    """
    Provide the SQLAlchemy database instance.

    The test database must already exist with the correct schema.
    Run the DDL script against PositionMatrix_Test before running tests.
    """
    yield _db


# =====================================================================
# Per-test cleanup (autouse, function-scoped)
# =====================================================================


@pytest.fixture(autouse=True, scope="function")
def _cleanup_test_data(app):  # pylint: disable=redefined-outer-name
    """
    Delete all test-created data after each test function.

    Runs automatically after every test.  Uses raw SQL for reliable
    FK-safe deletion order.  Identifies test data by:
        - Codes/names starting with ``_TST_`` or ``_tst_``
        - Email addresses ending with ``@test.local``

    IMPORTANT -- Flask-Login ``g._login_user`` cache:

    The ``app`` fixture holds a session-scoped app context that never
    pops between tests.  Flask stores ``g`` on the app context, and
    Flask-Login caches the authenticated user on ``g._login_user``.
    Because the app context persists, this cache survives across test
    boundaries.  After cleanup detaches the old User from the session,
    subsequent tests find a stale, detached User in the cache instead
    of calling the ``request_loader``.  The symptom is a
    ``DetachedInstanceError`` on ``current_user.is_active`` in every
    route test after the first.

    The fix: clear ``g._login_user`` before each test so Flask-Login
    is forced to call the ``request_loader`` and load a fresh User.
    """
    # -- Setup: clear Flask-Login's cached user from the persistent g. --
    # Without this, the stale User object from the previous test's
    # request remains in g._login_user and is returned by the
    # current_user proxy without ever hitting the request_loader.
    from flask import g as flask_g  # pylint: disable=import-outside-toplevel

    flask_g.pop("_login_user", None)

    yield

    try:
        # Clear Flask-Login's cached user again after the test so the
        # detached User object does not leak into the next test's
        # fixture setup phase (which may access current_user via
        # logging or middleware).
        flask_g.pop("_login_user", None)

        _db.session.rollback()

        cleanup_statements = [
            # Audit log entries referencing test users.
            """DELETE FROM audit.audit_log
               WHERE user_id IN (
                   SELECT id FROM auth.[user]
                   WHERE email LIKE '%@test.local'
               )""",
            # Budget: requirement history for test positions.
            """DELETE FROM budget.requirement_history
               WHERE position_id IN (
                   SELECT id FROM org.position
                   WHERE position_code LIKE '_TST_%'
               )""",
            # Budget: cost histories for test hardware.
            """DELETE FROM budget.hardware_cost_history
               WHERE hardware_id IN (
                   SELECT id FROM equip.hardware
                   WHERE name LIKE '_TST_%'
               )""",
            # Budget: cost histories for test hardware types.
            """DELETE FROM budget.hardware_type_cost_history
               WHERE hardware_type_id IN (
                   SELECT id FROM equip.hardware_type
                   WHERE type_name LIKE '_TST_%'
               )""",
            # Budget: cost histories for test software.
            """DELETE FROM budget.software_cost_history
               WHERE software_id IN (
                   SELECT id FROM equip.software
                   WHERE name LIKE '_TST_%'
               )""",
            # Position hardware requirements for test positions.
            """DELETE FROM equip.position_hardware
               WHERE position_id IN (
                   SELECT id FROM org.position
                   WHERE position_code LIKE '_TST_%'
               )""",
            # Position software requirements for test positions.
            """DELETE FROM equip.position_software
               WHERE position_id IN (
                   SELECT id FROM org.position
                   WHERE position_code LIKE '_TST_%'
               )""",
            # Software coverage for test software.
            """DELETE FROM equip.software_coverage
               WHERE software_id IN (
                   SELECT id FROM equip.software
                   WHERE name LIKE '_TST_%'
               )""",
            # User scopes for test users.
            """DELETE FROM auth.user_scope
               WHERE user_id IN (
                   SELECT id FROM auth.[user]
                   WHERE email LIKE '%@test.local'
               )""",
            # Test users themselves.
            """DELETE FROM auth.[user]
               WHERE email LIKE '%@test.local'""",
            # Org tables (children before parents).
            """DELETE FROM org.position
               WHERE position_code LIKE '_TST_%'""",
            """DELETE FROM org.division
               WHERE division_code LIKE '_TST_%'""",
            """DELETE FROM org.department
               WHERE department_code LIKE '_TST_%'""",
            # Equipment catalog.
            """DELETE FROM equip.hardware
               WHERE name LIKE '_TST_%'""",
            """DELETE FROM equip.hardware_type
               WHERE type_name LIKE '_TST_%'""",
            """DELETE FROM equip.software
               WHERE name LIKE '_TST_%'""",
            """DELETE FROM equip.software_type
               WHERE type_name LIKE '_TST_%'""",
        ]

        for stmt in cleanup_statements:
            _db.session.execute(text(stmt))

        _db.session.commit()
        _db.session.remove()

    except Exception:  # pylint: disable=broad-except
        _db.session.rollback()
        _db.session.remove()


# =====================================================================
# Database session (function-scoped)
# =====================================================================


@pytest.fixture(scope="function")
def db_session(app):  # pylint: disable=redefined-outer-name
    """
    Provide the real Flask-SQLAlchemy database session.

    This is the SAME session that route handlers use.  Test data
    created through this session and committed is visible to both
    service-layer code and route handlers during the test.

    Cleanup is handled by the ``_cleanup_test_data`` autouse fixture.
    """
    yield _db.session


# =====================================================================
# Test client (function-scoped)
# =====================================================================


@pytest.fixture(scope="function")
def client(app):  # pylint: disable=redefined-outer-name
    """
    Provide a Flask test client for making unauthenticated HTTP requests.

    Usage in tests::

        def test_health_check(client):
            response = client.get("/health")
            assert response.status_code == 200
    """
    with app.test_client() as test_client:
        yield test_client


# =====================================================================
# Authenticated client factory (function-scoped)
# =====================================================================


@pytest.fixture(scope="function")
def auth_client(app):  # pylint: disable=redefined-outer-name
    """
    Factory fixture that returns a logged-in Flask test client.

    Accepts a ``User`` model instance and returns a wrapper around
    Flask's test client that injects an ``X-Test-User-Id`` header
    into every request.  The ``request_loader`` registered in the
    ``app`` fixture reads this header and authenticates the user.

    This approach avoids ``session_transaction()`` entirely, which
    prevents SQLAlchemy identity-map conflicts in Flask-SQLAlchemy 3.x.
    Each request gets a fresh ``db.session.get(User, id)`` call from
    the request_loader, ensuring the user object is always current.

    Usage in tests::

        def test_admin_can_list_users(auth_client, admin_user):
            client = auth_client(admin_user)
            response = client.get("/admin/users")
            assert response.status_code == 200

        def test_two_roles(auth_client, admin_user, manager_user):
            admin_c = auth_client(admin_user)
            mgr_c = auth_client(manager_user)
            assert admin_c.get("/admin/users").status_code == 200
            assert mgr_c.get("/admin/users").status_code == 403
    """

    def _make_authenticated_client(user):
        """
        Create a test client that authenticates as the given user.

        Args:
            user: A ``User`` model instance that has been committed
                  to the database.

        Returns:
            An ``_AuthenticatedTestClient`` that injects the auth
            header into every HTTP request.
        """
        return _AuthenticatedTestClient(app, user.id)

    return _make_authenticated_client


class _AuthenticatedTestClient:
    """
    Wrapper around Flask's test client that adds the
    ``X-Test-User-Id`` authentication header to every request.

    Delegates all standard HTTP methods (get, post, put, patch,
    delete) to the underlying Flask test client after injecting
    the header.  Other attributes are proxied directly.
    """

    def __init__(self, flask_app, user_id):
        """
        Initialize the authenticated client.

        Args:
            flask_app: The Flask application instance.
            user_id:   The integer primary key of the User to
                       authenticate as.
        """
        self._client = flask_app.test_client()
        self._user_id = str(user_id)

    def _inject_auth(self, kwargs):
        """Add the X-Test-User-Id header to the request kwargs."""
        headers = dict(kwargs.pop("headers", None) or {})
        headers["X-Test-User-Id"] = self._user_id
        kwargs["headers"] = headers
        return kwargs

    def get(self, *args, **kwargs):
        """Send a GET request with authentication."""
        return self._client.get(*args, **self._inject_auth(kwargs))

    def post(self, *args, **kwargs):
        """Send a POST request with authentication."""
        return self._client.post(*args, **self._inject_auth(kwargs))

    def put(self, *args, **kwargs):
        """Send a PUT request with authentication."""
        return self._client.put(*args, **self._inject_auth(kwargs))

    def patch(self, *args, **kwargs):
        """Send a PATCH request with authentication."""
        return self._client.patch(*args, **self._inject_auth(kwargs))

    def delete(self, *args, **kwargs):
        """Send a DELETE request with authentication."""
        return self._client.delete(*args, **self._inject_auth(kwargs))

    @property
    def application(self):
        """Return the Flask application for context access."""
        return self._client.application

    def __getattr__(self, name):
        """Proxy any other attribute access to the underlying client."""
        return getattr(self._client, name)


# =====================================================================
# Role lookup helper (session-scoped)
# =====================================================================


@pytest.fixture(scope="session")
def roles(app):  # pylint: disable=redefined-outer-name
    """
    Look up the five seeded roles by name and return them as a dict.

    Returns:
        Dict mapping role names to Role model instances.
    """
    with app.app_context():
        role_names = [
            "admin",
            "it_staff",
            "manager",
            "budget_executive",
            "read_only",
        ]
        role_map = {}
        for name in role_names:
            role = Role.query.filter_by(role_name=name).first()
            assert role is not None, (
                f"Seed role '{name}' not found in auth.role. "
                f"Run the DDL script against the test database first."
            )
            role_map[name] = role

        return role_map


# =====================================================================
# User fixtures (function-scoped)
# =====================================================================


def _create_test_user(session, role_id, email_prefix, first, last, scopes=None):
    """
    Create a test user with specified role and scopes, then commit.

    Args:
        session:      The database session to use.
        role_id:      FK to auth.role.
        email_prefix: Prefix for the unique email address.
        first:        First name.
        last:         Last name.
        scopes:       List of scope dicts.  Defaults to org-wide.

    Returns:
        The committed User model instance.
    """
    user = User(
        email=_next_unique_code(email_prefix) + "@test.local",
        first_name=first,
        last_name=last,
        role_id=role_id,
        is_active=True,
    )
    session.add(user)
    session.flush()

    if scopes is None:
        scopes = [{"scope_type": "organization"}]

    for scope_data in scopes:
        scope = UserScope(
            user_id=user.id,
            scope_type=scope_data["scope_type"],
            department_id=scope_data.get("department_id"),
            division_id=scope_data.get("division_id"),
        )
        session.add(scope)

    session.commit()
    return user


@pytest.fixture(scope="function")
def admin_user(db_session, roles):
    """Create an admin user with organization-wide scope."""
    return _create_test_user(
        db_session,
        roles["admin"].id,
        "ADM",
        "Test",
        "Admin",
    )


@pytest.fixture(scope="function")
def it_staff_user(db_session, roles):
    """Create an IT staff user with organization-wide scope."""
    return _create_test_user(
        db_session,
        roles["it_staff"].id,
        "ITS",
        "Test",
        "ITStaff",
    )


@pytest.fixture(scope="function")
def budget_user(db_session, roles):
    """Create a budget executive user with organization-wide scope."""
    return _create_test_user(
        db_session,
        roles["budget_executive"].id,
        "BDG",
        "Test",
        "Budget",
    )


@pytest.fixture(scope="function")
def read_only_user(db_session, roles, sample_org):
    """Create a read-only user scoped to division div_a1."""
    return _create_test_user(
        db_session,
        roles["read_only"].id,
        "RO",
        "Test",
        "ReadOnly",
        scopes=[
            {
                "scope_type": "division",
                "division_id": sample_org["div_a1"].id,
            }
        ],
    )


@pytest.fixture(scope="function")
def manager_user(db_session, roles, sample_org):
    """Create a manager user scoped to division div_a1."""
    return _create_test_user(
        db_session,
        roles["manager"].id,
        "MGR",
        "Test",
        "Manager",
        scopes=[
            {
                "scope_type": "division",
                "division_id": sample_org["div_a1"].id,
            }
        ],
    )


@pytest.fixture(scope="function")
def manager_dept_scope_user(db_session, roles, sample_org):
    """Create a manager user scoped to department dept_a."""
    return _create_test_user(
        db_session,
        roles["manager"].id,
        "MGR_D",
        "Test",
        "ManagerDept",
        scopes=[
            {
                "scope_type": "department",
                "department_id": sample_org["dept_a"].id,
            }
        ],
    )


@pytest.fixture(scope="function")
def inactive_user(db_session, roles):
    """Create a deactivated user."""
    user = User(
        email=_next_unique_code("INACT") + "@test.local",
        first_name="Test",
        last_name="Inactive",
        role_id=roles["read_only"].id,
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()
    return user


# =====================================================================
# Organizational structure fixture
# =====================================================================


@pytest.fixture(scope="function")
def sample_org(db_session):
    """
    Create a two-department organizational structure for testing.

    Returns:
        Dict with string keys for every entity.
    """
    dept_a = Department(
        department_code=_next_unique_code("DEPT"),
        department_name="Test Department A",
    )
    dept_b = Department(
        department_code=_next_unique_code("DEPT"),
        department_name="Test Department B",
    )
    db_session.add_all([dept_a, dept_b])
    db_session.flush()

    div_a1 = Division(
        department_id=dept_a.id,
        division_code=_next_unique_code("DIV"),
        division_name="Test Division A-1",
    )
    div_a2 = Division(
        department_id=dept_a.id,
        division_code=_next_unique_code("DIV"),
        division_name="Test Division A-2",
    )
    div_b1 = Division(
        department_id=dept_b.id,
        division_code=_next_unique_code("DIV"),
        division_name="Test Division B-1",
    )
    div_b2 = Division(
        department_id=dept_b.id,
        division_code=_next_unique_code("DIV"),
        division_name="Test Division B-2",
    )
    db_session.add_all([div_a1, div_a2, div_b1, div_b2])
    db_session.flush()

    pos_a1_1 = Position(
        division_id=div_a1.id,
        position_code=_next_unique_code("POS"),
        position_title="Test Analyst A1-1",
        authorized_count=3,
    )
    pos_a1_2 = Position(
        division_id=div_a1.id,
        position_code=_next_unique_code("POS"),
        position_title="Test Specialist A1-2",
        authorized_count=5,
    )
    pos_a2_1 = Position(
        division_id=div_a2.id,
        position_code=_next_unique_code("POS"),
        position_title="Test Coordinator A2-1",
        authorized_count=2,
    )
    pos_b1_1 = Position(
        division_id=div_b1.id,
        position_code=_next_unique_code("POS"),
        position_title="Test Technician B1-1",
        authorized_count=4,
    )
    pos_b1_2 = Position(
        division_id=div_b1.id,
        position_code=_next_unique_code("POS"),
        position_title="Test Supervisor B1-2",
        authorized_count=1,
    )
    pos_b2_1 = Position(
        division_id=div_b2.id,
        position_code=_next_unique_code("POS"),
        position_title="Test Director B2-1",
        authorized_count=6,
    )
    db_session.add_all(
        [
            pos_a1_1,
            pos_a1_2,
            pos_a2_1,
            pos_b1_1,
            pos_b1_2,
            pos_b2_1,
        ]
    )
    db_session.commit()

    return {
        "dept_a": dept_a,
        "dept_b": dept_b,
        "div_a1": div_a1,
        "div_a2": div_a2,
        "div_b1": div_b1,
        "div_b2": div_b2,
        "pos_a1_1": pos_a1_1,
        "pos_a1_2": pos_a1_2,
        "pos_a2_1": pos_a2_1,
        "pos_b1_1": pos_b1_1,
        "pos_b1_2": pos_b1_2,
        "pos_b2_1": pos_b2_1,
    }


# =====================================================================
# Equipment catalog fixture
# =====================================================================


@pytest.fixture(scope="function")
def sample_catalog(db_session):
    """
    Create a minimal equipment catalog with known costs.

    Returns:
        Dict with string keys for every catalog entity.
    """
    hw_type_laptop = HardwareType(
        type_name=_next_unique_code("HWTYPE"),
        description="Test laptop category",
        estimated_cost=Decimal("0.00"),
        max_selections=1,
    )
    hw_type_monitor = HardwareType(
        type_name=_next_unique_code("HWTYPE"),
        description="Test monitor category",
        estimated_cost=Decimal("0.00"),
        max_selections=None,
    )
    db_session.add_all([hw_type_laptop, hw_type_monitor])
    db_session.flush()

    hw_laptop_standard = Hardware(
        hardware_type_id=hw_type_laptop.id,
        name=_next_unique_code("HW"),
        description="Standard test laptop",
        estimated_cost=Decimal("1200.00"),
    )
    hw_laptop_power = Hardware(
        hardware_type_id=hw_type_laptop.id,
        name=_next_unique_code("HW"),
        description="Power test laptop",
        estimated_cost=Decimal("2400.00"),
    )
    hw_monitor_24 = Hardware(
        hardware_type_id=hw_type_monitor.id,
        name=_next_unique_code("HW"),
        description="24-inch test monitor",
        estimated_cost=Decimal("350.00"),
    )
    db_session.add_all([hw_laptop_standard, hw_laptop_power, hw_monitor_24])
    db_session.flush()

    sw_type_productivity = SoftwareType(
        type_name=_next_unique_code("SWTYPE"),
        description="Test productivity software",
    )
    sw_type_security = SoftwareType(
        type_name=_next_unique_code("SWTYPE"),
        description="Test security software",
    )
    db_session.add_all([sw_type_productivity, sw_type_security])
    db_session.flush()

    sw_office_e3 = Software(
        name=_next_unique_code("SW"),
        software_type_id=sw_type_productivity.id,
        license_model="per_user",
        cost_per_license=Decimal("200.00"),
        total_cost=Decimal("0.00"),
    )
    sw_office_e5 = Software(
        name=_next_unique_code("SW"),
        software_type_id=sw_type_productivity.id,
        license_model="per_user",
        cost_per_license=Decimal("400.00"),
        total_cost=Decimal("0.00"),
    )
    sw_antivirus = Software(
        name=_next_unique_code("SW"),
        software_type_id=sw_type_security.id,
        license_model="tenant",
        cost_per_license=Decimal("0.00"),
        total_cost=Decimal("50000.00"),
    )
    db_session.add_all([sw_office_e3, sw_office_e5, sw_antivirus])
    db_session.commit()

    return {
        "hw_type_laptop": hw_type_laptop,
        "hw_type_monitor": hw_type_monitor,
        "hw_laptop_standard": hw_laptop_standard,
        "hw_laptop_power": hw_laptop_power,
        "hw_monitor_24": hw_monitor_24,
        "sw_type_productivity": sw_type_productivity,
        "sw_type_security": sw_type_security,
        "sw_office_e3": sw_office_e3,
        "sw_office_e5": sw_office_e5,
        "sw_antivirus": sw_antivirus,
    }


# =====================================================================
# Requirement builder helpers
# =====================================================================


@pytest.fixture(scope="function")
def create_hw_requirement(db_session):
    """Factory fixture for creating hardware requirements in tests."""

    def _create(position, hardware, quantity=1, notes=None):
        """Create a hardware requirement and commit it."""
        req = PositionHardware(
            position_id=position.id,
            hardware_id=hardware.id,
            quantity=quantity,
            notes=notes,
        )
        db_session.add(req)
        db_session.commit()
        return req

    return _create


@pytest.fixture(scope="function")
def create_sw_requirement(db_session):
    """Factory fixture for creating software requirements in tests."""

    def _create(position, software, quantity=1, notes=None):
        """Create a software requirement and commit it."""
        req = PositionSoftware(
            position_id=position.id,
            software_id=software.id,
            quantity=quantity,
            notes=notes,
        )
        db_session.add(req)
        db_session.commit()
        return req

    return _create


@pytest.fixture(scope="function")
def create_sw_coverage(db_session):
    """Factory fixture for creating software coverage records."""

    def _create(
        software,
        scope_type="organization",
        department_id=None,
        division_id=None,
        position_id=None,
    ):
        """Create a software coverage record and commit it."""
        cov = SoftwareCoverage(
            software_id=software.id,
            scope_type=scope_type,
            department_id=department_id,
            division_id=division_id,
            position_id=position_id,
        )
        db_session.add(cov)
        db_session.commit()
        return cov

    return _create


# =====================================================================
# User factory helper (for custom role/scope combos)
# =====================================================================


@pytest.fixture(scope="function")
def create_user(db_session, roles):
    """
    Factory fixture for creating users with arbitrary role/scope combos.

    Usage::

        def test_multi_scope(create_user, sample_org):
            user = create_user(
                role_name="manager",
                scopes=[
                    {"scope_type": "division",
                     "division_id": sample_org["div_a1"].id},
                    {"scope_type": "division",
                     "division_id": sample_org["div_b1"].id},
                ],
            )
    """

    def _create(
        role_name="read_only",
        scopes=None,
        is_active=True,
        first_name="Test",
        last_name="User",
    ):
        """Create a user with the specified role and scopes."""
        if is_active:
            return _create_test_user(
                db_session,
                roles[role_name].id,
                "USR",
                first_name,
                last_name,
                scopes=scopes,
            )
        user = User(
            email=_next_unique_code("USR") + "@test.local",
            first_name=first_name,
            last_name=last_name,
            role_id=roles[role_name].id,
            is_active=False,
        )
        db_session.add(user)
        db_session.commit()
        return user

    return _create
