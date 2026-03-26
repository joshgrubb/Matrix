"""
Unit tests for the HardwareType, Hardware, SoftwareType, Software,
SoftwareCoverage, PositionHardware, and PositionSoftware models in
the ``equip`` schema.

Verifies model relationships, column defaults, ``__repr__`` outputs,
the Hardware -> HardwareType parent chain, the Software -> SoftwareType
parent chain, SoftwareCoverage scope_type values, and the
PositionHardware / PositionSoftware junction table behavior that
underpins the requirements wizard and cost calculations.

These tests exercise the model layer directly against the real
SQL Server test database using the shared conftest fixtures.

Design decisions:
    - Tests use the ``sample_catalog`` conftest fixture for
      pre-built equipment with known costs and types.
    - Tests use the ``sample_org`` fixture when they need positions
      to attach requirements or coverage records to.
    - Junction table (PositionHardware / PositionSoftware) tests
      use the ``create_hw_requirement`` and ``create_sw_requirement``
      factory fixtures for consistency with the rest of the suite.
    - SoftwareCoverage tests verify all four documented scope_type
      values: organization, department, division, position.

Fixture reminder (from conftest.py ``sample_catalog``):
    hw_type_laptop:      max_selections=1  (single-select)
    hw_type_monitor:     max_selections=None (unlimited)
    hw_laptop_standard:  belongs to hw_type_laptop,  cost $1200
    hw_laptop_power:     belongs to hw_type_laptop,  cost $2400
    hw_monitor_24:       belongs to hw_type_monitor, cost $350
    sw_type_productivity: active software type
    sw_type_security:     active software type
    sw_office_e3:        per_user, $200/license
    sw_office_e5:        per_user, $400/license
    sw_antivirus:        tenant,   $50000 total

Run this file in isolation::

    pytest tests/test_models/test_equipment_model.py -v
"""

from decimal import Decimal

import pytest

from app.models.equipment import (
    Hardware,
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareType,
)
from app.models.requirement import PositionHardware, PositionSoftware


# =====================================================================
# 1. HardwareType model -- basic properties
# =====================================================================


class TestHardwareTypeBasicProperties:
    """Verify HardwareType column values and defaults."""

    def test_hardware_type_has_type_name(self, app, sample_catalog):
        """Fixture hardware types should have a non-empty type_name."""
        hw_type = sample_catalog["hw_type_laptop"]
        assert hw_type.type_name is not None
        assert len(hw_type.type_name) > 0

    def test_hardware_type_is_active_by_default(self, app, sample_catalog):
        """Fixture hardware types should be active."""
        assert sample_catalog["hw_type_laptop"].is_active is True
        assert sample_catalog["hw_type_monitor"].is_active is True

    def test_hardware_type_max_selections_laptop(self, app, sample_catalog):
        """
        The laptop type should have max_selections=1 (single-select),
        meaning a position can pick only one laptop model.
        """
        assert sample_catalog["hw_type_laptop"].max_selections == 1

    def test_hardware_type_max_selections_monitor_is_unlimited(
        self, app, sample_catalog
    ):
        """
        The monitor type should have max_selections=None (unlimited),
        meaning a position can pick multiple monitor models.
        """
        assert sample_catalog["hw_type_monitor"].max_selections is None

    def test_hardware_type_repr_contains_name(self, app, sample_catalog):
        """The HardwareType repr should include the type_name."""
        hw_type = sample_catalog["hw_type_laptop"]
        assert hw_type.type_name in repr(hw_type)

    def test_hardware_type_has_created_at(self, app, sample_catalog):
        """The created_at server default should be populated."""
        assert sample_catalog["hw_type_laptop"].created_at is not None


# =====================================================================
# 2. HardwareType -> Hardware relationship
# =====================================================================


