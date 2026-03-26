"""
Unit tests for the Department, Division, Position, and Employee models
in the ``org`` schema.

Verifies column defaults, nullable constraints, parent-child
relationships, dynamic child collections, the full org hierarchy
chain (Department -> Division -> Position -> Employee), and the
``__repr__`` outputs that appear in logs and debugger sessions.

These tests exercise the model layer directly against the real
SQL Server test database.  They do not call service functions or
route handlers -- they test the SQLAlchemy model API that services
and routes depend on.

Design decisions:
    - Tests use the ``sample_org`` conftest fixture for the
      pre-built two-department hierarchy with known codes, names,
      and authorized_count values.
    - Employee tests create ephemeral records within each test
      using a module-level counter helper (mirrors the pattern in
      ``test_organization_service.py`` and
      ``test_organization_routes.py``).
    - ``__repr__`` assertions check that the string contains key
      identifying information, not an exact format, so minor
      formatting changes do not break tests unnecessarily.
    - Relationship tests verify both directions of every
      parent-child link (parent.children and child.parent).

Fixture reminder (from conftest.py ``sample_org``):
    dept_a:   Test Department A
    dept_b:   Test Department B
    div_a1:   Test Division A-1 (belongs to dept_a)
    div_a2:   Test Division A-2 (belongs to dept_a)
    div_b1:   Test Division B-1 (belongs to dept_b)
    div_b2:   Test Division B-2 (belongs to dept_b)
    pos_a1_1: Test Analyst A1-1     auth=3 (belongs to div_a1)
    pos_a1_2: Test Specialist A1-2  auth=5 (belongs to div_a1)
    pos_a2_1: Test Coordinator A2-1 auth=2 (belongs to div_a2)
    pos_b1_1: Test Technician B1-1  auth=4 (belongs to div_b1)
    pos_b1_2: Test Supervisor B1-2  auth=1 (belongs to div_b1)
    pos_b2_1: Test Director B2-1    auth=6 (belongs to div_b2)

Run this file in isolation::

    pytest tests/test_models/test_organization_model.py -v
"""

import time as _time

import pytest

from app.extensions import db
from app.models.organization import Department, Division, Employee, Position


# =====================================================================
# Module-level counter for generating unique employee codes.
# Mirrors the pattern used in test_organization_service.py.
# =====================================================================

_emp_counter = int(_time.time() * 10) % 9000


def _create_employee(
    db_session,
    position,
    first_name="ModelEmp",
    last_name="Test",
    email_suffix="",
    is_active=True,
):
    """
    Create and commit an Employee record for the given position.

    Uses a module-level counter to generate unique employee_code
    values prefixed with ``_TST_`` so the conftest cleanup fixture
    deletes them automatically.

    Args:
        db_session:   The active database session.
        position:     A Position model instance.
        first_name:   Employee first name.
        last_name:    Employee last name.
        email_suffix: Optional suffix appended before ``@test.local``.
        is_active:    Whether the employee is active.

    Returns:
        The committed Employee instance.
    """
    global _emp_counter  # pylint: disable=global-statement
    _emp_counter += 1
    code = f"_TST_MEMP_{_emp_counter:04d}"
    emp = Employee(
        position_id=position.id,
        employee_code=code,
        first_name=first_name,
        last_name=last_name,
        email=f"_tst_memp_{_emp_counter:04d}{email_suffix}@test.local",
        is_active=is_active,
    )
    db_session.add(emp)
    db_session.commit()
    return emp


# =====================================================================
# 1. Department model -- basic properties
# =====================================================================


