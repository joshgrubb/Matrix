"""
Unit tests for the organization service layer.

Tests every public function in ``app.services.organization_service``
against the real SQL Server test database.  Verifies scope-filtered
queries for departments, divisions, positions, and employees, plus
the authorization helper functions and aggregate headcount queries.

This file complements ``test_scope_isolation.py``, which tests scope
enforcement at the **route** level.  These tests operate at the
**service** level, calling functions directly with different user
objects to verify that the underlying queries produce the correct
result sets.  If a query has a subtle scope-filtering bug that a
route test misses (because the route adds its own redirect logic),
these tests will catch it.

Design decisions:
    - All tests call service functions directly (not via HTTP routes)
      to isolate query behavior from route-layer error handling.
    - The ``sample_org`` conftest fixture provides the organizational
      hierarchy (2 depts, 4 divs, 6 positions with known
      authorized_count values).
    - The ``create_user`` factory fixture creates users with arbitrary
      role/scope combinations for targeted scope boundary tests.
    - Employee tests create ephemeral Employee records within each
      test to keep test data isolated and predictable.
    - ID sets are used for assertions (not list equality) because
      the test database may contain seed data from the DDL script,
      and the service returns results ordered by name, which could
      interleave seed and fixture records.

Fixture reminder (from conftest.py):
    admin_user:              org-wide scope
    manager_user:            scoped to div_a1 (within dept_a)
    manager_dept_scope_user: scoped to dept_a
    it_staff_user:           org-wide scope
    budget_user:             org-wide scope
    read_only_user:          scoped to div_a1

    sample_org keys:
        dept_a, dept_b, div_a1, div_a2, div_b1, div_b2,
        pos_a1_1 (auth=3), pos_a1_2 (auth=5), pos_a2_1 (auth=2),
        pos_b1_1 (auth=4), pos_b1_2 (auth=1), pos_b2_1 (auth=6)

    create_user(role_name, scopes, ...): factory for custom users

Run this file in isolation::

    pytest tests/test_services/test_organization_service.py -v
"""
import time as _time
import pytest

from app.extensions import db
from app.models.organization import Department, Division, Employee, Position
from app.services import organization_service


# =====================================================================
# Local helper: create an employee attached to a position
# =====================================================================

_emp_counter = int(_time.time() * 10) % 9000


def _create_employee(
    db_session, position, first_name="Emp", last_name="Test", is_active=True
):
    """
    Create and commit an Employee record for the given position.

    Uses a module-level counter to generate unique employee_code
    values that will not collide across tests.

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
        employee_code=f"_TST_EMP_{_emp_counter:04d}",
        first_name=first_name,
        last_name=last_name,
        email=f"_tst_emp_{_emp_counter:04d}@test.local",
        is_active=is_active,
    )
    db_session.add(emp)
    db_session.commit()
    return emp


# =====================================================================
# 1. get_departments -- scope filtering
# =====================================================================


class TestGetDepartments:
    """
    Verify that ``organization_service.get_departments()`` returns
    the correct departments based on the calling user's scope.
    """

    def test_org_scope_returns_all_fixture_departments(
        self, app, admin_user, sample_org
    ):
        """An org-wide user sees both test departments (and any seed data)."""
        depts = organization_service.get_departments(admin_user)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_a"].id in dept_ids
        assert sample_org["dept_b"].id in dept_ids

    def test_department_scope_returns_only_scoped_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a sees dept_a but not dept_b."""
        depts = organization_service.get_departments(manager_dept_scope_user)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_a"].id in dept_ids
        assert sample_org["dept_b"].id not in dept_ids

    def test_division_scope_returns_parent_department(
        self, app, manager_user, sample_org
    ):
        """
        A user scoped to div_a1 should see the parent department
        (dept_a) in the department list, because the service resolves
        division scopes to their parent department IDs.
        """
        depts = organization_service.get_departments(manager_user)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_a"].id in dept_ids
        assert sample_org["dept_b"].id not in dept_ids

    def test_division_scope_in_dept_b_does_not_see_dept_a(
        self, app, create_user, sample_org
    ):
        """
        A user scoped to div_b1 should see dept_b but not dept_a.
        This is the mirror of the test above, confirming the
        filtering works in both directions.
        """
        user = create_user(
            role_name="manager",
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                }
            ],
        )
        depts = organization_service.get_departments(user)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_b"].id in dept_ids
        assert sample_org["dept_a"].id not in dept_ids

    def test_results_are_ordered_by_name(self, app, admin_user, sample_org):
        """
        Departments should be returned in alphabetical order by name.

        SQL Server sorts using a case-insensitive collation
        (e.g., SQL_Latin1_General_CP1_CI_AS), so the Python
        comparison must also be case-insensitive to match.
        """
        depts = organization_service.get_departments(admin_user)
        names = [d.department_name for d in depts]
        assert names == sorted(names, key=str.casefold)

    def test_inactive_departments_excluded_by_default(
        self, app, db_session, admin_user, sample_org
    ):
        """
        Soft-deleted departments should not appear in the default
        result set.  Deactivate dept_b and verify it disappears.
        """
        sample_org["dept_b"].is_active = False
        db_session.commit()

        depts = organization_service.get_departments(admin_user)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_b"].id not in dept_ids

        # Restore for other tests.
        sample_org["dept_b"].is_active = True
        db_session.commit()

    def test_include_inactive_shows_deactivated_departments(
        self, app, db_session, admin_user, sample_org
    ):
        """
        When ``include_inactive=True``, soft-deleted departments
        should appear in the results.
        """
        sample_org["dept_b"].is_active = False
        db_session.commit()

        depts = organization_service.get_departments(admin_user, include_inactive=True)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_b"].id in dept_ids

        # Restore.
        sample_org["dept_b"].is_active = True
        db_session.commit()


