"""
Tests for the cost service layer.

Validates every cost calculation path that drives budget figures
in the application.  Organized in two parts:

Part 1 -- ``TestCalculatePositionCost`` (original):
    Foundational tests for ``calculate_position_cost()`` using a
    self-contained autouse fixture with isolated org/equipment data.
    Covers hardware cost, per-user software cost, combined totals,
    the authorized_count multiplier, and nonexistent position error.

Part 2 -- Edge cases and aggregation (expanded):
    Uses the shared ``sample_org`` and ``sample_catalog`` conftest
    fixtures so tests can span multiple positions, divisions, and
    departments.  Covers:
        - Tenant-licensed software cost allocation at every scope.
        - Headcount deduplication when coverage rows overlap.
        - Zero authorized_count positions (no division-by-zero).
        - Decimal precision and ROUND_HALF_UP behavior.
        - Multi-item positions with mixed hardware and software.
        - Division-level cost aggregation.
        - Department-level aggregation and consistency with positions.
        - Department average / min / max per-person statistics.
        - Cross-level aggregation consistency (the CIO check).

Fixture reminder (from conftest.py ``sample_org``):
    pos_a1_1  authorized_count = 3   (div_a1, dept_a)
    pos_a1_2  authorized_count = 5   (div_a1, dept_a)
    pos_a2_1  authorized_count = 2   (div_a2, dept_a)
    pos_b1_1  authorized_count = 4   (div_b1, dept_b)
    pos_b1_2  authorized_count = 1   (div_b1, dept_b)
    pos_b2_1  authorized_count = 6   (div_b2, dept_b)

Fixture reminder (from conftest.py ``sample_catalog``):
    hw_laptop_standard   $1,200.00  per unit
    hw_laptop_power      $2,400.00  per unit
    hw_monitor_24        $  350.00  per unit
    sw_office_e3         $  200.00  per_user  (cost_per_license)
    sw_office_e5         $  400.00  per_user  (cost_per_license)
    sw_antivirus         $50,000.00 tenant    (total_cost)

Run this file in isolation::

    pytest tests/test_services/test_cost_service.py -v
"""

from decimal import Decimal, ROUND_HALF_UP

import pytest

from app.extensions import db
from app.models.equipment import (
    Hardware,
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareType,
)
from app.models.organization import Department, Division, Position
from app.models.requirement import PositionHardware, PositionSoftware
from app.services import cost_service


# =====================================================================
# Part 1: Original foundational tests (self-contained fixtures)
# =====================================================================


class TestCalculatePositionCost:
    """Tests for the core position cost calculation."""

    @pytest.fixture(autouse=True)
    def _setup(self, app, db_session):
        """
        Create a minimal org structure and equipment catalog for
        each test.  The db_session fixture rolls back after each
        test, so this data is ephemeral.
        """
        self.session = db_session

        # -- Org structure: one department -> one division -> one position.
        self.dept = Department(
            department_code="TEST_DEPT",
            department_name="Test Department",
        )
        db_session.add(self.dept)
        db_session.flush()

        self.div = Division(
            department_id=self.dept.id,
            division_code="TEST_DIV",
            division_name="Test Division",
        )
        db_session.add(self.div)
        db_session.flush()

        self.position = Position(
            division_id=self.div.id,
            position_code="TEST_POS",
            position_title="Test Position",
            authorized_count=3,
        )
        db_session.add(self.position)
        db_session.flush()

        # -- Equipment catalog: one hardware type -> one hardware item.
        self.hw_type = HardwareType(
            type_name="Test Laptop Type",
            estimated_cost=Decimal("0.00"),
        )
        db_session.add(self.hw_type)
        db_session.flush()

        self.hw_item = Hardware(
            name="Standard Test Laptop",
            hardware_type_id=self.hw_type.id,
            estimated_cost=Decimal("1200.00"),
        )
        db_session.add(self.hw_item)
        db_session.flush()

        # -- Software catalog: one type -> one per-user product.
        self.sw_type = SoftwareType(type_name="Test Productivity")
        db_session.add(self.sw_type)
        db_session.flush()

        self.sw_item = Software(
            name="Test Office Suite",
            software_type_id=self.sw_type.id,
            license_model="per_user",
            cost_per_license=Decimal("200.00"),
        )
        db_session.add(self.sw_item)
        db_session.flush()

    def test_position_with_no_requirements_returns_zero(self):
        """A position with no hardware or software should cost $0."""
        result = cost_service.calculate_position_cost(self.position.id)

        assert result.hardware_total == Decimal("0.00")
        assert result.software_total == Decimal("0.00")
        assert result.grand_total == Decimal("0.00")
        assert result.authorized_count == 3
        assert len(result.hardware_lines) == 0
        assert len(result.software_lines) == 0

    def test_hardware_cost_calculation(self):
        """
        Hardware cost = quantity x unit_cost per person,
        position total = per_person x authorized_count.
        """
        # Assign 2 laptops per person.
        req = PositionHardware(
            position_id=self.position.id,
            hardware_id=self.hw_item.id,
            quantity=2,
        )
        self.session.add(req)
        self.session.flush()

        result = cost_service.calculate_position_cost(self.position.id)

        assert len(result.hardware_lines) == 1
        line = result.hardware_lines[0]
        # Per person: 2 x $1200 = $2400.
        assert line.quantity == 2
        assert line.unit_cost == Decimal("1200.00")
        assert line.line_total == Decimal("2400.00")
        # Position total: $2400 x 3 people = $7200.
        assert line.position_total == Decimal("7200.00")
        assert result.hardware_total == Decimal("7200.00")
        assert result.grand_total == Decimal("7200.00")

    def test_software_per_user_cost_calculation(self):
        """
        Per-user software cost = quantity x cost_per_license per person,
        position total = per_person x authorized_count.
        """
        req = PositionSoftware(
            position_id=self.position.id,
            software_id=self.sw_item.id,
            quantity=1,
        )
        self.session.add(req)
        self.session.flush()

        result = cost_service.calculate_position_cost(self.position.id)

        assert len(result.software_lines) == 1
        line = result.software_lines[0]
        # Per person: 1 x $200 = $200.
        assert line.unit_cost == Decimal("200.00")
        assert line.line_total == Decimal("200.00")
        # Position total: $200 x 3 people = $600.
        assert line.position_total == Decimal("600.00")
        assert result.software_total == Decimal("600.00")

    def test_combined_hardware_and_software(self):
        """Grand total should equal hardware + software totals."""
        hw_req = PositionHardware(
            position_id=self.position.id,
            hardware_id=self.hw_item.id,
            quantity=1,
        )
        sw_req = PositionSoftware(
            position_id=self.position.id,
            software_id=self.sw_item.id,
            quantity=1,
        )
        self.session.add(hw_req)
        self.session.add(sw_req)
        self.session.flush()

        result = cost_service.calculate_position_cost(self.position.id)

        # Hardware: 1 x $1200 x 3 = $3600.
        assert result.hardware_total == Decimal("3600.00")
        # Software: 1 x $200 x 3 = $600.
        assert result.software_total == Decimal("600.00")
        # Grand: $3600 + $600 = $4200.
        assert result.grand_total == Decimal("4200.00")
        # Per person: $1200 + $200 = $1400.
        assert result.total_per_person == Decimal("1400.00")

    def test_authorized_count_multiplier(self):
        """
        Mutation guard: if authorized_count were ignored, grand_total
        would equal total_per_person.  This test ensures the multiplier
        is actually applied.
        """
        hw_req = PositionHardware(
            position_id=self.position.id,
            hardware_id=self.hw_item.id,
            quantity=1,
        )
        self.session.add(hw_req)
        self.session.flush()

        result = cost_service.calculate_position_cost(self.position.id)

        # authorized_count is 3, so grand != per_person.
        assert result.authorized_count == 3
        assert result.grand_total == result.total_per_person * 3
        assert result.grand_total != result.total_per_person

    def test_nonexistent_position_raises_value_error(self):
        """Requesting cost for a missing position should raise."""
        with pytest.raises(ValueError, match="not found"):
            cost_service.calculate_position_cost(999999)