class TestHardwareTypeItemsRelationship:
    """
    Verify the HardwareType.hardware_items relationship navigates
    from parent type to child items.
    """

    def test_laptop_type_has_items(self, app, sample_catalog):
        """The laptop type should have child hardware items."""
        items = sample_catalog["hw_type_laptop"].hardware_items.all()
        assert len(items) >= 2

    def test_laptop_type_contains_both_laptops(self, app, sample_catalog):
        """The laptop type should contain both standard and power models."""
        item_ids = {i.id for i in sample_catalog["hw_type_laptop"].hardware_items.all()}
        assert sample_catalog["hw_laptop_standard"].id in item_ids
        assert sample_catalog["hw_laptop_power"].id in item_ids

    def test_laptop_type_does_not_contain_monitor(self, app, sample_catalog):
        """The laptop type should not contain the monitor item."""
        item_ids = {i.id for i in sample_catalog["hw_type_laptop"].hardware_items.all()}
        assert sample_catalog["hw_monitor_24"].id not in item_ids

    def test_monitor_type_contains_monitor_item(self, app, sample_catalog):
        """The monitor type should contain the 24-inch monitor."""
        item_ids = {
            i.id for i in sample_catalog["hw_type_monitor"].hardware_items.all()
        }
        assert sample_catalog["hw_monitor_24"].id in item_ids


# =====================================================================
# 3. Hardware model -- basic properties
# =====================================================================


class TestHardwareBasicProperties:
    """Verify Hardware column values and costs."""

    def test_hardware_has_name(self, app, sample_catalog):
        """Fixture hardware items should have a non-empty name."""
        hw = sample_catalog["hw_laptop_standard"]
        assert hw.name is not None
        assert len(hw.name) > 0

    def test_hardware_estimated_cost_is_decimal(self, app, sample_catalog):
        """estimated_cost should be a Decimal for precision."""
        hw = sample_catalog["hw_laptop_standard"]
        assert isinstance(hw.estimated_cost, Decimal)

    def test_hardware_standard_laptop_cost(self, app, sample_catalog):
        """The standard laptop should cost $1,200.00."""
        assert sample_catalog["hw_laptop_standard"].estimated_cost == Decimal("1200.00")

    def test_hardware_power_laptop_cost(self, app, sample_catalog):
        """The power laptop should cost $2,400.00."""
        assert sample_catalog["hw_laptop_power"].estimated_cost == Decimal("2400.00")

    def test_hardware_monitor_cost(self, app, sample_catalog):
        """The 24-inch monitor should cost $350.00."""
        assert sample_catalog["hw_monitor_24"].estimated_cost == Decimal("350.00")

    def test_hardware_is_active_by_default(self, app, sample_catalog):
        """Fixture hardware items should be active."""
        assert sample_catalog["hw_laptop_standard"].is_active is True

    def test_hardware_repr_contains_name(self, app, sample_catalog):
        """The Hardware repr should include the item name."""
        hw = sample_catalog["hw_laptop_standard"]
        assert hw.name in repr(hw)

    def test_hardware_repr_contains_cost(self, app, sample_catalog):
        """The Hardware repr should include the cost."""
        hw = sample_catalog["hw_laptop_standard"]
        assert "1200" in repr(hw)


# =====================================================================
# 4. Hardware -> HardwareType relationship (child to parent)
# =====================================================================


class TestHardwareTypeRelationship:
    """
    Verify the Hardware.hardware_type relationship navigates
    from child item to parent type.
    """

    def test_hardware_has_hardware_type(self, app, sample_catalog):
        """A hardware item's hardware_type should not be None."""
        hw = sample_catalog["hw_laptop_standard"]
        assert hw.hardware_type is not None

    def test_hardware_type_is_correct_instance(self, app, sample_catalog):
        """The related object should be a HardwareType instance."""
        hw = sample_catalog["hw_laptop_standard"]
        assert isinstance(hw.hardware_type, HardwareType)

    def test_laptop_belongs_to_laptop_type(self, app, sample_catalog):
        """Standard laptop -> hw_type_laptop."""
        hw = sample_catalog["hw_laptop_standard"]
        assert hw.hardware_type.id == sample_catalog["hw_type_laptop"].id

    def test_monitor_belongs_to_monitor_type(self, app, sample_catalog):
        """24-inch monitor -> hw_type_monitor."""
        hw = sample_catalog["hw_monitor_24"]
        assert hw.hardware_type.id == sample_catalog["hw_type_monitor"].id


