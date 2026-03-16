"""
Unit tests for the requirement service layer.

Tests every public function in ``app.services.requirement_service``
against the real SQL Server test database.  Verifies CRUD operations,
bulk replace semantics, copy logic, max_selections validation, usage
counts, status tracking, division common-item suggestions, and audit
trail persistence via ``budget.requirement_history``.

Design decisions:
    - All tests call service functions directly (not via HTTP routes)
      to isolate service-layer behavior from route-layer error handling.
    - The ``sample_org`` and ``sample_catalog`` conftest fixtures
      provide the organizational hierarchy and equipment catalog.
      Tests that need pre-existing requirements use the
      ``create_hw_requirement`` / ``create_sw_requirement`` factory
      fixtures for clarity and consistency.
    - The ``admin_user`` fixture supplies a ``user_id`` for audit
      logging parameters.  The specific role does not matter at
      the service layer; what matters is that audit entries are
      written with a traceable user reference.

Fixture reminder (from conftest.py):
    hw_type_laptop:      max_selections=1  (single-select)
    hw_type_monitor:     max_selections=None (unlimited)
    hw_laptop_standard:  belongs to hw_type_laptop,  cost $1200
    hw_laptop_power:     belongs to hw_type_laptop,  cost $2400
    hw_monitor_24:       belongs to hw_type_monitor, cost $350
    sw_office_e3:        per_user, $200/license
    sw_office_e5:        per_user, $400/license
    sw_antivirus:        tenant,   $50000 total

Run this file in isolation::

    pytest tests/test_services/test_requirement_service.py -v
"""

import pytest

from app.models.budget import RequirementHistory
from app.models.requirement import PositionHardware, PositionSoftware
from app.services import requirement_service


# =====================================================================
# 1. Get hardware/software requirements (read)
# =====================================================================