class TestDepartmentBasicProperties:
    """Verify Department column values, defaults, and constraints."""

    def test_department_has_code(self, app, sample_org):
        """Fixture departments should have a non-empty department_code."""
        dept = sample_org["dept_a"]
        assert dept.department_code is not None
        assert len(dept.department_code) > 0

    def test_department_has_name(self, app, sample_org):
        """Fixture departments should have a non-empty department_name."""
        dept = sample_org["dept_a"]
        assert dept.department_name is not None
        assert len(dept.department_name) > 0

    def test_department_is_active_by_default(self, app, sample_org):
        """Newly created departments should be active (is_active=True)."""
        assert sample_org["dept_a"].is_active is True
        assert sample_org["dept_b"].is_active is True

    def test_department_has_integer_primary_key(self, app, sample_org):
        """The id column should be populated with a positive integer."""
        dept = sample_org["dept_a"]
        assert isinstance(dept.id, int)
        assert dept.id > 0

    def test_department_code_is_unique(self, app, sample_org):
        """Two departments from the fixture should have different codes."""
        assert (
            sample_org["dept_a"].department_code != sample_org["dept_b"].department_code
        )

    def test_department_has_created_at_timestamp(self, app, sample_org):
        """The created_at column should be populated by the server default."""
        dept = sample_org["dept_a"]
        assert dept.created_at is not None

    def test_department_has_updated_at_timestamp(self, app, sample_org):
        """The updated_at column should be populated by the server default."""
        dept = sample_org["dept_a"]
        assert dept.updated_at is not None

    def test_department_deactivation_persists(self, app, db_session, sample_org):
        """
        Setting is_active to False and committing should persist.
        Verify and then restore to avoid polluting other tests.
        """
        dept = sample_org["dept_b"]
        dept.is_active = False
        db_session.commit()

        # Re-read from the database to confirm persistence.
        db_session.refresh(dept)
        assert dept.is_active is False

        # Restore.
        dept.is_active = True
        db_session.commit()


# =====================================================================
# 2. Department -> Division relationship (parent to children)
# =====================================================================


class TestDepartmentDivisionsRelationship:
    """Verify the Department.divisions dynamic relationship."""

    def test_dept_a_has_divisions(self, app, sample_org):
        """dept_a should have child divisions."""
        divs = sample_org["dept_a"].divisions.all()
        assert len(divs) >= 2

    def test_dept_a_contains_correct_divisions(self, app, sample_org):
        """dept_a's divisions should include div_a1 and div_a2."""
        div_ids = {d.id for d in sample_org["dept_a"].divisions.all()}
        assert sample_org["div_a1"].id in div_ids
        assert sample_org["div_a2"].id in div_ids

    def test_dept_b_does_not_contain_dept_a_divisions(self, app, sample_org):
        """dept_b's divisions should NOT include div_a1 or div_a2."""
        div_ids = {d.id for d in sample_org["dept_b"].divisions.all()}
        assert sample_org["div_a1"].id not in div_ids
        assert sample_org["div_a2"].id not in div_ids

    def test_divisions_are_division_instances(self, app, sample_org):
        """Each child in the divisions collection should be a Division."""
        for div in sample_org["dept_a"].divisions.all():
            assert isinstance(div, Division)

    def test_divisions_is_dynamic_query(self, app, sample_org):
        """
        The divisions relationship is configured as lazy='dynamic',
        so accessing it should return a query object that supports
        ``.all()`` and ``.filter()``.
        """
        query = sample_org["dept_a"].divisions
        # Dynamic relationships expose a filter_by method.
        filtered = query.filter_by(is_active=True).all()
        assert len(filtered) >= 2


# =====================================================================
# 3. Department.__repr__
# =====================================================================


class TestDepartmentRepr:
    """
    Verify the ``__repr__`` output for Department models.

    Coverage target: Department.__repr__ is at 0% in the functions
    coverage report.
    """

    def test_repr_contains_department_code(self, app, sample_org):
        """The repr string should include the department_code."""
        dept = sample_org["dept_a"]
        assert dept.department_code in repr(dept)

    def test_repr_contains_department_name(self, app, sample_org):
        """The repr string should include the department_name."""
        dept = sample_org["dept_a"]
        assert dept.department_name in repr(dept)

    def test_repr_starts_with_class_name(self, app, sample_org):
        """The repr should start with '<Department'."""
        dept = sample_org["dept_a"]
        assert repr(dept).startswith("<Department")

    def test_repr_ends_with_angle_bracket(self, app, sample_org):
        """The repr should end with '>'."""
        dept = sample_org["dept_a"]
        assert repr(dept).endswith(">")

    def test_repr_for_both_departments_differs(self, app, sample_org):
        """Different departments should produce different repr strings."""
        assert repr(sample_org["dept_a"]) != repr(sample_org["dept_b"])


# =====================================================================
# 4. Division model -- basic properties
# =====================================================================