# =====================================================================
# 5. SoftwareType model -- basic properties
# =====================================================================


class TestSoftwareTypeBasicProperties:
    """Verify SoftwareType column values."""

    def test_software_type_has_type_name(self, app, sample_catalog):
        """Fixture software types should have a non-empty type_name."""
        sw_type = sample_catalog["sw_type_productivity"]
        assert sw_type.type_name is not None
        assert len(sw_type.type_name) > 0

    def test_software_type_is_active_by_default(self, app, sample_catalog):
        """Fixture software types should be active."""
        assert sample_catalog["sw_type_productivity"].is_active is True
        assert sample_catalog["sw_type_security"].is_active is True

    def test_software_type_repr_contains_name(self, app, sample_catalog):
        """The SoftwareType repr should include the type_name."""
        sw_type = sample_catalog["sw_type_productivity"]
        assert sw_type.type_name in repr(sw_type)


# =====================================================================
# 6. SoftwareType -> Software relationship
# =====================================================================


class TestSoftwareTypeItemsRelationship:
    """Verify the SoftwareType.software relationship."""

    def test_productivity_type_has_items(self, app, sample_catalog):
        """Productivity type should have child software products."""
        items = sample_catalog["sw_type_productivity"].software.all()
        assert len(items) >= 2

    def test_productivity_type_contains_office_products(self, app, sample_catalog):
        """The productivity type should include E3 and E5 products."""
        item_ids = {s.id for s in sample_catalog["sw_type_productivity"].software.all()}
        assert sample_catalog["sw_office_e3"].id in item_ids
        assert sample_catalog["sw_office_e5"].id in item_ids

    def test_security_type_contains_antivirus(self, app, sample_catalog):
        """The security type should include the antivirus product."""
        item_ids = {s.id for s in sample_catalog["sw_type_security"].software.all()}
        assert sample_catalog["sw_antivirus"].id in item_ids


# =====================================================================
# 7. Software model -- basic properties and license model
# =====================================================================


class TestSoftwareBasicProperties:
    """Verify Software column values, license model, and costs."""

    def test_software_has_name(self, app, sample_catalog):
        """Fixture software products should have a non-empty name."""
        sw = sample_catalog["sw_office_e3"]
        assert sw.name is not None
        assert len(sw.name) > 0

    def test_per_user_license_model(self, app, sample_catalog):
        """Office E3 should have license_model='per_user'."""
        assert sample_catalog["sw_office_e3"].license_model == "per_user"

    def test_tenant_license_model(self, app, sample_catalog):
        """Antivirus should have license_model='tenant'."""
        assert sample_catalog["sw_antivirus"].license_model == "tenant"

    def test_per_user_cost_per_license(self, app, sample_catalog):
        """Office E3 should have cost_per_license=$200.00."""
        assert sample_catalog["sw_office_e3"].cost_per_license == Decimal("200.00")

    def test_per_user_e5_cost(self, app, sample_catalog):
        """Office E5 should have cost_per_license=$400.00."""
        assert sample_catalog["sw_office_e5"].cost_per_license == Decimal("400.00")

    def test_tenant_total_cost(self, app, sample_catalog):
        """Antivirus should have total_cost=$50,000.00."""
        assert sample_catalog["sw_antivirus"].total_cost == Decimal("50000.00")

    def test_software_is_active_by_default(self, app, sample_catalog):
        """Fixture software products should be active."""
        assert sample_catalog["sw_office_e3"].is_active is True

    def test_software_repr_contains_name(self, app, sample_catalog):
        """The Software repr should include the product name."""
        sw = sample_catalog["sw_office_e3"]
        assert sw.name in repr(sw)

    def test_software_repr_contains_license_model(self, app, sample_catalog):
        """The Software repr should include the license model."""
        sw = sample_catalog["sw_office_e3"]
        assert "per_user" in repr(sw)

    def test_tenant_repr_contains_tenant(self, app, sample_catalog):
        """The antivirus repr should show 'tenant'."""
        sw = sample_catalog["sw_antivirus"]
        assert "tenant" in repr(sw)


