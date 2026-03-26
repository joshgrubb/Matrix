"""
Integration tests for the organization blueprint routes.

Covers every route in ``app/blueprints/organization/routes.py`` with
real database operations against the SQL Server test instance.

The organization blueprint is read-only (all data comes from NeoGov
sync).  Routes serve department listings, department detail with
child divisions, division detail with child positions, flat list
views for all divisions/positions/employees, and HTMX dynamic
dropdown endpoints.

The HTMX endpoints (htmx_divisions, htmx_positions) are already at
100% coverage from the requirements wizard tests.  This file focuses
on the six page-rendering functions that are at 0% coverage:
    - departments()
    - department_detail()
    - division_detail()
    - all_divisions()
    - all_positions()
    - all_employees()

Sections:
    1.  Department Listing
    2.  Department Detail (with scope check)
    3.  Division Detail (with headcount statistics)
    4.  All Divisions (flat list with filters)
    5.  All Positions (flat list with filters)
    6.  All Employees (flat list with filters)
    7.  Scope Enforcement at the Route Level
    8.  Authentication Enforcement
    9.  Nonexistent Resource Handling
    10. HTMX Endpoint Smoke Tests

Fixture reminder (from conftest.py ``sample_org``):
    dept_a: "Test Department A"
    dept_b: "Test Department B"
    div_a1: "Test Division A-1"   (in dept_a)
    div_a2: "Test Division A-2"   (in dept_a)
    div_b1: "Test Division B-1"   (in dept_b)
    div_b2: "Test Division B-2"   (in dept_b)
    pos_a1_1: "Test Analyst A1-1"      authorized_count=3  (div_a1)
    pos_a1_2: "Test Specialist A1-2"   authorized_count=5  (div_a1)
    pos_a2_1: "Test Coordinator A2-1"  authorized_count=2  (div_a2)
    pos_b1_1: "Test Technician B1-1"   authorized_count=4  (div_b1)
    pos_b1_2: "Test Supervisor B1-2"   authorized_count=1  (div_b1)
    pos_b2_1: "Test Director B2-1"     authorized_count=6  (div_b2)

User fixtures (from conftest.py):
    admin_user:              org-wide scope
    manager_user:            scoped to div_a1 (within dept_a)
    manager_dept_scope_user: scoped to dept_a
    it_staff_user:           org-wide scope
    budget_user:             org-wide scope
    read_only_user:          scoped to div_a1

Run this file in isolation::

    pytest tests/test_routes/test_organization_routes.py -v
"""

import time as _time

import pytest

from app.models.organization import Employee


# =====================================================================
# Local helper: create an employee attached to a position.
# Mirrors the pattern from test_organization_service.py.
# =====================================================================

_emp_counter = int(_time.time() * 10) % 9000


def _create_employee(
    db_session,
    position,
    first_name="RouteEmp",
    last_name="Test",
    is_active=True,
):
    """
    Create and commit an Employee record for the given position.

    Uses a module-level counter to generate unique employee_code
    values prefixed with ``_TST_`` so the conftest cleanup fixture
    deletes them automatically.

    Args:
        db_session: The active database session.
        position:   A Position model instance.
        first_name: Employee first name.
        last_name:  Employee last name.
        is_active:  Whether the employee is active.

    Returns:
        The committed Employee instance.
    """
    global _emp_counter  # pylint: disable=global-statement
    _emp_counter += 1
    emp = Employee(
        position_id=position.id,
        employee_code=f"_TST_RTEMP_{_emp_counter:04d}",
        first_name=first_name,
        last_name=last_name,
        email=f"_tst_rtemp_{_emp_counter:04d}@test.local",
        is_active=is_active,
    )
    db_session.add(emp)
    db_session.commit()
    return emp


# =====================================================================
# 1. Department Listing
# =====================================================================


