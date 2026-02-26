"""
Tests for cost_service.calculate_position_cost().

These tests validate the core cost calculation logic that drives
all budget figures in the application.  They use the test database
with seeded organizational data.

Quick Win #10 from the 2026-02-26 code audit.
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.equipment import Hardware, HardwareType, Software, SoftwareType
from app.models.organization import Department, Division, Position
from app.models.requirement import PositionHardware, PositionSoftware
from app.services import cost_service


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

        # -- Org structure: one department → one division → one position.
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

        # -- Equipment catalog: one hardware type → one hardware item.
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

        # -- Software catalog: one type → one per-user product.
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
        Hardware cost = quantity × unit_cost per person,
        position total = per_person × authorized_count.
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
        # Per person: 2 × $1200 = $2400.
        assert line.quantity == 2
        assert line.unit_cost == Decimal("1200.00")
        assert line.line_total == Decimal("2400.00")
        # Position total: $2400 × 3 people = $7200.
        assert line.position_total == Decimal("7200.00")
        assert result.hardware_total == Decimal("7200.00")
        assert result.grand_total == Decimal("7200.00")

    def test_software_per_user_cost_calculation(self):
        """
        Per-user software cost = quantity × cost_per_license per person,
        position total = per_person × authorized_count.
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
        # Per person: 1 × $200 = $200.
        assert line.unit_cost == Decimal("200.00")
        assert line.line_total == Decimal("200.00")
        # Position total: $200 × 3 people = $600.
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

        # Hardware: 1 × $1200 × 3 = $3600.
        assert result.hardware_total == Decimal("3600.00")
        # Software: 1 × $200 × 3 = $600.
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