# =====================================================================
# 8. Software -> SoftwareType relationship (child to parent)
# =====================================================================


class TestSoftwareTypeRelationship:
    """Verify the Software.software_type relationship."""

    def test_software_has_software_type(self, app, sample_catalog):
        """A software product's software_type should not be None."""
        sw = sample_catalog["sw_office_e3"]
        assert sw.software_type is not None

    def test_software_type_is_correct_instance(self, app, sample_catalog):
        """The related object should be a SoftwareType instance."""
        sw = sample_catalog["sw_office_e3"]
        assert isinstance(sw.software_type, SoftwareType)

    def test_office_belongs_to_productivity(self, app, sample_catalog):
        """Office E3 -> sw_type_productivity."""
        sw = sample_catalog["sw_office_e3"]
        assert sw.software_type.id == sample_catalog["sw_type_productivity"].id

    def test_antivirus_belongs_to_security(self, app, sample_catalog):
        """Antivirus -> sw_type_security."""
        sw = sample_catalog["sw_antivirus"]
        assert sw.software_type.id == sample_catalog["sw_type_security"].id


# =====================================================================
# 9. SoftwareCoverage model -- scope types
# =====================================================================


class TestSoftwareCoverageScopeTypes:
    """
    Verify that the SoftwareCoverage model accepts all documented
    scope_type values and stores the correct FK references.
    """

    def test_organization_scope_creates_successfully(
        self, app, sample_catalog, create_sw_coverage
    ):
        """An organization-scoped coverage record should persist."""
        cov = create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="organization",
        )
        assert cov.id is not None
        assert cov.scope_type == "organization"
        assert cov.department_id is None
        assert cov.division_id is None

    def test_department_scope_creates_successfully(
        self, app, sample_org, sample_catalog, create_sw_coverage
    ):
        """A department-scoped coverage should store the department_id."""
        cov = create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="department",
            department_id=sample_org["dept_a"].id,
        )
        assert cov.scope_type == "department"
        assert cov.department_id == sample_org["dept_a"].id

    def test_division_scope_creates_successfully(
        self, app, sample_org, sample_catalog, create_sw_coverage
    ):
        """A division-scoped coverage should store the division_id."""
        cov = create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="division",
            division_id=sample_org["div_a1"].id,
        )
        assert cov.scope_type == "division"
        assert cov.division_id == sample_org["div_a1"].id

    def test_position_scope_creates_successfully(
        self, app, sample_org, sample_catalog, create_sw_coverage
    ):
        """A position-scoped coverage should store the position_id."""
        cov = create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="position",
            position_id=sample_org["pos_a1_1"].id,
        )
        assert cov.scope_type == "position"
        assert cov.position_id == sample_org["pos_a1_1"].id

    def test_coverage_repr_contains_software_id(
        self, app, sample_catalog, create_sw_coverage
    ):
        """The SoftwareCoverage repr should include the software_id."""
        cov = create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="organization",
        )
        assert str(sample_catalog["sw_antivirus"].id) in repr(cov)

    def test_coverage_repr_contains_scope_type(
        self, app, sample_catalog, create_sw_coverage
    ):
        """The SoftwareCoverage repr should include the scope_type."""
        cov = create_sw_coverage(
            software=sample_catalog["sw_antivirus"],
            scope_type="organization",
        )
        assert "organization" in repr(cov)

    def test_software_coverage_relationship_from_software(
        self, app, sample_catalog, create_sw_coverage
    ):
        """
        After creating coverage, the Software.coverage relationship
        should include the new record.
        """
        sw = sample_catalog["sw_antivirus"]
        cov = create_sw_coverage(software=sw, scope_type="organization")
        cov_ids = {c.id for c in sw.coverage}
        assert cov.id in cov_ids