class TestDepartmentListing:
    """
    Verify the departments listing page loads and displays the
    correct data based on user scope and the inactive toggle.
    """

    def test_departments_page_loads_for_admin(
        self, auth_client, admin_user, sample_org
    ):
        """
        GET /org/departments as admin returns 200 and
        shows both departments from sample_org.
        """
        client = auth_client(admin_user)
        response = client.get("/org/departments")
        assert response.status_code == 200
        assert sample_org["dept_a"].department_name.encode() in response.data
        assert sample_org["dept_b"].department_name.encode() in response.data

    def test_departments_page_loads_for_manager(
        self, auth_client, manager_user, sample_org
    ):
        """
        GET /org/departments as a division-scoped manager
        returns 200 and shows only the parent department (dept_a).
        """
        client = auth_client(manager_user)
        response = client.get("/org/departments")
        assert response.status_code == 200
        # Manager scoped to div_a1 should see dept_a.
        assert sample_org["dept_a"].department_name.encode() in response.data
        # Manager should NOT see dept_b.
        assert sample_org["dept_b"].department_name.encode() not in response.data

    def test_departments_page_loads_for_it_staff(
        self, auth_client, it_staff_user, sample_org
    ):
        """IT staff with org scope should see all departments."""
        client = auth_client(it_staff_user)
        response = client.get("/org/departments")
        assert response.status_code == 200
        assert sample_org["dept_a"].department_name.encode() in response.data
        assert sample_org["dept_b"].department_name.encode() in response.data

    def test_departments_excludes_inactive_by_default(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        A deactivated department should not appear in the default
        listing (show_inactive is off by default).
        """
        sample_org["dept_b"].is_active = False
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get("/org/departments")
        assert response.status_code == 200
        assert sample_org["dept_b"].department_name.encode() not in response.data

        # Restore for other tests.
        sample_org["dept_b"].is_active = True
        db_session.commit()

    def test_departments_shows_inactive_when_requested(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/departments?show_inactive=1 should
        include deactivated departments.
        """
        sample_org["dept_b"].is_active = False
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get("/org/departments?show_inactive=1")
        assert response.status_code == 200
        assert sample_org["dept_b"].department_name.encode() in response.data

        sample_org["dept_b"].is_active = True
        db_session.commit()

    def test_departments_page_accessible_to_read_only(
        self, auth_client, read_only_user, sample_org
    ):
        """
        Read-only users should be able to view the departments page.
        They are scoped to div_a1, so they should see dept_a.
        """
        client = auth_client(read_only_user)
        response = client.get("/org/departments")
        assert response.status_code == 200
        assert sample_org["dept_a"].department_name.encode() in response.data

    def test_departments_page_accessible_to_budget_executive(
        self, auth_client, budget_user, sample_org
    ):
        """Budget executive with org scope sees all departments."""
        client = auth_client(budget_user)
        response = client.get("/org/departments")
        assert response.status_code == 200
        assert sample_org["dept_a"].department_name.encode() in response.data
        assert sample_org["dept_b"].department_name.encode() in response.data


# =====================================================================
# 2. Department Detail (with scope check)
# =====================================================================


class TestDepartmentDetail:
    """
    Verify the department detail page loads correctly, displays
    child divisions, and enforces scope-based access control.
    """

    def test_department_detail_shows_divisions(
        self, auth_client, admin_user, sample_org
    ):
        """
        GET /org/department/<id> for dept_a as admin
        returns 200 and displays its two child divisions.
        """
        client = auth_client(admin_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/department/{dept.id}")
        assert response.status_code == 200
        assert sample_org["div_a1"].division_name.encode() in response.data
        assert sample_org["div_a2"].division_name.encode() in response.data

    def test_department_detail_does_not_show_other_departments_divisions(
        self, auth_client, admin_user, sample_org
    ):
        """
        The detail page for dept_a should NOT show dept_b's divisions.
        """
        client = auth_client(admin_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/department/{dept.id}")
        assert response.status_code == 200
        assert sample_org["div_b1"].division_name.encode() not in response.data
        assert sample_org["div_b2"].division_name.encode() not in response.data

    def test_department_detail_scope_check_allows_access(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 should be allowed to view the
        detail page for dept_a (which is the parent department
        of their scoped division).
        """
        client = auth_client(manager_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/department/{dept.id}")
        assert response.status_code == 200

    def test_department_detail_scope_check_blocks_access(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 should receive 403 when trying
        to view the detail page for dept_b (outside their scope).
        """
        client = auth_client(manager_user)
        dept_b = sample_org["dept_b"]
        response = client.get(f"/org/department/{dept_b.id}")
        assert response.status_code == 403

    def test_department_detail_dept_scope_allows_own_department(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a should be allowed to view dept_a
        detail.
        """
        client = auth_client(manager_dept_scope_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/department/{dept.id}")
        assert response.status_code == 200

    def test_department_detail_dept_scope_blocks_other_department(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a should receive 403 when trying
        to view dept_b detail.
        """
        client = auth_client(manager_dept_scope_user)
        dept_b = sample_org["dept_b"]
        response = client.get(f"/org/department/{dept_b.id}")
        assert response.status_code == 403

    def test_department_detail_admin_can_access_any_department(
        self, auth_client, admin_user, sample_org
    ):
        """Admin with org scope should access any department detail."""
        client = auth_client(admin_user)
        for dept_key in ("dept_a", "dept_b"):
            dept = sample_org[dept_key]
            response = client.get(f"/org/department/{dept.id}")
            assert response.status_code == 200, (
                f"Admin should access {dept_key} but got " f"{response.status_code}"
            )

    def test_department_detail_scoped_manager_sees_only_scoped_divisions(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 viewing dept_a detail should
        see div_a1 but NOT div_a2 (which is in dept_a but outside
        the user's division scope).
        """
        client = auth_client(manager_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/department/{dept.id}")
        assert response.status_code == 200
        # The route calls get_divisions(current_user, department_id=...)
        # which applies scope filtering.  A div_a1-scoped user should
        # only see div_a1.
        assert sample_org["div_a1"].division_name.encode() in response.data
        assert sample_org["div_a2"].division_name.encode() not in response.data


# =====================================================================
# 3. Division Detail (with headcount statistics)
# =====================================================================


class TestDivisionDetail:
    """
    Verify the division detail page loads correctly, displays
    child positions, and computes headcount statistics.
    """

    def test_division_detail_shows_positions(self, auth_client, admin_user, sample_org):
        """
        GET /org/division/<id> for div_a1 returns 200
        and displays both positions (pos_a1_1 and pos_a1_2).
        """
        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/division/{div.id}")
        assert response.status_code == 200
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        assert sample_org["pos_a1_2"].position_title.encode() in response.data

    def test_division_detail_does_not_show_other_divisions_positions(
        self, auth_client, admin_user, sample_org
    ):
        """
        The detail page for div_a1 should NOT show positions
        belonging to div_a2 or any dept_b divisions.
        """
        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/division/{div.id}")
        assert response.status_code == 200
        assert sample_org["pos_a2_1"].position_title.encode() not in response.data
        assert sample_org["pos_b1_1"].position_title.encode() not in response.data

    def test_division_detail_shows_parent_department(
        self, auth_client, admin_user, sample_org
    ):
        """
        The division detail page should display the parent
        department name for context.
        """
        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/division/{div.id}")
        assert response.status_code == 200
        # The template receives the department object; verify its name
        # appears somewhere in the rendered page.
        assert sample_org["dept_a"].department_name.encode() in response.data

    def test_division_detail_computes_authorized_count(
        self, auth_client, admin_user, sample_org
    ):
        """
        The division detail page should display the total
        authorized headcount across its positions.

        div_a1 has pos_a1_1 (auth=3) + pos_a1_2 (auth=5) = 8 total.
        """
        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/division/{div.id}")
        assert response.status_code == 200
        # The route passes authorized_count=8 to the template.
        # Check that the number 8 appears in the rendered page.
        assert b"8" in response.data

    def test_division_detail_computes_filled_count(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        The division detail page should display the filled count.

        The filled_count is read from Position.filled_count which
        is set during HR sync.  Since the test fixture creates
        positions with the default filled_count (0), we update it
        manually to verify the page displays it.
        """
        pos = sample_org["pos_a1_1"]
        pos.filled_count = 2
        db_session.commit()

        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/division/{div.id}")
        assert response.status_code == 200
        assert b"2" in response.data

        # Restore.
        pos.filled_count = 0
        db_session.commit()

    def test_division_detail_accessible_to_any_role(
        self, auth_client, read_only_user, sample_org
    ):
        """
        Division detail uses only @login_required (no role_required),
        so any authenticated user should get 200.
        """
        client = auth_client(read_only_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/division/{div.id}")
        assert response.status_code == 200


# =====================================================================
# 4. All Divisions (flat list with filters)
# =====================================================================


class TestAllDivisions:
    """
    Verify the flat divisions listing page loads, respects scope
    filtering, and supports department and inactive filters.
    """

    def test_all_divisions_page_loads(self, auth_client, admin_user, sample_org):
        """
        GET /org/divisions as admin returns 200 and
        displays divisions from both departments.
        """
        client = auth_client(admin_user)
        response = client.get("/org/divisions")
        assert response.status_code == 200
        assert sample_org["div_a1"].division_name.encode() in response.data
        assert sample_org["div_b1"].division_name.encode() in response.data

    def test_all_divisions_respects_scope(self, auth_client, manager_user, sample_org):
        """
        A manager scoped to div_a1 should see only div_a1 in the
        flat listing, not sibling div_a2 or any dept_b divisions.
        """
        client = auth_client(manager_user)
        response = client.get("/org/divisions")
        assert response.status_code == 200
        assert sample_org["div_a1"].division_name.encode() in response.data
        assert sample_org["div_a2"].division_name.encode() not in response.data
        assert sample_org["div_b1"].division_name.encode() not in response.data

    def test_all_divisions_filters_by_department(
        self, auth_client, admin_user, sample_org
    ):
        """
        GET /org/divisions?department_id=<dept_a_id> should
        show only dept_a divisions.
        """
        client = auth_client(admin_user)
        dept_a = sample_org["dept_a"]
        response = client.get(f"/org/divisions?department_id={dept_a.id}")
        assert response.status_code == 200
        assert sample_org["div_a1"].division_name.encode() in response.data
        assert sample_org["div_a2"].division_name.encode() in response.data
        assert sample_org["div_b1"].division_name.encode() not in response.data

    def test_all_divisions_excludes_inactive_by_default(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """Deactivated divisions should not appear by default."""
        sample_org["div_a2"].is_active = False
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get("/org/divisions")
        assert response.status_code == 200
        assert sample_org["div_a2"].division_name.encode() not in response.data

        sample_org["div_a2"].is_active = True
        db_session.commit()

    def test_all_divisions_shows_inactive_when_requested(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/divisions?show_inactive=1 should include
        deactivated divisions.
        """
        sample_org["div_a2"].is_active = False
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get("/org/divisions?show_inactive=1")
        assert response.status_code == 200
        assert sample_org["div_a2"].division_name.encode() in response.data

        sample_org["div_a2"].is_active = True
        db_session.commit()


# =====================================================================
# 5. All Positions (flat list with filters)
# =====================================================================


class TestAllPositions:
    """
    Verify the flat positions listing page loads, respects scope
    filtering, and supports department/division/inactive filters.
    """

    def test_all_positions_page_loads(self, auth_client, admin_user, sample_org):
        """
        GET /org/positions as admin returns 200 and
        displays position titles from the sample_org fixture.
        """
        client = auth_client(admin_user)
        response = client.get("/org/positions")
        assert response.status_code == 200
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        assert sample_org["pos_b2_1"].position_title.encode() in response.data

    def test_all_positions_respects_scope(self, auth_client, manager_user, sample_org):
        """
        A manager scoped to div_a1 should see only div_a1 positions
        in the flat listing.
        """
        client = auth_client(manager_user)
        response = client.get("/org/positions")
        assert response.status_code == 200
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        assert sample_org["pos_a1_2"].position_title.encode() in response.data
        # Out of scope.
        assert sample_org["pos_a2_1"].position_title.encode() not in response.data
        assert sample_org["pos_b1_1"].position_title.encode() not in response.data

    def test_all_positions_dept_scope_sees_entire_department(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a should see all three dept_a
        positions (across both div_a1 and div_a2).
        """
        client = auth_client(manager_dept_scope_user)
        response = client.get("/org/positions")
        assert response.status_code == 200
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        assert sample_org["pos_a2_1"].position_title.encode() in response.data
        # dept_b should be excluded.
        assert sample_org["pos_b1_1"].position_title.encode() not in response.data

    def test_all_positions_filters_by_department(
        self, auth_client, admin_user, sample_org
    ):
        """
        GET /org/positions?department_id=<dept_b_id> should
        show only dept_b positions.
        """
        client = auth_client(admin_user)
        dept_b = sample_org["dept_b"]
        response = client.get(f"/org/positions?department_id={dept_b.id}")
        assert response.status_code == 200
        assert sample_org["pos_b1_1"].position_title.encode() in response.data
        assert sample_org["pos_b2_1"].position_title.encode() in response.data
        assert sample_org["pos_a1_1"].position_title.encode() not in response.data

    def test_all_positions_filters_by_division(
        self, auth_client, admin_user, sample_org
    ):
        """
        GET /org/positions?division_id=<div_a1_id> should
        show only div_a1 positions.
        """
        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/positions?division_id={div.id}")
        assert response.status_code == 200
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        assert sample_org["pos_a1_2"].position_title.encode() in response.data
        assert sample_org["pos_a2_1"].position_title.encode() not in response.data

    def test_all_positions_excludes_inactive_by_default(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """Deactivated positions should not appear by default."""
        sample_org["pos_b2_1"].is_active = False
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get("/org/positions")
        assert response.status_code == 200
        assert sample_org["pos_b2_1"].position_title.encode() not in response.data

        sample_org["pos_b2_1"].is_active = True
        db_session.commit()

    def test_all_positions_shows_inactive_when_requested(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/positions?show_inactive=1 should include
        deactivated positions.
        """
        sample_org["pos_b2_1"].is_active = False
        db_session.commit()

        client = auth_client(admin_user)
        response = client.get("/org/positions?show_inactive=1")
        assert response.status_code == 200
        assert sample_org["pos_b2_1"].position_title.encode() in response.data

        sample_org["pos_b2_1"].is_active = True
        db_session.commit()

    def test_all_positions_scope_combined_with_department_filter(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 filtering by dept_a should see
        only div_a1 positions (scope further restricts the filter).
        """
        client = auth_client(manager_user)
        dept_a = sample_org["dept_a"]
        response = client.get(f"/org/positions?department_id={dept_a.id}")
        assert response.status_code == 200
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        # div_a2 is in dept_a but outside scope.
        assert sample_org["pos_a2_1"].position_title.encode() not in response.data


# =====================================================================
# 6. All Employees (flat list with filters)
# =====================================================================


class TestAllEmployees:
    """
    Verify the flat employees listing page loads, respects scope
    filtering, and supports department/division/position/inactive
    filters.
    """

    def test_all_employees_page_loads(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/employees as admin returns 200 and
        displays employee names.
        """
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="EmpPageLoad",
            last_name="TestRoute",
        )

        client = auth_client(admin_user)
        response = client.get("/org/employees")
        assert response.status_code == 200
        assert b"EmpPageLoad" in response.data

    def test_all_employees_respects_scope(
        self, auth_client, manager_user, sample_org, db_session
    ):
        """
        A manager scoped to div_a1 should see employees in div_a1
        positions but not employees in div_b1 positions.
        """
        emp_in_scope = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="InScope",
            last_name="RouteEmp",
        )
        emp_out_scope = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="OutScope",
            last_name="RouteEmp",
        )

        client = auth_client(manager_user)
        response = client.get("/org/employees")
        assert response.status_code == 200
        assert b"InScope" in response.data
        assert b"OutScope" not in response.data

    def test_all_employees_excludes_inactive_by_default(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        Deactivated employees should not appear in the default
        listing (show_inactive is off by default).
        """
        active_emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="ActiveEmp",
            last_name="Visible",
        )
        inactive_emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="InactiveEmp",
            last_name="Hidden",
            is_active=False,
        )

        client = auth_client(admin_user)
        response = client.get("/org/employees")
        assert response.status_code == 200
        assert b"ActiveEmp" in response.data
        assert b"InactiveEmp" not in response.data

    def test_all_employees_shows_inactive_when_requested(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/employees?show_inactive=1 should include
        deactivated employees.
        """
        inactive_emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="InactiveShown",
            last_name="RouteEmp",
            is_active=False,
        )

        client = auth_client(admin_user)
        response = client.get("/org/employees?show_inactive=1")
        assert response.status_code == 200
        assert b"InactiveShown" in response.data

    def test_all_employees_filters_by_department(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/employees?department_id=<dept_a_id> should
        show only employees in dept_a positions.
        """
        emp_a = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="DeptAEmp",
            last_name="RouteFilter",
        )
        emp_b = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="DeptBEmp",
            last_name="RouteFilter",
        )

        client = auth_client(admin_user)
        dept_a = sample_org["dept_a"]
        response = client.get(f"/org/employees?department_id={dept_a.id}")
        assert response.status_code == 200
        assert b"DeptAEmp" in response.data
        assert b"DeptBEmp" not in response.data

    def test_all_employees_filters_by_division(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/employees?division_id=<div_a1_id> should
        show only employees in div_a1 positions.
        """
        emp_a1 = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="DivA1Emp",
            last_name="RouteFilter",
        )
        emp_a2 = _create_employee(
            db_session,
            sample_org["pos_a2_1"],
            first_name="DivA2Emp",
            last_name="RouteFilter",
        )

        client = auth_client(admin_user)
        div_a1 = sample_org["div_a1"]
        response = client.get(f"/org/employees?division_id={div_a1.id}")
        assert response.status_code == 200
        assert b"DivA1Emp" in response.data
        assert b"DivA2Emp" not in response.data

    def test_all_employees_filters_by_position(
        self, auth_client, admin_user, sample_org, db_session
    ):
        """
        GET /org/employees?position_id=<pos_a1_1_id> should
        show only employees in that specific position.
        """
        emp_target = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="TargetPos",
            last_name="RouteFilter",
        )
        emp_other = _create_employee(
            db_session,
            sample_org["pos_a1_2"],
            first_name="OtherPos",
            last_name="RouteFilter",
        )

        client = auth_client(admin_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/org/employees?position_id={pos.id}")
        assert response.status_code == 200
        assert b"TargetPos" in response.data
        assert b"OtherPos" not in response.data

    def test_all_employees_scope_combined_with_department_filter(
        self, auth_client, manager_user, sample_org, db_session
    ):
        """
        A manager scoped to div_a1 filtering by dept_a should see
        only employees in div_a1 positions, not div_a2 employees.
        """
        emp_div_a1 = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="ScopeA1",
            last_name="RouteFilter",
        )
        emp_div_a2 = _create_employee(
            db_session,
            sample_org["pos_a2_1"],
            first_name="ScopeA2",
            last_name="RouteFilter",
        )

        client = auth_client(manager_user)
        dept_a = sample_org["dept_a"]
        response = client.get(f"/org/employees?department_id={dept_a.id}")
        assert response.status_code == 200
        assert b"ScopeA1" in response.data
        assert b"ScopeA2" not in response.data


# =====================================================================
# 7. Scope Enforcement at the Route Level
# =====================================================================


class TestOrganizationScopeEnforcement:
    """
    Verify that scope enforcement works correctly at the route
    level for critical organization pages that apply scope checks.
    """

    def test_dept_detail_blocked_for_out_of_scope_division_user(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 trying to access dept_b detail
        should get 403. This is the "career-ending demo bug" test.
        """
        client = auth_client(manager_user)
        dept_b = sample_org["dept_b"]
        response = client.get(f"/org/department/{dept_b.id}")
        assert response.status_code == 403

    def test_dept_detail_blocked_for_out_of_scope_dept_user(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a trying to access dept_b detail
        should get 403.
        """
        client = auth_client(manager_dept_scope_user)
        dept_b = sample_org["dept_b"]
        response = client.get(f"/org/department/{dept_b.id}")
        assert response.status_code == 403

    def test_dept_detail_allowed_for_org_scope_user(
        self, auth_client, it_staff_user, sample_org
    ):
        """
        An IT staff user with org scope should access any department
        detail page without restriction.
        """
        client = auth_client(it_staff_user)
        for dept_key in ("dept_a", "dept_b"):
            dept = sample_org[dept_key]
            response = client.get(f"/org/department/{dept.id}")
            assert response.status_code == 200


# =====================================================================
# 8. Authentication Enforcement
# =====================================================================


class TestOrganizationAuthenticationEnforcement:
    """
    Verify that unauthenticated users are redirected away from
    all organization routes.
    """

    @pytest.mark.parametrize(
        "url_suffix",
        [
            "/org/departments",
            "/org/divisions",
            "/org/positions",
            "/org/employees",
        ],
        ids=[
            "departments",
            "divisions",
            "positions",
            "employees",
        ],
    )
    def test_unauthenticated_user_redirected_from_listing(self, client, url_suffix):
        """
        An unauthenticated GET to any organization listing route
        should return a redirect (302) to the login page.
        """
        response = client.get(url_suffix, follow_redirects=False)
        assert response.status_code in (302, 401), (
            f"Expected redirect for unauthenticated access to "
            f"{url_suffix}, got {response.status_code}"
        )

    def test_unauthenticated_user_redirected_from_department_detail(
        self, client, sample_org
    ):
        """
        An unauthenticated GET to a department detail page should
        redirect to login.
        """
        dept = sample_org["dept_a"]
        response = client.get(
            f"/org/department/{dept.id}",
            follow_redirects=False,
        )
        assert response.status_code in (302, 401)

    def test_unauthenticated_user_redirected_from_division_detail(
        self, client, sample_org
    ):
        """
        An unauthenticated GET to a division detail page should
        redirect to login.
        """
        div = sample_org["div_a1"]
        response = client.get(
            f"/org/division/{div.id}",
            follow_redirects=False,
        )
        assert response.status_code in (302, 401)


# =====================================================================
# 9. Nonexistent Resource Handling
# =====================================================================


class TestOrganizationNonexistentResources:
    """
    Verify that accessing nonexistent resource IDs results in
    graceful 404 responses, not 500 errors.
    """

    def test_department_detail_nonexistent_returns_404(self, auth_client, admin_user):
        """
        GET /org/department/999999 for a nonexistent
        department should return 404.
        """
        client = auth_client(admin_user)
        response = client.get("/org/department/999999")
        assert response.status_code == 404

    def test_division_detail_nonexistent_returns_404(self, auth_client, admin_user):
        """
        GET /org/division/999999 for a nonexistent
        division should return 404.
        """
        client = auth_client(admin_user)
        response = client.get("/org/division/999999")
        assert response.status_code == 404


# =====================================================================
# 10. HTMX Endpoint Smoke Tests
# =====================================================================


class TestHTMXEndpoints:
    """
    Smoke tests for the HTMX dynamic dropdown endpoints.

    These endpoints are already at 100% coverage from the
    requirements wizard tests, but we include basic smoke tests
    here for completeness and to verify they return HTML fragments
    (not full pages).
    """

    def test_htmx_divisions_returns_options(self, auth_client, admin_user, sample_org):
        """
        GET /org/htmx/divisions/<dept_id> returns 200
        and contains division option elements.
        """
        client = auth_client(admin_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/htmx/divisions/{dept.id}")
        assert response.status_code == 200
        # The endpoint returns HTML <option> fragments.
        assert b"option" in response.data.lower()
        # Should contain div_a1 and div_a2 for dept_a.
        assert sample_org["div_a1"].division_name.encode() in response.data

    def test_htmx_divisions_respects_scope(self, auth_client, manager_user, sample_org):
        """
        A manager scoped to div_a1 requesting HTMX divisions for
        dept_a should see only div_a1, not div_a2.
        """
        client = auth_client(manager_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/htmx/divisions/{dept.id}")
        assert response.status_code == 200
        assert sample_org["div_a1"].division_name.encode() in response.data
        assert sample_org["div_a2"].division_name.encode() not in response.data

    def test_htmx_positions_returns_options(self, auth_client, admin_user, sample_org):
        """
        GET /org/htmx/positions/<div_id> returns 200
        and contains position option elements.
        """
        client = auth_client(admin_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/htmx/positions/{div.id}")
        assert response.status_code == 200
        assert b"option" in response.data.lower()
        assert sample_org["pos_a1_1"].position_title.encode() in response.data

    def test_htmx_divisions_unauthenticated_redirects(self, client, sample_org):
        """Unauthenticated HTMX request should redirect to login."""
        dept = sample_org["dept_a"]
        response = client.get(
            f"/org/htmx/divisions/{dept.id}",
            follow_redirects=False,
        )
        assert response.status_code in (302, 401)

    def test_htmx_positions_unauthenticated_redirects(self, client, sample_org):
        """Unauthenticated HTMX request should redirect to login."""
        div = sample_org["div_a1"]
        response = client.get(
            f"/org/htmx/positions/{div.id}",
            follow_redirects=False,
        )
        assert response.status_code in (302, 401)