class TestGetRequirements:
    """Verify the read functions return the correct records."""

    def test_get_hardware_requirements_returns_empty_for_clean_position(
        self, app, sample_org
    ):
        """A position with no requirements should return an empty list."""
        pos = sample_org["pos_a1_1"]
        result = requirement_service.get_hardware_requirements(pos.id)
        assert result == []

    def test_get_hardware_requirements_returns_all_records(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """All hardware requirements for a position are returned."""
        pos = sample_org["pos_a1_1"]
        create_hw_requirement(
            position=pos,
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )
        create_hw_requirement(
            position=pos,
            hardware=sample_catalog["hw_monitor_24"],
            quantity=2,
        )

        result = requirement_service.get_hardware_requirements(pos.id)
        assert len(result) == 2
        hw_ids = {r.hardware_id for r in result}
        assert sample_catalog["hw_laptop_standard"].id in hw_ids
        assert sample_catalog["hw_monitor_24"].id in hw_ids

    def test_get_hardware_requirements_does_not_return_other_positions(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """Requirements from a different position are not included."""
        pos_a = sample_org["pos_a1_1"]
        pos_b = sample_org["pos_a1_2"]
        create_hw_requirement(
            position=pos_a,
            hardware=sample_catalog["hw_laptop_standard"],
        )
        create_hw_requirement(
            position=pos_b,
            hardware=sample_catalog["hw_monitor_24"],
        )

        result = requirement_service.get_hardware_requirements(pos_a.id)
        assert len(result) == 1
        assert result[0].hardware_id == sample_catalog["hw_laptop_standard"].id

    def test_get_software_requirements_returns_empty_for_clean_position(
        self, app, sample_org
    ):
        """A position with no software requirements returns empty."""
        pos = sample_org["pos_a1_1"]
        result = requirement_service.get_software_requirements(pos.id)
        assert result == []

    def test_get_software_requirements_returns_all_records(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """All software requirements for a position are returned."""
        pos = sample_org["pos_a1_1"]
        create_sw_requirement(
            position=pos,
            software=sample_catalog["sw_office_e3"],
        )
        create_sw_requirement(
            position=pos,
            software=sample_catalog["sw_antivirus"],
        )

        result = requirement_service.get_software_requirements(pos.id)
        assert len(result) == 2


# =====================================================================
# 2. Add individual requirements (upsert)
# =====================================================================


class TestAddHardwareRequirement:
    """
    Verify add_hardware_requirement creates new records and
    updates existing ones (upsert behavior).
    """

    def test_add_creates_new_record(
        self, app, sample_org, sample_catalog, admin_user, db_session
    ):
        """Adding a hardware item that does not exist creates a new record."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        result = requirement_service.add_hardware_requirement(
            position_id=pos.id,
            hardware_id=hw.id,
            quantity=2,
            notes="Dual monitor setup",
            user_id=admin_user.id,
        )

        assert result is not None
        assert result.position_id == pos.id
        assert result.hardware_id == hw.id
        assert result.quantity == 2
        assert result.notes == "Dual monitor setup"

    def test_add_duplicate_updates_existing_record(
        self,
        app,
        sample_org,
        sample_catalog,
        admin_user,
        create_hw_requirement,
        db_session,
    ):
        """
        Adding the same hardware item to the same position a second
        time should update the existing record (quantity and notes),
        not create a duplicate.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        # Create initial requirement.
        original = create_hw_requirement(
            position=pos,
            hardware=hw,
            quantity=1,
            notes="Original",
        )
        original_id = original.id

        # Add again with different quantity and notes.
        updated = requirement_service.add_hardware_requirement(
            position_id=pos.id,
            hardware_id=hw.id,
            quantity=3,
            notes="Updated to triple",
            user_id=admin_user.id,
        )

        # Should be the same record, not a new one.
        assert updated.id == original_id
        assert updated.quantity == 3
        assert updated.notes == "Updated to triple"

        # Only one record should exist.
        all_reqs = PositionHardware.query.filter_by(
            position_id=pos.id, hardware_id=hw.id
        ).all()
        assert len(all_reqs) == 1

    def test_add_defaults_quantity_to_one(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """When quantity is not specified, it should default to 1."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        result = requirement_service.add_hardware_requirement(
            position_id=pos.id,
            hardware_id=hw.id,
            user_id=admin_user.id,
        )
        assert result.quantity == 1


class TestAddSoftwareRequirement:
    """Verify add_software_requirement upsert behavior."""

    def test_add_creates_new_record(self, app, sample_org, sample_catalog, admin_user):
        """Adding a new software product creates a record."""
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        result = requirement_service.add_software_requirement(
            position_id=pos.id,
            software_id=sw.id,
            quantity=1,
            notes="Standard productivity suite",
            user_id=admin_user.id,
        )

        assert result is not None
        assert result.software_id == sw.id
        assert result.quantity == 1
        assert result.notes == "Standard productivity suite"

    def test_add_duplicate_updates_existing_record(
        self,
        app,
        sample_org,
        sample_catalog,
        admin_user,
        create_sw_requirement,
        db_session,
    ):
        """Duplicate add should update, not create a second record."""
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        original = create_sw_requirement(
            position=pos,
            software=sw,
            quantity=1,
        )
        original_id = original.id

        updated = requirement_service.add_software_requirement(
            position_id=pos.id,
            software_id=sw.id,
            quantity=2,
            notes="Upgraded",
            user_id=admin_user.id,
        )

        assert updated.id == original_id
        assert updated.quantity == 2

        all_reqs = PositionSoftware.query.filter_by(
            position_id=pos.id, software_id=sw.id
        ).all()
        assert len(all_reqs) == 1


# =====================================================================
# 3. Update individual requirements
# =====================================================================


class TestUpdateRequirement:
    """Verify targeted updates to individual requirement records."""

    def test_update_hardware_quantity(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """Updating quantity changes only the quantity field."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]
        req = create_hw_requirement(
            position=pos,
            hardware=hw,
            quantity=1,
            notes="Original",
        )

        result = requirement_service.update_hardware_requirement(
            requirement_id=req.id,
            quantity=4,
            user_id=admin_user.id,
        )

        assert result.quantity == 4
        # Notes should be unchanged.
        assert result.notes == "Original"

    def test_update_hardware_notes(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """Updating notes changes only the notes field."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]
        req = create_hw_requirement(
            position=pos,
            hardware=hw,
            quantity=2,
        )

        result = requirement_service.update_hardware_requirement(
            requirement_id=req.id,
            notes="Now with adjustable arm",
            user_id=admin_user.id,
        )

        assert result.notes == "Now with adjustable arm"
        assert result.quantity == 2

    def test_update_hardware_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent requirement raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            requirement_service.update_hardware_requirement(
                requirement_id=999999,
                quantity=1,
                user_id=admin_user.id,
            )

    def test_update_software_quantity_and_notes(
        self, app, sample_org, sample_catalog, create_sw_requirement, admin_user
    ):
        """Both quantity and notes can be updated in a single call."""
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]
        req = create_sw_requirement(
            position=pos,
            software=sw,
            quantity=1,
            notes="Old",
        )

        result = requirement_service.update_software_requirement(
            requirement_id=req.id,
            quantity=5,
            notes="New note",
            user_id=admin_user.id,
        )

        assert result.quantity == 5
        assert result.notes == "New note"

    def test_update_software_nonexistent_raises(self, app, admin_user):
        """Updating a nonexistent software requirement raises."""
        with pytest.raises(ValueError, match="not found"):
            requirement_service.update_software_requirement(
                requirement_id=999999,
                quantity=1,
                user_id=admin_user.id,
            )


# =====================================================================
# 4. Remove individual requirements
# =====================================================================


class TestRemoveRequirement:
    """Verify hard-delete of individual requirement records."""

    def test_remove_hardware_deletes_record(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        admin_user,
        db_session,
    ):
        """Removing a hardware requirement hard-deletes the row."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]
        req = create_hw_requirement(position=pos, hardware=hw, quantity=1)
        req_id = req.id

        requirement_service.remove_hardware_requirement(
            requirement_id=req_id,
            user_id=admin_user.id,
        )

        assert db_session.get(PositionHardware, req_id) is None

    def test_remove_hardware_nonexistent_raises(self, app, admin_user):
        """Removing a nonexistent hardware requirement raises."""
        with pytest.raises(ValueError, match="not found"):
            requirement_service.remove_hardware_requirement(
                requirement_id=999999,
                user_id=admin_user.id,
            )

    def test_remove_software_deletes_record(
        self,
        app,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        admin_user,
        db_session,
    ):
        """Removing a software requirement hard-deletes the row."""
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]
        req = create_sw_requirement(position=pos, software=sw, quantity=1)
        req_id = req.id

        requirement_service.remove_software_requirement(
            requirement_id=req_id,
            user_id=admin_user.id,
        )

        assert db_session.get(PositionSoftware, req_id) is None

    def test_remove_software_nonexistent_raises(self, app, admin_user):
        """Removing a nonexistent software requirement raises."""
        with pytest.raises(ValueError, match="not found"):
            requirement_service.remove_software_requirement(
                requirement_id=999999,
                user_id=admin_user.id,
            )


# =====================================================================
# 5. Bulk replace -- set_position_hardware
# =====================================================================


class TestSetPositionHardware:
    """
    Verify the bulk-replace function that the wizard form POST
    delegates to.  This deletes all existing hardware requirements
    for a position and inserts the new set atomically.
    """

    def test_set_creates_records_on_empty_position(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """Setting hardware on a position with none creates records."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[{"hardware_id": hw.id, "quantity": 2}],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].hardware_id == hw.id
        assert result[0].quantity == 2

    def test_set_replaces_existing_requirements(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """
        Calling set_position_hardware replaces ALL existing
        hardware requirements, not merges.
        """
        pos = sample_org["pos_a1_1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        # Start with laptop.
        create_hw_requirement(position=pos, hardware=hw_laptop, quantity=1)

        # Replace with monitor only.
        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[{"hardware_id": hw_monitor.id, "quantity": 2}],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].hardware_id == hw_monitor.id

        # Laptop should be gone.
        all_reqs = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(all_reqs) == 1

    def test_set_empty_list_clears_all(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """Passing an empty items list removes all requirements."""
        pos = sample_org["pos_a1_1"]
        create_hw_requirement(
            position=pos,
            hardware=sample_catalog["hw_laptop_standard"],
        )

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[],
            user_id=admin_user.id,
        )

        assert result == []
        remaining = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(remaining) == 0

    def test_set_preserves_notes(self, app, sample_org, sample_catalog, admin_user):
        """Notes passed in the items dict are stored on the record."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[
                {
                    "hardware_id": hw.id,
                    "quantity": 1,
                    "notes": "Ultrawide preferred",
                }
            ],
            user_id=admin_user.id,
        )

        assert result[0].notes == "Ultrawide preferred"

    def test_set_multiple_items_different_types(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """
        Setting items from different hardware types in one call
        creates one record per item.

        Uses laptop (max_selections=1, qty=1) and monitor
        (max_selections=None, qty=2) together.
        """
        pos = sample_org["pos_a1_1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[
                {"hardware_id": hw_laptop.id, "quantity": 1},
                {"hardware_id": hw_monitor.id, "quantity": 2},
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 2
        ids = {r.hardware_id for r in result}
        assert hw_laptop.id in ids
        assert hw_monitor.id in ids

    def test_set_defaults_quantity_to_one_when_omitted(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """If quantity is not in the item dict, it defaults to 1."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[{"hardware_id": hw.id}],
            user_id=admin_user.id,
        )

        assert result[0].quantity == 1


# =====================================================================
# 6. Bulk replace -- set_position_software
# =====================================================================


class TestSetPositionSoftware:
    """
    Verify the bulk-replace function for software requirements.
    Same replace semantics as hardware but without max_selections
    validation.
    """

    def test_set_creates_records_on_empty_position(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """Setting software on a clean position creates records."""
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        result = requirement_service.set_position_software(
            position_id=pos.id,
            items=[{"software_id": sw.id, "quantity": 1}],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].software_id == sw.id

    def test_set_replaces_existing_requirements(
        self, app, sample_org, sample_catalog, create_sw_requirement, admin_user
    ):
        """Calling set replaces all existing software requirements."""
        pos = sample_org["pos_a1_1"]
        sw_e3 = sample_catalog["sw_office_e3"]
        sw_av = sample_catalog["sw_antivirus"]

        create_sw_requirement(position=pos, software=sw_e3, quantity=1)

        result = requirement_service.set_position_software(
            position_id=pos.id,
            items=[{"software_id": sw_av.id, "quantity": 1}],
            user_id=admin_user.id,
        )

        assert len(result) == 1
        assert result[0].software_id == sw_av.id

        remaining = PositionSoftware.query.filter_by(position_id=pos.id).all()
        assert len(remaining) == 1

    def test_set_empty_list_clears_all(
        self, app, sample_org, sample_catalog, create_sw_requirement, admin_user
    ):
        """Passing an empty items list removes all software requirements."""
        pos = sample_org["pos_a1_1"]
        create_sw_requirement(
            position=pos,
            software=sample_catalog["sw_office_e3"],
        )

        result = requirement_service.set_position_software(
            position_id=pos.id,
            items=[],
            user_id=admin_user.id,
        )

        assert result == []
        remaining = PositionSoftware.query.filter_by(position_id=pos.id).all()
        assert len(remaining) == 0

    def test_set_multiple_products(self, app, sample_org, sample_catalog, admin_user):
        """Multiple software products can be set in a single call."""
        pos = sample_org["pos_a1_1"]
        sw_e3 = sample_catalog["sw_office_e3"]
        sw_e5 = sample_catalog["sw_office_e5"]
        sw_av = sample_catalog["sw_antivirus"]

        result = requirement_service.set_position_software(
            position_id=pos.id,
            items=[
                {"software_id": sw_e3.id, "quantity": 1},
                {"software_id": sw_e5.id, "quantity": 1},
                {"software_id": sw_av.id, "quantity": 1},
            ],
            user_id=admin_user.id,
        )

        assert len(result) == 3


# =====================================================================
# 7. max_selections validation
# =====================================================================


class TestMaxSelectionsValidation:
    """
    The _validate_max_selections function enforces hardware type
    constraints.  hw_type_laptop has max_selections=1; hw_type_monitor
    has max_selections=None (unlimited).

    These tests call set_position_hardware because _validate_max_selections
    is a private function invoked at the start of that call.
    """

    def test_single_select_type_quantity_one_passes(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """
        A laptop (max_selections=1) with quantity=1 should be
        accepted without error.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]

        # Should not raise.
        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[{"hardware_id": hw.id, "quantity": 1}],
            user_id=admin_user.id,
        )
        assert len(result) == 1

    def test_single_select_type_quantity_exceeds_raises(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """
        A laptop (max_selections=1) with quantity=2 should raise
        ValueError because the total quantity exceeds the limit.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]

        with pytest.raises(ValueError, match="maximum total quantity"):
            requirement_service.set_position_hardware(
                position_id=pos.id,
                items=[{"hardware_id": hw.id, "quantity": 2}],
                user_id=admin_user.id,
            )

    def test_two_items_in_single_select_type_raises(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """
        Selecting both laptop_standard AND laptop_power (both
        max_selections=1 type) with quantity=1 each should raise
        because total quantity for the type would be 2.
        """
        pos = sample_org["pos_a1_1"]
        hw_std = sample_catalog["hw_laptop_standard"]
        hw_pwr = sample_catalog["hw_laptop_power"]

        with pytest.raises(ValueError, match="maximum total quantity"):
            requirement_service.set_position_hardware(
                position_id=pos.id,
                items=[
                    {"hardware_id": hw_std.id, "quantity": 1},
                    {"hardware_id": hw_pwr.id, "quantity": 1},
                ],
                user_id=admin_user.id,
            )

    def test_unlimited_type_high_quantity_passes(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """
        A monitor (max_selections=None) with quantity=10 should
        pass without error because the type is unlimited.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[{"hardware_id": hw.id, "quantity": 10}],
            user_id=admin_user.id,
        )
        assert result[0].quantity == 10

    def test_mixed_types_valid_combination_passes(
        self, app, sample_org, sample_catalog, admin_user
    ):
        """
        One laptop (qty=1, at the max_selections=1 limit) plus
        monitors (qty=3, unlimited type) should pass.
        """
        pos = sample_org["pos_a1_1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        result = requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[
                {"hardware_id": hw_laptop.id, "quantity": 1},
                {"hardware_id": hw_monitor.id, "quantity": 3},
            ],
            user_id=admin_user.id,
        )
        assert len(result) == 2

    def test_nonexistent_hardware_id_raises(self, app, sample_org, admin_user):
        """
        Referencing a hardware_id that does not exist should raise
        ValueError during validation, not a database FK error.
        """
        pos = sample_org["pos_a1_1"]

        with pytest.raises(ValueError, match="not found"):
            requirement_service.set_position_hardware(
                position_id=pos.id,
                items=[{"hardware_id": 999999, "quantity": 1}],
                user_id=admin_user.id,
            )

    def test_validation_does_not_corrupt_existing_data_on_failure(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """
        If validation fails (max_selections exceeded), the existing
        requirements should remain untouched because the service
        rolls back the transaction.
        """
        pos = sample_org["pos_a1_1"]
        hw_monitor = sample_catalog["hw_monitor_24"]
        hw_laptop = sample_catalog["hw_laptop_standard"]

        # Create a valid existing requirement.
        create_hw_requirement(position=pos, hardware=hw_monitor, quantity=1)

        # Attempt an invalid replacement (laptop qty=2 exceeds limit).
        with pytest.raises(ValueError):
            requirement_service.set_position_hardware(
                position_id=pos.id,
                items=[{"hardware_id": hw_laptop.id, "quantity": 2}],
                user_id=admin_user.id,
            )

        # Original monitor requirement should still exist.
        remaining = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(remaining) == 1
        assert remaining[0].hardware_id == hw_monitor.id


# =====================================================================
# 8. Copy requirements between positions
# =====================================================================


class TestCopyPositionRequirements:
    """
    Verify copy_position_requirements copies hardware and software
    from source to target, replacing the target's existing data.
    """

    def test_copy_hardware_only(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """
        Copying from a source that has only hardware requirements
        creates matching hardware on the target.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw = sample_catalog["hw_laptop_standard"]

        create_hw_requirement(position=source, hardware=hw, quantity=1)

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        target_reqs = requirement_service.get_hardware_requirements(target.id)
        assert len(target_reqs) == 1
        assert target_reqs[0].hardware_id == hw.id
        assert target_reqs[0].quantity == 1

    def test_copy_software_only(
        self, app, sample_org, sample_catalog, create_sw_requirement, admin_user
    ):
        """
        Copying from a source that has only software requirements
        creates matching software on the target.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        sw = sample_catalog["sw_office_e3"]

        create_sw_requirement(position=source, software=sw, quantity=1)

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        target_reqs = requirement_service.get_software_requirements(target.id)
        assert len(target_reqs) == 1
        assert target_reqs[0].software_id == sw.id

    def test_copy_both_hardware_and_software(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
        admin_user,
    ):
        """Copying transfers both hardware and software records."""
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]

        create_hw_requirement(
            position=source,
            hardware=sample_catalog["hw_laptop_standard"],
            quantity=1,
        )
        create_sw_requirement(
            position=source,
            software=sample_catalog["sw_office_e3"],
            quantity=1,
        )

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        hw_reqs = requirement_service.get_hardware_requirements(target.id)
        sw_reqs = requirement_service.get_software_requirements(target.id)
        assert len(hw_reqs) == 1
        assert len(sw_reqs) == 1

    def test_copy_replaces_existing_target_data(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """
        Copying to a target that already has requirements should
        replace them completely.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        # Source has laptop.
        create_hw_requirement(position=source, hardware=hw_laptop, quantity=1)
        # Target has monitor (should be replaced).
        create_hw_requirement(position=target, hardware=hw_monitor, quantity=2)

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        target_reqs = requirement_service.get_hardware_requirements(target.id)
        assert len(target_reqs) == 1
        assert target_reqs[0].hardware_id == hw_laptop.id

    def test_copy_preserves_notes(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """Notes from the source position are copied to the target."""
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw = sample_catalog["hw_monitor_24"]

        create_hw_requirement(
            position=source,
            hardware=hw,
            quantity=2,
            notes="Must be adjustable height",
        )

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        target_reqs = requirement_service.get_hardware_requirements(target.id)
        assert target_reqs[0].notes == "Must be adjustable height"

    def test_copy_empty_source_raises(self, app, sample_org, admin_user):
        """
        Copying from a source with zero requirements should raise
        ValueError so the caller can display a meaningful error.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]

        with pytest.raises(ValueError, match="no equipment"):
            requirement_service.copy_position_requirements(
                source_position_id=source.id,
                target_position_id=target.id,
                user_id=admin_user.id,
            )

    def test_copy_does_not_modify_source(
        self, app, sample_org, sample_catalog, create_hw_requirement, admin_user
    ):
        """The source position's requirements are unchanged after copy."""
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw = sample_catalog["hw_laptop_standard"]

        create_hw_requirement(position=source, hardware=hw, quantity=1)

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        source_reqs = requirement_service.get_hardware_requirements(source.id)
        assert len(source_reqs) == 1
        assert source_reqs[0].hardware_id == hw.id


# =====================================================================
# 9. Usage counts
# =====================================================================


class TestUsageCounts:
    """
    Verify the aggregate queries that power the "Used by N positions"
    badges on the hardware and software selection pages.
    """

    def test_hardware_usage_counts_empty_when_no_requirements(
        self, app, sample_org, sample_catalog
    ):
        """With no requirements, usage counts should be empty."""
        counts = requirement_service.get_hardware_usage_counts()
        hw = sample_catalog["hw_laptop_standard"]
        # The fixture item should not appear in counts.
        assert counts.get(hw.id, 0) == 0

    def test_hardware_usage_counts_across_positions(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """
        If two positions use the same hardware item, the usage
        count for that item should be 2.
        """
        hw = sample_catalog["hw_monitor_24"]
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=hw,
            quantity=1,
        )
        create_hw_requirement(
            position=sample_org["pos_a1_2"],
            hardware=hw,
            quantity=2,
        )

        counts = requirement_service.get_hardware_usage_counts()
        assert counts[hw.id] == 2

    def test_software_usage_counts_across_positions(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """
        If three positions use the same software product, the
        count should be 3.
        """
        sw = sample_catalog["sw_office_e3"]
        create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sw,
        )
        create_sw_requirement(
            position=sample_org["pos_a1_2"],
            software=sw,
        )
        create_sw_requirement(
            position=sample_org["pos_a2_1"],
            software=sw,
        )

        counts = requirement_service.get_software_usage_counts()
        assert counts[sw.id] == 3

    def test_usage_count_does_not_double_count_quantity(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """
        A position with quantity=5 for a monitor should still
        count as 1 position using that hardware, not 5.
        """
        hw = sample_catalog["hw_monitor_24"]
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=hw,
            quantity=5,
        )

        counts = requirement_service.get_hardware_usage_counts()
        assert counts[hw.id] == 1


# =====================================================================
# 10. Requirements status tracking
# =====================================================================


class TestRequirementsStatus:
    """
    Verify update_requirements_status and get_requirements_status
    for the draft/submitted/reviewed workflow.
    """

    def test_set_status_draft(self, app, sample_org, admin_user, db_session):
        """Setting status to 'draft' persists on the position."""
        pos = sample_org["pos_a1_1"]

        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="draft",
            user_id=admin_user.id,
        )

        assert requirement_service.get_requirements_status(pos.id) == "draft"

    def test_set_status_submitted(self, app, sample_org, admin_user, db_session):
        """Setting status to 'submitted' persists on the position."""
        pos = sample_org["pos_a1_1"]

        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="submitted",
            user_id=admin_user.id,
        )

        assert requirement_service.get_requirements_status(pos.id) == "submitted"

    def test_set_status_reviewed(self, app, sample_org, admin_user, db_session):
        """Setting status to 'reviewed' persists on the position."""
        pos = sample_org["pos_a1_1"]

        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="reviewed",
            user_id=admin_user.id,
        )

        assert requirement_service.get_requirements_status(pos.id) == "reviewed"

    def test_reset_status_to_none(self, app, sample_org, admin_user, db_session):
        """Setting status to None resets the position to 'not started'."""
        pos = sample_org["pos_a1_1"]

        # First set to draft.
        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="draft",
            user_id=admin_user.id,
        )
        # Then reset.
        requirement_service.update_requirements_status(
            position_id=pos.id,
            status=None,
            user_id=admin_user.id,
        )

        assert requirement_service.get_requirements_status(pos.id) is None

    def test_invalid_status_raises(self, app, sample_org, admin_user):
        """Setting an invalid status string raises ValueError."""
        pos = sample_org["pos_a1_1"]

        with pytest.raises(ValueError, match="Invalid requirements status"):
            requirement_service.update_requirements_status(
                position_id=pos.id,
                status="approved",
                user_id=admin_user.id,
            )

    def test_nonexistent_position_raises(self, app, admin_user):
        """Setting status on a nonexistent position raises."""
        with pytest.raises(ValueError, match="not found"):
            requirement_service.update_requirements_status(
                position_id=999999,
                status="draft",
                user_id=admin_user.id,
            )

    def test_get_status_returns_none_for_nonexistent_position(self, app):
        """get_requirements_status returns None for a missing position."""
        result = requirement_service.get_requirements_status(999999)
        assert result is None

    def test_same_status_is_idempotent(self, app, sample_org, admin_user, db_session):
        """
        Setting the same status twice should not raise and should
        leave the position unchanged (the service short-circuits).
        """
        pos = sample_org["pos_a1_1"]

        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="draft",
            user_id=admin_user.id,
        )
        # Second call with same status -- should not raise.
        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="draft",
            user_id=admin_user.id,
        )

        assert requirement_service.get_requirements_status(pos.id) == "draft"