# =====================================================================
# 10. PositionHardware junction model
# =====================================================================


class TestPositionHardwareModel:
    """
    Verify the PositionHardware junction table model that links
    positions to hardware items with a quantity.
    """

    def test_position_hardware_stores_quantity(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """The quantity field should persist the assigned value."""
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=2,
        )
        assert req.quantity == 2

    def test_position_hardware_default_quantity_is_one(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """When quantity is not specified, it defaults to 1."""
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_monitor_24"],
        )
        assert req.quantity == 1

    def test_position_hardware_stores_notes(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """The notes field should persist the assigned value."""
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_monitor_24"],
            notes="Must be adjustable height",
        )
        assert req.notes == "Must be adjustable height"

    def test_position_hardware_has_position_relationship(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """The .position relationship should resolve to the correct Position."""
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
        )
        assert req.position is not None
        assert req.position.id == sample_org["pos_a1_1"].id

    def test_position_hardware_has_hardware_relationship(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """The .hardware relationship should resolve to the correct Hardware."""
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
        )
        assert req.hardware is not None
        assert req.hardware.id == sample_catalog["hw_laptop_standard"].id

    def test_position_hardware_repr_contains_ids(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """The repr should include position_id and hardware_id."""
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_monitor_24"],
            quantity=3,
        )
        r = repr(req)
        assert str(sample_org["pos_a1_1"].id) in r
        assert str(sample_catalog["hw_monitor_24"].id) in r
        assert "qty=3" in r

    def test_position_hardware_accessible_from_position(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """
        Position.hardware_requirements should include the newly
        created junction record.
        """
        req = create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
        )
        req_ids = {r.id for r in sample_org["pos_a1_1"].hardware_requirements.all()}
        assert req.id in req_ids


# =====================================================================
# 11. PositionSoftware junction model
# =====================================================================


class TestPositionSoftwareModel:
    """
    Verify the PositionSoftware junction table model that links
    positions to software products with a quantity.
    """

    def test_position_software_stores_quantity(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """The quantity field should persist the assigned value."""
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sample_catalog["sw_office_e3"],
            quantity=1,
        )
        assert req.quantity == 1

    def test_position_software_stores_notes(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """The notes field should persist the assigned value."""
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sample_catalog["sw_office_e3"],
            notes="E3 is sufficient for this role",
        )
        assert req.notes == "E3 is sufficient for this role"

    def test_position_software_has_position_relationship(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """The .position relationship should resolve correctly."""
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sample_catalog["sw_office_e3"],
        )
        assert req.position is not None
        assert req.position.id == sample_org["pos_a1_1"].id

    def test_position_software_has_software_relationship(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """The .software relationship should resolve correctly."""
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sample_catalog["sw_office_e3"],
        )
        assert req.software is not None
        assert req.software.id == sample_catalog["sw_office_e3"].id

    def test_position_software_repr_contains_ids(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """The repr should include position_id and software_id."""
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sample_catalog["sw_antivirus"],
            quantity=1,
        )
        r = repr(req)
        assert str(sample_org["pos_a1_1"].id) in r
        assert str(sample_catalog["sw_antivirus"].id) in r

    def test_position_software_accessible_from_position(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """
        Position.software_requirements should include the newly
        created junction record.
        """
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sample_catalog["sw_office_e3"],
        )
        req_ids = {r.id for r in sample_org["pos_a1_1"].software_requirements.all()}
        assert req.id in req_ids

    def test_software_position_software_reverse_relationship(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """
        Software.position_software should include junction records
        referencing this software product.
        """
        sw = sample_catalog["sw_office_e3"]
        req = create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sw,
        )
        req_ids = {r.id for r in sw.position_software.all()}
        assert req.id in req_ids
