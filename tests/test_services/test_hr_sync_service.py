"""
Tests for the HR sync service layer (``app.services.hr_sync_service``).

Validates the full NeoGov sync pipeline: department, division, position,
and employee creation/update/deactivation, plus user auto-provisioning,
filled-count recalculation, sync log recording, and error handling.

Every test mocks ``NeoGovApiClient`` so that no real HTTP calls are made.
Database operations use the real SQL Server test database with the
commit-and-cleanup pattern from conftest.py.

Strategy reference: Section 6.4 (8 baseline tests) plus enhancements
from prioritized_testing_next_steps.md (idempotency, filled counts,
division sync, employee updates, user deactivation edge cases).

Run this file in isolation::

    pytest tests/test_services/test_hr_sync_service.py -v
"""

import time as _time
from unittest.mock import MagicMock, patch

import pytest

from app.extensions import db
from app.models.audit import AuditLog, HRSyncLog
from app.models.organization import Department, Division, Employee, Position
from app.models.user import Role, User, UserScope


# =====================================================================
# Module-level counter for generating unique codes within this file.
# Mirrors the pattern used in conftest.py and other test modules.
# =====================================================================

_sync_counter = int(_time.time() * 10) % 9000


def _next_code(prefix: str) -> str:
    """
    Generate a unique code string for test data specific to this module.

    Each call increments a module-level counter and returns a string
    like ``_TST_HRSYNC_DEPT_0001``.  The ``_TST_`` prefix ensures the
    conftest cleanup fixture deletes these records after each test.

    Args:
        prefix: A short label for the entity type (e.g., ``DEPT``).

    Returns:
        A unique string safe for use in ``_code`` columns.
    """
    global _sync_counter  # pylint: disable=global-statement
    _sync_counter += 1
    return f"_TST_HRSYNC_{prefix}_{_sync_counter:04d}"


# =====================================================================
# Helper: build mock API data payloads
# =====================================================================


def _build_api_data(
    departments=None,
    divisions=None,
    positions=None,
    employees=None,
):
    """
    Build a dict matching the shape returned by
    ``NeoGovApiClient.fetch_all_organization_data()``.

    Any argument left as ``None`` defaults to an empty list.

    Args:
        departments: List of department dicts with keys
                     ``department_code``, ``department_name``.
        divisions:   List of division dicts with keys
                     ``division_code``, ``division_name``,
                     ``department_code``.
        positions:   List of position dicts with keys
                     ``position_code``, ``position_title``,
                     ``division_code``, ``authorized_count``.
        employees:   List of employee dicts with keys
                     ``employee_id``, ``first_name``, ``last_name``,
                     ``email``, ``position_code``, ``is_active``.

    Returns:
        Dict with keys ``departments``, ``divisions``, ``positions``,
        ``employees``.
    """
    return {
        "departments": departments or [],
        "divisions": divisions or [],
        "positions": positions or [],
        "employees": employees or [],
    }


# =====================================================================
# Shared fixtures for this module
# =====================================================================


@pytest.fixture()
def dept_code():
    """Return a unique department code for each test."""
    return _next_code("DEPT")


@pytest.fixture()
def div_code():
    """Return a unique division code for each test."""
    return _next_code("DIV")


@pytest.fixture()
def pos_code():
    """Return a unique position code for each test."""
    return _next_code("POS")


@pytest.fixture()
def emp_code():
    """Return a unique employee code for each test."""
    return _next_code("EMP")


@pytest.fixture()
def mock_neogov_client():
    """
    Patch ``NeoGovApiClient`` inside ``hr_sync_service`` so that
    instantiation returns a mock whose ``fetch_all_organization_data``
    method can be configured per test.

    Yields:
        The mock *class* (not instance).  Configure the return value
        of ``fetch_all_organization_data`` on
        ``mock_cls.return_value.fetch_all_organization_data``.
    """
    with patch("app.services.hr_sync_service.NeoGovApiClient") as mock_cls:
        yield mock_cls


# =====================================================================
# 1. Department sync tests
# =====================================================================


class TestFullSyncCreatesDepartments:
    """
    Verify that ``run_full_sync`` creates new Department records
    when the API returns departments not present in the database.
    """

    def test_full_sync_creates_new_departments(
        self, app, db_session, mock_neogov_client, dept_code
    ):
        """
        Provide mock data with a single new department.
        Assert a Department record is created in the database
        with the correct code and name.
        """
        from app.services import hr_sync_service

        # Arrange: configure the mock API to return one new department.
        dept_name = "HR Sync Test Department"
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": dept_code,
                        "department_name": dept_name,
                    }
                ]
            )
        )

        # Act: run the full sync.
        sync_log = hr_sync_service.run_full_sync(user_id=None)

        # Assert: the department was created.
        dept = Department.query.filter_by(department_code=dept_code).first()
        assert dept is not None, f"Department with code '{dept_code}' was not created."
        assert dept.department_name == dept_name
        assert dept.is_active is True

        # Assert: the sync log reports success.
        assert sync_log.status == "completed"
        assert sync_log.records_created >= 1

    def test_full_sync_creates_multiple_departments(
        self, app, db_session, mock_neogov_client
    ):
        """
        Provide mock data with three new departments.  Assert all
        three are created and the sync log processed count is at
        least 3.
        """
        from app.services import hr_sync_service

        codes = [_next_code("DEPT") for _ in range(3)]
        dept_data = [
            {"department_code": c, "department_name": f"Dept {c}"} for c in codes
        ]

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(departments=dept_data)
        )

        sync_log = hr_sync_service.run_full_sync()

        for code in codes:
            dept = Department.query.filter_by(department_code=code).first()
            assert dept is not None, f"Department '{code}' was not created."

        assert sync_log.status == "completed"
        assert sync_log.records_processed >= 3