# =====================================================================
# 11. Division common items ("Suggested for your division")
# =====================================================================


class TestDivisionCommonItems:
    """
    Verify the threshold-based logic that identifies commonly-used
    hardware and software within a division for "Suggested" badges.
    """

    def test_common_hardware_empty_when_no_requirements(self, app, sample_org):
        """A division with no configured positions returns empty."""
        div = sample_org["div_a1"]
        result = requirement_service.get_division_common_hardware(div.id)
        assert result == set()

    def test_common_hardware_item_used_by_all_positions(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """
        If both positions in div_a1 use the same hardware item,
        it should appear in the common set (at any threshold <= 1.0).
        """
        div = sample_org["div_a1"]
        hw = sample_catalog["hw_monitor_24"]

        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=hw,
            quantity=1,
        )
        create_hw_requirement(
            position=sample_org["pos_a1_2"],
            hardware=hw,
            quantity=2,
        )

        result = requirement_service.get_division_common_hardware(
            division_id=div.id,
            threshold=0.4,
        )
        assert hw.id in result

    def test_common_hardware_item_used_by_one_of_two_not_common(
        self, app, sample_org, sample_catalog, create_hw_requirement
    ):
        """
        If only 1 of 2 configured positions uses an item, it may
        or may not be common depending on the threshold.  At the
        default threshold of 0.4, with 2 configured positions,
        min_positions = max(1, int(2 * 0.4)) = max(1, 0) = 1.
        So an item used by 1 of 2 IS common at threshold 0.4.

        But at threshold 0.6, min_positions = max(1, int(2 * 0.6))
        = max(1, 1) = 1 -- still common.

        At threshold 1.0, min_positions = max(1, int(2 * 1.0))
        = max(1, 2) = 2 -- NOT common (only used by 1).
        """
        div = sample_org["div_a1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        # pos_a1_1 uses both laptop and monitor.
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=hw_laptop,
            quantity=1,
        )
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=hw_monitor,
            quantity=1,
        )
        # pos_a1_2 uses only monitor.
        create_hw_requirement(
            position=sample_org["pos_a1_2"],
            hardware=hw_monitor,
            quantity=1,
        )

        # At threshold=1.0, only the monitor (used by both) is common.
        result = requirement_service.get_division_common_hardware(
            division_id=div.id,
            threshold=1.0,
        )
        assert hw_monitor.id in result
        assert hw_laptop.id not in result

    def test_common_software_empty_when_no_requirements(self, app, sample_org):
        """A division with no software requirements returns empty."""
        div = sample_org["div_a1"]
        result = requirement_service.get_division_common_software(div.id)
        assert result == set()

    def test_common_software_item_used_by_all_positions(
        self, app, sample_org, sample_catalog, create_sw_requirement
    ):
        """
        If both positions in div_a1 use the same software, it
        should be in the common set.
        """
        div = sample_org["div_a1"]
        sw = sample_catalog["sw_office_e3"]

        create_sw_requirement(
            position=sample_org["pos_a1_1"],
            software=sw,
        )
        create_sw_requirement(
            position=sample_org["pos_a1_2"],
            software=sw,
        )

        result = requirement_service.get_division_common_software(
            division_id=div.id,
            threshold=0.4,
        )
        assert sw.id in result