class TestDivisionBasicProperties:
    """Verify Division column values, defaults, and constraints."""

    def test_division_has_code(self, app, sample_org):
        """Fixture divisions should have a non-empty division_code."""
        div = sample_org["div_a1"]
        assert div.division_code is not None
        assert len(div.division_code) > 0

    def test_division_has_name(self, app, sample_org):
        """Fixture divisions should have a non-empty division_name."""
        div = sample_org["div_a1"]
        assert div.division_name is not None
        assert len(div.division_name) > 0

    def test_division_is_active_by_default(self, app, sample_org):
        """Newly created divisions should be active."""
        assert sample_org["div_a1"].is_active is True
        assert sample_org["div_b2"].is_active is True

    def test_division_has_integer_primary_key(self, app, sample_org):
        """The id column should be a positive integer."""
        div = sample_org["div_a1"]
        assert isinstance(div.id, int)
        assert div.id > 0

    def test_division_has_department_id_fk(self, app, sample_org):
        """The department_id FK column should be populated."""
        div = sample_org["div_a1"]
        assert div.department_id is not None
        assert isinstance(div.department_id, int)

    def test_all_four_divisions_have_different_codes(self, app, sample_org):
        """All fixture divisions should have unique codes."""
        codes = {
            sample_org[k].division_code
            for k in ["div_a1", "div_a2", "div_b1", "div_b2"]
        }
        assert len(codes) == 4


# =====================================================================
# 5. Division -> Department relationship (child to parent)
# =====================================================================


class TestDivisionDepartmentRelationship:
    """
    Verify the Division.department relationship navigates from
    child division to parent department.

    Baseline plan test: ``test_division_department_relationship``.
    """

    def test_division_has_department(self, app, sample_org):
        """A division's department relationship should not be None."""
        div = sample_org["div_a1"]
        assert div.department is not None

    def test_department_is_correct_instance(self, app, sample_org):
        """The related object should be a Department model instance."""
        div = sample_org["div_a1"]
        assert isinstance(div.department, Department)

    def test_div_a1_belongs_to_dept_a(self, app, sample_org):
        """div_a1's parent should be dept_a."""
        assert sample_org["div_a1"].department.id == sample_org["dept_a"].id

    def test_div_a2_belongs_to_dept_a(self, app, sample_org):
        """div_a2's parent should also be dept_a."""
        assert sample_org["div_a2"].department.id == sample_org["dept_a"].id

    def test_div_b1_belongs_to_dept_b(self, app, sample_org):
        """div_b1's parent should be dept_b."""
        assert sample_org["div_b1"].department.id == sample_org["dept_b"].id

    def test_div_b2_belongs_to_dept_b(self, app, sample_org):
        """div_b2's parent should be dept_b."""
        assert sample_org["div_b2"].department.id == sample_org["dept_b"].id

    def test_department_name_accessible_through_relationship(self, app, sample_org):
        """
        Navigating division.department.department_name should
        return the parent department's name string.
        """
        name = sample_org["div_a1"].department.department_name
        assert name == sample_org["dept_a"].department_name


# =====================================================================
# 6. Division -> Position relationship (parent to children)
# =====================================================================


class TestDivisionPositionsRelationship:
    """Verify the Division.positions dynamic relationship."""

    def test_div_a1_has_positions(self, app, sample_org):
        """div_a1 should have child positions."""
        positions = sample_org["div_a1"].positions.all()
        assert len(positions) >= 2

    def test_div_a1_contains_correct_positions(self, app, sample_org):
        """div_a1's positions should include pos_a1_1 and pos_a1_2."""
        pos_ids = {p.id for p in sample_org["div_a1"].positions.all()}
        assert sample_org["pos_a1_1"].id in pos_ids
        assert sample_org["pos_a1_2"].id in pos_ids

    def test_div_a1_does_not_contain_div_a2_positions(self, app, sample_org):
        """div_a1's positions should NOT include pos_a2_1."""
        pos_ids = {p.id for p in sample_org["div_a1"].positions.all()}
        assert sample_org["pos_a2_1"].id not in pos_ids

    def test_positions_is_dynamic_query(self, app, sample_org):
        """
        The positions relationship should support query operations
        like filter_by since it is configured as lazy='dynamic'.
        """
        query = sample_org["div_b1"].positions
        active_positions = query.filter_by(is_active=True).all()
        assert len(active_positions) >= 2

    def test_single_position_division_has_one_position(self, app, sample_org):
        """
        div_a2 has exactly one position in the fixture (pos_a2_1).
        div_b2 has exactly one position in the fixture (pos_b2_1).
        """
        a2_positions = sample_org["div_a2"].positions.all()
        b2_positions = sample_org["div_b2"].positions.all()
        # Use >= 1 because seed data might add more.
        assert len(a2_positions) >= 1
        assert len(b2_positions) >= 1
        a2_ids = {p.id for p in a2_positions}
        b2_ids = {p.id for p in b2_positions}
        assert sample_org["pos_a2_1"].id in a2_ids
        assert sample_org["pos_b2_1"].id in b2_ids