class TestFullSyncUpdatesDepartments:
    """
    Verify that ``run_full_sync`` updates existing Department records
    when the API returns changed data for an existing department code.
    """

    def test_full_sync_updates_changed_departments(
        self, app, db_session, mock_neogov_client, dept_code
    ):
        """
        Create a department in the database, then run a sync with
        a new name for the same department code.  Assert the existing
        record's name is updated rather than a duplicate being created.
        """
        from app.services import hr_sync_service

        # Arrange: pre-create the department with the original name.
        original_name = "Original Department Name"
        dept = Department(
            department_code=dept_code,
            department_name=original_name,
        )
        db_session.add(dept)
        db_session.commit()

        # Arrange: API returns the same code with a new name.
        updated_name = "Updated Department Name"
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": dept_code,
                        "department_name": updated_name,
                    }
                ]
            )
        )

        # Act: run the full sync.
        sync_log = hr_sync_service.run_full_sync()

        # Assert: the department was updated, not duplicated.
        db_session.refresh(dept)
        assert dept.department_name == updated_name
        assert dept.is_active is True

        # Verify no duplicate was created.
        count = Department.query.filter_by(department_code=dept_code).count()
        assert count == 1

        assert sync_log.status == "completed"
        assert sync_log.records_updated >= 1

    def test_full_sync_reactivates_inactive_department(
        self, app, db_session, mock_neogov_client, dept_code
    ):
        """
        A department that was previously deactivated should be
        reactivated when the API includes it again.
        """
        from app.services import hr_sync_service

        # Arrange: pre-create an inactive department.
        dept = Department(
            department_code=dept_code,
            department_name="Reactivation Test",
            is_active=False,
        )
        db_session.add(dept)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": dept_code,
                        "department_name": "Reactivation Test",
                    }
                ]
            )
        )

        # Act.
        hr_sync_service.run_full_sync()

        # Assert: the department is now active again.
        db_session.refresh(dept)
        assert dept.is_active is True


class TestFullSyncDeactivatesDepartments:
    """
    Verify that ``run_full_sync`` soft-deletes Department records
    that are no longer present in the API response.
    """

    def test_full_sync_deactivates_removed_departments(
        self, app, db_session, mock_neogov_client, dept_code
    ):
        """
        Pre-create a department in the database.  Run a sync
        whose API data does not include that department code.
        Assert the department's ``is_active`` is set to False.
        """
        from app.services import hr_sync_service

        # Arrange: pre-create an active department.
        dept = Department(
            department_code=dept_code,
            department_name="Soon To Be Deactivated",
        )
        db_session.add(dept)
        db_session.commit()
        assert dept.is_active is True

        # Arrange: API returns a *different* department, omitting
        # the one we just created.  The service should deactivate
        # our department.
        other_code = _next_code("DEPT")
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": other_code,
                        "department_name": "Some Other Department",
                    }
                ]
            )
        )

        # Act.
        hr_sync_service.run_full_sync()

        # Assert: our department was deactivated.
        db_session.refresh(dept)
        assert dept.is_active is False

    def test_full_sync_skips_deactivation_on_empty_api_data(
        self, app, db_session, mock_neogov_client, dept_code
    ):
        """
        If the API returns zero departments (likely an outage),
        the service should NOT deactivate existing departments.
        This is the safety guard documented in the source code.
        """
        from app.services import hr_sync_service

        # Arrange: pre-create an active department.
        dept = Department(
            department_code=dept_code,
            department_name="Should Survive Empty Sync",
        )
        db_session.add(dept)
        db_session.commit()

        # Arrange: API returns completely empty data.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data()
        )

        # Act.
        hr_sync_service.run_full_sync()

        # Assert: the department is still active.
        db_session.refresh(dept)
        assert dept.is_active is True


# =====================================================================
# 2. Division sync tests
# =====================================================================


