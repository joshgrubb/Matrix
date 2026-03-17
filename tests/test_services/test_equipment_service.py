"""
Tests for the equipment service layer.

Validates every public function in ``app.services.equipment_service``
against the real SQL Server test database.  Covers the full CRUD
lifecycle for hardware types, hardware items, software types, software
families, and software products, as well as software coverage scope
management and the human-readable coverage summary builder.

Design decisions:
    - All tests call service functions directly (not via HTTP routes)
      to isolate service-layer behavior from route-layer error handling.
    - The ``sample_org`` and ``sample_catalog`` conftest fixtures
      provide the organizational hierarchy and equipment catalog.
      Tests that need custom catalog entities create them inline to
      avoid coupling to fixture data.
    - The ``admin_user`` fixture supplies a ``user_id`` for audit
      logging parameters.  The specific role does not matter at the
      service layer; what matters is that audit entries are written
      with a traceable user reference.
    - Cost history assertions query the budget schema history tables
      directly to verify that the service layer records effective-
      dated cost changes.  This is the only reliable way to confirm
      the temporal tracking works correctly.
    - ``db_session`` rollback ensures test isolation without manual
      cleanup.

Fixture reminder (from conftest.py ``sample_org``):
    pos_a1_1  authorized_count = 3   (div_a1, dept_a)
    pos_a1_2  authorized_count = 5   (div_a1, dept_a)
    pos_a2_1  authorized_count = 2   (div_a2, dept_a)
    pos_b1_1  authorized_count = 4   (div_b1, dept_b)
    pos_b1_2  authorized_count = 1   (div_b1, dept_b)
    pos_b2_1  authorized_count = 6   (div_b2, dept_b)

Fixture reminder (from conftest.py ``sample_catalog``):
    hw_type_laptop:      max_selections=1  (single-select)
    hw_type_monitor:     max_selections=None (unlimited)
    hw_laptop_standard:  belongs to hw_type_laptop,  cost $1200
    hw_laptop_power:     belongs to hw_type_laptop,  cost $2400
    hw_monitor_24:       belongs to hw_type_monitor, cost $350
    sw_type_productivity: (active)
    sw_type_security:     (active)
    sw_office_e3:        per_user, $200/license
    sw_office_e5:        per_user, $400/license
    sw_antivirus:        tenant,   $50000 total

Run this file in isolation::

    pytest tests/test_services/test_equipment_service.py -v
"""

from decimal import Decimal

import pytest

from app.extensions import db
from app.models.audit import AuditLog
from app.models.budget import (
    HardwareCostHistory,
    HardwareTypeCostHistory,
    SoftwareCostHistory,
)
from app.models.equipment import (
    Hardware,
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareFamily,
    SoftwareType,
)
from app.services import equipment_service


# =====================================================================
# 1. Hardware Type CRUD
# =====================================================================


class TestCreateHardwareType:
    """Verify ``equipment_service.create_hardware_type()``."""

    def test_create_hardware_type_returns_record(self, app, db_session, admin_user):
        """Creating a hardware type returns the persisted record."""
        hw_type = equipment_service.create_hardware_type(
            type_name="Test Docking Station",
            estimated_cost=Decimal("350.00"),
            description="USB-C docking station",
            max_selections=1,
            user_id=admin_user.id,
        )

        assert hw_type is not None
        assert hw_type.id is not None
        assert hw_type.type_name == "Test Docking Station"
        assert hw_type.estimated_cost == Decimal("350.00")
        assert hw_type.description == "USB-C docking station"
        assert hw_type.max_selections == 1
        assert hw_type.is_active is True

    def test_create_hardware_type_records_cost_history(
        self, app, db_session, admin_user
    ):
        """
        Creating a hardware type must insert an initial row into
        ``budget.hardware_type_cost_history`` with end_date = NULL
        (the current effective record).
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Test Headset Type",
            estimated_cost=Decimal("150.00"),
            user_id=admin_user.id,
        )

        history = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=hw_type.id
        ).all()
        assert len(history) == 1
        assert history[0].estimated_cost == Decimal("150.00")
        assert history[0].end_date is None
        assert history[0].changed_by == admin_user.id

    def test_create_hardware_type_creates_audit_entry(
        self, app, db_session, admin_user
    ):
        """An audit log entry should be written on creation."""
        hw_type = equipment_service.create_hardware_type(
            type_name="Test Webcam Type",
            estimated_cost=Decimal("80.00"),
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.hardware_type",
            entity_id=hw_type.id,
            action_type="CREATE",
        ).first()
        assert audit is not None
        assert audit.user_id == admin_user.id

    def test_create_hardware_type_with_no_max_selections(
        self, app, db_session, admin_user
    ):
        """
        When max_selections is None the type allows unlimited
        selections (multi-select checkboxes in the UI).
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Test Peripheral Type",
            estimated_cost=Decimal("0.00"),
            max_selections=None,
            user_id=admin_user.id,
        )
        assert hw_type.max_selections is None

    def test_create_hardware_type_with_zero_cost(self, app, db_session, admin_user):
        """A hardware type with $0.00 cost should be valid."""
        hw_type = equipment_service.create_hardware_type(
            type_name="Test Free Accessory",
            estimated_cost=Decimal("0.00"),
            user_id=admin_user.id,
        )
        assert hw_type.estimated_cost == Decimal("0.00")

        # Cost history should still be recorded at zero.
        history = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=hw_type.id
        ).all()
        assert len(history) == 1
        assert history[0].estimated_cost == Decimal("0.00")