# =====================================================================
# 12. Audit trail via requirement_history
# =====================================================================


class TestRequirementHistory:
    """
    Verify that service operations write records to
    budget.requirement_history so the audit trail is preserved
    even after hard-deletes.
    """

    def test_add_hardware_writes_added_history(
        self, app, sample_org, sample_catalog, admin_user, db_session
    ):
        """add_hardware_requirement writes an ADDED history entry."""
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        requirement_service.add_hardware_requirement(
            position_id=pos.id,
            hardware_id=hw.id,
            quantity=2,
            user_id=admin_user.id,
        )

        history = RequirementHistory.query.filter_by(
            position_id=pos.id,
            item_type="hardware",
            item_id=hw.id,
            action_type="ADDED",
        ).first()
        assert history is not None
        assert history.quantity == 2
        assert history.changed_by == admin_user.id

    def test_remove_hardware_writes_removed_history(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        admin_user,
        db_session,
    ):
        """
        remove_hardware_requirement writes a REMOVED history entry
        before deleting the record.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]
        req = create_hw_requirement(position=pos, hardware=hw, quantity=3)

        requirement_service.remove_hardware_requirement(
            requirement_id=req.id,
            user_id=admin_user.id,
        )

        history = RequirementHistory.query.filter_by(
            position_id=pos.id,
            item_type="hardware",
            item_id=hw.id,
            action_type="REMOVED",
        ).first()
        assert history is not None
        assert history.quantity == 3

    def test_set_position_hardware_writes_history_for_each_item(
        self, app, sample_org, sample_catalog, admin_user, db_session
    ):
        """
        set_position_hardware writes ADDED history for each item
        in the new set.
        """
        pos = sample_org["pos_a1_1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        requirement_service.set_position_hardware(
            position_id=pos.id,
            items=[
                {"hardware_id": hw_laptop.id, "quantity": 1},
                {"hardware_id": hw_monitor.id, "quantity": 2},
            ],
            user_id=admin_user.id,
        )

        added_entries = RequirementHistory.query.filter_by(
            position_id=pos.id,
            item_type="hardware",
            action_type="ADDED",
        ).all()
        assert len(added_entries) == 2

    def test_update_hardware_writes_modified_history(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        admin_user,
        db_session,
    ):
        """
        update_hardware_requirement writes a MODIFIED history entry.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]
        req = create_hw_requirement(position=pos, hardware=hw, quantity=1)

        requirement_service.update_hardware_requirement(
            requirement_id=req.id,
            quantity=4,
            user_id=admin_user.id,
        )

        history = RequirementHistory.query.filter_by(
            position_id=pos.id,
            item_type="hardware",
            item_id=hw.id,
            action_type="MODIFIED",
        ).first()
        assert history is not None
        assert history.quantity == 4

    def test_copy_writes_history_on_target(
        self,
        app,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        admin_user,
        db_session,
    ):
        """
        copy_position_requirements writes ADDED history entries
        on the target position (via set_position_hardware).
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw = sample_catalog["hw_laptop_standard"]

        create_hw_requirement(position=source, hardware=hw, quantity=1)

        requirement_service.copy_position_requirements(
            source_position_id=source.id,
            target_position_id=target.id,
            user_id=admin_user.id,
        )

        added = RequirementHistory.query.filter_by(
            position_id=target.id,
            item_type="hardware",
            action_type="ADDED",
        ).all()
        assert len(added) >= 1