class TestFullSyncDivisions:
    """
    Verify division create, update, and deactivation during sync.
    Divisions require a parent department to exist first.
    """

    def test_full_sync_creates_divisions_with_correct_parent(
        self, app, db_session, mock_neogov_client
    ):
        """
        Provide mock data with a department and a division linked
        to it.  Assert the Division record is created with the
        correct ``department_id`` foreign key.
        """
        from app.services import hr_sync_service

        dept_code = _next_code("DEPT")
        div_code = _next_code("DIV")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": dept_code,
                        "department_name": "Parent Department",
                    }
                ],
                divisions=[
                    {
                        "division_code": div_code,
                        "division_name": "Child Division",
                        "department_code": dept_code,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        div = Division.query.filter_by(division_code=div_code).first()
        assert div is not None, f"Division '{div_code}' was not created."
        assert div.division_name == "Child Division"

        # Verify FK chain.
        parent_dept = Department.query.filter_by(department_code=dept_code).first()
        assert div.department_id == parent_dept.id

    def test_full_sync_updates_division_name(self, app, db_session, mock_neogov_client):
        """
        Pre-create a department and division.  Sync with a new
        division name.  Assert the name is updated in-place.
        """
        from app.services import hr_sync_service

        dept_code = _next_code("DEPT")
        div_code = _next_code("DIV")

        # Pre-create.
        dept = Department(
            department_code=dept_code,
            department_name="Div Update Test Dept",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=div_code,
            division_name="Old Division Name",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.commit()

        # Sync with updated name.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": dept_code,
                        "department_name": "Div Update Test Dept",
                    }
                ],
                divisions=[
                    {
                        "division_code": div_code,
                        "division_name": "New Division Name",
                        "department_code": dept_code,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(div)
        assert div.division_name == "New Division Name"

    def test_full_sync_deactivates_removed_divisions(
        self, app, db_session, mock_neogov_client
    ):
        """
        Pre-create a division.  Run a sync that omits it.
        Assert the division is deactivated.
        """
        from app.services import hr_sync_service

        dept_code = _next_code("DEPT")
        div_code = _next_code("DIV")
        other_div_code = _next_code("DIV")

        # Pre-create department and division.
        dept = Department(
            department_code=dept_code,
            department_name="Div Deactivation Test Dept",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=div_code,
            division_name="Will Be Deactivated",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.commit()
        assert div.is_active is True

        # Sync with a different division, omitting the original.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": dept_code,
                        "department_name": "Div Deactivation Test Dept",
                    }
                ],
                divisions=[
                    {
                        "division_code": other_div_code,
                        "division_name": "Replacement Division",
                        "department_code": dept_code,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(div)
        assert div.is_active is False


# =====================================================================
# 3. Position sync tests
# =====================================================================


class TestFullSyncPositions:
    """
    Verify position create, update, and deactivation during sync.
    Positions require a parent division (and grandparent department).
    """

    def _create_dept_and_div(self, db_session):
        """
        Helper: create and commit a department and division pair.

        Returns:
            Tuple of (dept_code, div_code, division_instance).
        """
        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")

        dept = Department(
            department_code=d_code,
            department_name=f"Pos Test Dept {d_code}",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name=f"Pos Test Div {v_code}",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.commit()

        return d_code, v_code, div

    def test_full_sync_creates_positions_with_correct_fks(
        self, app, db_session, mock_neogov_client
    ):
        """
        Provide mock data with a full hierarchy (department,
        division, position).  Assert the Position record is created
        with the correct ``division_id`` and ``authorized_count``.
        """
        from app.services import hr_sync_service

        d_code, v_code, div = self._create_dept_and_div(db_session)
        p_code = _next_code("POS")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {"department_code": d_code, "department_name": "Pos Dept"}
                ],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "Pos Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "Senior Analyst",
                        "division_code": v_code,
                        "authorized_count": 7,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        pos = Position.query.filter_by(position_code=p_code).first()
        assert pos is not None, f"Position '{p_code}' was not created."
        assert pos.position_title == "Senior Analyst"
        assert pos.division_id == div.id
        assert pos.authorized_count == 7
        assert pos.is_active is True

    def test_full_sync_updates_position_title_and_auth_count(
        self, app, db_session, mock_neogov_client
    ):
        """
        Pre-create a position.  Sync with an updated title and
        authorized_count.  Assert the record is updated in-place.
        """
        from app.services import hr_sync_service

        d_code, v_code, div = self._create_dept_and_div(db_session)
        p_code = _next_code("POS")

        pos = Position(
            position_code=p_code,
            position_title="Old Title",
            division_id=div.id,
            authorized_count=2,
        )
        db_session.add(pos)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "U Dept"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "U Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "New Title",
                        "division_code": v_code,
                        "authorized_count": 10,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(pos)
        assert pos.position_title == "New Title"
        assert pos.authorized_count == 10

    def test_full_sync_deactivates_removed_positions(
        self, app, db_session, mock_neogov_client
    ):
        """
        Pre-create a position.  Run a sync that omits it.
        Assert the position is deactivated.
        """
        from app.services import hr_sync_service

        d_code, v_code, div = self._create_dept_and_div(db_session)
        p_code = _next_code("POS")
        other_p_code = _next_code("POS")

        pos = Position(
            position_code=p_code,
            position_title="Doomed Position",
            division_id=div.id,
            authorized_count=1,
        )
        db_session.add(pos)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "D Dept"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "D Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": other_p_code,
                        "position_title": "Replacement",
                        "division_code": v_code,
                        "authorized_count": 1,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(pos)
        assert pos.is_active is False

    def test_full_sync_skips_position_with_missing_division(
        self, app, db_session, mock_neogov_client
    ):
        """
        If a position references a division_code that does not exist
        in the database (and is not in the current sync payload),
        the service should log a warning and increment the error
        count rather than crash.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        p_code = _next_code("POS")
        bogus_div_code = _next_code("DIV_BOGUS")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "D"}],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "Orphan Position",
                        "division_code": bogus_div_code,
                        "authorized_count": 1,
                    }
                ],
            )
        )

        sync_log = hr_sync_service.run_full_sync()

        # The position should NOT have been created.
        pos = Position.query.filter_by(position_code=p_code).first()
        assert pos is None

        # Sync should still complete (the error is non-fatal).
        assert sync_log.status == "completed"
        assert sync_log.records_errors >= 1


# =====================================================================
# 4. Employee sync tests
# =====================================================================


class TestFullSyncEmployees:
    """
    Verify employee creation, update, and deactivation during sync.
    Employees require a parent position chain.
    """

    def _create_full_hierarchy(self, db_session):
        """
        Helper: create a complete dept -> div -> position chain.

        Returns:
            Tuple of (dept_code, div_code, pos_code, position_instance).
        """
        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")

        dept = Department(
            department_code=d_code,
            department_name=f"Emp Dept {d_code}",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name=f"Emp Div {v_code}",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title=f"Emp Pos {p_code}",
            division_id=div.id,
            authorized_count=5,
        )
        db_session.add(pos)
        db_session.commit()

        return d_code, v_code, p_code, pos

    def _make_api_payload(self, d_code, v_code, p_code, employees):
        """
        Build a complete API payload including the parent hierarchy
        so that the sync can resolve FK references.

        Args:
            d_code:    Department code.
            v_code:    Division code.
            p_code:    Position code.
            employees: List of employee dicts.

        Returns:
            Dict matching ``fetch_all_organization_data`` return shape.
        """
        return _build_api_data(
            departments=[{"department_code": d_code, "department_name": "E Dept"}],
            divisions=[
                {
                    "division_code": v_code,
                    "division_name": "E Div",
                    "department_code": d_code,
                }
            ],
            positions=[
                {
                    "position_code": p_code,
                    "position_title": "E Pos",
                    "division_code": v_code,
                    "authorized_count": 5,
                }
            ],
            employees=employees,
        )

    def test_full_sync_creates_new_employees(self, app, db_session, mock_neogov_client):
        """
        Provide mock data with a new active employee.  Assert an
        Employee record is created with the correct fields and FK.
        """
        from app.services import hr_sync_service

        d_code, v_code, p_code, pos = self._create_full_hierarchy(db_session)
        e_code = _next_code("EMP")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            self._make_api_payload(
                d_code,
                v_code,
                p_code,
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": True,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        emp = Employee.query.filter_by(employee_code=e_code).first()
        assert emp is not None, f"Employee '{e_code}' was not created."
        assert emp.first_name == "Jane"
        assert emp.last_name == "Doe"
        assert emp.position_id == pos.id
        assert emp.is_active is True

    def test_full_sync_updates_changed_employee_fields(
        self, app, db_session, mock_neogov_client
    ):
        """
        Pre-create an employee.  Sync with an updated last name.
        Assert the existing record is updated, not duplicated.
        """
        from app.services import hr_sync_service

        d_code, v_code, p_code, pos = self._create_full_hierarchy(db_session)
        e_code = _next_code("EMP")

        # Pre-create the employee.
        emp = Employee(
            employee_code=e_code,
            first_name="John",
            last_name="Smith",
            email=f"{e_code.lower()}@townofclaytonnc.org",
            position_id=pos.id,
        )
        db_session.add(emp)
        db_session.commit()

        # Sync with an updated last name.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            self._make_api_payload(
                d_code,
                v_code,
                p_code,
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "John",
                        "last_name": "Johnson",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": True,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(emp)
        assert emp.last_name == "Johnson"

        # No duplicate.
        count = Employee.query.filter_by(employee_code=e_code).count()
        assert count == 1

    def test_full_sync_deactivates_terminated_employee(
        self, app, db_session, mock_neogov_client
    ):
        """
        Pre-create an active employee.  Sync with ``is_active=False``
        for that employee.  Assert the record is deactivated.
        """
        from app.services import hr_sync_service

        d_code, v_code, p_code, pos = self._create_full_hierarchy(db_session)
        e_code = _next_code("EMP")

        emp = Employee(
            employee_code=e_code,
            first_name="Departing",
            last_name="Employee",
            email=f"{e_code.lower()}@townofclaytonnc.org",
            position_id=pos.id,
            is_active=True,
        )
        db_session.add(emp)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            self._make_api_payload(
                d_code,
                v_code,
                p_code,
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Departing",
                        "last_name": "Employee",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": False,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(emp)
        assert emp.is_active is False

    def test_full_sync_skips_inactive_employee_with_no_local_record(
        self, app, db_session, mock_neogov_client
    ):
        """
        If the API reports an inactive employee who does not already
        exist locally, the service should skip creating a record
        (no point creating a record just to deactivate it).
        """
        from app.services import hr_sync_service

        d_code, v_code, p_code, pos = self._create_full_hierarchy(db_session)
        e_code = _next_code("EMP")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            self._make_api_payload(
                d_code,
                v_code,
                p_code,
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Ghost",
                        "last_name": "Employee",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": False,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        emp = Employee.query.filter_by(employee_code=e_code).first()
        assert emp is None, (
            "An inactive employee with no prior local record should " "not be created."
        )

    def test_full_sync_reactivates_previously_deactivated_employee(
        self, app, db_session, mock_neogov_client
    ):
        """
        Pre-create an inactive employee.  Sync with ``is_active=True``.
        Assert the record is reactivated and fields are updated.
        """
        from app.services import hr_sync_service

        d_code, v_code, p_code, pos = self._create_full_hierarchy(db_session)
        e_code = _next_code("EMP")

        emp = Employee(
            employee_code=e_code,
            first_name="Returning",
            last_name="Worker",
            email=f"{e_code.lower()}@townofclaytonnc.org",
            position_id=pos.id,
            is_active=False,
        )
        db_session.add(emp)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            self._make_api_payload(
                d_code,
                v_code,
                p_code,
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Returning",
                        "last_name": "Worker",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": True,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(emp)
        assert emp.is_active is True


# =====================================================================
# 5. User provisioning tests
# =====================================================================


class TestFullSyncProvisionsUsers:
    """
    Verify that ``run_full_sync`` auto-provisions ``auth.user`` records
    for new employees with valid tenant-domain email addresses.
    """

    def _setup_hierarchy_with_employee(self, db_session, email_suffix=""):
        """
        Helper: create a full org hierarchy and one active employee.

        Args:
            db_session:    Database session.
            email_suffix:  Optional suffix appended before the domain.

        Returns:
            Tuple of (dept_code, div_code, pos_code, emp_code,
            division_instance, employee_instance).
        """
        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")
        e_code = _next_code("EMP")

        dept = Department(
            department_code=d_code,
            department_name=f"Prov Dept {d_code}",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name=f"Prov Div {v_code}",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title=f"Prov Pos {p_code}",
            division_id=div.id,
            authorized_count=3,
        )
        db_session.add(pos)
        db_session.flush()

        email = f"{e_code.lower()}{email_suffix}@townofclaytonnc.org"
        emp = Employee(
            employee_code=e_code,
            first_name="Provision",
            last_name="Test",
            email=email,
            position_id=pos.id,
            is_active=True,
        )
        db_session.add(emp)
        db_session.commit()

        return d_code, v_code, p_code, e_code, div, emp

    def test_full_sync_provisions_users_for_new_employees(
        self, app, db_session, mock_neogov_client
    ):
        """
        After syncing employees, the service should create a User
        record for each new employee with a valid tenant email.
        The user should get the ``read_only`` role and a
        division-level scope.
        """
        from app.services import hr_sync_service

        d_code, v_code, p_code, e_code, div, emp = self._setup_hierarchy_with_employee(
            db_session
        )

        # The sync must include the hierarchy so the entity sync
        # phase does not modify our pre-created records unexpectedly.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "PD"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "PDiv",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "PP",
                        "division_code": v_code,
                        "authorized_count": 3,
                    }
                ],
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Provision",
                        "last_name": "Test",
                        "email": emp.email,
                        "position_code": p_code,
                        "is_active": True,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        # Assert: a User record was created for the employee.
        user = User.query.filter_by(employee_id=emp.id).first()
        assert (
            user is not None
        ), f"No User record was provisioned for employee {e_code}."
        assert user.email == emp.email
        assert user.first_name == "Provision"
        assert user.last_name == "Test"
        assert user.is_active is True

        # Assert: the user has the read_only role.
        assert user.role_name == "read_only"

        # Assert: the user has a division-level scope matching the
        # employee's position's division.
        assert len(user.scopes) >= 1
        div_scopes = [s for s in user.scopes if s.scope_type == "division"]
        assert len(div_scopes) >= 1
        assert div_scopes[0].division_id == div.id

    def test_full_sync_skips_non_tenant_email(
        self, app, db_session, mock_neogov_client
    ):
        """
        Employees with non-tenant email addresses (e.g., personal
        Gmail) should NOT receive a User record.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")
        e_code = _next_code("EMP")

        dept = Department(
            department_code=d_code,
            department_name="Non-Tenant Dept",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name="Non-Tenant Div",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title="Non-Tenant Pos",
            division_id=div.id,
            authorized_count=1,
        )
        db_session.add(pos)
        db_session.flush()

        # Employee with a personal Gmail address.
        emp = Employee(
            employee_code=e_code,
            first_name="Personal",
            last_name="Email",
            email=f"{e_code.lower()}@gmail.com",
            position_id=pos.id,
            is_active=True,
        )
        db_session.add(emp)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "NT D"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "NT Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "NT P",
                        "division_code": v_code,
                        "authorized_count": 1,
                    }
                ],
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Personal",
                        "last_name": "Email",
                        "email": f"{e_code.lower()}@gmail.com",
                        "position_code": p_code,
                        "is_active": True,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        user = User.query.filter_by(employee_id=emp.id).first()
        assert user is None, "A non-tenant email should not get a User record."

    def test_full_sync_skips_employee_with_no_email(
        self, app, db_session, mock_neogov_client
    ):
        """
        Employees without an email address cannot authenticate, so
        user provisioning should be skipped for them.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")
        e_code = _next_code("EMP")

        dept = Department(department_code=d_code, department_name="No Email Dept")
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name="No Email Div",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title="No Email Pos",
            division_id=div.id,
            authorized_count=1,
        )
        db_session.add(pos)
        db_session.flush()

        # Employee with no email.
        emp = Employee(
            employee_code=e_code,
            first_name="No",
            last_name="Email",
            email=None,
            position_id=pos.id,
            is_active=True,
        )
        db_session.add(emp)
        db_session.commit()

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "NE D"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "NE Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "NE Pos",
                        "division_code": v_code,
                        "authorized_count": 1,
                    }
                ],
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "No",
                        "last_name": "Email",
                        "email": None,
                        "position_code": p_code,
                        "is_active": True,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        user = User.query.filter_by(employee_id=emp.id).first()
        assert user is None


class TestFullSyncDeactivatesUsers:
    """
    Verify that user accounts linked to deactivated employees are
    themselves deactivated during sync.
    """

    def test_full_sync_deactivates_users_for_removed_employees(
        self, app, db_session, mock_neogov_client, roles
    ):
        """
        Pre-create an employee linked to a User.  Sync with
        ``is_active=False`` for that employee.  Assert the linked
        User is deactivated.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")
        e_code = _next_code("EMP")

        dept = Department(
            department_code=d_code,
            department_name="Deact User Dept",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name="Deact User Div",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title="Deact User Pos",
            division_id=div.id,
            authorized_count=1,
        )
        db_session.add(pos)
        db_session.flush()

        emp = Employee(
            employee_code=e_code,
            first_name="Departing",
            last_name="UserEmp",
            email=f"{e_code.lower()}@townofclaytonnc.org",
            position_id=pos.id,
            is_active=True,
        )
        db_session.add(emp)
        db_session.flush()

        # Create a linked User.
        user = User(
            email=emp.email,
            first_name=emp.first_name,
            last_name=emp.last_name,
            role_id=roles["read_only"].id,
            employee_id=emp.id,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        # Sync with the employee marked as inactive.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "DU D"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "DU Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "DU Pos",
                        "division_code": v_code,
                        "authorized_count": 1,
                    }
                ],
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Departing",
                        "last_name": "UserEmp",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": False,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        # The employee should now be inactive.
        db_session.refresh(emp)
        assert emp.is_active is False

        # The linked user should also be deactivated.
        db_session.refresh(user)
        assert user.is_active is False


# =====================================================================
# 6. Sync log tests
# =====================================================================


class TestFullSyncRecordsSyncLog:
    """
    Verify that ``run_full_sync`` creates and completes an
    ``HRSyncLog`` record with accurate statistics.
    """

    def test_full_sync_records_sync_log(self, app, db_session, mock_neogov_client):
        """
        After a successful sync, assert an HRSyncLog record was
        created with status ``completed`` and correct statistics.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {"department_code": d_code, "department_name": "Log Dept"}
                ],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "Log Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "Log Pos",
                        "division_code": v_code,
                        "authorized_count": 2,
                    }
                ],
            )
        )

        sync_log = hr_sync_service.run_full_sync()

        # Assert: the returned object is an HRSyncLog.
        assert isinstance(sync_log, HRSyncLog)
        assert sync_log.status == "completed"
        assert sync_log.sync_type == "full"

        # Assert: statistics are populated.
        assert sync_log.records_processed >= 3  # 1 dept + 1 div + 1 pos.
        assert sync_log.records_created >= 3
        assert sync_log.records_errors == 0

        # Assert: timestamps are populated.
        assert sync_log.started_at is not None
        assert sync_log.completed_at is not None
        assert sync_log.completed_at >= sync_log.started_at

    def test_full_sync_records_sync_log_with_triggering_user(
        self, app, db_session, mock_neogov_client, admin_user
    ):
        """
        When ``user_id`` is provided, the sync log should record
        who triggered the sync.
        """
        from app.services import hr_sync_service

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data()
        )

        sync_log = hr_sync_service.run_full_sync(user_id=admin_user.id)

        assert sync_log.triggered_by == admin_user.id

    def test_full_sync_creates_audit_entry(self, app, db_session, mock_neogov_client):
        """
        A SYNC audit log entry should be created after a successful
        sync, recording the aggregate statistics.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": d_code,
                        "department_name": "Audit Entry Dept",
                    }
                ],
            )
        )

        sync_log = hr_sync_service.run_full_sync()

        # Find the SYNC audit entry for this sync log.
        entry = AuditLog.query.filter_by(
            action_type="SYNC",
            entity_type="org.hr_sync",
            entity_id=sync_log.id,
        ).first()

        assert (
            entry is not None
        ), "No SYNC audit log entry found for the completed sync."