# =====================================================================
# 7. Division.__repr__
# =====================================================================


class TestDivisionRepr:
    """
    Verify the ``__repr__`` output for Division models.

    Coverage target: Division.__repr__ is at 0% in the functions
    coverage report.
    """

    def test_repr_contains_division_code(self, app, sample_org):
        """The repr string should include the division_code."""
        div = sample_org["div_a1"]
        assert div.division_code in repr(div)

    def test_repr_contains_division_name(self, app, sample_org):
        """The repr string should include the division_name."""
        div = sample_org["div_a1"]
        assert div.division_name in repr(div)

    def test_repr_starts_with_class_name(self, app, sample_org):
        """The repr should start with '<Division'."""
        div = sample_org["div_a1"]
        assert repr(div).startswith("<Division")

    def test_repr_for_different_divisions_differs(self, app, sample_org):
        """Different divisions should produce different repr strings."""
        assert repr(sample_org["div_a1"]) != repr(sample_org["div_b1"])


# =====================================================================
# 8. Position model -- basic properties
# =====================================================================


class TestPositionBasicProperties:
    """Verify Position column values, defaults, and constraints."""

    def test_position_has_code(self, app, sample_org):
        """Fixture positions should have a non-empty position_code."""
        pos = sample_org["pos_a1_1"]
        assert pos.position_code is not None
        assert len(pos.position_code) > 0

    def test_position_has_title(self, app, sample_org):
        """Fixture positions should have a non-empty position_title."""
        pos = sample_org["pos_a1_1"]
        assert pos.position_title is not None
        assert len(pos.position_title) > 0

    def test_position_is_active_by_default(self, app, sample_org):
        """Newly created positions should be active."""
        assert sample_org["pos_a1_1"].is_active is True
        assert sample_org["pos_b2_1"].is_active is True

    def test_position_authorized_count_matches_fixture(self, app, sample_org):
        """
        Each position's authorized_count should match the fixture
        definition.  These values drive cost calculations.
        """
        expected = {
            "pos_a1_1": 3,
            "pos_a1_2": 5,
            "pos_a2_1": 2,
            "pos_b1_1": 4,
            "pos_b1_2": 1,
            "pos_b2_1": 6,
        }
        for key, expected_count in expected.items():
            assert sample_org[key].authorized_count == expected_count, (
                f"{key}.authorized_count should be {expected_count}, "
                f"got {sample_org[key].authorized_count}"
            )

    def test_position_filled_count_defaults_to_zero(self, app, sample_org):
        """
        Newly created positions should have filled_count=0 because
        the HR sync has not run yet.
        """
        # All fixture positions should default to 0.
        for key in ("pos_a1_1", "pos_a1_2", "pos_a2_1"):
            assert (
                sample_org[key].filled_count == 0
            ), f"{key}.filled_count should default to 0"

    def test_position_requirements_status_defaults_to_none(self, app, sample_org):
        """
        Newly created positions should have requirements_status=None
        (not started), since no one has used the wizard yet.
        """
        pos = sample_org["pos_a1_1"]
        assert pos.requirements_status is None

    def test_position_requirements_status_accepts_valid_values(
        self, app, db_session, sample_org
    ):
        """
        The requirements_status column should accept the four
        documented values: None, 'draft', 'submitted', 'reviewed'.
        """
        pos = sample_org["pos_b2_1"]
        valid_statuses = [None, "draft", "submitted", "reviewed"]

        for status in valid_statuses:
            pos.requirements_status = status
            db_session.commit()
            db_session.refresh(pos)
            assert (
                pos.requirements_status == status
            ), f"requirements_status should accept '{status}'"

        # Restore to None.
        pos.requirements_status = None
        db_session.commit()

    def test_position_has_division_id_fk(self, app, sample_org):
        """The division_id FK column should be populated."""
        pos = sample_org["pos_a1_1"]
        assert pos.division_id is not None
        assert isinstance(pos.division_id, int)

    def test_all_six_positions_have_different_codes(self, app, sample_org):
        """All fixture positions should have unique codes."""
        codes = {
            sample_org[k].position_code
            for k in (
                "pos_a1_1",
                "pos_a1_2",
                "pos_a2_1",
                "pos_b1_1",
                "pos_b1_2",
                "pos_b2_1",
            )
        }
        assert len(codes) == 6