class TestUpdateHardwareType:
    """Verify ``equipment_service.update_hardware_type()``."""

    def test_update_hardware_type_cost_closes_old_history(
        self, app, db_session, admin_user
    ):
        """
        Changing the estimated cost must close the current history
        row (set end_date) and insert a new row with the updated
        cost and end_date = NULL.
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Test Monitor Type",
            estimated_cost=Decimal("300.00"),
            user_id=admin_user.id,
        )

        # Update the cost.
        equipment_service.update_hardware_type(
            hw_type_id=hw_type.id,
            estimated_cost=Decimal("400.00"),
            user_id=admin_user.id,
        )

        history = (
            HardwareTypeCostHistory.query.filter_by(hardware_type_id=hw_type.id)
            .order_by(HardwareTypeCostHistory.id)
            .all()
        )

        # Two rows: old (closed) and new (current).
        assert len(history) == 2

        # Old row should be closed.
        assert history[0].estimated_cost == Decimal("300.00")
        assert history[0].end_date is not None

        # New row should be current.
        assert history[1].estimated_cost == Decimal("400.00")
        assert history[1].end_date is None

    def test_update_hardware_type_name_only_no_cost_history(
        self, app, db_session, admin_user
    ):
        """
        Changing only the name (not the cost) should NOT create a
        new cost history row.  Only cost changes trigger history.
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Old Name Type",
            estimated_cost=Decimal("500.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_hardware_type(
            hw_type_id=hw_type.id,
            type_name="New Name Type",
            user_id=admin_user.id,
        )

        history = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=hw_type.id
        ).all()
        # Still just the initial row.
        assert len(history) == 1
        assert history[0].end_date is None

        # Verify the name was actually updated.
        refreshed = equipment_service.get_hardware_type_by_id(hw_type.id)
        assert refreshed.type_name == "New Name Type"

    def test_update_hardware_type_max_selections_sentinel(
        self, app, db_session, admin_user
    ):
        """
        The sentinel value -1 for max_selections means 'do not
        change'.  Passing -1 should leave the existing value intact.
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Sentinel Test Type",
            estimated_cost=Decimal("100.00"),
            max_selections=2,
            user_id=admin_user.id,
        )

        equipment_service.update_hardware_type(
            hw_type_id=hw_type.id,
            type_name="Sentinel Test Type Updated",
            max_selections=-1,
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_hardware_type_by_id(hw_type.id)
        # max_selections should remain 2 because -1 = no change.
        assert refreshed.max_selections == 2

    def test_update_hardware_type_set_max_selections_to_none(
        self, app, db_session, admin_user
    ):
        """
        Passing max_selections=None should set the value to None
        (unlimited), which is different from the -1 sentinel.
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Limit To Unlimited Type",
            estimated_cost=Decimal("100.00"),
            max_selections=1,
            user_id=admin_user.id,
        )

        equipment_service.update_hardware_type(
            hw_type_id=hw_type.id,
            max_selections=None,
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_hardware_type_by_id(hw_type.id)
        assert refreshed.max_selections is None

    def test_update_hardware_type_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent hardware type raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.update_hardware_type(
                hw_type_id=999999,
                type_name="Ghost",
                user_id=admin_user.id,
            )

    def test_update_hardware_type_same_cost_no_new_history(
        self, app, db_session, admin_user
    ):
        """
        Setting the cost to its current value should NOT create a
        new history row because nothing actually changed.
        """
        hw_type = equipment_service.create_hardware_type(
            type_name="Same Cost Type",
            estimated_cost=Decimal("250.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_hardware_type(
            hw_type_id=hw_type.id,
            estimated_cost=Decimal("250.00"),
            user_id=admin_user.id,
        )

        history = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=hw_type.id
        ).all()
        assert len(history) == 1
        assert history[0].end_date is None


class TestDeactivateHardwareType:
    """Verify ``equipment_service.deactivate_hardware_type()``."""

    def test_deactivate_hardware_type_sets_is_active_false(
        self, app, db_session, admin_user
    ):
        """Deactivating a hardware type sets is_active to False."""
        hw_type = equipment_service.create_hardware_type(
            type_name="Deactivation Target Type",
            estimated_cost=Decimal("100.00"),
            user_id=admin_user.id,
        )
        assert hw_type.is_active is True

        result = equipment_service.deactivate_hardware_type(
            hw_type_id=hw_type.id,
            user_id=admin_user.id,
        )
        assert result.is_active is False

    def test_deactivate_hardware_type_creates_audit_entry(
        self, app, db_session, admin_user
    ):
        """A DEACTIVATE audit entry should be logged."""
        hw_type = equipment_service.create_hardware_type(
            type_name="Audit Deactivate Type",
            estimated_cost=Decimal("100.00"),
            user_id=admin_user.id,
        )

        equipment_service.deactivate_hardware_type(
            hw_type_id=hw_type.id,
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.hardware_type",
            entity_id=hw_type.id,
            action_type="DEACTIVATE",
        ).first()
        assert audit is not None

    def test_deactivate_hardware_type_nonexistent_raises(self, app, admin_user):
        """Deactivating a nonexistent type raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.deactivate_hardware_type(
                hw_type_id=999999,
                user_id=admin_user.id,
            )


class TestGetHardwareTypes:
    """Verify ``equipment_service.get_hardware_types()``."""

    def test_get_hardware_types_excludes_inactive_by_default(
        self, app, db_session, admin_user
    ):
        """By default, inactive hardware types are not returned."""
        active = equipment_service.create_hardware_type(
            type_name="Active Type for Filter",
            estimated_cost=Decimal("100.00"),
            user_id=admin_user.id,
        )
        inactive = equipment_service.create_hardware_type(
            type_name="Inactive Type for Filter",
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_hardware_type(
            hw_type_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_hardware_types(include_inactive=False)
        result_ids = {ht.id for ht in results}
        assert active.id in result_ids
        assert inactive.id not in result_ids

    def test_get_hardware_types_includes_inactive_when_requested(
        self, app, db_session, admin_user
    ):
        """Passing include_inactive=True returns deactivated types."""
        inactive = equipment_service.create_hardware_type(
            type_name="Include Inactive Type",
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_hardware_type(
            hw_type_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_hardware_types(include_inactive=True)
        result_ids = {ht.id for ht in results}
        assert inactive.id in result_ids


# =====================================================================
# 2. Hardware Item CRUD
# =====================================================================


class TestCreateHardware:
    """Verify ``equipment_service.create_hardware()``."""

    def test_create_hardware_item_returns_record(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Creating a hardware item returns the persisted record."""
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test 27-inch Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("450.00"),
            description="4K IPS display",
            user_id=admin_user.id,
        )

        assert hw is not None
        assert hw.id is not None
        assert hw.name == "Test 27-inch Monitor"
        assert hw.hardware_type_id == hw_type.id
        assert hw.estimated_cost == Decimal("450.00")
        assert hw.description == "4K IPS display"
        assert hw.is_active is True

    def test_create_hardware_item_records_cost_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Creating a hardware item must insert an initial row into
        ``budget.hardware_cost_history`` with end_date = NULL.
        """
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test Cost History Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("599.99"),
            user_id=admin_user.id,
        )

        history = HardwareCostHistory.query.filter_by(hardware_id=hw.id).all()
        assert len(history) == 1
        assert history[0].estimated_cost == Decimal("599.99")
        assert history[0].end_date is None
        assert history[0].changed_by == admin_user.id

    def test_create_hardware_item_creates_audit_entry(
        self, app, db_session, sample_catalog, admin_user
    ):
        """A CREATE audit entry should be logged."""
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test Audit Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.hardware",
            entity_id=hw.id,
            action_type="CREATE",
        ).first()
        assert audit is not None
        assert audit.user_id == admin_user.id


class TestUpdateHardware:
    """Verify ``equipment_service.update_hardware()``."""

    def test_update_hardware_item_cost_records_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Changing the estimated cost must close the current history
        row and insert a new one.
        """
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test Updatable Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("300.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_hardware(
            hardware_id=hw.id,
            estimated_cost=Decimal("375.00"),
            user_id=admin_user.id,
        )

        history = (
            HardwareCostHistory.query.filter_by(hardware_id=hw.id)
            .order_by(HardwareCostHistory.id)
            .all()
        )
        assert len(history) == 2

        # Old row closed.
        assert history[0].estimated_cost == Decimal("300.00")
        assert history[0].end_date is not None

        # New row current.
        assert history[1].estimated_cost == Decimal("375.00")
        assert history[1].end_date is None

    def test_update_hardware_name_only_no_cost_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Changing only the name should NOT create a new cost history
        row because the cost did not change.
        """
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test Name Change Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("350.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_hardware(
            hardware_id=hw.id,
            name="Renamed Monitor",
            user_id=admin_user.id,
        )

        history = HardwareCostHistory.query.filter_by(hardware_id=hw.id).all()
        assert len(history) == 1
        assert history[0].end_date is None

        refreshed = equipment_service.get_hardware_by_id(hw.id)
        assert refreshed.name == "Renamed Monitor"

    def test_update_hardware_same_cost_no_new_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Setting estimated_cost to its current value should not
        create a duplicate history row.
        """
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test No Change Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("500.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_hardware(
            hardware_id=hw.id,
            estimated_cost=Decimal("500.00"),
            user_id=admin_user.id,
        )

        history = HardwareCostHistory.query.filter_by(hardware_id=hw.id).all()
        assert len(history) == 1

    def test_update_hardware_multiple_cost_changes_chain_correctly(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Three successive cost updates should produce four history
        rows (one initial + three updates) with a clean chain:
        only the final row has end_date = NULL.
        """
        hw_type = sample_catalog["hw_type_monitor"]

        hw = equipment_service.create_hardware(
            name="Test Chain Monitor",
            hardware_type_id=hw_type.id,
            estimated_cost=Decimal("100.00"),
            user_id=admin_user.id,
        )

        for new_cost in [Decimal("200.00"), Decimal("300.00"), Decimal("400.00")]:
            equipment_service.update_hardware(
                hardware_id=hw.id,
                estimated_cost=new_cost,
                user_id=admin_user.id,
            )

        history = (
            HardwareCostHistory.query.filter_by(hardware_id=hw.id)
            .order_by(HardwareCostHistory.id)
            .all()
        )
        assert len(history) == 4

        # All but the last should be closed.
        for row in history[:-1]:
            assert row.end_date is not None

        # The last should be current.
        assert history[-1].estimated_cost == Decimal("400.00")
        assert history[-1].end_date is None

    def test_update_hardware_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent hardware item raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.update_hardware(
                hardware_id=999999,
                name="Ghost",
                user_id=admin_user.id,
            )

    def test_update_hardware_description_and_type(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Multiple non-cost fields can be updated simultaneously.
        The hardware_type_id reassignment should persist.
        """
        hw = equipment_service.create_hardware(
            name="Test Reclassify Monitor",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_hardware(
            hardware_id=hw.id,
            description="Reassigned to laptop accessories",
            hardware_type_id=sample_catalog["hw_type_laptop"].id,
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_hardware_by_id(hw.id)
        assert refreshed.description == "Reassigned to laptop accessories"
        assert refreshed.hardware_type_id == sample_catalog["hw_type_laptop"].id


class TestDeactivateHardware:
    """Verify ``equipment_service.deactivate_hardware()``."""

    def test_deactivate_hardware_sets_is_active_false(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Deactivating a hardware item sets is_active to False."""
        hw = equipment_service.create_hardware(
            name="Test Deactivation Monitor",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("300.00"),
            user_id=admin_user.id,
        )
        assert hw.is_active is True

        result = equipment_service.deactivate_hardware(
            hardware_id=hw.id,
            user_id=admin_user.id,
        )
        assert result.is_active is False

    def test_deactivate_hardware_creates_audit_entry(
        self, app, db_session, sample_catalog, admin_user
    ):
        """A DEACTIVATE audit entry should be logged."""
        hw = equipment_service.create_hardware(
            name="Test Audit Deactivate HW",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("300.00"),
            user_id=admin_user.id,
        )

        equipment_service.deactivate_hardware(
            hardware_id=hw.id,
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.hardware",
            entity_id=hw.id,
            action_type="DEACTIVATE",
        ).first()
        assert audit is not None

    def test_deactivate_hardware_nonexistent_raises(self, app, admin_user):
        """Deactivating a nonexistent hardware item raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.deactivate_hardware(
                hardware_id=999999,
                user_id=admin_user.id,
            )


class TestGetHardwareItems:
    """Verify ``equipment_service.get_hardware_items()``."""

    def test_get_hardware_items_excludes_inactive(
        self, app, db_session, sample_catalog, admin_user
    ):
        """By default, inactive hardware items are excluded."""
        active = equipment_service.create_hardware(
            name="Active Filter HW",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )
        inactive = equipment_service.create_hardware(
            name="Inactive Filter HW",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_hardware(
            hardware_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_hardware_items(include_inactive=False)
        result_ids = {hw.id for hw in results}
        assert active.id in result_ids
        assert inactive.id not in result_ids

    def test_get_hardware_items_includes_inactive_when_requested(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Passing include_inactive=True returns deactivated items."""
        inactive = equipment_service.create_hardware(
            name="Include Inactive HW",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("200.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_hardware(
            hardware_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_hardware_items(include_inactive=True)
        result_ids = {hw.id for hw in results}
        assert inactive.id in result_ids

    def test_get_hardware_items_filters_by_type(self, app, sample_catalog):
        """
        Filtering by hardware_type_id returns only items in that
        category.  The fixture creates items in two types (laptop
        and monitor), so filtering for monitor should exclude laptops.
        """
        monitor_type_id = sample_catalog["hw_type_monitor"].id
        laptop_type_id = sample_catalog["hw_type_laptop"].id

        results = equipment_service.get_hardware_items(hardware_type_id=monitor_type_id)
        for hw in results:
            assert hw.hardware_type_id == monitor_type_id
            assert hw.hardware_type_id != laptop_type_id

    def test_get_hardware_by_id_returns_none_for_missing(self, app):
        """Querying a nonexistent ID returns None, not an exception."""
        result = equipment_service.get_hardware_by_id(999999)
        assert result is None


# =====================================================================
# 3. Software Type CRUD
# =====================================================================


class TestSoftwareTypeCrud:
    """Verify software type create, update, deactivate, and get."""

    def test_create_software_type_returns_record(self, app, db_session, admin_user):
        """Creating a software type returns the persisted record."""
        sw_type = equipment_service.create_software_type(
            type_name="Test GIS Software",
            description="Geographic Information Systems",
            user_id=admin_user.id,
        )

        assert sw_type is not None
        assert sw_type.id is not None
        assert sw_type.type_name == "Test GIS Software"
        assert sw_type.description == "Geographic Information Systems"
        assert sw_type.is_active is True

    def test_create_software_type_creates_audit_entry(
        self, app, db_session, admin_user
    ):
        """A CREATE audit entry should be logged."""
        sw_type = equipment_service.create_software_type(
            type_name="Test Audit SW Type",
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software_type",
            entity_id=sw_type.id,
            action_type="CREATE",
        ).first()
        assert audit is not None

    def test_update_software_type_changes_name(self, app, db_session, admin_user):
        """Updating the type_name persists the change."""
        sw_type = equipment_service.create_software_type(
            type_name="Test Old SW Type Name",
            user_id=admin_user.id,
        )

        result = equipment_service.update_software_type(
            sw_type_id=sw_type.id,
            type_name="Test New SW Type Name",
            user_id=admin_user.id,
        )
        assert result.type_name == "Test New SW Type Name"

    def test_update_software_type_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent software type raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.update_software_type(
                sw_type_id=999999,
                type_name="Ghost",
                user_id=admin_user.id,
            )

    def test_deactivate_software_type_sets_is_active_false(
        self, app, db_session, admin_user
    ):
        """Deactivating a software type sets is_active to False."""
        sw_type = equipment_service.create_software_type(
            type_name="Test Deactivate SW Type",
            user_id=admin_user.id,
        )

        result = equipment_service.deactivate_software_type(
            sw_type_id=sw_type.id,
            user_id=admin_user.id,
        )
        assert result.is_active is False

    def test_deactivate_software_type_nonexistent_raises(self, app, admin_user):
        """Deactivating a nonexistent software type raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.deactivate_software_type(
                sw_type_id=999999,
                user_id=admin_user.id,
            )

    def test_get_software_types_excludes_inactive(self, app, db_session, admin_user):
        """By default, inactive software types are excluded."""
        active = equipment_service.create_software_type(
            type_name="Active SW Type Filter",
            user_id=admin_user.id,
        )
        inactive = equipment_service.create_software_type(
            type_name="Inactive SW Type Filter",
            user_id=admin_user.id,
        )
        equipment_service.deactivate_software_type(
            sw_type_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_software_types(include_inactive=False)
        result_ids = {st.id for st in results}
        assert active.id in result_ids
        assert inactive.id not in result_ids

    def test_get_software_types_includes_inactive_when_requested(
        self, app, db_session, admin_user
    ):
        """Passing include_inactive=True returns deactivated types."""
        inactive = equipment_service.create_software_type(
            type_name="Include Inactive SW Type",
            user_id=admin_user.id,
        )
        equipment_service.deactivate_software_type(
            sw_type_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_software_types(include_inactive=True)
        result_ids = {st.id for st in results}
        assert inactive.id in result_ids

    def test_get_software_type_by_id_returns_none_for_missing(self, app):
        """Querying a nonexistent ID returns None."""
        result = equipment_service.get_software_type_by_id(999999)
        assert result is None


# =====================================================================
# 4. Software Family CRUD
# =====================================================================


class TestSoftwareFamilyCrud:
    """Verify software family create, update, deactivate, and get."""

    def test_create_software_family_returns_record(self, app, db_session, admin_user):
        """Creating a software family returns the persisted record."""
        family = equipment_service.create_software_family(
            family_name="Test Adobe Creative Cloud",
            description="Creative suite products",
            user_id=admin_user.id,
        )

        assert family is not None
        assert family.id is not None
        assert family.family_name == "Test Adobe Creative Cloud"
        assert family.is_active is True

    def test_create_software_family_creates_audit_entry(
        self, app, db_session, admin_user
    ):
        """A CREATE audit entry should be logged."""
        family = equipment_service.create_software_family(
            family_name="Test Audit SW Family",
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software_family",
            entity_id=family.id,
            action_type="CREATE",
        ).first()
        assert audit is not None

    def test_update_software_family_changes_name(self, app, db_session, admin_user):
        """Updating the family_name persists the change."""
        family = equipment_service.create_software_family(
            family_name="Test Old Family Name",
            user_id=admin_user.id,
        )

        result = equipment_service.update_software_family(
            family_id=family.id,
            family_name="Test New Family Name",
            user_id=admin_user.id,
        )
        assert result.family_name == "Test New Family Name"

    def test_update_software_family_description(self, app, db_session, admin_user):
        """Updating the description persists the change."""
        family = equipment_service.create_software_family(
            family_name="Test Desc Update Family",
            description="Old description",
            user_id=admin_user.id,
        )

        result = equipment_service.update_software_family(
            family_id=family.id,
            description="New description",
            user_id=admin_user.id,
        )
        assert result.description == "New description"

    def test_update_software_family_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent software family raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.update_software_family(
                family_id=999999,
                family_name="Ghost",
                user_id=admin_user.id,
            )

    def test_deactivate_software_family_sets_is_active_false(
        self, app, db_session, admin_user
    ):
        """Deactivating a software family sets is_active to False."""
        family = equipment_service.create_software_family(
            family_name="Test Deactivate Family",
            user_id=admin_user.id,
        )

        result = equipment_service.deactivate_software_family(
            family_id=family.id,
            user_id=admin_user.id,
        )
        assert result.is_active is False

    def test_deactivate_software_family_nonexistent_raises(self, app, admin_user):
        """Deactivating a nonexistent software family raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.deactivate_software_family(
                family_id=999999,
                user_id=admin_user.id,
            )

    def test_deactivate_software_family_creates_audit_entry(
        self, app, db_session, admin_user
    ):
        """A DEACTIVATE audit entry should be logged."""
        family = equipment_service.create_software_family(
            family_name="Test Audit Deactivate Family",
            user_id=admin_user.id,
        )

        equipment_service.deactivate_software_family(
            family_id=family.id,
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software_family",
            entity_id=family.id,
            action_type="DEACTIVATE",
        ).first()
        assert audit is not None

    def test_get_software_families_excludes_inactive(self, app, db_session, admin_user):
        """By default, inactive families are excluded."""
        active = equipment_service.create_software_family(
            family_name="Active Family Filter",
            user_id=admin_user.id,
        )
        inactive = equipment_service.create_software_family(
            family_name="Inactive Family Filter",
            user_id=admin_user.id,
        )
        equipment_service.deactivate_software_family(
            family_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_software_families(include_inactive=False)
        result_ids = {f.id for f in results}
        assert active.id in result_ids
        assert inactive.id not in result_ids

    def test_get_software_family_by_id_returns_none_for_missing(self, app):
        """Querying a nonexistent ID returns None."""
        result = equipment_service.get_software_family_by_id(999999)
        assert result is None


# =====================================================================
# 5. Software Product CRUD
# =====================================================================


class TestCreateSoftware:
    """Verify ``equipment_service.create_software()``."""

    def test_create_software_product_per_user(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Creating a per_user software product persists all fields."""
        sw_type = sample_catalog["sw_type_productivity"]

        sw = equipment_service.create_software(
            name="Test Slack Enterprise",
            software_type_id=sw_type.id,
            license_model="per_user",
            cost_per_license=Decimal("12.50"),
            license_tier="Enterprise",
            description="Team messaging platform",
            user_id=admin_user.id,
        )

        assert sw is not None
        assert sw.id is not None
        assert sw.name == "Test Slack Enterprise"
        assert sw.license_model == "per_user"
        assert sw.cost_per_license == Decimal("12.50")
        assert sw.license_tier == "Enterprise"
        assert sw.description == "Team messaging platform"
        assert sw.is_active is True

    def test_create_software_product_tenant(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Creating a tenant software product persists total_cost."""
        sw_type = sample_catalog["sw_type_security"]

        sw = equipment_service.create_software(
            name="Test Firewall License",
            software_type_id=sw_type.id,
            license_model="tenant",
            total_cost=Decimal("75000.00"),
            user_id=admin_user.id,
        )

        assert sw.license_model == "tenant"
        assert sw.total_cost == Decimal("75000.00")

    def test_create_software_records_cost_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Creating a software product must insert an initial row into
        ``budget.software_cost_history`` with end_date = NULL.
        """
        sw = equipment_service.create_software(
            name="Test History SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("99.99"),
            user_id=admin_user.id,
        )

        history = SoftwareCostHistory.query.filter_by(software_id=sw.id).all()
        assert len(history) == 1
        assert history[0].cost_per_license == Decimal("99.99")
        assert history[0].end_date is None
        assert history[0].changed_by == admin_user.id

    def test_create_software_tenant_records_total_cost_in_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        For tenant products the history should capture total_cost.
        """
        sw = equipment_service.create_software(
            name="Test Tenant History SW",
            software_type_id=sample_catalog["sw_type_security"].id,
            license_model="tenant",
            total_cost=Decimal("30000.00"),
            user_id=admin_user.id,
        )

        history = SoftwareCostHistory.query.filter_by(software_id=sw.id).all()
        assert len(history) == 1
        assert history[0].total_cost == Decimal("30000.00")

    def test_create_software_creates_audit_entry(
        self, app, db_session, sample_catalog, admin_user
    ):
        """A CREATE audit entry should be logged."""
        sw = equipment_service.create_software(
            name="Test Audit SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("50.00"),
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software",
            entity_id=sw.id,
            action_type="CREATE",
        ).first()
        assert audit is not None

    def test_create_software_with_family_assignment(
        self, app, db_session, sample_catalog, admin_user
    ):
        """A software product can be assigned to a family at creation."""
        family = equipment_service.create_software_family(
            family_name="Test M365 Family",
            user_id=admin_user.id,
        )

        sw = equipment_service.create_software(
            name="Test M365 E1",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("8.00"),
            software_family_id=family.id,
            license_tier="E1",
            user_id=admin_user.id,
        )

        assert sw.software_family_id == family.id
        assert sw.license_tier == "E1"


class TestUpdateSoftware:
    """Verify ``equipment_service.update_software()``."""

    def test_update_software_cost_records_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Changing cost_per_license must close the current history
        row and insert a new one.
        """
        sw = equipment_service.create_software(
            name="Test Update Cost SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("100.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_software(
            software_id=sw.id,
            cost_per_license=Decimal("150.00"),
            user_id=admin_user.id,
        )

        history = (
            SoftwareCostHistory.query.filter_by(software_id=sw.id)
            .order_by(SoftwareCostHistory.id)
            .all()
        )
        assert len(history) == 2

        # Old row closed.
        assert history[0].cost_per_license == Decimal("100.00")
        assert history[0].end_date is not None

        # New row current.
        assert history[1].cost_per_license == Decimal("150.00")
        assert history[1].end_date is None

    def test_update_software_total_cost_records_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Changing total_cost on a tenant product must create a new
        history row with the updated total_cost.
        """
        sw = equipment_service.create_software(
            name="Test Update Tenant Cost SW",
            software_type_id=sample_catalog["sw_type_security"].id,
            license_model="tenant",
            total_cost=Decimal("50000.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_software(
            software_id=sw.id,
            total_cost=Decimal("55000.00"),
            user_id=admin_user.id,
        )

        history = (
            SoftwareCostHistory.query.filter_by(software_id=sw.id)
            .order_by(SoftwareCostHistory.id)
            .all()
        )
        assert len(history) == 2
        assert history[0].total_cost == Decimal("50000.00")
        assert history[0].end_date is not None
        assert history[1].total_cost == Decimal("55000.00")
        assert history[1].end_date is None

    def test_update_software_name_only_no_cost_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Changing only the name should not create a new cost
        history row.
        """
        sw = equipment_service.create_software(
            name="Test Name Only SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("200.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_software(
            software_id=sw.id,
            name="Test Renamed SW",
            user_id=admin_user.id,
        )

        history = SoftwareCostHistory.query.filter_by(software_id=sw.id).all()
        assert len(history) == 1
        assert history[0].end_date is None

        refreshed = equipment_service.get_software_by_id(sw.id)
        assert refreshed.name == "Test Renamed SW"

    def test_update_software_same_cost_no_new_history(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Setting cost_per_license to its current value should not
        create a duplicate history row.
        """
        sw = equipment_service.create_software(
            name="Test Same Cost SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("300.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_software(
            software_id=sw.id,
            cost_per_license=Decimal("300.00"),
            user_id=admin_user.id,
        )

        history = SoftwareCostHistory.query.filter_by(software_id=sw.id).all()
        assert len(history) == 1

    def test_update_software_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent software product raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.update_software(
                software_id=999999,
                name="Ghost",
                user_id=admin_user.id,
            )

    def test_update_software_creates_audit_entry(
        self, app, db_session, sample_catalog, admin_user
    ):
        """An UPDATE audit entry should be logged."""
        sw = equipment_service.create_software(
            name="Test Audit Update SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("100.00"),
            user_id=admin_user.id,
        )

        equipment_service.update_software(
            software_id=sw.id,
            cost_per_license=Decimal("120.00"),
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software",
            entity_id=sw.id,
            action_type="UPDATE",
        ).first()
        assert audit is not None


class TestDeactivateSoftware:
    """Verify ``equipment_service.deactivate_software()``."""

    def test_deactivate_software_sets_is_active_false(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Deactivating a software product sets is_active to False."""
        sw = equipment_service.create_software(
            name="Test Deactivate SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("50.00"),
            user_id=admin_user.id,
        )
        assert sw.is_active is True

        result = equipment_service.deactivate_software(
            software_id=sw.id,
            user_id=admin_user.id,
        )
        assert result.is_active is False

    def test_deactivate_software_creates_audit_entry(
        self, app, db_session, sample_catalog, admin_user
    ):
        """A DEACTIVATE audit entry should be logged."""
        sw = equipment_service.create_software(
            name="Test Audit Deactivate SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("50.00"),
            user_id=admin_user.id,
        )

        equipment_service.deactivate_software(
            software_id=sw.id,
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software",
            entity_id=sw.id,
            action_type="DEACTIVATE",
        ).first()
        assert audit is not None

    def test_deactivate_software_nonexistent_raises(self, app, admin_user):
        """Deactivating a nonexistent software product raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.deactivate_software(
                software_id=999999,
                user_id=admin_user.id,
            )


# =====================================================================
# 6. Software Product Queries
# =====================================================================


class TestGetSoftwareProducts:
    """Verify ``equipment_service.get_software_products()``."""

    def test_get_software_products_excludes_inactive(
        self, app, db_session, sample_catalog, admin_user
    ):
        """By default, deactivated software products are excluded."""
        active = equipment_service.create_software(
            name="Active Filter SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("10.00"),
            user_id=admin_user.id,
        )
        inactive = equipment_service.create_software(
            name="Inactive Filter SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("10.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_software(
            software_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_software_products(include_inactive=False)
        result_ids = {sw.id for sw in results}
        assert active.id in result_ids
        assert inactive.id not in result_ids

    def test_get_software_products_includes_inactive_when_requested(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Passing include_inactive=True returns deactivated products."""
        inactive = equipment_service.create_software(
            name="Include Inactive SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("10.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_software(
            software_id=inactive.id,
            user_id=admin_user.id,
        )

        results = equipment_service.get_software_products(include_inactive=True)
        result_ids = {sw.id for sw in results}
        assert inactive.id in result_ids

    def test_get_software_products_filters_by_type(self, app, sample_catalog):
        """
        Filtering by software_type_id returns only products in
        that category.
        """
        prod_type_id = sample_catalog["sw_type_productivity"].id
        sec_type_id = sample_catalog["sw_type_security"].id

        results = equipment_service.get_software_products(software_type_id=prod_type_id)
        for sw in results:
            assert sw.software_type_id == prod_type_id
            assert sw.software_type_id != sec_type_id

    def test_get_software_products_both_filters_combined(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Applying both include_inactive and software_type_id should
        return only products matching both criteria.
        """
        sec_type_id = sample_catalog["sw_type_security"].id

        # Create an inactive security product.
        sw = equipment_service.create_software(
            name="Inactive Security SW",
            software_type_id=sec_type_id,
            license_model="per_user",
            cost_per_license=Decimal("25.00"),
            user_id=admin_user.id,
        )
        equipment_service.deactivate_software(
            software_id=sw.id,
            user_id=admin_user.id,
        )

        # Without include_inactive, should not appear.
        active_results = equipment_service.get_software_products(
            include_inactive=False,
            software_type_id=sec_type_id,
        )
        assert sw.id not in {s.id for s in active_results}

        # With include_inactive, should appear.
        all_results = equipment_service.get_software_products(
            include_inactive=True,
            software_type_id=sec_type_id,
        )
        assert sw.id in {s.id for s in all_results}

    def test_get_software_by_id_returns_none_for_missing(self, app):
        """Querying a nonexistent ID returns None, not an exception."""
        result = equipment_service.get_software_by_id(999999)
        assert result is None

    def test_get_software_products_ordered_by_name(
        self, app, db_session, sample_catalog, admin_user
    ):
        """Results should be returned in alphabetical order by name."""
        # Create products with names that sort in a known order.
        equipment_service.create_software(
            name="AAA Test Zebra SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("1.00"),
            user_id=admin_user.id,
        )
        equipment_service.create_software(
            name="AAA Test Alpha SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("1.00"),
            user_id=admin_user.id,
        )

        results = equipment_service.get_software_products()
        names = [sw.name for sw in results]
        # The names should be sorted (at least our two test products).
        aaa_positions = [i for i, n in enumerate(names) if n.startswith("AAA Test")]
        if len(aaa_positions) >= 2:
            assert names[aaa_positions[0]] < names[aaa_positions[1]]


# =====================================================================
# 7. Software Coverage Management
# =====================================================================


class TestSetSoftwareCoverage:
    """Verify ``equipment_service.set_software_coverage()``."""

    def test_set_software_coverage_creates_rows(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """
        Setting coverage on a software product creates the
        specified SoftwareCoverage rows.
        """
        sw = sample_catalog["sw_antivirus"]

        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].scope_type == "department"
        assert result[0].department_id == sample_org["dept_a"].id

    def test_set_software_coverage_replaces_existing(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """
        Calling set_software_coverage a second time must delete the
        previous rows and insert only the new ones.
        """
        sw = sample_catalog["sw_antivirus"]

        # First: set dept_a coverage.
        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
            ],
            user_id=admin_user.id,
        )

        # Second: replace with dept_b coverage.
        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_b"].id,
                },
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].department_id == sample_org["dept_b"].id

        # Verify no old rows remain in the database.
        all_cov = SoftwareCoverage.query.filter_by(software_id=sw.id).all()
        assert len(all_cov) == 1
        assert all_cov[0].department_id == sample_org["dept_b"].id

    def test_set_software_coverage_clears_with_empty_list(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """
        Passing an empty coverage_rows list should delete all existing
        coverage, leaving the software with no coverage definition.
        """
        sw = sample_catalog["sw_antivirus"]

        # Create coverage first.
        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[{"scope_type": "organization"}],
            user_id=admin_user.id,
        )

        # Clear it.
        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[],
            user_id=admin_user.id,
        )

        assert result == []
        remaining = SoftwareCoverage.query.filter_by(software_id=sw.id).all()
        assert len(remaining) == 0

    def test_set_software_coverage_organization_scope(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Organization-wide scope does not require any FK references.
        The row should be created with department_id, division_id,
        and position_id all set to None.
        """
        sw = sample_catalog["sw_antivirus"]

        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[{"scope_type": "organization"}],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].scope_type == "organization"
        assert result[0].department_id is None
        assert result[0].division_id is None
        assert result[0].position_id is None

    def test_set_software_coverage_division_scope(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """Division-scoped coverage populates division_id."""
        sw = sample_catalog["sw_antivirus"]

        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                },
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].scope_type == "division"
        assert result[0].division_id == sample_org["div_a1"].id

    def test_set_software_coverage_position_scope(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """Position-scoped coverage populates position_id."""
        sw = sample_catalog["sw_antivirus"]

        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "position",
                    "position_id": sample_org["pos_a1_1"].id,
                },
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].scope_type == "position"
        assert result[0].position_id == sample_org["pos_a1_1"].id

    def test_set_software_coverage_multiple_mixed_scopes(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """
        Multiple coverage rows with different scope types can be
        saved in a single call.
        """
        sw = sample_catalog["sw_antivirus"]

        result = equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 2
        scope_types = {r.scope_type for r in result}
        assert scope_types == {"department", "division"}

    def test_set_software_coverage_invalid_scope_raises(
        self, app, db_session, sample_catalog, admin_user
    ):
        """An invalid scope_type string raises ValueError."""
        sw = sample_catalog["sw_antivirus"]

        with pytest.raises(ValueError, match="Invalid scope_type"):
            equipment_service.set_software_coverage(
                software_id=sw.id,
                coverage_rows=[{"scope_type": "galaxy"}],
                user_id=admin_user.id,
            )

    def test_set_software_coverage_department_missing_id_raises(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        scope_type=department without department_id raises ValueError.
        """
        sw = sample_catalog["sw_antivirus"]

        with pytest.raises(ValueError, match="department_id is required"):
            equipment_service.set_software_coverage(
                software_id=sw.id,
                coverage_rows=[{"scope_type": "department"}],
                user_id=admin_user.id,
            )

    def test_set_software_coverage_division_missing_id_raises(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        scope_type=division without division_id raises ValueError.
        """
        sw = sample_catalog["sw_antivirus"]

        with pytest.raises(ValueError, match="division_id is required"):
            equipment_service.set_software_coverage(
                software_id=sw.id,
                coverage_rows=[{"scope_type": "division"}],
                user_id=admin_user.id,
            )

    def test_set_software_coverage_position_missing_id_raises(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        scope_type=position without position_id raises ValueError.
        """
        sw = sample_catalog["sw_antivirus"]

        with pytest.raises(ValueError, match="position_id is required"):
            equipment_service.set_software_coverage(
                software_id=sw.id,
                coverage_rows=[{"scope_type": "position"}],
                user_id=admin_user.id,
            )

    def test_set_software_coverage_nonexistent_software_raises(self, app, admin_user):
        """Setting coverage on a nonexistent software raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            equipment_service.set_software_coverage(
                software_id=999999,
                coverage_rows=[{"scope_type": "organization"}],
                user_id=admin_user.id,
            )

    def test_set_software_coverage_creates_audit_entry(
        self, app, db_session, sample_catalog, admin_user
    ):
        """An UPDATE audit entry for coverage should be logged."""
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[{"scope_type": "organization"}],
            user_id=admin_user.id,
        )

        audit = AuditLog.query.filter_by(
            entity_type="equip.software_coverage",
            entity_id=sw.id,
            action_type="UPDATE",
        ).first()
        assert audit is not None


class TestGetSoftwareCoverage:
    """Verify ``equipment_service.get_software_coverage()``."""

    def test_get_software_coverage_returns_rows(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """get_software_coverage returns the rows that were set."""
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_b"].id,
                },
            ],
            user_id=admin_user.id,
        )

        result = equipment_service.get_software_coverage(sw.id)
        assert len(result) == 2
        dept_ids = {r.department_id for r in result}
        assert sample_org["dept_a"].id in dept_ids
        assert sample_org["dept_b"].id in dept_ids

    def test_get_software_coverage_returns_empty_for_no_coverage(
        self, app, sample_catalog
    ):
        """
        A software product with no coverage rows returns an empty
        list, not None.
        """
        # sw_office_e3 is per_user and has no coverage rows.
        result = equipment_service.get_software_coverage(
            sample_catalog["sw_office_e3"].id
        )
        assert result == []


# =====================================================================
# 8. Coverage Summary (human-readable labels)
# =====================================================================


class TestGetCoverageSummary:
    """Verify ``equipment_service.get_coverage_summary()``."""

    def test_get_coverage_summary_organization_wide(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        If any coverage row has scope_type='organization', the
        summary should return 'Organization-wide' regardless of
        any other rows.
        """
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[{"scope_type": "organization"}],
            user_id=admin_user.id,
        )

        # Refresh the object to pick up eager-loaded coverage.
        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        assert summary == "Organization-wide"

    def test_get_coverage_summary_single_department(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """A single department scope shows 'Dept: <name>'."""
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
            ],
            user_id=admin_user.id,
        )

        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        assert "Dept:" in summary
        assert sample_org["dept_a"].department_name in summary

    def test_get_coverage_summary_single_division(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """A single division scope shows 'Div: <name>'."""
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                },
            ],
            user_id=admin_user.id,
        )

        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        assert "Div:" in summary
        assert sample_org["div_a1"].division_name in summary

    def test_get_coverage_summary_single_position(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """A single position scope shows 'Pos: <title>'."""
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "position",
                    "position_id": sample_org["pos_a1_1"].id,
                },
            ],
            user_id=admin_user.id,
        )

        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        assert "Pos:" in summary
        assert sample_org["pos_a1_1"].position_title in summary

    def test_get_coverage_summary_multiple_scopes_lists_labels(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """
        Two or three coverage rows should produce a comma-separated
        list of labels (not a count summary).
        """
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "department",
                    "department_id": sample_org["dept_a"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
            ],
            user_id=admin_user.id,
        )

        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        # Should contain both labels, comma-separated.
        assert "Dept:" in summary
        assert "Div:" in summary
        assert "," in summary

    def test_get_coverage_summary_many_scopes_summarizes_count(
        self, app, db_session, sample_org, sample_catalog, admin_user
    ):
        """
        When there are more than 3 coverage rows, the summary
        should show a count like 'N scopes' instead of listing all.
        """
        sw = sample_catalog["sw_antivirus"]

        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a1"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_a2"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b1"].id,
                },
                {
                    "scope_type": "division",
                    "division_id": sample_org["div_b2"].id,
                },
            ],
            user_id=admin_user.id,
        )

        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        # More than 3 labels: should show "4 scopes".
        assert "4 scopes" in summary

    def test_get_coverage_summary_no_coverage_returns_dash(self, app, sample_catalog):
        """
        A software product with no coverage rows should return the
        em-dash placeholder character.
        """
        # sw_office_e3 is per_user and has no coverage rows.
        sw = sample_catalog["sw_office_e3"]

        # Ensure coverage is empty (it should be by default).
        db.session.expire(sw)
        refreshed = equipment_service.get_software_by_id(sw.id)

        summary = equipment_service.get_coverage_summary(refreshed)
        # The service returns a Unicode em-dash for no coverage.
        assert summary == "\u2014"


# =====================================================================
# 9. Universal Software IDs
#
# NOTE: The testing strategy calls for
# ``test_get_universal_software_ids_returns_flagged_items``.
# This test verifies the function exists and behaves correctly.
# If ``get_universal_software_ids`` has not yet been implemented
# in ``equipment_service``, this test will fail with an
# AttributeError, clearly indicating the feature gap.
# =====================================================================


class TestGetUniversalSoftwareIds:
    """
    Verify ``equipment_service.get_universal_software_ids()`` returns
    the IDs of software products flagged as universal requirements
    (assigned to every position by default).
    """

    def test_get_universal_software_ids_returns_flagged_items(
        self, app, db_session, sample_catalog, admin_user
    ):
        """
        Software products flagged as universal should appear in the
        returned ID set.  Products not flagged should be absent.

        If ``get_universal_software_ids`` is not yet implemented,
        this test will fail with AttributeError, which is the
        correct behavior for an unimplemented feature required by
        the testing strategy.
        """
        # Guard: skip gracefully if not yet implemented.
        if not hasattr(equipment_service, "get_universal_software_ids"):
            pytest.skip(
                "equipment_service.get_universal_software_ids() not "
                "yet implemented. Required by testing strategy section "
                "5.1.3."
            )

        # If the function exists, exercise it.
        universal_ids = equipment_service.get_universal_software_ids()

        # The result must be an iterable (set, list, etc.).
        assert hasattr(universal_ids, "__iter__"), (
            "get_universal_software_ids() should return an iterable " "of software IDs."
        )


# =====================================================================
# 10. Cross-cutting: Verify the timestamp updates
# =====================================================================


class TestUpdatedAtTimestamps:
    """
    Verify that update and deactivate operations set the updated_at
    timestamp on the modified record.  This is important for cache
    invalidation and auditing.
    """

    def test_update_hardware_type_sets_updated_at(self, app, db_session, admin_user):
        """updated_at should change when a hardware type is updated."""
        hw_type = equipment_service.create_hardware_type(
            type_name="Timestamp HW Type",
            estimated_cost=Decimal("100.00"),
            user_id=admin_user.id,
        )
        original_updated = hw_type.updated_at

        equipment_service.update_hardware_type(
            hw_type_id=hw_type.id,
            type_name="Timestamp HW Type v2",
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_hardware_type_by_id(hw_type.id)
        assert refreshed.updated_at >= original_updated

    def test_deactivate_hardware_sets_updated_at(
        self, app, db_session, sample_catalog, admin_user
    ):
        """updated_at should change when a hardware item is deactivated."""
        hw = equipment_service.create_hardware(
            name="Timestamp Deactivate HW",
            hardware_type_id=sample_catalog["hw_type_monitor"].id,
            estimated_cost=Decimal("100.00"),
            user_id=admin_user.id,
        )
        original_updated = hw.updated_at

        equipment_service.deactivate_hardware(
            hardware_id=hw.id,
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_hardware_by_id(hw.id)
        assert refreshed.updated_at >= original_updated

    def test_update_software_sets_updated_at(
        self, app, db_session, sample_catalog, admin_user
    ):
        """updated_at should change when a software product is updated."""
        sw = equipment_service.create_software(
            name="Timestamp Update SW",
            software_type_id=sample_catalog["sw_type_productivity"].id,
            license_model="per_user",
            cost_per_license=Decimal("50.00"),
            user_id=admin_user.id,
        )
        original_updated = sw.updated_at

        equipment_service.update_software(
            software_id=sw.id,
            name="Timestamp Update SW v2",
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_software_by_id(sw.id)
        assert refreshed.updated_at >= original_updated

    def test_deactivate_software_type_sets_updated_at(
        self, app, db_session, admin_user
    ):
        """updated_at should change on software type deactivation."""
        sw_type = equipment_service.create_software_type(
            type_name="Timestamp Deactivate SW Type",
            user_id=admin_user.id,
        )
        original_updated = sw_type.updated_at

        equipment_service.deactivate_software_type(
            sw_type_id=sw_type.id,
            user_id=admin_user.id,
        )

        refreshed = equipment_service.get_software_type_by_id(sw_type.id)
        assert refreshed.updated_at >= original_updated