# =====================================================================
# 7. Error handling tests
# =====================================================================


class TestFullSyncErrorHandling:
    """
    Verify that ``run_full_sync`` handles failures gracefully:
    no unhandled exceptions, sync log marked as ``failed``.
    """

    def test_full_sync_handles_api_failure_gracefully(
        self, app, db_session, mock_neogov_client
    ):
        """
        Make the mock raise a ConnectionError.  Assert that the
        sync log is created with status ``failed`` and the error
        message is recorded.  The exception must NOT propagate
        to the caller.
        """
        from app.services import hr_sync_service

        mock_neogov_client.return_value.fetch_all_organization_data.side_effect = (
            ConnectionError("NeoGov API is unreachable")
        )

        # Act: this should NOT raise.
        sync_log = hr_sync_service.run_full_sync()

        # Assert: the sync log exists with failed status.
        assert isinstance(sync_log, HRSyncLog)
        assert sync_log.status == "failed"
        assert sync_log.error_message is not None
        assert "unreachable" in sync_log.error_message.lower()

    def test_full_sync_handles_generic_exception(
        self, app, db_session, mock_neogov_client
    ):
        """
        Make the mock raise a generic RuntimeError.  Assert
        graceful handling and failed sync log.
        """
        from app.services import hr_sync_service

        mock_neogov_client.return_value.fetch_all_organization_data.side_effect = (
            RuntimeError("Unexpected internal error")
        )

        sync_log = hr_sync_service.run_full_sync()

        assert sync_log.status == "failed"
        assert "Unexpected internal error" in sync_log.error_message

    def test_full_sync_rollback_does_not_persist_partial_data(
        self, app, db_session, mock_neogov_client
    ):
        """
        If the API call succeeds but an exception occurs during
        processing, the partial database changes should be rolled
        back.  We simulate this by having the mock return valid
        department data but then raising during division processing.

        We patch ``_sync_divisions`` to raise after departments
        have been synced.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[
                    {
                        "department_code": d_code,
                        "department_name": "Rollback Test Dept",
                    }
                ],
            )
        )

        # Patch _sync_divisions to raise after _sync_departments succeeds.
        with patch(
            "app.services.hr_sync_service._sync_divisions",
            side_effect=RuntimeError("Division sync exploded"),
        ):
            sync_log = hr_sync_service.run_full_sync()

        assert sync_log.status == "failed"

        # The department should NOT have been persisted because the
        # transaction was rolled back.
        dept = Department.query.filter_by(department_code=d_code).first()
        assert dept is None, "Partial data should not persist after a rollback."


# =====================================================================
# 8. Filled count recalculation tests
# =====================================================================


class TestFullSyncRecalculatesFilledCounts:
    """
    Verify that ``run_full_sync`` recalculates
    ``Position.filled_count`` based on active employee counts.
    """

    def test_full_sync_recalculates_filled_counts(
        self, app, db_session, mock_neogov_client
    ):
        """
        Create a position with two active employees.  After sync,
        the position's ``filled_count`` should be updated to 2.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")

        dept = Department(
            department_code=d_code,
            department_name="Filled Count Dept",
        )
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name="Filled Count Div",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title="Filled Count Pos",
            division_id=div.id,
            authorized_count=5,
            filled_count=0,
        )
        db_session.add(pos)
        db_session.flush()

        # Create two active employees.
        e1_code = _next_code("EMP")
        e2_code = _next_code("EMP")
        for ec in [e1_code, e2_code]:
            emp = Employee(
                employee_code=ec,
                first_name="Filled",
                last_name="Counter",
                email=f"{ec.lower()}@townofclaytonnc.org",
                position_id=pos.id,
                is_active=True,
            )
            db_session.add(emp)
        db_session.commit()

        assert pos.filled_count == 0  # Starts at zero.

        # Sync with the hierarchy (employees already exist locally).
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "FC D"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "FC Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "FC Pos",
                        "division_code": v_code,
                        "authorized_count": 5,
                    }
                ],
                employees=[
                    {
                        "employee_id": e1_code,
                        "first_name": "Filled",
                        "last_name": "Counter",
                        "email": f"{e1_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": True,
                    },
                    {
                        "employee_id": e2_code,
                        "first_name": "Filled",
                        "last_name": "Counter",
                        "email": f"{e2_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": True,
                    },
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(pos)
        assert pos.filled_count == 2

    def test_full_sync_resets_filled_count_to_zero_when_all_deactivated(
        self, app, db_session, mock_neogov_client
    ):
        """
        If all employees in a position are deactivated, the
        filled_count should be reset to 0.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")
        e_code = _next_code("EMP")

        dept = Department(department_code=d_code, department_name="Zero Fill Dept")
        db_session.add(dept)
        db_session.flush()

        div = Division(
            division_code=v_code,
            division_name="Zero Fill Div",
            department_id=dept.id,
        )
        db_session.add(div)
        db_session.flush()

        pos = Position(
            position_code=p_code,
            position_title="Zero Fill Pos",
            division_id=div.id,
            authorized_count=1,
            filled_count=1,  # Starts at 1 (stale).
        )
        db_session.add(pos)
        db_session.flush()

        # Create one employee who will be deactivated.
        emp = Employee(
            employee_code=e_code,
            first_name="Last",
            last_name="Standing",
            email=f"{e_code.lower()}@townofclaytonnc.org",
            position_id=pos.id,
            is_active=True,
        )
        db_session.add(emp)
        db_session.commit()

        # Sync with the employee marked as inactive.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "ZF D"}],
                divisions=[
                    {
                        "division_code": v_code,
                        "division_name": "ZF Div",
                        "department_code": d_code,
                    }
                ],
                positions=[
                    {
                        "position_code": p_code,
                        "position_title": "ZF Pos",
                        "division_code": v_code,
                        "authorized_count": 1,
                    }
                ],
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Last",
                        "last_name": "Standing",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": p_code,
                        "is_active": False,
                    }
                ],
            )
        )

        hr_sync_service.run_full_sync()

        db_session.refresh(pos)
        assert pos.filled_count == 0


# =====================================================================
# 9. Idempotency tests
# =====================================================================


class TestFullSyncIdempotency:
    """
    Verify that running the sync twice with the same data does not
    create duplicate records.
    """

    def test_full_sync_is_idempotent(self, app, db_session, mock_neogov_client):
        """
        Run the sync twice with identical API data.  Assert that
        the second run does not create duplicate Department,
        Division, Position, or Employee records.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        v_code = _next_code("DIV")
        p_code = _next_code("POS")
        e_code = _next_code("EMP")

        api_data = _build_api_data(
            departments=[{"department_code": d_code, "department_name": "Idemp Dept"}],
            divisions=[
                {
                    "division_code": v_code,
                    "division_name": "Idemp Div",
                    "department_code": d_code,
                }
            ],
            positions=[
                {
                    "position_code": p_code,
                    "position_title": "Idemp Pos",
                    "division_code": v_code,
                    "authorized_count": 3,
                }
            ],
            employees=[
                {
                    "employee_id": e_code,
                    "first_name": "Idemp",
                    "last_name": "Test",
                    "email": f"{e_code.lower()}@townofclaytonnc.org",
                    "position_code": p_code,
                    "is_active": True,
                }
            ],
        )

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            api_data
        )

        # First sync.
        sync_log_1 = hr_sync_service.run_full_sync()
        assert sync_log_1.status == "completed"

        # Capture counts after first sync.
        dept_count_1 = Department.query.filter_by(department_code=d_code).count()
        div_count_1 = Division.query.filter_by(division_code=v_code).count()
        pos_count_1 = Position.query.filter_by(position_code=p_code).count()
        emp_count_1 = Employee.query.filter_by(employee_code=e_code).count()

        assert dept_count_1 == 1
        assert div_count_1 == 1
        assert pos_count_1 == 1
        assert emp_count_1 == 1

        # Second sync with the same data.
        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            api_data
        )
        sync_log_2 = hr_sync_service.run_full_sync()
        assert sync_log_2.status == "completed"

        # Counts should remain the same.
        assert Department.query.filter_by(department_code=d_code).count() == 1
        assert Division.query.filter_by(division_code=v_code).count() == 1
        assert Position.query.filter_by(position_code=p_code).count() == 1
        assert Employee.query.filter_by(employee_code=e_code).count() == 1

        # Second sync should report 0 created (all entities
        # already exist).
        assert sync_log_2.records_created == 0