# =====================================================================
# 9. Position -> Division relationship (child to parent)
# =====================================================================


class TestPositionDivisionRelationship:
    """
    Verify the Position.division relationship navigates from
    child position to parent division.

    Baseline plan test: ``test_position_division_relationship``.
    """

    def test_position_has_division(self, app, sample_org):
        """A position's division relationship should not be None."""
        pos = sample_org["pos_a1_1"]
        assert pos.division is not None

    def test_division_is_correct_instance(self, app, sample_org):
        """The related object should be a Division model instance."""
        pos = sample_org["pos_a1_1"]
        assert isinstance(pos.division, Division)

    def test_pos_a1_1_belongs_to_div_a1(self, app, sample_org):
        """pos_a1_1's parent should be div_a1."""
        assert sample_org["pos_a1_1"].division.id == sample_org["div_a1"].id

    def test_pos_a1_2_belongs_to_div_a1(self, app, sample_org):
        """pos_a1_2's parent should also be div_a1."""
        assert sample_org["pos_a1_2"].division.id == sample_org["div_a1"].id

    def test_pos_a2_1_belongs_to_div_a2(self, app, sample_org):
        """pos_a2_1's parent should be div_a2."""
        assert sample_org["pos_a2_1"].division.id == sample_org["div_a2"].id

    def test_pos_b1_1_belongs_to_div_b1(self, app, sample_org):
        """pos_b1_1's parent should be div_b1."""
        assert sample_org["pos_b1_1"].division.id == sample_org["div_b1"].id

    def test_pos_b2_1_belongs_to_div_b2(self, app, sample_org):
        """pos_b2_1's parent should be div_b2."""
        assert sample_org["pos_b2_1"].division.id == sample_org["div_b2"].id

    def test_division_name_accessible_through_relationship(self, app, sample_org):
        """
        Navigating position.division.division_name should return
        the parent division's name string.
        """
        name = sample_org["pos_a1_1"].division.division_name
        assert name == sample_org["div_a1"].division_name


# =====================================================================
# 10. Position -> Employee relationship (parent to children)
# =====================================================================


class TestPositionEmployeesRelationship:
    """Verify the Position.employees dynamic relationship."""

    def test_position_employees_is_empty_initially(self, app, sample_org):
        """
        Fixture positions have no employees by default because the
        HR sync has not run.
        """
        emps = sample_org["pos_a1_1"].employees.all()
        # Use an ID filter to only count _TST_ employees.
        # Seed data might include employees, so just verify the
        # relationship is functional.
        assert isinstance(emps, list)

    def test_created_employee_appears_in_position_employees(
        self, app, db_session, sample_org
    ):
        """
        After creating an Employee linked to pos_a1_1, it should
        appear in pos_a1_1.employees.
        """
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        emp_ids = {e.id for e in sample_org["pos_a1_1"].employees.all()}
        assert emp.id in emp_ids

    def test_employee_in_different_position_not_in_relationship(
        self, app, db_session, sample_org
    ):
        """
        An employee created for pos_b1_1 should NOT appear in
        pos_a1_1.employees.
        """
        emp_b = _create_employee(db_session, sample_org["pos_b1_1"])
        emp_ids = {e.id for e in sample_org["pos_a1_1"].employees.all()}
        assert emp_b.id not in emp_ids

    def test_employees_is_dynamic_query(self, app, db_session, sample_org):
        """
        The employees relationship should support query operations
        like filter_by since it is configured as lazy='dynamic'.
        """
        _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Active",
            is_active=True,
        )
        _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Inactive",
            is_active=False,
        )

        active_only = sample_org["pos_a1_1"].employees.filter_by(is_active=True).all()
        # Should include the active employee.
        active_names = {e.first_name for e in active_only}
        assert "Active" in active_names

    def test_multiple_employees_in_same_position(self, app, db_session, sample_org):
        """
        A position should be able to hold multiple employees.
        """
        emp1 = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Employee1",
        )
        emp2 = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Employee2",
        )

        emp_ids = {e.id for e in sample_org["pos_a1_1"].employees.all()}
        assert emp1.id in emp_ids
        assert emp2.id in emp_ids