# =====================================================================
# 2. get_divisions -- scope filtering and department filter
# =====================================================================


class TestGetDivisions:
    """
    Verify that ``organization_service.get_divisions()`` returns
    the correct divisions based on user scope and optional filters.
    """

    def test_org_scope_returns_all_fixture_divisions(self, app, admin_user, sample_org):
        """An org-wide user sees all four test divisions."""
        divs = organization_service.get_divisions(admin_user)
        div_ids = {d.id for d in divs}
        for key in ("div_a1", "div_a2", "div_b1", "div_b2"):
            assert sample_org[key].id in div_ids

    def test_department_scope_returns_divisions_in_scoped_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a sees div_a1 and div_a2 but not div_b*."""
        divs = organization_service.get_divisions(manager_dept_scope_user)
        div_ids = {d.id for d in divs}
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_a2"].id in div_ids
        assert sample_org["div_b1"].id not in div_ids
        assert sample_org["div_b2"].id not in div_ids

    def test_division_scope_returns_only_scoped_division(
        self, app, manager_user, sample_org
    ):
        """
        A user scoped to div_a1 sees only div_a1, not sibling
        div_a2 or any div_b* divisions.
        """
        divs = organization_service.get_divisions(manager_user)
        div_ids = {d.id for d in divs}
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_a2"].id not in div_ids
        assert sample_org["div_b1"].id not in div_ids

    def test_department_id_filter_restricts_to_one_department(
        self, app, admin_user, sample_org
    ):
        """
        The ``department_id`` parameter should restrict results to
        divisions within that department, even for an org-wide user.
        """
        divs = organization_service.get_divisions(
            admin_user, department_id=sample_org["dept_a"].id
        )
        div_ids = {d.id for d in divs}
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_a2"].id in div_ids
        assert sample_org["div_b1"].id not in div_ids
        assert sample_org["div_b2"].id not in div_ids

    def test_department_id_filter_combined_with_scope(
        self, app, manager_user, sample_org
    ):
        """
        When a division-scoped user passes ``department_id=dept_a``,
        they should only see divisions within that department that
        also match their scope (div_a1, not div_a2).
        """
        divs = organization_service.get_divisions(
            manager_user, department_id=sample_org["dept_a"].id
        )
        div_ids = {d.id for d in divs}
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_a2"].id not in div_ids

    def test_results_are_ordered_by_name(self, app, admin_user, sample_org):
        """
        Divisions should be returned in alphabetical order by name.

        SQL Server sorts using a case-insensitive collation, so the
        Python comparison must also be case-insensitive to match.
        """
        divs = organization_service.get_divisions(admin_user)
        names = [d.division_name for d in divs]
        assert names == sorted(names, key=str.casefold)

    def test_inactive_divisions_excluded_by_default(
        self, app, db_session, admin_user, sample_org
    ):
        """Soft-deleted divisions should not appear by default."""
        sample_org["div_b2"].is_active = False
        db_session.commit()

        divs = organization_service.get_divisions(admin_user)
        div_ids = {d.id for d in divs}
        assert sample_org["div_b2"].id not in div_ids

        sample_org["div_b2"].is_active = True
        db_session.commit()

    def test_include_inactive_shows_deactivated_divisions(
        self, app, db_session, admin_user, sample_org
    ):
        """``include_inactive=True`` should include soft-deleted divisions."""
        sample_org["div_b2"].is_active = False
        db_session.commit()

        divs = organization_service.get_divisions(admin_user, include_inactive=True)
        div_ids = {d.id for d in divs}
        assert sample_org["div_b2"].id in div_ids

        sample_org["div_b2"].is_active = True
        db_session.commit()


# =====================================================================
# 3. get_divisions_for_department (no scope filtering)
# =====================================================================


class TestGetDivisionsForDepartment:
    """
    Verify the unscoped ``get_divisions_for_department()`` helper
    used by HTMX dropdown endpoints.
    """

    def test_returns_all_active_divisions_in_department(self, app, sample_org):
        """All active divisions in dept_a should be returned."""
        divs = organization_service.get_divisions_for_department(
            sample_org["dept_a"].id
        )
        div_ids = {d.id for d in divs}
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_a2"].id in div_ids
        # No dept_b divisions.
        assert sample_org["div_b1"].id not in div_ids

    def test_does_not_return_divisions_from_other_department(self, app, sample_org):
        """Passing dept_b should return only dept_b divisions."""
        divs = organization_service.get_divisions_for_department(
            sample_org["dept_b"].id
        )
        div_ids = {d.id for d in divs}
        assert sample_org["div_b1"].id in div_ids
        assert sample_org["div_b2"].id in div_ids
        assert sample_org["div_a1"].id not in div_ids

    def test_excludes_inactive_divisions(self, app, db_session, sample_org):
        """Inactive divisions should not appear in the HTMX dropdown."""
        sample_org["div_a2"].is_active = False
        db_session.commit()

        divs = organization_service.get_divisions_for_department(
            sample_org["dept_a"].id
        )
        div_ids = {d.id for d in divs}
        assert sample_org["div_a2"].id not in div_ids

        sample_org["div_a2"].is_active = True
        db_session.commit()


# =====================================================================
# 4. get_positions -- scope filtering and entity filters
# =====================================================================


class TestGetPositions:
    """
    Verify that ``organization_service.get_positions()`` returns
    the correct positions based on user scope and optional filters.
    """

    def test_org_scope_returns_all_fixture_positions(self, app, admin_user, sample_org):
        """An org-wide user sees all six test positions."""
        positions = organization_service.get_positions(admin_user)
        pos_ids = {p.id for p in positions}
        for key in (
            "pos_a1_1",
            "pos_a1_2",
            "pos_a2_1",
            "pos_b1_1",
            "pos_b1_2",
            "pos_b2_1",
        ):
            assert sample_org[key].id in pos_ids

    def test_division_scope_returns_only_scoped_positions(
        self, app, manager_user, sample_org
    ):
        """
        A user scoped to div_a1 sees pos_a1_1 and pos_a1_2 but
        no positions from div_a2, div_b1, or div_b2.
        """
        positions = organization_service.get_positions(manager_user)
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        assert sample_org["pos_a2_1"].id not in pos_ids
        assert sample_org["pos_b1_1"].id not in pos_ids

    def test_department_scope_returns_all_positions_in_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """
        A user scoped to dept_a sees all three dept_a positions
        (across div_a1 and div_a2) but no dept_b positions.
        """
        positions = organization_service.get_positions(manager_dept_scope_user)
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        assert sample_org["pos_a2_1"].id in pos_ids
        assert sample_org["pos_b1_1"].id not in pos_ids
        assert sample_org["pos_b2_1"].id not in pos_ids

    def test_division_id_filter_restricts_results(self, app, admin_user, sample_org):
        """
        The ``division_id`` parameter should restrict results to
        positions within that division, even for an org-wide user.
        """
        positions = organization_service.get_positions(
            admin_user, division_id=sample_org["div_a1"].id
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        assert sample_org["pos_a2_1"].id not in pos_ids
        assert sample_org["pos_b1_1"].id not in pos_ids

    def test_department_id_filter_restricts_results(self, app, admin_user, sample_org):
        """
        The ``department_id`` parameter should restrict results to
        positions within that department (across all its divisions).
        """
        positions = organization_service.get_positions(
            admin_user, department_id=sample_org["dept_b"].id
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_b1_1"].id in pos_ids
        assert sample_org["pos_b1_2"].id in pos_ids
        assert sample_org["pos_b2_1"].id in pos_ids
        assert sample_org["pos_a1_1"].id not in pos_ids
        assert sample_org["pos_a2_1"].id not in pos_ids

    def test_department_id_filter_combined_with_division_scope(
        self, app, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1, filtering by dept_a, should
        see only div_a1 positions (scope further restricts the
        department filter).
        """
        positions = organization_service.get_positions(
            manager_user, department_id=sample_org["dept_a"].id
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        # div_a2 is in dept_a but outside the user's scope.
        assert sample_org["pos_a2_1"].id not in pos_ids

    def test_results_are_ordered_by_title(self, app, admin_user, sample_org):
        """
        Positions should be returned in alphabetical order by title.

        SQL Server sorts using a case-insensitive collation
        (e.g., SQL_Latin1_General_CP1_CI_AS), so the Python
        comparison must also be case-insensitive to match.
        """
        positions = organization_service.get_positions(admin_user)
        titles = [p.position_title for p in positions]
        assert titles == sorted(titles, key=str.casefold)

    def test_inactive_positions_excluded_by_default(
        self, app, db_session, admin_user, sample_org
    ):
        """Soft-deleted positions should not appear by default."""
        sample_org["pos_b2_1"].is_active = False
        db_session.commit()

        positions = organization_service.get_positions(admin_user)
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_b2_1"].id not in pos_ids

        sample_org["pos_b2_1"].is_active = True
        db_session.commit()

    def test_include_inactive_shows_deactivated_positions(
        self, app, db_session, admin_user, sample_org
    ):
        """``include_inactive=True`` should include soft-deleted positions."""
        sample_org["pos_b2_1"].is_active = False
        db_session.commit()

        positions = organization_service.get_positions(
            admin_user, include_inactive=True
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_b2_1"].id in pos_ids

        sample_org["pos_b2_1"].is_active = True
        db_session.commit()


# =====================================================================
# 5. get_positions_for_division (no scope filtering)
# =====================================================================


class TestGetPositionsForDivision:
    """
    Verify the unscoped ``get_positions_for_division()`` helper
    used by HTMX dropdown endpoints.
    """

    def test_returns_all_active_positions_in_division(self, app, sample_org):
        """All active positions in div_a1 should be returned."""
        positions = organization_service.get_positions_for_division(
            sample_org["div_a1"].id
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        assert len(pos_ids) == 2

    def test_does_not_return_positions_from_other_division(self, app, sample_org):
        """Passing div_b1 should return only div_b1 positions."""
        positions = organization_service.get_positions_for_division(
            sample_org["div_b1"].id
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_b1_1"].id in pos_ids
        assert sample_org["pos_b1_2"].id in pos_ids
        assert sample_org["pos_a1_1"].id not in pos_ids

    def test_excludes_inactive_positions(self, app, db_session, sample_org):
        """Inactive positions should not appear in the HTMX dropdown."""
        sample_org["pos_a1_2"].is_active = False
        db_session.commit()

        positions = organization_service.get_positions_for_division(
            sample_org["div_a1"].id
        )
        pos_ids = {p.id for p in positions}
        assert sample_org["pos_a1_2"].id not in pos_ids

        sample_org["pos_a1_2"].is_active = True
        db_session.commit()


# =====================================================================
# 6. user_can_access_position -- authorization helper
# =====================================================================


class TestUserCanAccessPosition:
    """
    Verify the ``user_can_access_position()`` authorization helper.

    Note: ``test_scope_isolation.py`` already covers these paths
    at the route level.  These tests isolate the service function
    to confirm the raw boolean logic without route-layer redirects.
    """

    def test_org_scope_can_access_any_position(self, app, admin_user, sample_org):
        """Organization-wide scope grants access to every position."""
        for key in ("pos_a1_1", "pos_a2_1", "pos_b1_1", "pos_b2_1"):
            assert organization_service.user_can_access_position(
                admin_user, sample_org[key].id
            ), f"Admin should access {key}"

    def test_division_scope_can_access_own_positions(
        self, app, manager_user, sample_org
    ):
        """A user scoped to div_a1 can access both div_a1 positions."""
        assert organization_service.user_can_access_position(
            manager_user, sample_org["pos_a1_1"].id
        )
        assert organization_service.user_can_access_position(
            manager_user, sample_org["pos_a1_2"].id
        )

    def test_division_scope_cannot_access_sibling_division(
        self, app, manager_user, sample_org
    ):
        """
        div_a2 is in the same department but NOT in the user's
        scope.  Access must be denied.
        """
        assert not organization_service.user_can_access_position(
            manager_user, sample_org["pos_a2_1"].id
        )

    def test_division_scope_cannot_access_other_department(
        self, app, manager_user, sample_org
    ):
        """div_b1 is in a completely different department."""
        assert not organization_service.user_can_access_position(
            manager_user, sample_org["pos_b1_1"].id
        )

    def test_department_scope_can_access_all_positions_in_dept(
        self, app, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a can access all dept_a positions."""
        for key in ("pos_a1_1", "pos_a1_2", "pos_a2_1"):
            assert organization_service.user_can_access_position(
                manager_dept_scope_user, sample_org[key].id
            ), f"Dept-scoped user should access {key}"

    def test_department_scope_cannot_access_other_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a cannot access dept_b positions."""
        for key in ("pos_b1_1", "pos_b1_2", "pos_b2_1"):
            assert not organization_service.user_can_access_position(
                manager_dept_scope_user, sample_org[key].id
            ), f"Dept-a user should NOT access {key}"

    def test_nonexistent_position_returns_false(self, app, manager_user):
        """
        A nonexistent position ID returns False for scoped users.
        (Org-scope users return True before the lookup, by design.)
        """
        assert not organization_service.user_can_access_position(manager_user, 999999)

    def test_org_scope_returns_true_for_nonexistent_position(self, app, admin_user):
        """
        An org-wide user returns True even for a nonexistent ID
        because the scope check short-circuits before lookup.
        """
        assert organization_service.user_can_access_position(admin_user, 999999)


# =====================================================================
# 7. user_can_access_department -- authorization helper
# =====================================================================


class TestUserCanAccessDepartment:
    """
    Verify the ``user_can_access_department()`` authorization helper.
    """

    def test_org_scope_can_access_any_department(self, app, admin_user, sample_org):
        """Organization-wide scope grants access to every department."""
        assert organization_service.user_can_access_department(
            admin_user, sample_org["dept_a"].id
        )
        assert organization_service.user_can_access_department(
            admin_user, sample_org["dept_b"].id
        )

    def test_department_scope_can_access_own_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a can access dept_a."""
        assert organization_service.user_can_access_department(
            manager_dept_scope_user, sample_org["dept_a"].id
        )

    def test_department_scope_cannot_access_other_department(
        self, app, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a cannot access dept_b."""
        assert not organization_service.user_can_access_department(
            manager_dept_scope_user, sample_org["dept_b"].id
        )

    def test_division_scope_can_access_parent_department(
        self, app, manager_user, sample_org
    ):
        """
        A user with division-level scope can access the parent
        department of that division (dept_a contains div_a1).
        """
        assert organization_service.user_can_access_department(
            manager_user, sample_org["dept_a"].id
        )

    def test_division_scope_cannot_access_unrelated_department(
        self, app, manager_user, sample_org
    ):
        """A user scoped to div_a1 cannot access dept_b."""
        assert not organization_service.user_can_access_department(
            manager_user, sample_org["dept_b"].id
        )


# =====================================================================
# 8. get_employees -- scope filtering and entity filters
# =====================================================================


class TestGetEmployees:
    """
    Verify that ``organization_service.get_employees()`` returns
    the correct employees based on user scope and optional filters.
    """

    def test_org_scope_returns_all_employees_in_fixture_positions(
        self, app, db_session, admin_user, sample_org
    ):
        """
        An org-wide user sees employees in both departments.
        """
        emp_a = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Alice",
            last_name="Able",
        )
        emp_b = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="Bob",
            last_name="Baker",
        )

        employees = organization_service.get_employees(admin_user)
        emp_ids = {e.id for e in employees}
        assert emp_a.id in emp_ids
        assert emp_b.id in emp_ids

    def test_division_scope_returns_only_scoped_employees(
        self, app, db_session, manager_user, sample_org
    ):
        """
        A user scoped to div_a1 sees employees in div_a1 positions
        but not employees in div_a2 or dept_b positions.
        """
        emp_in_scope = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="InScope",
            last_name="Employee",
        )
        emp_out_scope = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="OutScope",
            last_name="Employee",
        )
        emp_sibling = _create_employee(
            db_session,
            sample_org["pos_a2_1"],
            first_name="Sibling",
            last_name="Employee",
        )

        employees = organization_service.get_employees(manager_user)
        emp_ids = {e.id for e in employees}
        assert emp_in_scope.id in emp_ids
        assert emp_out_scope.id not in emp_ids
        assert emp_sibling.id not in emp_ids

    def test_department_scope_returns_employees_across_divisions(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """
        A user scoped to dept_a sees employees in both div_a1 and
        div_a2 but not in dept_b.
        """
        emp_div_a1 = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="DivA1",
            last_name="Emp",
        )
        emp_div_a2 = _create_employee(
            db_session,
            sample_org["pos_a2_1"],
            first_name="DivA2",
            last_name="Emp",
        )
        emp_dept_b = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="DeptB",
            last_name="Emp",
        )

        employees = organization_service.get_employees(manager_dept_scope_user)
        emp_ids = {e.id for e in employees}
        assert emp_div_a1.id in emp_ids
        assert emp_div_a2.id in emp_ids
        assert emp_dept_b.id not in emp_ids

    def test_department_id_filter_restricts_results(
        self, app, db_session, admin_user, sample_org
    ):
        """The ``department_id`` filter limits to employees in that department."""
        emp_a = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="DeptAEmp",
            last_name="Filter",
        )
        emp_b = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="DeptBEmp",
            last_name="Filter",
        )

        employees = organization_service.get_employees(
            admin_user, department_id=sample_org["dept_a"].id
        )
        emp_ids = {e.id for e in employees}
        assert emp_a.id in emp_ids
        assert emp_b.id not in emp_ids

    def test_division_id_filter_restricts_results(
        self, app, db_session, admin_user, sample_org
    ):
        """The ``division_id`` filter limits to employees in that division."""
        emp_a1 = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="DivA1Emp",
            last_name="Filter",
        )
        emp_a2 = _create_employee(
            db_session,
            sample_org["pos_a2_1"],
            first_name="DivA2Emp",
            last_name="Filter",
        )

        employees = organization_service.get_employees(
            admin_user, division_id=sample_org["div_a1"].id
        )
        emp_ids = {e.id for e in employees}
        assert emp_a1.id in emp_ids
        assert emp_a2.id not in emp_ids

    def test_position_id_filter_restricts_results(
        self, app, db_session, admin_user, sample_org
    ):
        """The ``position_id`` filter limits to employees in that position."""
        emp_target = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Target",
            last_name="Emp",
        )
        emp_other = _create_employee(
            db_session,
            sample_org["pos_a1_2"],
            first_name="Other",
            last_name="Emp",
        )

        employees = organization_service.get_employees(
            admin_user, position_id=sample_org["pos_a1_1"].id
        )
        emp_ids = {e.id for e in employees}
        assert emp_target.id in emp_ids
        assert emp_other.id not in emp_ids

    def test_inactive_employees_excluded_by_default(
        self, app, db_session, admin_user, sample_org
    ):
        """Deactivated employees should not appear in the default results."""
        active_emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Active",
            last_name="Emp",
        )
        inactive_emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Inactive",
            last_name="Emp",
            is_active=False,
        )

        employees = organization_service.get_employees(admin_user)
        emp_ids = {e.id for e in employees}
        assert active_emp.id in emp_ids
        assert inactive_emp.id not in emp_ids

    def test_include_inactive_shows_deactivated_employees(
        self, app, db_session, admin_user, sample_org
    ):
        """``include_inactive=True`` should include deactivated employees."""
        inactive_emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="InactiveShown",
            last_name="Emp",
            is_active=False,
        )

        employees = organization_service.get_employees(
            admin_user, include_inactive=True
        )
        emp_ids = {e.id for e in employees}
        assert inactive_emp.id in emp_ids

    def test_results_are_ordered_by_last_name_first_name(
        self, app, db_session, admin_user, sample_org
    ):
        """Employees should be returned sorted by last name, then first name."""
        _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Zara",
            last_name="Adams",
        )
        _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Alex",
            last_name="Adams",
        )
        _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Beth",
            last_name="Brown",
        )

        employees = organization_service.get_employees(
            admin_user,
            position_id=sample_org["pos_a1_1"].id,
        )
        # Filter to just our test employees by their known last names.
        test_emps = [e for e in employees if e.last_name in ("Adams", "Brown")]
        names = [(e.last_name, e.first_name) for e in test_emps]
        assert names == sorted(names, key=lambda t: (t[0].casefold(), t[1].casefold()))