# =====================================================================
# 10. get_sync_logs pagination test
# =====================================================================


class TestGetSyncLogs:
    """Verify the ``get_sync_logs`` pagination helper."""

    def test_get_sync_logs_returns_paginated_results(
        self, app, db_session, mock_neogov_client
    ):
        """
        Run two syncs, then call ``get_sync_logs``.  Assert the
        results contain at least two entries and are ordered by
        started_at descending (most recent first).
        """
        from app.services import hr_sync_service

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data()
        )

        # Run two syncs.
        hr_sync_service.run_full_sync()
        hr_sync_service.run_full_sync()

        logs = hr_sync_service.get_sync_logs(page=1, per_page=10)

        assert logs.total >= 2
        items = logs.items
        assert len(items) >= 2

        # Most recent first.
        assert items[0].started_at >= items[1].started_at


# =====================================================================
# 11. Edge case: employee with missing position for active employee
# =====================================================================


class TestEmployeeMissingPositionEdgeCase:
    """
    Verify that the sync handles an active employee whose
    ``position_code`` does not match any known position.
    """

    def test_active_employee_with_unknown_position_is_skipped(
        self, app, db_session, mock_neogov_client
    ):
        """
        An active employee referencing a non-existent position code
        should be logged as an error and skipped.
        """
        from app.services import hr_sync_service

        d_code = _next_code("DEPT")
        e_code = _next_code("EMP")
        bogus_pos = _next_code("POS_BOGUS")

        mock_neogov_client.return_value.fetch_all_organization_data.return_value = (
            _build_api_data(
                departments=[{"department_code": d_code, "department_name": "EP D"}],
                employees=[
                    {
                        "employee_id": e_code,
                        "first_name": "Orphan",
                        "last_name": "Employee",
                        "email": f"{e_code.lower()}@townofclaytonnc.org",
                        "position_code": bogus_pos,
                        "is_active": True,
                    }
                ],
            )
        )

        sync_log = hr_sync_service.run_full_sync()

        # Employee should NOT have been created.
        emp = Employee.query.filter_by(employee_code=e_code).first()
        assert emp is None

        # Sync should still complete.
        assert sync_log.status == "completed"
        assert sync_log.records_errors >= 1