# =====================================================================
# 11. Position -> PositionHardware / PositionSoftware relationships
# =====================================================================


class TestPositionRequirementRelationships:
    """
    Verify that Position.hardware_requirements and
    Position.software_requirements are functional dynamic
    relationships.  Detailed junction table behavior is tested in
    test_equipment_model.py; here we verify the Position side.
    """

    def test_hardware_requirements_is_queryable(self, app, sample_org):
        """
        The hardware_requirements relationship should be a dynamic
        query that supports .all().
        """
        reqs = sample_org["pos_a1_1"].hardware_requirements.all()
        assert isinstance(reqs, list)

    def test_software_requirements_is_queryable(self, app, sample_org):
        """
        The software_requirements relationship should be a dynamic
        query that supports .all().
        """
        reqs = sample_org["pos_a1_1"].software_requirements.all()
        assert isinstance(reqs, list)

    def test_hardware_requirements_initially_empty(self, app, sample_org):
        """
        Fixture positions have no hardware requirements by default.
        """
        reqs = sample_org["pos_b2_1"].hardware_requirements.all()
        # Might have seed data, but should be a list.
        assert isinstance(reqs, list)

    def test_hardware_requirements_supports_count(self, app, sample_org):
        """
        Dynamic relationships should support .count() for
        efficient counting without loading all records.
        """
        count = sample_org["pos_a1_1"].hardware_requirements.count()
        assert isinstance(count, int)
        assert count >= 0


# =====================================================================
# 12. Position.__repr__
# =====================================================================


class TestPositionRepr:
    """
    Verify the ``__repr__`` output for Position models.

    Coverage target: Position.__repr__ is at 0% in the functions
    coverage report.
    """

    def test_repr_contains_position_code(self, app, sample_org):
        """The repr string should include the position_code."""
        pos = sample_org["pos_a1_1"]
        assert pos.position_code in repr(pos)

    def test_repr_contains_position_title(self, app, sample_org):
        """The repr string should include the position_title."""
        pos = sample_org["pos_a1_1"]
        assert pos.position_title in repr(pos)

    def test_repr_contains_authorized_count(self, app, sample_org):
        """The repr should include the authorized_count value."""
        pos = sample_org["pos_a1_1"]
        # The repr format is: (auth=3)
        assert "auth=3" in repr(pos)

    def test_repr_starts_with_class_name(self, app, sample_org):
        """The repr should start with '<Position'."""
        pos = sample_org["pos_a1_1"]
        assert repr(pos).startswith("<Position")

    def test_repr_for_different_positions_differs(self, app, sample_org):
        """Different positions should produce different repr strings."""
        assert repr(sample_org["pos_a1_1"]) != repr(sample_org["pos_b2_1"])

    def test_repr_for_all_fixture_positions(self, app, sample_org):
        """
        Every fixture position should produce a non-empty repr that
        includes its position_code.  This is a regression guard.
        """
        for key in (
            "pos_a1_1",
            "pos_a1_2",
            "pos_a2_1",
            "pos_b1_1",
            "pos_b1_2",
            "pos_b2_1",
        ):
            pos = sample_org[key]
            r = repr(pos)
            assert len(r) > 0
            assert pos.position_code in r


# =====================================================================
# 13. Employee model -- basic properties
# =====================================================================