# =====================================================================
# Part 2: Edge cases and aggregation (shared conftest fixtures)
# =====================================================================


# =====================================================================
# 2.1 Tenant software cost allocation
# =====================================================================


class TestTenantSoftwareCostCalculation:
    """
    Verify that tenant-licensed software cost is calculated as:

        per_person = total_cost / covered_headcount
        position_total = per_person * authorized_count

    Coverage scope determines which positions' authorized_count
    values compose the covered_headcount denominator.
    """

    def test_tenant_cost_with_organization_coverage(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Organization-wide coverage divides total_cost across every
        active position's authorized_count in the entire database,
        not just the test fixture positions.

        The test database contains seed data from the DDL script, so
        the org-wide headcount is larger than the 6 fixture positions.
        We query the actual headcount dynamically to compute expected
        values, then verify the service matches.
        """
        from sqlalchemy import func as sa_func

        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_antivirus"]

        # Create org-wide coverage for the tenant software.
        create_sw_coverage(software=sw, scope_type="organization")

        # Assign the software to the position.
        create_sw_requirement(position=pos, software=sw, quantity=1)

        # Query the ACTUAL org-wide headcount from the database.
        # This includes seed positions plus our 6 test fixture positions.
        actual_headcount = (
            db.session.query(sa_func.sum(Position.authorized_count))
            .filter(Position.is_active == True)  # noqa: E712
            .scalar()
        )
        assert actual_headcount is not None and actual_headcount > 0

        result = cost_service.calculate_position_cost(pos.id)

        # Dynamically calculated expected values.
        expected_per_person = (
            Decimal("50000.00") / Decimal(str(actual_headcount))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        expected_position_total = expected_per_person * Decimal("3")

        assert len(result.software_lines) == 1

        line = result.software_lines[0]
        assert line.license_model == "tenant"
        assert line.unit_cost == expected_per_person
        assert line.line_total == expected_per_person
        assert line.position_total == expected_position_total

        assert result.software_total == expected_position_total
        assert result.grand_total == expected_position_total

    def test_tenant_cost_with_department_coverage(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Department-scoped coverage divides total_cost across only
        positions in that department.

        Coverage on dept_a: headcount = 3 + 5 + 2 = 10.
        Per person = $50,000 / 10 = $5,000.00.
        pos_a1_1 (authorized=3): position_total = $5,000.00 * 3 = $15,000.00.
        """
        pos = sample_org["pos_a1_1"]
        dept_a = sample_org["dept_a"]
        sw = sample_catalog["sw_antivirus"]

        create_sw_coverage(
            software=sw,
            scope_type="department",
            department_id=dept_a.id,
        )
        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        expected_per_person = Decimal("50000.00") / Decimal("10")
        expected_position_total = expected_per_person * Decimal("3")

        assert result.software_lines[0].unit_cost == Decimal("5000.00")
        assert result.software_lines[0].position_total == Decimal("15000.00")
        assert result.software_total == expected_position_total

    def test_tenant_cost_with_division_coverage(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Division-scoped coverage divides total_cost across only
        positions in that division.

        Coverage on div_a1: headcount = 3 + 5 = 8.
        Per person = $50,000 / 8 = $6,250.00.
        pos_a1_1 (authorized=3): position_total = $6,250.00 * 3 = $18,750.00.
        """
        pos = sample_org["pos_a1_1"]
        div_a1 = sample_org["div_a1"]
        sw = sample_catalog["sw_antivirus"]

        create_sw_coverage(
            software=sw,
            scope_type="division",
            division_id=div_a1.id,
        )
        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        assert result.software_lines[0].unit_cost == Decimal("6250.00")
        assert result.software_lines[0].position_total == Decimal("18750.00")

    def test_tenant_cost_with_position_coverage(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Position-scoped coverage divides total_cost across only that
        single position's authorized_count.

        Coverage on pos_a1_1 only: headcount = 3.
        Per person = $50,000 / 3 = $16,666.67 (ROUND_HALF_UP).
        position_total = $16,666.67 * 3 = $50,000.01.

        Note: the one-penny overshoot is expected because per-person
        rounding at two decimal places times authorized_count does not
        perfectly reconstruct the original total_cost.  The service
        rounds per-person first, then multiplies -- this is by design.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_antivirus"]

        create_sw_coverage(
            software=sw,
            scope_type="position",
            position_id=pos.id,
        )
        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        expected_per_person = (Decimal("50000.00") / Decimal("3")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        # $16,666.67
        assert expected_per_person == Decimal("16666.67")

        line = result.software_lines[0]
        assert line.unit_cost == expected_per_person
        assert line.position_total == expected_per_person * Decimal("3")

    def test_tenant_cost_with_zero_total_cost_returns_zero(
        self,
        app,
        db_session,
        sample_org,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        A tenant software with total_cost = $0.00 should produce
        $0.00 cost regardless of coverage, not a division-by-zero.
        """
        pos = sample_org["pos_a1_1"]

        # Create a zero-cost tenant product.
        sw_type = SoftwareType(type_name="_TST_SWTYPE_ZEROCOST")
        db_session.add(sw_type)
        db_session.flush()

        sw_free = Software(
            name="_TST_SW_FREE",
            software_type_id=sw_type.id,
            license_model="tenant",
            total_cost=Decimal("0.00"),
        )
        db_session.add(sw_free)
        db_session.commit()

        create_sw_coverage(software=sw_free, scope_type="organization")
        create_sw_requirement(position=pos, software=sw_free, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        assert len(result.software_lines) == 1
        assert result.software_lines[0].unit_cost == Decimal("0.00")
        assert result.software_lines[0].position_total == Decimal("0.00")
        assert result.software_total == Decimal("0.00")

    def test_tenant_cost_with_no_coverage_rows_returns_zero(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
    ):
        """
        A tenant software with no SoftwareCoverage rows produces
        covered_headcount = 0, and the service returns $0.00
        rather than raising a ZeroDivisionError.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_antivirus"]

        # Deliberately do NOT create any coverage rows.
        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        assert result.software_lines[0].unit_cost == Decimal("0.00")
        assert result.software_lines[0].position_total == Decimal("0.00")


# =====================================================================
# 2.2 Headcount deduplication across overlapping coverage
# =====================================================================


class TestTenantHeadcountDeduplication:
    """
    When multiple SoftwareCoverage rows overlap (e.g., org-wide plus
    a specific department), the covered_headcount must count each
    position only once.  Double-counting would deflate the per-person
    cost and produce incorrect budget figures.
    """

    def test_org_plus_department_coverage_does_not_double_count(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Org-wide coverage already covers all positions.  Adding a
        department-level coverage row for dept_a should NOT increase
        the covered_headcount beyond the org total.

        The org-wide headcount includes seed data positions, so we
        query the actual value dynamically.  The key assertion is
        that the per-person cost with overlapping coverage equals
        the per-person cost with org-only coverage.
        """
        from sqlalchemy import func as sa_func

        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_antivirus"]

        # Two overlapping coverage rows.
        create_sw_coverage(software=sw, scope_type="organization")
        create_sw_coverage(
            software=sw,
            scope_type="department",
            department_id=sample_org["dept_a"].id,
        )

        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        # The actual org-wide headcount (seed data + test fixtures).
        actual_headcount = (
            db.session.query(sa_func.sum(Position.authorized_count))
            .filter(Position.is_active == True)  # noqa: E712
            .scalar()
        )

        # The overlapping dept_a coverage should NOT inflate the
        # denominator beyond the org total.
        expected_per_person = (
            Decimal("50000.00") / Decimal(str(actual_headcount))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert result.software_lines[0].unit_cost == expected_per_person

    def test_department_plus_division_coverage_does_not_double_count(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        dept_a coverage includes div_a1 and div_a2.  Adding an
        explicit div_a1 coverage row should not double-count
        div_a1 positions.

        dept_a headcount = 3 + 5 + 2 = 10.
        With overlapping div_a1 coverage, headcount should still be 10.
        Per person = $50,000 / 10 = $5,000.00.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_antivirus"]

        create_sw_coverage(
            software=sw,
            scope_type="department",
            department_id=sample_org["dept_a"].id,
        )
        create_sw_coverage(
            software=sw,
            scope_type="division",
            division_id=sample_org["div_a1"].id,
        )

        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)
        assert result.software_lines[0].unit_cost == Decimal("5000.00")

    def test_two_department_coverages_sum_correctly(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Coverage for dept_a AND dept_b (without org-wide) should
        include all positions from both departments.

        dept_a headcount = 3 + 5 + 2 = 10.
        dept_b headcount = 4 + 1 + 6 = 11.
        Total = 21 (same as org-wide in this fixture set).
        Per person = $50,000 / 21 = $2,380.95.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_antivirus"]

        create_sw_coverage(
            software=sw,
            scope_type="department",
            department_id=sample_org["dept_a"].id,
        )
        create_sw_coverage(
            software=sw,
            scope_type="department",
            department_id=sample_org["dept_b"].id,
        )

        create_sw_requirement(position=pos, software=sw, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        expected_per_person = (Decimal("50000.00") / Decimal("21")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert result.software_lines[0].unit_cost == expected_per_person


# =====================================================================
# 2.3 Zero authorized_count edge case
# =====================================================================


class TestZeroAuthorizedCountPosition:
    """
    A position with authorized_count = 0 is a valid edge case
    (e.g., a position that has been frozen but not deleted).
    Cost calculations must not produce division-by-zero errors
    and must report $0.00 position totals.
    """

    def test_zero_authorized_hw_only_returns_zero_position_total(
        self,
        app,
        db_session,
        sample_org,
        sample_catalog,
    ):
        """
        Hardware cost: per_person = quantity * unit_cost = $1,200.
        position_total = $1,200 * 0 = $0.00.
        grand_total = $0.00.

        The per_person fields should still reflect the unit economics
        even though the position total is zero.
        """
        # Create a zero-authorized position in existing div_a1.
        pos_zero = Position(
            division_id=sample_org["div_a1"].id,
            position_code="_TST_POS_ZERO",
            position_title="Frozen Position",
            authorized_count=0,
        )
        db_session.add(pos_zero)
        db_session.commit()

        # Assign hardware.
        hw_req = PositionHardware(
            position_id=pos_zero.id,
            hardware_id=sample_catalog["hw_laptop_standard"].id,
            quantity=1,
        )
        db_session.add(hw_req)
        db_session.commit()

        result = cost_service.calculate_position_cost(pos_zero.id)

        assert result.authorized_count == 0
        # Per-person cost still reflects the item cost.
        assert result.hardware_total_per_person == Decimal("1200.00")
        # Position total is zero because 0 people need equipment.
        assert result.hardware_total == Decimal("0.00")
        assert result.grand_total == Decimal("0.00")

    def test_zero_authorized_per_user_sw_returns_zero_position_total(
        self,
        app,
        db_session,
        sample_org,
        sample_catalog,
    ):
        """
        Per-user software on a zero-authorized position should
        produce $0.00 position total.
        """
        pos_zero = Position(
            division_id=sample_org["div_a1"].id,
            position_code="_TST_POS_ZERO2",
            position_title="Frozen Position 2",
            authorized_count=0,
        )
        db_session.add(pos_zero)
        db_session.commit()

        sw_req = PositionSoftware(
            position_id=pos_zero.id,
            software_id=sample_catalog["sw_office_e3"].id,
            quantity=1,
        )
        db_session.add(sw_req)
        db_session.commit()

        result = cost_service.calculate_position_cost(pos_zero.id)

        assert result.authorized_count == 0
        assert result.software_total_per_person == Decimal("200.00")
        assert result.software_total == Decimal("0.00")
        assert result.grand_total == Decimal("0.00")

    def test_zero_authorized_combined_returns_zero_grand_total(
        self,
        app,
        db_session,
        sample_org,
        sample_catalog,
    ):
        """
        A zero-authorized position with both hardware and software
        requirements should report non-zero per-person economics
        but $0.00 grand total.
        """
        pos_zero = Position(
            division_id=sample_org["div_a1"].id,
            position_code="_TST_POS_ZERO3",
            position_title="Frozen Position 3",
            authorized_count=0,
        )
        db_session.add(pos_zero)
        db_session.commit()

        hw_req = PositionHardware(
            position_id=pos_zero.id,
            hardware_id=sample_catalog["hw_laptop_standard"].id,
            quantity=1,
        )
        sw_req = PositionSoftware(
            position_id=pos_zero.id,
            software_id=sample_catalog["sw_office_e3"].id,
            quantity=1,
        )
        db_session.add_all([hw_req, sw_req])
        db_session.commit()

        result = cost_service.calculate_position_cost(pos_zero.id)

        # Per-person economics are still meaningful for budgeting.
        assert result.total_per_person == Decimal("1400.00")
        # But the position incurs no actual cost.
        assert result.grand_total == Decimal("0.00")


# =====================================================================
# 2.4 Decimal precision and rounding
# =====================================================================


class TestCostDecimalPrecision:
    """
    Cost calculations must produce Decimal results rounded to
    exactly two decimal places using ROUND_HALF_UP.  Repeating
    decimals (e.g., $10,000 / 3) must not propagate unrounded
    through aggregations.
    """

    def test_tenant_cost_repeating_decimal_rounds_correctly(
        self,
        app,
        db_session,
        sample_org,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        $10,000 total / 3 headcount = $3,333.333... per person.
        Should round to $3,333.33 (third decimal is 3, below 5).

        position_total for pos with authorized_count = 3:
        $3,333.33 * 3 = $9,999.99 (one penny less than total_cost).
        This demonstrates that rounding per-person first is the
        intended behavior, even though it does not perfectly
        reconstruct the original total.
        """
        # Create a tenant product with a cost that produces repeating decimals.
        sw_type = SoftwareType(type_name="_TST_SWTYPE_REPEAT")
        db_session.add(sw_type)
        db_session.flush()

        sw_repeat = Software(
            name="_TST_SW_REPEAT",
            software_type_id=sw_type.id,
            license_model="tenant",
            total_cost=Decimal("10000.00"),
        )
        db_session.add(sw_repeat)
        db_session.commit()

        # Position-level coverage on pos_a1_1 gives headcount = 3.
        pos = sample_org["pos_a1_1"]  # authorized_count = 3
        create_sw_coverage(
            software=sw_repeat,
            scope_type="position",
            position_id=pos.id,
        )
        create_sw_requirement(position=pos, software=sw_repeat, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        # $10,000 / 3 = $3,333.333... rounds to $3,333.33.
        expected_per_person = Decimal("3333.33")
        assert result.software_lines[0].unit_cost == expected_per_person

        # $3,333.33 * 3 = $9,999.99 (not $10,000.00).
        assert result.software_total == Decimal("9999.99")

    def test_tenant_cost_round_half_up_at_boundary(
        self,
        app,
        db_session,
        sample_org,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Verify ROUND_HALF_UP behavior with a denominator that
        produces a repeating 6 in the third decimal place.

        $10,000 / 6 = $1,666.666... should round to $1,666.67
        because the third decimal (6) is >= 5.
        """
        sw_type = SoftwareType(type_name="_TST_SWTYPE_HALFUP")
        db_session.add(sw_type)
        db_session.flush()

        sw_test = Software(
            name="_TST_SW_HALFUP",
            software_type_id=sw_type.id,
            license_model="tenant",
            total_cost=Decimal("10000.00"),
        )
        db_session.add(sw_test)
        db_session.commit()

        # Coverage on div_b2: only pos_b2_1 (authorized_count=6).
        pos = sample_org["pos_b2_1"]
        create_sw_coverage(
            software=sw_test,
            scope_type="division",
            division_id=sample_org["div_b2"].id,
        )
        create_sw_requirement(position=pos, software=sw_test, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        # $10,000 / 6 = $1,666.6666... -> $1,666.67 (ROUND_HALF_UP).
        expected = (Decimal("10000.00") / Decimal("6")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert expected == Decimal("1666.67")
        assert result.software_lines[0].unit_cost == expected

        # position_total = $1,666.67 * 6 = $10,000.02.
        # Overshoot is expected and correct -- the service rounds
        # per-person first, then multiplies by authorized_count.
        assert result.software_total == Decimal("10000.02")

    def test_hardware_cost_precision_with_large_quantity(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        Hardware uses exact integer multiplication (no rounding
        needed), but verify precision is maintained through the
        full calculation chain.

        3 monitors at $350.00 per person, authorized_count = 5.
        per_person = 3 * $350.00 = $1,050.00.
        position_total = $1,050.00 * 5 = $5,250.00.
        """
        pos = sample_org["pos_a1_2"]  # authorized_count = 5
        hw = sample_catalog["hw_monitor_24"]  # $350.00

        create_hw_requirement(position=pos, hardware=hw, quantity=3)

        result = cost_service.calculate_position_cost(pos.id)

        line = result.hardware_lines[0]
        assert line.quantity == 3
        assert line.unit_cost == Decimal("350.00")
        assert line.line_total == Decimal("1050.00")
        assert line.position_total == Decimal("5250.00")
        assert result.hardware_total_per_person == Decimal("1050.00")
        assert result.hardware_total == Decimal("5250.00")


# =====================================================================
# 2.5 Multi-item complex positions
# =====================================================================


class TestMultiItemPositionCost:
    """
    Verify that a position with multiple hardware items AND multiple
    software products aggregates all line items correctly.
    """

    def test_multiple_hw_and_sw_items_aggregate_correctly(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
    ):
        """
        pos_a1_2 (authorized_count=5):
          - 1x laptop_standard @ $1,200 -> per_person $1,200, total $6,000
          - 2x monitor_24      @ $350   -> per_person $700,   total $3,500
          - 1x sw_office_e3    @ $200   -> per_person $200,   total $1,000
          - 1x sw_office_e5    @ $400   -> per_person $400,   total $2,000

        hw_per_person  = $1,200 + $700  = $1,900
        sw_per_person  = $200 + $400    = $600
        total_per_person = $2,500
        hw_total  = $1,900 * 5 = $9,500
        sw_total  = $600 * 5   = $3,000
        grand_total = $12,500
        """
        pos = sample_org["pos_a1_2"]  # authorized_count = 5
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]
        sw_e3 = sample_catalog["sw_office_e3"]
        sw_e5 = sample_catalog["sw_office_e5"]

        create_hw_requirement(position=pos, hardware=hw_laptop, quantity=1)
        create_hw_requirement(position=pos, hardware=hw_monitor, quantity=2)
        create_sw_requirement(position=pos, software=sw_e3, quantity=1)
        create_sw_requirement(position=pos, software=sw_e5, quantity=1)

        result = cost_service.calculate_position_cost(pos.id)

        assert len(result.hardware_lines) == 2
        assert len(result.software_lines) == 2

        assert result.hardware_total_per_person == Decimal("1900.00")
        assert result.software_total_per_person == Decimal("600.00")
        assert result.total_per_person == Decimal("2500.00")
        assert result.hardware_total == Decimal("9500.00")
        assert result.software_total == Decimal("3000.00")
        assert result.grand_total == Decimal("12500.00")

    def test_mixed_per_user_and_tenant_software_on_same_position(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        A position can have both per-user and tenant software.
        Verify each uses the correct formula and they sum correctly.

        pos_a1_1 (authorized_count=3):
          - sw_office_e3 (per_user, $200) -> per_person $200, total $600
          - sw_antivirus (tenant, $50,000, org coverage)
                -> per_person = $50,000 / actual_org_headcount
                -> total = per_person * 3
        """
        from sqlalchemy import func as sa_func

        pos = sample_org["pos_a1_1"]

        create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="organization",
        )
        create_sw_requirement(
            position=pos, software=sample_catalog["sw_office_e3"], quantity=1
        )
        create_sw_requirement(
            position=pos, software=sample_catalog["sw_antivirus"], quantity=1
        )

        result = cost_service.calculate_position_cost(pos.id)

        # Identify lines by license model.
        per_user_line = next(
            line for line in result.software_lines if line.license_model == "per_user"
        )
        tenant_line = next(
            line for line in result.software_lines if line.license_model == "tenant"
        )

        assert per_user_line.unit_cost == Decimal("200.00")
        assert per_user_line.position_total == Decimal("600.00")

        # Dynamically compute the org-wide headcount (includes seed data).
        actual_headcount = (
            db.session.query(sa_func.sum(Position.authorized_count))
            .filter(Position.is_active == True)  # noqa: E712
            .scalar()
        )
        tenant_expected = (
            Decimal("50000.00") / Decimal(str(actual_headcount))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert tenant_line.unit_cost == tenant_expected
        assert tenant_line.position_total == tenant_expected * Decimal("3")

        assert result.software_total_per_person == (Decimal("200.00") + tenant_expected)
        assert result.software_total == (
            Decimal("600.00") + tenant_expected * Decimal("3")
        )


# =====================================================================
# 2.6 Division-level aggregation
# =====================================================================


class TestDivisionCostBreakdown:
    """
    Verify that ``get_division_cost_breakdown()`` correctly sums
    costs across all positions in a division.
    """

    def test_division_total_equals_sum_of_position_totals(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
    ):
        """
        Give two positions in div_a1 different requirements, then
        verify the division total equals the sum of the individual
        position totals.

        pos_a1_1 (authorized=3): 1x laptop @ $1,200 -> $3,600
        pos_a1_2 (authorized=5): 1x monitor @ $350  -> $1,750

        Division hw_total = $3,600 + $1,750 = $5,350.
        """
        pos1 = sample_org["pos_a1_1"]
        pos2 = sample_org["pos_a1_2"]

        create_hw_requirement(
            position=pos1, hardware=sample_catalog["hw_laptop_standard"], quantity=1
        )
        create_hw_requirement(
            position=pos2, hardware=sample_catalog["hw_monitor_24"], quantity=1
        )

        # Calculate each position individually.
        cost1 = cost_service.calculate_position_cost(pos1.id)
        cost2 = cost_service.calculate_position_cost(pos2.id)

        # Calculate the division aggregate.
        div_cost = cost_service.get_division_cost_breakdown(sample_org["div_a1"].id)

        assert div_cost.hardware_total == cost1.hardware_total + cost2.hardware_total
        assert div_cost.software_total == cost1.software_total + cost2.software_total
        assert div_cost.grand_total == cost1.grand_total + cost2.grand_total

        # Verify the metadata fields.
        assert div_cost.position_count == 2
        assert div_cost.total_authorized == 3 + 5

    def test_division_with_no_requirements_returns_zero_totals(
        self,
        app,
        sample_org,
    ):
        """
        A division whose positions have no requirements should
        still return a valid summary with $0.00 totals, not raise.
        """
        div_cost = cost_service.get_division_cost_breakdown(sample_org["div_a2"].id)

        assert div_cost.grand_total == Decimal("0.00")
        assert div_cost.hardware_total == Decimal("0.00")
        assert div_cost.software_total == Decimal("0.00")
        # div_a2 has one position (pos_a2_1).
        assert div_cost.position_count == 1
        assert div_cost.total_authorized == 2

    def test_nonexistent_division_raises_value_error(self, app):
        """Requesting cost breakdown for a missing division raises."""
        with pytest.raises(ValueError, match="not found"):
            cost_service.get_division_cost_breakdown(999999)


# =====================================================================
# 2.7 Department-level aggregation
# =====================================================================


class TestDepartmentCostBreakdown:
    """
    Verify that ``get_department_cost_breakdown()`` aggregates
    correctly across divisions and that department totals match
    the sum of their constituent position totals.
    """

    def test_department_total_matches_sum_of_position_totals(
        self,
        app,
        admin_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
    ):
        """
        Configure requirements on all three dept_a positions, then
        verify that the department total equals the sum of position
        totals (calculated independently).

        pos_a1_1 (authorized=3): 1x laptop_standard  -> $3,600
        pos_a1_2 (authorized=5): 1x sw_office_e3     -> $1,000
        pos_a2_1 (authorized=2): 1x monitor_24       -> $700

        dept_a grand_total = $3,600 + $1,000 + $700 = $5,300.
        """
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )
        create_sw_requirement(
            position=sample_org["pos_a1_2"],
            software=sample_catalog["sw_office_e3"],
            quantity=1,
        )
        create_hw_requirement(
            position=sample_org["pos_a2_1"],
            hardware=sample_catalog["hw_monitor_24"],
            quantity=1,
        )

        # Individual position costs.
        cost_a1_1 = cost_service.calculate_position_cost(sample_org["pos_a1_1"].id)
        cost_a1_2 = cost_service.calculate_position_cost(sample_org["pos_a1_2"].id)
        cost_a2_1 = cost_service.calculate_position_cost(sample_org["pos_a2_1"].id)

        expected_grand = (
            cost_a1_1.grand_total + cost_a1_2.grand_total + cost_a2_1.grand_total
        )
        expected_hw = (
            cost_a1_1.hardware_total
            + cost_a1_2.hardware_total
            + cost_a2_1.hardware_total
        )
        expected_sw = (
            cost_a1_1.software_total
            + cost_a1_2.software_total
            + cost_a2_1.software_total
        )

        # Admin user has org-wide scope, sees all departments.
        dept_summaries = cost_service.get_department_cost_breakdown(user=admin_user)

        # Find dept_a in the results.
        dept_a_summary = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_a"].id
        )

        assert dept_a_summary.grand_total == expected_grand
        assert dept_a_summary.hardware_total == expected_hw
        assert dept_a_summary.software_total == expected_sw
        assert dept_a_summary.grand_total == Decimal("5300.00")

    def test_department_with_no_configured_positions_returns_zero(
        self,
        app,
        admin_user,
        sample_org,
    ):
        """
        A department whose positions have zero requirements should
        report $0.00 totals without errors.
        """
        dept_summaries = cost_service.get_department_cost_breakdown(user=admin_user)

        dept_b_summary = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_b"].id
        )

        assert dept_b_summary.grand_total == Decimal("0.00")
        assert dept_b_summary.hardware_total == Decimal("0.00")
        assert dept_b_summary.software_total == Decimal("0.00")

    def test_department_metadata_counts_are_correct(
        self,
        app,
        admin_user,
        sample_org,
    ):
        """
        Verify position_count, division_count, and total_authorized
        reflect the actual organizational structure.

        dept_a: 2 divisions (div_a1, div_a2), 3 positions
                (pos_a1_1=3, pos_a1_2=5, pos_a2_1=2)
                total_authorized = 10.
        dept_b: 2 divisions (div_b1, div_b2), 3 positions
                (pos_b1_1=4, pos_b1_2=1, pos_b2_1=6)
                total_authorized = 11.
        """
        dept_summaries = cost_service.get_department_cost_breakdown(user=admin_user)

        dept_a = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_a"].id
        )
        dept_b = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_b"].id
        )

        assert dept_a.division_count == 2
        assert dept_a.position_count == 3
        assert dept_a.total_authorized == 10

        assert dept_b.division_count == 2
        assert dept_b.position_count == 3
        assert dept_b.total_authorized == 11


# =====================================================================
# 2.8 Department average cost per person
# =====================================================================


class TestDepartmentAverageCostPerPerson:
    """
    Verify ``get_department_average_cost_per_person()`` which
    reports average, min, and max per-person costs across all
    configured positions in a department.
    """

    def test_avg_min_max_with_multiple_configured_positions(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
    ):
        """
        Configure two positions with different per-person costs.

        pos_a1_1 (authorized=3): 1x laptop ($1,200) -> per_person = $1,200
        pos_a1_2 (authorized=5): 1x sw_e5  ($400)   -> per_person = $400

        avg = ($1,200 + $400) / 2 = $800
        min = $400
        max = $1,200
        configured_count = 2
        """
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )
        create_sw_requirement(
            position=sample_org["pos_a1_2"],
            software=sample_catalog["sw_office_e5"],
            quantity=1,
        )

        stats = cost_service.get_department_average_cost_per_person(
            sample_org["dept_a"].id
        )

        assert stats is not None
        assert stats["configured_count"] == 2
        assert stats["avg_per_person"] == Decimal("800.00")
        assert stats["min_per_person"] == Decimal("400.00")
        assert stats["max_per_person"] == Decimal("1200.00")

    def test_unconfigured_positions_excluded_from_average(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        Only positions with at least one requirement are included
        in the average.  pos_a1_2 and pos_a2_1 have no requirements
        and should be excluded.

        pos_a1_1 (authorized=3): 1x laptop ($1,200) -> per_person = $1,200
        pos_a1_2: no requirements (excluded)
        pos_a2_1: no requirements (excluded)

        configured_count = 1
        avg = min = max = $1,200
        """
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )

        stats = cost_service.get_department_average_cost_per_person(
            sample_org["dept_a"].id
        )

        assert stats is not None
        assert stats["configured_count"] == 1
        assert stats["avg_per_person"] == Decimal("1200.00")
        assert stats["min_per_person"] == Decimal("1200.00")
        assert stats["max_per_person"] == Decimal("1200.00")

    def test_no_configured_positions_returns_none(
        self,
        app,
        sample_org,
    ):
        """
        A department with zero configured positions returns None,
        not an empty dict or a division-by-zero error.
        """
        stats = cost_service.get_department_average_cost_per_person(
            sample_org["dept_a"].id
        )
        assert stats is None

    def test_nonexistent_department_returns_none(self, app):
        """
        A department ID that does not exist should return None
        because no positions will be found.
        """
        stats = cost_service.get_department_average_cost_per_person(999999)
        assert stats is None


# =====================================================================
# 2.9 Cross-level aggregation consistency (the CIO check)
# =====================================================================


class TestCrossLevelAggregationConsistency:
    """
    The most important property of the cost system: numbers
    computed at different levels must agree.

    position totals -> division totals -> department totals.

    If the CIO sees $5,300 on the department report and $5,301
    when they add up the positions, the system loses credibility.
    """

    def test_dept_grand_total_equals_sum_of_division_grand_totals(
        self,
        app,
        admin_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
    ):
        """
        Give positions in different divisions of the same department
        requirements, then verify:

        dept.grand_total == div_a1.grand_total + div_a2.grand_total

        pos_a1_1: 1x laptop_power ($2,400), authorized=3 -> $7,200
        pos_a1_2: 1x sw_e3 ($200),          authorized=5 -> $1,000
        pos_a2_1: 1x monitor ($350),         authorized=2 -> $700

        div_a1 total = $7,200 + $1,000 = $8,200
        div_a2 total = $700
        dept_a total = $8,900
        """
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_power"],
            quantity=1,
        )
        create_sw_requirement(
            position=sample_org["pos_a1_2"],
            software=sample_catalog["sw_office_e3"],
            quantity=1,
        )
        create_hw_requirement(
            position=sample_org["pos_a2_1"],
            hardware=sample_catalog["hw_monitor_24"],
            quantity=1,
        )

        # Division-level.
        div_a1_cost = cost_service.get_division_cost_breakdown(sample_org["div_a1"].id)
        div_a2_cost = cost_service.get_division_cost_breakdown(sample_org["div_a2"].id)

        # Department-level.
        dept_summaries = cost_service.get_department_cost_breakdown(user=admin_user)
        dept_a = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_a"].id
        )

        # The critical assertion: department = sum of divisions.
        assert dept_a.grand_total == (div_a1_cost.grand_total + div_a2_cost.grand_total)
        assert dept_a.hardware_total == (
            div_a1_cost.hardware_total + div_a2_cost.hardware_total
        )
        assert dept_a.software_total == (
            div_a1_cost.software_total + div_a2_cost.software_total
        )

        # Sanity-check the absolute values.
        assert div_a1_cost.grand_total == Decimal("8200.00")
        assert div_a2_cost.grand_total == Decimal("700.00")
        assert dept_a.grand_total == Decimal("8900.00")

    def test_all_departments_sum_to_org_totals(
        self,
        app,
        admin_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        When requirements exist in multiple departments, the sum
        of all department grand_totals should equal what an org-wide
        report would show.

        pos_a1_1 (dept_a): 1x laptop ($1,200), authorized=3 -> $3,600
        pos_b1_1 (dept_b): 1x monitor ($350),  authorized=4 -> $1,400

        dept_a total = $3,600
        dept_b total = $1,400
        org total    = $5,000
        """
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )
        create_hw_requirement(
            position=sample_org["pos_b1_1"],
            hardware=sample_catalog["hw_monitor_24"],
            quantity=1,
        )

        dept_summaries = cost_service.get_department_cost_breakdown(user=admin_user)

        # Find our two test departments within the full results
        # (which may include seed data departments).
        dept_a_summary = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_a"].id
        )
        dept_b_summary = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_b"].id
        )

        # Assert the test department values we control.
        assert dept_a_summary.grand_total == Decimal("3600.00")
        assert dept_b_summary.grand_total == Decimal("1400.00")

        # Structural consistency: org total = sum of ALL department totals.
        org_grand_total = sum(d.grand_total for d in dept_summaries)
        org_hw_total = sum(d.hardware_total for d in dept_summaries)
        org_sw_total = sum(d.software_total for d in dept_summaries)

        assert org_grand_total == org_hw_total + org_sw_total

        # Our two test departments contribute $5,000 to the org total.
        # The org total may be higher if seed data positions have
        # pre-existing requirements, but the identity must hold.
        assert org_grand_total >= Decimal("5000.00")

    def test_tenant_software_does_not_break_aggregation_consistency(
        self,
        app,
        admin_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
        create_sw_coverage,
    ):
        """
        Tenant software with rounding can produce penny discrepancies
        if not handled correctly.  Verify that even with tenant costs,
        the department total still equals the sum of its division totals.

        pos_a1_1: 1x laptop ($1,200) + 1x sw_antivirus (tenant, org cov)
        pos_a2_1: 1x sw_antivirus (tenant, org cov)

        The tenant per-person cost involves rounding, but since both
        positions use the same rounded per-person figure, the division
        and department sums should still be consistent.
        """
        from sqlalchemy import func as sa_func

        sw_av = sample_catalog["sw_antivirus"]
        create_sw_coverage(software=sw_av, scope_type="organization")

        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )
        create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sw_av,
            quantity=1,
        )
        create_sw_requirement(
            position=sample_org["pos_a2_1"],
            software=sw_av,
            quantity=1,
        )

        # Division-level.
        div_a1_cost = cost_service.get_division_cost_breakdown(sample_org["div_a1"].id)
        div_a2_cost = cost_service.get_division_cost_breakdown(sample_org["div_a2"].id)

        # Department-level.
        dept_summaries = cost_service.get_department_cost_breakdown(user=admin_user)
        dept_a = next(
            s for s in dept_summaries if s.department_id == sample_org["dept_a"].id
        )

        # Consistency: department = sum of divisions.
        assert dept_a.grand_total == (div_a1_cost.grand_total + div_a2_cost.grand_total)
        assert dept_a.hardware_total == (
            div_a1_cost.hardware_total + div_a2_cost.hardware_total
        )
        assert dept_a.software_total == (
            div_a1_cost.software_total + div_a2_cost.software_total
        )

        # The tenant per-person share should be the same for both positions
        # since they share the same org-wide coverage.
        actual_headcount = (
            db.session.query(sa_func.sum(Position.authorized_count))
            .filter(Position.is_active == True)  # noqa: E712
            .scalar()
        )
        tenant_per_person = (
            Decimal("50000.00") / Decimal(str(actual_headcount))
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # pos_a1_1 sw: tenant_per_person * 3 = tenant share for this position.
        # pos_a2_1 sw: tenant_per_person * 2 = tenant share for that position.
        expected_sw_total = tenant_per_person * Decimal(
            "3"
        ) + tenant_per_person * Decimal(  # pos_a1_1 in div_a1
            "2"
        )  # pos_a2_1 in div_a2
        assert dept_a.software_total == expected_sw_total