# =====================================================================
# 9. Lookup-by-ID helpers
# =====================================================================


class TestLookupById:
    """
    Verify the ``get_*_by_id()`` helper functions return the
    correct record or None.
    """

    def test_get_department_by_id_returns_record(self, app, sample_org):
        """A valid department ID returns the correct Department."""
        dept = organization_service.get_department_by_id(sample_org["dept_a"].id)
        assert dept is not None
        assert dept.id == sample_org["dept_a"].id
        assert dept.department_name == sample_org["dept_a"].department_name

    def test_get_department_by_id_returns_none_for_invalid_id(self, app):
        """A nonexistent department ID returns None."""
        assert organization_service.get_department_by_id(999999) is None

    def test_get_division_by_id_returns_record(self, app, sample_org):
        """A valid division ID returns the correct Division."""
        div = organization_service.get_division_by_id(sample_org["div_a1"].id)
        assert div is not None
        assert div.id == sample_org["div_a1"].id

    def test_get_division_by_id_returns_none_for_invalid_id(self, app):
        """A nonexistent division ID returns None."""
        assert organization_service.get_division_by_id(999999) is None

    def test_get_position_by_id_returns_record(self, app, sample_org):
        """A valid position ID returns the correct Position."""
        pos = organization_service.get_position_by_id(sample_org["pos_a1_1"].id)
        assert pos is not None
        assert pos.id == sample_org["pos_a1_1"].id
        assert pos.authorized_count == 3

    def test_get_position_by_id_returns_none_for_invalid_id(self, app):
        """A nonexistent position ID returns None."""
        assert organization_service.get_position_by_id(999999) is None

    def test_get_employee_by_id_returns_record(self, app, db_session, sample_org):
        """A valid employee ID returns the correct Employee."""
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Lookup",
            last_name="Test",
        )
        result = organization_service.get_employee_by_id(emp.id)
        assert result is not None
        assert result.id == emp.id
        assert result.first_name == "Lookup"

    def test_get_employee_by_id_returns_none_for_invalid_id(self, app):
        """A nonexistent employee ID returns None."""
        assert organization_service.get_employee_by_id(999999) is None