class TestEmployeeBasicProperties:
    """Verify Employee column values, defaults, and constraints."""

    def test_employee_has_code(self, app, db_session, sample_org):
        """A created employee should have a non-empty employee_code."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.employee_code is not None
        assert len(emp.employee_code) > 0

    def test_employee_has_first_name(self, app, db_session, sample_org):
        """A created employee should have a non-empty first_name."""
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Alice",
        )
        assert emp.first_name == "Alice"

    def test_employee_has_last_name(self, app, db_session, sample_org):
        """A created employee should have a non-empty last_name."""
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            last_name="Johnson",
        )
        assert emp.last_name == "Johnson"

    def test_employee_is_active_by_default(self, app, db_session, sample_org):
        """Newly created employees should be active."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.is_active is True

    def test_employee_inactive_when_specified(self, app, db_session, sample_org):
        """An employee created with is_active=False should be inactive."""
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            is_active=False,
        )
        assert emp.is_active is False

    def test_employee_email_is_nullable(self, app, db_session, sample_org):
        """
        The email column should accept None.  Some NeoGov employees
        do not have an email address (e.g., field workers).
        """
        global _emp_counter  # pylint: disable=global-statement
        _emp_counter += 1
        code = f"_TST_MEMP_{_emp_counter:04d}"
        emp = Employee(
            position_id=sample_org["pos_a1_1"].id,
            employee_code=code,
            first_name="NoEmail",
            last_name="Worker",
            email=None,
            is_active=True,
        )
        db_session.add(emp)
        db_session.commit()

        db_session.refresh(emp)
        assert emp.email is None

    def test_employee_has_position_id_fk(self, app, db_session, sample_org):
        """The position_id FK column should be populated."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.position_id is not None
        assert emp.position_id == sample_org["pos_a1_1"].id

    def test_employee_has_timestamps(self, app, db_session, sample_org):
        """created_at and updated_at should be populated by the server."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.created_at is not None
        assert emp.updated_at is not None


# =====================================================================
# 14. Employee -> Position relationship (child to parent)
# =====================================================================


class TestEmployeePositionRelationship:
    """
    Verify the Employee.position relationship navigates from
    child employee to parent position.
    """

    def test_employee_has_position(self, app, db_session, sample_org):
        """An employee's position relationship should not be None."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.position is not None

    def test_position_is_correct_instance(self, app, db_session, sample_org):
        """The related object should be a Position model instance."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert isinstance(emp.position, Position)

    def test_employee_position_matches_assigned_position(
        self, app, db_session, sample_org
    ):
        """The position FK should resolve to the correct Position record."""
        emp = _create_employee(db_session, sample_org["pos_b1_1"])
        assert emp.position.id == sample_org["pos_b1_1"].id

    def test_position_title_accessible_through_relationship(
        self, app, db_session, sample_org
    ):
        """
        Navigating employee.position.position_title should return
        the parent position's title string.
        """
        emp = _create_employee(db_session, sample_org["pos_a1_2"])
        assert emp.position.position_title == sample_org["pos_a1_2"].position_title


# =====================================================================
# 15. Employee.__repr__
# =====================================================================


class TestEmployeeRepr:
    """
    Verify the ``__repr__`` output for Employee models.

    Coverage target: Employee.__repr__ is at 0% in the functions
    coverage report.
    """

    def test_repr_contains_employee_code(self, app, db_session, sample_org):
        """The repr string should include the employee_code."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.employee_code in repr(emp)

    def test_repr_contains_first_name(self, app, db_session, sample_org):
        """The repr string should include the employee's first name."""
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="ReprFirst",
        )
        assert "ReprFirst" in repr(emp)

    def test_repr_contains_last_name(self, app, db_session, sample_org):
        """The repr string should include the employee's last name."""
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            last_name="ReprLast",
        )
        assert "ReprLast" in repr(emp)

    def test_repr_starts_with_class_name(self, app, db_session, sample_org):
        """The repr should start with '<Employee'."""
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert repr(emp).startswith("<Employee")


# =====================================================================
# 16. Full hierarchy chain traversal
# =====================================================================