# =====================================================================
# 10. Aggregate helpers -- authorized count and filled count
# =====================================================================


class TestAggregateHelpers:
    """
    Verify ``get_total_authorized_count()`` and ``get_filled_count()``
    which power headcount displays on organization pages.
    """

    def test_authorized_count_for_division(self, app, sample_org):
        """
        div_a1 has pos_a1_1 (auth=3) and pos_a1_2 (auth=5).
        Total authorized = 8.
        """
        count = organization_service.get_total_authorized_count(
            division_id=sample_org["div_a1"].id
        )
        assert count == 8

    def test_authorized_count_for_department(self, app, sample_org):
        """
        dept_a has pos_a1_1 (3) + pos_a1_2 (5) + pos_a2_1 (2) = 10.
        """
        count = organization_service.get_total_authorized_count(
            department_id=sample_org["dept_a"].id
        )
        assert count == 10

    def test_authorized_count_for_other_department(self, app, sample_org):
        """
        dept_b has pos_b1_1 (4) + pos_b1_2 (1) + pos_b2_1 (6) = 11.
        """
        count = organization_service.get_total_authorized_count(
            department_id=sample_org["dept_b"].id
        )
        assert count == 11

    def test_authorized_count_all_positions(self, app, sample_org):
        """
        With no filter, the total should include at least the 21
        from our 6 fixture positions (may be higher with seed data).
        """
        count = organization_service.get_total_authorized_count()
        assert count >= 21

    def test_filled_count_for_division_with_employees(
        self, app, db_session, sample_org
    ):
        """
        Create 2 active employees in div_a1 positions, then verify
        the filled count is 2.
        """
        _create_employee(db_session, sample_org["pos_a1_1"])
        _create_employee(db_session, sample_org["pos_a1_2"])

        count = organization_service.get_filled_count(
            division_id=sample_org["div_a1"].id
        )
        assert count == 2

    def test_filled_count_excludes_inactive_employees(
        self, app, db_session, sample_org
    ):
        """Inactive employees should not be counted."""
        _create_employee(db_session, sample_org["pos_a1_1"])
        _create_employee(db_session, sample_org["pos_a1_1"], is_active=False)

        count = organization_service.get_filled_count(
            division_id=sample_org["div_a1"].id
        )
        assert count == 1

    def test_filled_count_for_department(self, app, db_session, sample_org):
        """
        Create employees across div_a1 and div_a2, then verify
        the department-level count includes both.
        """
        _create_employee(db_session, sample_org["pos_a1_1"])
        _create_employee(db_session, sample_org["pos_a2_1"])

        count = organization_service.get_filled_count(
            department_id=sample_org["dept_a"].id
        )
        assert count == 2

    def test_filled_count_empty_division_returns_zero(self, app, sample_org):
        """A division with no employees should return 0."""
        count = organization_service.get_filled_count(
            division_id=sample_org["div_b2"].id
        )
        assert count == 0