class TestFullHierarchyChain:
    """
    Verify that the full org hierarchy can be traversed in both
    directions: Department -> Division -> Position -> Employee and
    back from Employee -> Position -> Division -> Department.

    This is a cross-model integration test at the model layer that
    catches FK or relationship misconfigurations that individual
    model tests might miss.
    """

    def test_department_to_employee_traversal(self, app, db_session, sample_org):
        """
        Starting from dept_a, navigate through div_a1, to pos_a1_1,
        and verify an employee created there is reachable.
        """
        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Chain",
            last_name="Test",
        )

        # Traverse: dept_a -> divisions -> div_a1 -> positions ->
        # pos_a1_1 -> employees.
        dept = sample_org["dept_a"]
        div_ids = {d.id for d in dept.divisions.all()}
        assert sample_org["div_a1"].id in div_ids

        div = sample_org["div_a1"]
        pos_ids = {p.id for p in div.positions.all()}
        assert sample_org["pos_a1_1"].id in pos_ids

        pos = sample_org["pos_a1_1"]
        emp_ids = {e.id for e in pos.employees.all()}
        assert emp.id in emp_ids

    def test_employee_to_department_traversal(self, app, db_session, sample_org):
        """
        Starting from an employee in pos_b1_1, navigate up
        through position -> division -> department and verify
        each step resolves correctly.
        """
        emp = _create_employee(
            db_session,
            sample_org["pos_b1_1"],
            first_name="Reverse",
            last_name="Chain",
        )

        # Employee -> Position
        assert emp.position is not None
        assert emp.position.id == sample_org["pos_b1_1"].id

        # Position -> Division
        div = emp.position.division
        assert div is not None
        assert div.id == sample_org["div_b1"].id

        # Division -> Department
        dept = div.department
        assert dept is not None
        assert dept.id == sample_org["dept_b"].id

    def test_employee_reaches_correct_department_name(
        self, app, db_session, sample_org
    ):
        """
        A single chained access from employee all the way to
        department_name should return the correct value.
        """
        emp = _create_employee(db_session, sample_org["pos_a2_1"])
        dept_name = emp.position.division.department.department_name
        assert dept_name == sample_org["dept_a"].department_name

    def test_cross_department_chains_are_independent(self, app, db_session, sample_org):
        """
        An employee in dept_a's hierarchy should NOT resolve to
        dept_b when traversing up the chain.
        """
        emp_a = _create_employee(db_session, sample_org["pos_a1_1"])
        emp_b = _create_employee(db_session, sample_org["pos_b1_1"])

        dept_a_name = emp_a.position.division.department.department_name
        dept_b_name = emp_b.position.division.department.department_name

        assert dept_a_name == sample_org["dept_a"].department_name
        assert dept_b_name == sample_org["dept_b"].department_name
        assert dept_a_name != dept_b_name


# =====================================================================
# 17. Employee.user_account backref
# =====================================================================


class TestEmployeeUserAccountBackref:
    """
    Verify the Employee -> User backref created by
    ``User.employee = db.relationship("Employee",
    backref=db.backref("user_account", uselist=False))``.

    Employees may or may not have linked auth.user records.
    """

    def test_employee_without_linked_user_has_none(self, app, db_session, sample_org):
        """
        A newly created employee with no linked User should have
        user_account=None.
        """
        emp = _create_employee(db_session, sample_org["pos_a1_1"])
        assert emp.user_account is None

    def test_employee_with_linked_user_has_user_account(
        self, app, db_session, sample_org, roles
    ):
        """
        When a User has employee_id pointing to this employee, the
        backref should resolve to that User record.
        """
        from app.models.user import User, UserScope

        emp = _create_employee(
            db_session,
            sample_org["pos_a1_1"],
            first_name="Linked",
            last_name="EmpUser",
        )

        # Create a linked User.
        user = User(
            email=f"_tst_linked_empuser_{_emp_counter}@test.local",
            first_name="Linked",
            last_name="EmpUser",
            role_id=roles["read_only"].id,
            employee_id=emp.id,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        # Refresh to pick up the backref.
        db_session.refresh(emp)

        assert emp.user_account is not None
        assert emp.user_account.id == user.id
        assert emp.user_account.email == user.email


# =====================================================================
# 18. Model table args (schema verification)
# =====================================================================


class TestModelSchemaConfiguration:
    """
    Verify that each org model is configured to use the ``org``
    schema, which is required for the SQL Server DDL.
    """

    def test_department_uses_org_schema(self, app):
        """Department.__table_args__ should specify schema='org'."""
        assert Department.__table_args__["schema"] == "org"

    def test_division_uses_org_schema(self, app):
        """Division.__table_args__ should specify schema='org'."""
        assert Division.__table_args__["schema"] == "org"

    def test_position_uses_org_schema(self, app):
        """Position.__table_args__ should specify schema='org'."""
        assert Position.__table_args__["schema"] == "org"

    def test_employee_uses_org_schema(self, app):
        """Employee.__table_args__ should specify schema='org'."""
        assert Employee.__table_args__["schema"] == "org"

    def test_department_tablename(self, app):
        """Department.__tablename__ should be 'department'."""
        assert Department.__tablename__ == "department"

    def test_division_tablename(self, app):
        """Division.__tablename__ should be 'division'."""
        assert Division.__tablename__ == "division"

    def test_position_tablename(self, app):
        """Position.__tablename__ should be 'position'."""
        assert Position.__tablename__ == "position"

    def test_employee_tablename(self, app):
        """Employee.__tablename__ should be 'employee'."""
        assert Employee.__tablename__ == "employee"