# =====================================================================
# 11. Multi-scope users (edge case)
# =====================================================================


class TestMultiScopeUser:
    """
    Verify behavior for users who hold scopes across multiple
    non-contiguous organizational units (e.g., div_a1 + div_b1).
    """

    def test_multi_division_scope_sees_positions_in_both_divisions(
        self, app, create_user, sample_org
    ):
        """
        A user with division scopes in div_a1 and div_b1 should
        see positions from both but not from div_a2 or div_b2.
        """
        user = create_user(
            role_name="manager",
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
            ],
        )

        positions = organization_service.get_positions(user)
        pos_ids = {p.id for p in positions}

        # In scope.
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        assert sample_org["pos_b1_1"].id in pos_ids
        assert sample_org["pos_b1_2"].id in pos_ids

        # Out of scope.
        assert sample_org["pos_a2_1"].id not in pos_ids
        assert sample_org["pos_b2_1"].id not in pos_ids

    def test_multi_division_scope_sees_both_parent_departments(
        self, app, create_user, sample_org
    ):
        """
        A user with div_a1 + div_b1 scopes should see both dept_a
        and dept_b in the department list (each division resolves
        to its parent department).
        """
        user = create_user(
            role_name="manager",
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
            ],
        )

        depts = organization_service.get_departments(user)
        dept_ids = {d.id for d in depts}
        assert sample_org["dept_a"].id in dept_ids
        assert sample_org["dept_b"].id in dept_ids

    def test_mixed_department_and_division_scope(self, app, create_user, sample_org):
        """
        A user with dept_a (department scope) + div_b1 (division
        scope) should see all dept_a positions plus div_b1 positions,
        but not div_b2 positions.
        """
        user = create_user(
            role_name="manager",
            scopes=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
            ],
        )

        positions = organization_service.get_positions(user)
        pos_ids = {p.id for p in positions}

        # All of dept_a.
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids
        assert sample_org["pos_a2_1"].id in pos_ids

        # div_b1 only.
        assert sample_org["pos_b1_1"].id in pos_ids
        assert sample_org["pos_b1_2"].id in pos_ids

        # div_b2 is out of scope.
        assert sample_org["pos_b2_1"].id not in pos_ids
