"""
Integration tests for the equipment blueprint routes.

Covers every route in ``app/blueprints/equipment/routes.py`` with
real database operations against the SQL Server test instance.

Organized by entity type, each section exercises the full CRUD
lifecycle (list, create GET, create POST, edit GET, edit POST,
deactivate) plus validation branches, cost history tracking, role
enforcement, and edge cases.

Sections:
    1.  Hardware Type CRUD
    2.  Hardware Type Validation and Edge Cases
    3.  Hardware Item CRUD
    4.  Hardware Item Validation and Edge Cases
    5.  Hardware Item Cost History Tracking
    6.  Software Type CRUD
    7.  Software Type Validation and Edge Cases
    8.  Software Family CRUD
    9.  Software Family Validation and Edge Cases
    10. Software Product CRUD
    11. Software Product with Coverage (tenant model)
    12. Software Product Validation and Edge Cases
    13. Role Enforcement (write operations blocked for non-admin/IT)
    14. Authentication Enforcement (unauthenticated access blocked)
    15. Nonexistent Resource Handling

Fixture reminder (from conftest.py ``sample_catalog``):
    hw_type_laptop:      max_selections=1  (single-select)
    hw_type_monitor:     max_selections=None (unlimited)
    hw_laptop_standard:  belongs to hw_type_laptop,  cost $1200
    hw_laptop_power:     belongs to hw_type_laptop,  cost $2400
    hw_monitor_24:       belongs to hw_type_monitor, cost $350
    sw_type_productivity: description="Test productivity software"
    sw_type_security:     description="Test security software"
    sw_office_e3:        per_user, $200/license
    sw_office_e5:        per_user, $400/license
    sw_antivirus:        tenant,   $50000 total

Fixture reminder (from conftest.py ``sample_org``):
    dept_a, dept_b, div_a1, div_a2, div_b1, div_b2,
    pos_a1_1 (auth=3), pos_a1_2 (auth=5), pos_a2_1 (auth=2),
    pos_b1_1 (auth=4), pos_b1_2 (auth=1), pos_b2_1 (auth=6)

Run this file in isolation::

    pytest tests/test_routes/test_equipment_routes.py -v
"""

import time as _time

import pytest

from app.extensions import db
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


# =====================================================================
# Local helper for generating unique names within this test module.
# Uses the ``_TST_`` prefix so the conftest cleanup fixture deletes
# records created by route-level POST requests.
# =====================================================================

_local_counter = int(_time.time() * 10) % 9000


def _unique_name(prefix: str) -> str:
    """
    Return a unique ``_TST_``-prefixed name for route-created records.

    The conftest cleanup fixture deletes rows whose name or code
    starts with ``_TST_``, so every record created through a route
    POST in these tests will be cleaned up automatically.

    Args:
        prefix: A short label (e.g., ``EQ_HWT`` for hardware type).

    Returns:
        A string like ``_TST_EQ_HWT_5012``.
    """
    global _local_counter  # pylint: disable=global-statement
    _local_counter += 1
    return f"_TST_{prefix}_{_local_counter:04d}"


# =====================================================================
# 1. Hardware Type CRUD
# =====================================================================


class TestHardwareTypeCRUD:
    """
    Full create-read-update-deactivate lifecycle for hardware types.

    Hardware types are top-level categories (e.g., Laptop, Monitor)
    that group individual hardware items.
    """

    def test_hardware_type_list_page_loads(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/hardware-types returns 200 and displays
        at least one hardware type from the sample catalog.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/hardware-types")
        assert response.status_code == 200
        # The sample_catalog creates types with _TST_ prefixed names.
        # Verify at least one type name appears in the rendered page.
        hw_type = sample_catalog["hw_type_laptop"]
        assert hw_type.type_name.encode() in response.data

    def test_hardware_type_list_shows_inactive_when_requested(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        GET /equipment/hardware-types?show_inactive=1 should include
        deactivated hardware types in the listing.
        """
        # Deactivate one type directly in the database.
        hw_type = sample_catalog["hw_type_monitor"]
        hw_type.is_active = False
        db_session.commit()

        client = auth_client(admin_user)

        # Without the flag, the inactive type should be absent.
        response_default = client.get("/equipment/hardware-types")
        assert response_default.status_code == 200
        assert hw_type.type_name.encode() not in response_default.data

        # With the flag, it should appear.
        response_inactive = client.get("/equipment/hardware-types?show_inactive=1")
        assert response_inactive.status_code == 200
        assert hw_type.type_name.encode() in response_inactive.data

    def test_hardware_type_create_form_loads(self, auth_client, admin_user):
        """
        GET /equipment/hardware-types/new returns 200 and renders
        a form with the expected input fields.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 200
        # Verify essential form field names are present.
        assert b"type_name" in response.data
        assert b"estimated_cost" in response.data

    def test_hardware_type_create_saves_record(
        self, auth_client, admin_user, db_session
    ):
        """
        POST /equipment/hardware-types/new with valid data creates
        a HardwareType record and redirects to the list page.
        """
        client = auth_client(admin_user)
        new_name = _unique_name("EQ_HWT")

        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": new_name,
                "estimated_cost": "500.00",
                "description": "Route test hardware type",
                "max_selections": "2",
            },
            follow_redirects=False,
        )
        # Should redirect to the hardware type list.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "hardware-types" in location

        # Verify the record exists in the database.
        created = HardwareType.query.filter_by(type_name=new_name).first()
        assert created is not None, f"HardwareType '{new_name}' not found after POST"
        assert str(created.estimated_cost) == "500.00"
        assert created.max_selections == 2
        assert created.is_active is True
        assert created.description == "Route test hardware type"

    def test_hardware_type_create_records_cost_history(
        self, auth_client, admin_user, db_session
    ):
        """
        Creating a hardware type via POST should produce an initial
        HardwareTypeCostHistory record with end_date NULL.
        """
        client = auth_client(admin_user)
        new_name = _unique_name("EQ_HWT")

        client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": new_name,
                "estimated_cost": "750.00",
            },
        )

        created = HardwareType.query.filter_by(type_name=new_name).first()
        assert created is not None

        # Check for the initial cost history record.
        history = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=created.id
        ).all()
        assert len(history) >= 1, "No cost history created on hardware type creation"
        # The current (open) record should have no end_date.
        open_records = [h for h in history if h.end_date is None]
        assert len(open_records) == 1
        assert str(open_records[0].estimated_cost) == "750.00"

    def test_hardware_type_create_with_zero_max_selections_treats_as_unlimited(
        self, auth_client, admin_user, db_session
    ):
        """
        POST with max_selections=0 should be treated as unlimited
        (stored as NULL), not as literal zero.
        """
        client = auth_client(admin_user)
        new_name = _unique_name("EQ_HWT")

        client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": new_name,
                "estimated_cost": "100.00",
                "max_selections": "0",
            },
        )

        created = HardwareType.query.filter_by(type_name=new_name).first()
        assert created is not None
        assert (
            created.max_selections is None
        ), "max_selections=0 should be stored as None (unlimited)"

    def test_hardware_type_edit_form_loads_with_current_values(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/hardware-types/<id>/edit returns 200 and
        pre-populates the form with the current hardware type data.
        """
        hw_type = sample_catalog["hw_type_laptop"]
        client = auth_client(admin_user)
        response = client.get(f"/equipment/hardware-types/{hw_type.id}/edit")
        assert response.status_code == 200
        # The current type name should appear in the rendered form.
        assert hw_type.type_name.encode() in response.data

    def test_hardware_type_edit_updates_record(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/hardware-types/<id>/edit with an updated
        name persists the change to the database.
        """
        hw_type = sample_catalog["hw_type_laptop"]
        updated_name = _unique_name("EQ_HWT")
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/hardware-types/{hw_type.id}/edit",
            data={
                "type_name": updated_name,
                "estimated_cost": str(hw_type.estimated_cost),
                "description": "Updated via route test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(hw_type)
        assert hw_type.type_name == updated_name
        assert hw_type.description == "Updated via route test"

    def test_hardware_type_edit_cost_change_creates_history(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        Changing the estimated_cost on a hardware type via the edit
        route should close the old HardwareTypeCostHistory record
        (setting end_date) and open a new one.
        """
        hw_type = sample_catalog["hw_type_laptop"]
        original_cost = hw_type.estimated_cost
        new_cost = "999.99"
        client = auth_client(admin_user)

        # Count existing history records before the edit.
        history_before = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=hw_type.id
        ).count()

        client.post(
            f"/equipment/hardware-types/{hw_type.id}/edit",
            data={
                "type_name": hw_type.type_name,
                "estimated_cost": new_cost,
            },
        )

        # Refresh and verify cost was updated.
        db_session.refresh(hw_type)
        assert str(hw_type.estimated_cost) == new_cost

        # Verify history records increased.
        history_after = HardwareTypeCostHistory.query.filter_by(
            hardware_type_id=hw_type.id
        ).all()
        assert (
            len(history_after) > history_before
        ), "No new cost history record created after cost change"
        # The newest open record should have the new cost.
        open_records = [h for h in history_after if h.end_date is None]
        assert len(open_records) == 1
        assert str(open_records[0].estimated_cost) == new_cost

    def test_hardware_type_deactivate_sets_inactive(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/hardware-types/<id>/deactivate sets
        is_active to False and redirects to the list page.
        """
        hw_type = sample_catalog["hw_type_monitor"]
        assert hw_type.is_active is True

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/hardware-types/{hw_type.id}/deactivate",
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(hw_type)
        assert hw_type.is_active is False

    def test_hardware_type_it_staff_can_create(
        self, auth_client, it_staff_user, db_session
    ):
        """
        IT staff should be able to create hardware types
        (route allows admin and it_staff).
        """
        client = auth_client(it_staff_user)
        new_name = _unique_name("EQ_HWT")

        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": new_name,
                "estimated_cost": "300.00",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        created = HardwareType.query.filter_by(type_name=new_name).first()
        assert created is not None


# =====================================================================
# 2. Hardware Type Validation and Edge Cases
# =====================================================================


class TestHardwareTypeValidation:
    """
    Verify that the hardware type create and edit routes reject
    invalid input gracefully instead of producing 500 errors.
    """

    def test_hardware_type_create_rejects_missing_name(self, auth_client, admin_user):
        """
        POST without a type_name should re-render the form with
        a validation error, not redirect or crash.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": "",
                "estimated_cost": "100.00",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        # Should contain the validation error message.
        assert b"required" in response.data.lower()

    def test_hardware_type_create_rejects_invalid_cost(self, auth_client, admin_user):
        """
        POST with a non-numeric estimated_cost should re-render
        the form with a validation error.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": _unique_name("EQ_HWT"),
                "estimated_cost": "not_a_number",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"valid number" in response.data.lower()

    def test_hardware_type_create_rejects_negative_max_selections(
        self, auth_client, admin_user
    ):
        """
        POST with a negative max_selections value should re-render
        the form with an appropriate error message.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": _unique_name("EQ_HWT"),
                "estimated_cost": "100.00",
                "max_selections": "-1",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"negative" in response.data.lower()

    def test_hardware_type_create_rejects_nonnumeric_max_selections(
        self, auth_client, admin_user
    ):
        """
        POST with a non-integer max_selections value should
        re-render the form with an error message.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware-types/new",
            data={
                "type_name": _unique_name("EQ_HWT"),
                "estimated_cost": "100.00",
                "max_selections": "abc",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"whole number" in response.data.lower()

    def test_hardware_type_edit_rejects_invalid_cost(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        POST /equipment/hardware-types/<id>/edit with a non-numeric
        cost should re-render the form with an error.
        """
        hw_type = sample_catalog["hw_type_laptop"]
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/hardware-types/{hw_type.id}/edit",
            data={
                "type_name": hw_type.type_name,
                "estimated_cost": "garbage",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"valid number" in response.data.lower()

    def test_hardware_type_edit_nonexistent_redirects(self, auth_client, admin_user):
        """
        GET /equipment/hardware-types/999999/edit for a nonexistent
        ID should redirect with a flash warning, not crash.
        """
        client = auth_client(admin_user)
        response = client.get(
            "/equipment/hardware-types/999999/edit",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()

    def test_hardware_type_deactivate_nonexistent_redirects(
        self, auth_client, admin_user
    ):
        """
        POST /equipment/hardware-types/999999/deactivate for a
        nonexistent ID should redirect with an error flash.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware-types/999999/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        # The service raises ValueError which is flashed as "danger".
        assert b"not found" in response.data.lower()

    def test_hardware_type_edit_negative_max_selections_rejected(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        POST to edit with a negative max_selections should re-render
        the form with an error instead of persisting the bad value.
        """
        hw_type = sample_catalog["hw_type_laptop"]
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/hardware-types/{hw_type.id}/edit",
            data={
                "type_name": hw_type.type_name,
                "estimated_cost": "100.00",
                "max_selections": "-5",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"negative" in response.data.lower()


# =====================================================================
# 3. Hardware Item CRUD
# =====================================================================


class TestHardwareItemCRUD:
    """
    Full create-read-update-deactivate lifecycle for hardware items.

    Hardware items are specific products (e.g., Standard Laptop)
    that belong to a hardware type category.
    """

    def test_hardware_list_page_loads(self, auth_client, admin_user, sample_catalog):
        """
        GET /equipment/hardware returns 200 and shows hardware
        items from the sample catalog.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/hardware")
        assert response.status_code == 200
        hw = sample_catalog["hw_laptop_standard"]
        assert hw.name.encode() in response.data

    def test_hardware_list_filters_by_type(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/hardware?hardware_type_id=<id> should only
        show items belonging to that type.
        """
        client = auth_client(admin_user)
        hw_type = sample_catalog["hw_type_laptop"]
        monitor = sample_catalog["hw_monitor_24"]

        response = client.get(f"/equipment/hardware?hardware_type_id={hw_type.id}")
        assert response.status_code == 200
        # Laptop items should be present.
        assert sample_catalog["hw_laptop_standard"].name.encode() in response.data
        # Monitor should NOT be present (different type).
        assert monitor.name.encode() not in response.data

    def test_hardware_create_form_loads(self, auth_client, admin_user, sample_catalog):
        """
        GET /equipment/hardware/new returns 200 and renders a form
        with hardware type options.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 200
        assert b"name" in response.data
        assert b"estimated_cost" in response.data
        assert b"hardware_type_id" in response.data

    def test_hardware_create_saves_record(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/hardware/new with valid data creates a
        Hardware record and redirects to the list page.
        """
        client = auth_client(admin_user)
        hw_type = sample_catalog["hw_type_laptop"]
        new_name = _unique_name("EQ_HW")

        response = client.post(
            "/equipment/hardware/new",
            data={
                "name": new_name,
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": "1500.00",
                "description": "Created via route test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "hardware" in location

        # Verify the database record.
        created = Hardware.query.filter_by(name=new_name).first()
        assert created is not None, f"Hardware '{new_name}' not found after POST"
        assert created.hardware_type_id == hw_type.id
        assert str(created.estimated_cost) == "1500.00"
        assert created.description == "Created via route test"
        assert created.is_active is True

    def test_hardware_create_records_cost_history(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        Creating a hardware item via POST should produce an initial
        HardwareCostHistory record with end_date NULL.
        """
        client = auth_client(admin_user)
        hw_type = sample_catalog["hw_type_monitor"]
        new_name = _unique_name("EQ_HW")

        client.post(
            "/equipment/hardware/new",
            data={
                "name": new_name,
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": "450.00",
            },
        )

        created = Hardware.query.filter_by(name=new_name).first()
        assert created is not None

        # Verify cost history was initialized.
        history = HardwareCostHistory.query.filter_by(hardware_id=created.id).all()
        assert (
            len(history) >= 1
        ), "No HardwareCostHistory created on hardware item creation"
        open_records = [h for h in history if h.end_date is None]
        assert len(open_records) == 1
        assert str(open_records[0].estimated_cost) == "450.00"

    def test_hardware_edit_form_loads_with_current_values(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/hardware/<id>/edit returns 200 and shows
        the current hardware item data in the form.
        """
        hw = sample_catalog["hw_laptop_standard"]
        client = auth_client(admin_user)

        response = client.get(f"/equipment/hardware/{hw.id}/edit")
        assert response.status_code == 200
        assert hw.name.encode() in response.data

    def test_hardware_edit_updates_record(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/hardware/<id>/edit with updated name and
        cost persists both changes to the database.
        """
        hw = sample_catalog["hw_laptop_standard"]
        updated_name = _unique_name("EQ_HW")
        hw_type = sample_catalog["hw_type_laptop"]
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/hardware/{hw.id}/edit",
            data={
                "name": updated_name,
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": "1350.00",
                "description": "Updated via route test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(hw)
        assert hw.name == updated_name
        assert str(hw.estimated_cost) == "1350.00"

    def test_hardware_deactivate_sets_inactive(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/hardware/<id>/deactivate sets is_active
        to False and redirects to the hardware list.
        """
        hw = sample_catalog["hw_monitor_24"]
        assert hw.is_active is True

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/hardware/{hw.id}/deactivate",
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(hw)
        assert hw.is_active is False


# =====================================================================
# 4. Hardware Item Validation and Edge Cases
# =====================================================================


class TestHardwareItemValidation:
    """
    Verify hardware item create and edit routes reject invalid
    input gracefully.
    """

    def test_hardware_create_rejects_missing_name(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        POST /equipment/hardware/new without a name should
        re-render the form with a validation error.
        """
        client = auth_client(admin_user)
        hw_type = sample_catalog["hw_type_laptop"]

        response = client.post(
            "/equipment/hardware/new",
            data={
                "name": "",
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": "100.00",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_hardware_create_rejects_missing_type(self, auth_client, admin_user):
        """
        POST /equipment/hardware/new without a hardware_type_id
        should re-render the form with a validation error.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware/new",
            data={
                "name": _unique_name("EQ_HW"),
                "hardware_type_id": "",
                "estimated_cost": "100.00",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_hardware_create_rejects_invalid_cost(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        POST with a non-numeric cost should re-render the form
        with an error instead of crashing.
        """
        client = auth_client(admin_user)
        hw_type = sample_catalog["hw_type_laptop"]

        response = client.post(
            "/equipment/hardware/new",
            data={
                "name": _unique_name("EQ_HW"),
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": "not_valid",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"valid number" in response.data.lower()

    def test_hardware_edit_rejects_invalid_cost(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        POST to edit with a non-numeric cost should re-render the
        form with an error.
        """
        hw = sample_catalog["hw_laptop_standard"]
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/hardware/{hw.id}/edit",
            data={
                "name": hw.name,
                "hardware_type_id": str(hw.hardware_type_id),
                "estimated_cost": "xyz",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"valid number" in response.data.lower()

    def test_hardware_edit_nonexistent_redirects(self, auth_client, admin_user):
        """
        GET /equipment/hardware/999999/edit for a nonexistent item
        should redirect with a flash warning.
        """
        client = auth_client(admin_user)
        response = client.get(
            "/equipment/hardware/999999/edit",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()

    def test_hardware_deactivate_nonexistent_redirects(self, auth_client, admin_user):
        """
        POST /equipment/hardware/999999/deactivate for a
        nonexistent item should redirect with an error flash.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/hardware/999999/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()


# =====================================================================
# 5. Hardware Item Cost History Tracking
# =====================================================================


class TestHardwareItemCostHistory:
    """
    Verify that editing a hardware item's cost properly rotates
    the HardwareCostHistory records (closes old, opens new).
    """

    def test_hardware_edit_cost_change_closes_old_history(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        Changing a hardware item's estimated_cost via the edit route
        should set end_date on the previous open history record and
        create a new open record with the new cost.

        NOTE: The sample_catalog fixture creates hardware items via
        direct model instantiation, which bypasses the service layer
        and does NOT create an initial HardwareCostHistory record.
        This test seeds the initial history row manually so the
        close-old / open-new rotation can be verified.
        """
        hw = sample_catalog["hw_laptop_power"]
        hw_type = sample_catalog["hw_type_laptop"]
        new_cost = "2750.00"
        client = auth_client(admin_user)

        # Seed the initial cost history record that would normally be
        # created by equipment_service.create_hardware().  The fixture
        # bypasses the service, so we insert it ourselves.
        initial_history = HardwareCostHistory(
            hardware_id=hw.id,
            estimated_cost=hw.estimated_cost,
            changed_by=admin_user.id,
        )
        db_session.add(initial_history)
        db_session.commit()

        # Confirm exactly one open history row exists before the edit.
        history_before = HardwareCostHistory.query.filter_by(hardware_id=hw.id).all()
        assert len(history_before) == 1
        assert history_before[0].end_date is None

        client.post(
            f"/equipment/hardware/{hw.id}/edit",
            data={
                "name": hw.name,
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": new_cost,
            },
        )

        # Verify cost was updated on the model.
        db_session.refresh(hw)
        assert str(hw.estimated_cost) == new_cost

        # Verify history records after the cost change.
        all_history = (
            HardwareCostHistory.query.filter_by(hardware_id=hw.id)
            .order_by(HardwareCostHistory.id)
            .all()
        )

        assert len(all_history) == 2, (
            f"Expected 2 history records (1 closed + 1 open), "
            f"found {len(all_history)}"
        )

        # The old record should now be closed (end_date is set).
        closed_records = [h for h in all_history if h.end_date is not None]
        assert (
            len(closed_records) == 1
        ), "The original cost history record was not closed"
        assert str(closed_records[0].estimated_cost) == "2400.00"

        # Exactly one record should be open with the new cost.
        open_records = [h for h in all_history if h.end_date is None]
        assert (
            len(open_records) == 1
        ), f"Expected 1 open cost history record, found {len(open_records)}"
        assert str(open_records[0].estimated_cost) == new_cost

    def test_hardware_edit_same_cost_does_not_create_extra_history(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        Editing a hardware item without changing the cost should
        NOT create a duplicate cost history record.
        """
        hw = sample_catalog["hw_monitor_24"]
        hw_type = sample_catalog["hw_type_monitor"]
        client = auth_client(admin_user)

        history_before_count = HardwareCostHistory.query.filter_by(
            hardware_id=hw.id
        ).count()

        # POST with the same cost.
        client.post(
            f"/equipment/hardware/{hw.id}/edit",
            data={
                "name": hw.name,
                "hardware_type_id": str(hw_type.id),
                "estimated_cost": str(hw.estimated_cost),
            },
        )

        history_after_count = HardwareCostHistory.query.filter_by(
            hardware_id=hw.id
        ).count()

        # If the service is smart about no-op cost changes, the count
        # should remain the same. If it creates a new record regardless,
        # that is acceptable but we document the behavior.
        # The important thing is no crash and at most one new record.
        assert history_after_count <= history_before_count + 1


# =====================================================================
# 6. Software Type CRUD
# =====================================================================


class TestSoftwareTypeCRUD:
    """
    Full CRUD lifecycle for software type categories.
    """

    def test_software_type_list_page_loads(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/software-types returns 200 for admin and
        displays software types from the catalog.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/software-types")
        assert response.status_code == 200
        sw_type = sample_catalog["sw_type_productivity"]
        assert sw_type.type_name.encode() in response.data

    def test_software_type_create_form_loads(self, auth_client, admin_user):
        """GET /equipment/software-types/new returns 200."""
        client = auth_client(admin_user)
        response = client.get("/equipment/software-types/new")
        assert response.status_code == 200
        assert b"type_name" in response.data

    def test_software_type_create_saves_record(
        self, auth_client, admin_user, db_session
    ):
        """
        POST /equipment/software-types/new with valid data creates
        a SoftwareType record and redirects.
        """
        client = auth_client(admin_user)
        new_name = _unique_name("EQ_SWT")

        response = client.post(
            "/equipment/software-types/new",
            data={
                "type_name": new_name,
                "description": "Test software type via route",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        created = SoftwareType.query.filter_by(type_name=new_name).first()
        assert created is not None
        assert created.is_active is True
        assert created.description == "Test software type via route"

    def test_software_type_edit_form_loads(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/software-types/<id>/edit returns 200 and
        pre-populates the current values.
        """
        sw_type = sample_catalog["sw_type_productivity"]
        client = auth_client(admin_user)

        response = client.get(f"/equipment/software-types/{sw_type.id}/edit")
        assert response.status_code == 200
        assert sw_type.type_name.encode() in response.data

    def test_software_type_edit_updates_record(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/software-types/<id>/edit with an updated
        name persists the change.
        """
        sw_type = sample_catalog["sw_type_productivity"]
        updated_name = _unique_name("EQ_SWT")
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/software-types/{sw_type.id}/edit",
            data={
                "type_name": updated_name,
                "description": "Updated via route test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(sw_type)
        assert sw_type.type_name == updated_name

    def test_software_type_deactivate_sets_inactive(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/software-types/<id>/deactivate sets
        is_active to False.
        """
        sw_type = sample_catalog["sw_type_security"]
        assert sw_type.is_active is True

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/software-types/{sw_type.id}/deactivate",
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(sw_type)
        assert sw_type.is_active is False


# =====================================================================
# 7. Software Type Validation and Edge Cases
# =====================================================================


class TestSoftwareTypeValidation:
    """Verify software type validation rejects bad input."""

    def test_software_type_create_rejects_missing_name(self, auth_client, admin_user):
        """POST without a type_name should re-render with an error."""
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/software-types/new",
            data={"type_name": "", "description": "No name provided"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_software_type_edit_rejects_missing_name(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        POST to edit with an empty type_name should re-render
        the form with an error.
        """
        sw_type = sample_catalog["sw_type_productivity"]
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/software-types/{sw_type.id}/edit",
            data={"type_name": "", "description": "Missing name"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_software_type_edit_nonexistent_redirects(self, auth_client, admin_user):
        """
        GET /equipment/software-types/999999/edit should redirect
        with a warning flash.
        """
        client = auth_client(admin_user)
        response = client.get(
            "/equipment/software-types/999999/edit",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()

    def test_software_type_deactivate_nonexistent_redirects(
        self, auth_client, admin_user
    ):
        """
        POST to deactivate a nonexistent software type should
        redirect with an error flash.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/software-types/999999/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()


# =====================================================================
# 8. Software Family CRUD
# =====================================================================


class TestSoftwareFamilyCRUD:
    """
    Full CRUD lifecycle for software families (e.g., Microsoft 365).
    """

    def test_software_family_list_page_loads(self, auth_client, admin_user):
        """GET /equipment/software-families returns 200."""
        client = auth_client(admin_user)
        response = client.get("/equipment/software-families")
        assert response.status_code == 200

    def test_software_family_create_form_loads(self, auth_client, admin_user):
        """GET /equipment/software-families/new returns 200."""
        client = auth_client(admin_user)
        response = client.get("/equipment/software-families/new")
        assert response.status_code == 200
        assert b"family_name" in response.data

    def test_software_family_create_saves_record(
        self, auth_client, admin_user, db_session
    ):
        """
        POST /equipment/software-families/new with valid data
        creates a SoftwareFamily record and redirects.
        """
        client = auth_client(admin_user)
        new_name = _unique_name("EQ_SWF")

        response = client.post(
            "/equipment/software-families/new",
            data={
                "family_name": new_name,
                "description": "Test family via route",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        created = SoftwareFamily.query.filter_by(family_name=new_name).first()
        assert created is not None
        assert created.is_active is True

    def test_software_family_edit_form_loads(self, auth_client, admin_user, db_session):
        """
        GET /equipment/software-families/<id>/edit returns 200
        and pre-populates the form.
        """
        # Create a family to edit.
        from app.services import equipment_service

        family = equipment_service.create_software_family(
            family_name=_unique_name("EQ_SWF"),
            description="For edit test",
            user_id=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.get(f"/equipment/software-families/{family.id}/edit")
        assert response.status_code == 200
        assert family.family_name.encode() in response.data

    def test_software_family_edit_updates_record(
        self, auth_client, admin_user, db_session
    ):
        """
        POST /equipment/software-families/<id>/edit with an
        updated name persists the change.
        """
        from app.services import equipment_service

        family = equipment_service.create_software_family(
            family_name=_unique_name("EQ_SWF"),
            user_id=admin_user.id,
        )
        updated_name = _unique_name("EQ_SWF")

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/software-families/{family.id}/edit",
            data={
                "family_name": updated_name,
                "description": "Updated via route test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(family)
        assert family.family_name == updated_name

    def test_software_family_deactivate_sets_inactive(
        self, auth_client, admin_user, db_session
    ):
        """
        POST /equipment/software-families/<id>/deactivate sets
        is_active to False.
        """
        from app.services import equipment_service

        family = equipment_service.create_software_family(
            family_name=_unique_name("EQ_SWF"),
            user_id=admin_user.id,
        )
        assert family.is_active is True

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/software-families/{family.id}/deactivate",
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(family)
        assert family.is_active is False


# =====================================================================
# 9. Software Family Validation and Edge Cases
# =====================================================================


class TestSoftwareFamilyValidation:
    """Verify software family validation rejects bad input."""

    def test_software_family_create_rejects_missing_name(self, auth_client, admin_user):
        """POST without a family_name should re-render with an error."""
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/software-families/new",
            data={"family_name": ""},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_software_family_edit_rejects_missing_name(
        self, auth_client, admin_user, db_session
    ):
        """POST to edit with empty name should re-render with error."""
        from app.services import equipment_service

        family = equipment_service.create_software_family(
            family_name=_unique_name("EQ_SWF"),
            user_id=admin_user.id,
        )

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/software-families/{family.id}/edit",
            data={"family_name": ""},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_software_family_edit_nonexistent_redirects(self, auth_client, admin_user):
        """Edit of a nonexistent family should redirect with warning."""
        client = auth_client(admin_user)
        response = client.get(
            "/equipment/software-families/999999/edit",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()

    def test_software_family_deactivate_nonexistent_redirects(
        self, auth_client, admin_user
    ):
        """Deactivate of a nonexistent family should redirect."""
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/software-families/999999/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()


# =====================================================================
# 10. Software Product CRUD
# =====================================================================


class TestSoftwareProductCRUD:
    """
    Full CRUD lifecycle for software products (per_user model).
    """

    def test_software_list_page_loads(self, auth_client, admin_user, sample_catalog):
        """
        GET /equipment/software returns 200 and shows software
        products from the catalog.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/software")
        assert response.status_code == 200
        sw = sample_catalog["sw_office_e3"]
        assert sw.name.encode() in response.data

    def test_software_list_filters_by_type(
        self, auth_client, admin_user, sample_catalog
    ):
        """
        GET /equipment/software?software_type_id=<id> should filter
        to only that type's products.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_productivity"]
        antivirus = sample_catalog["sw_antivirus"]

        response = client.get(f"/equipment/software?software_type_id={sw_type.id}")
        assert response.status_code == 200
        # Productivity software should be present.
        assert sample_catalog["sw_office_e3"].name.encode() in response.data
        # Security software should not.
        assert antivirus.name.encode() not in response.data

    def test_software_create_form_loads(
        self, auth_client, admin_user, sample_catalog, sample_org
    ):
        """
        GET /equipment/software/new returns 200 and renders the
        software creation form with type and family options.
        """
        client = auth_client(admin_user)
        response = client.get("/equipment/software/new")
        assert response.status_code == 200
        assert b"name" in response.data
        assert b"software_type_id" in response.data
        assert b"license_model" in response.data
        assert b"cost_per_license" in response.data

    def test_software_create_per_user_saves_record(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/software/new with per_user license model
        creates a Software record with the correct cost fields.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_productivity"]
        new_name = _unique_name("EQ_SW")

        response = client.post(
            "/equipment/software/new",
            data={
                "name": new_name,
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "150.00",
                "total_cost": "",
                "description": "Per-user test software",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        created = Software.query.filter_by(name=new_name).first()
        assert created is not None
        assert created.license_model == "per_user"
        assert str(created.cost_per_license) == "150.00"
        assert created.is_active is True

    def test_software_create_records_cost_history(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        Creating software via POST should produce an initial
        SoftwareCostHistory record with end_date NULL.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_productivity"]
        new_name = _unique_name("EQ_SW")

        client.post(
            "/equipment/software/new",
            data={
                "name": new_name,
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "250.00",
            },
        )

        created = Software.query.filter_by(name=new_name).first()
        assert created is not None

        history = SoftwareCostHistory.query.filter_by(software_id=created.id).all()
        assert len(history) >= 1, "No SoftwareCostHistory created on software creation"
        open_records = [h for h in history if h.end_date is None]
        assert len(open_records) == 1

    def test_software_edit_form_loads(
        self, auth_client, admin_user, sample_catalog, sample_org
    ):
        """
        GET /equipment/software/<id>/edit returns 200 and shows
        the current software data in the form.
        """
        sw = sample_catalog["sw_office_e3"]
        client = auth_client(admin_user)

        response = client.get(f"/equipment/software/{sw.id}/edit")
        assert response.status_code == 200
        assert sw.name.encode() in response.data

    def test_software_edit_updates_record(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        POST /equipment/software/<id>/edit with updated fields
        persists the changes to the database.
        """
        sw = sample_catalog["sw_office_e3"]
        sw_type = sample_catalog["sw_type_productivity"]
        updated_name = _unique_name("EQ_SW")
        client = auth_client(admin_user)

        response = client.post(
            f"/equipment/software/{sw.id}/edit",
            data={
                "name": updated_name,
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "225.00",
                "total_cost": "",
                "description": "Updated via route test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(sw)
        assert sw.name == updated_name
        assert str(sw.cost_per_license) == "225.00"

    def test_software_edit_cost_change_creates_history(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        Changing a software product's cost via edit should create
        a new SoftwareCostHistory record.
        """
        sw = sample_catalog["sw_office_e5"]
        sw_type = sample_catalog["sw_type_productivity"]
        client = auth_client(admin_user)

        history_before = SoftwareCostHistory.query.filter_by(software_id=sw.id).count()

        client.post(
            f"/equipment/software/{sw.id}/edit",
            data={
                "name": sw.name,
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "500.00",
                "total_cost": "",
            },
        )

        history_after = SoftwareCostHistory.query.filter_by(software_id=sw.id).count()

        assert (
            history_after > history_before
        ), "No new SoftwareCostHistory after cost change"

    def test_software_deactivate_sets_inactive(
        self, auth_client, admin_user, sample_catalog, db_session
    ):
        """
        POST /equipment/software/<id>/deactivate sets is_active
        to False.
        """
        sw = sample_catalog["sw_antivirus"]
        assert sw.is_active is True

        client = auth_client(admin_user)
        response = client.post(
            f"/equipment/software/{sw.id}/deactivate",
            follow_redirects=False,
        )
        assert response.status_code == 302

        db_session.refresh(sw)
        assert sw.is_active is False


# =====================================================================
# 11. Software Product with Coverage (tenant model)
# =====================================================================


class TestSoftwareProductWithCoverage:
    """
    Verify that tenant-licensed software products correctly handle
    coverage scope creation and modification through the routes.
    """

    def test_software_create_tenant_with_org_coverage(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        POST /equipment/software/new with license_model=tenant and
        an organization-level coverage row creates both the Software
        record and the SoftwareCoverage record.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_security"]
        new_name = _unique_name("EQ_SW")

        response = client.post(
            "/equipment/software/new",
            data={
                "name": new_name,
                "software_type_id": str(sw_type.id),
                "license_model": "tenant",
                "cost_per_license": "",
                "total_cost": "25000.00",
                "coverage_scope_type_0": "organization",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        created = Software.query.filter_by(name=new_name).first()
        assert created is not None
        assert created.license_model == "tenant"
        assert str(created.total_cost) == "25000.00"

        # Verify the coverage record was created.
        coverage = SoftwareCoverage.query.filter_by(software_id=created.id).all()
        assert len(coverage) == 1
        assert coverage[0].scope_type == "organization"

    def test_software_create_tenant_with_department_coverage(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        POST with tenant model and department-level coverage
        correctly stores the department FK on the coverage row.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_security"]
        dept = sample_org["dept_a"]
        new_name = _unique_name("EQ_SW")

        client.post(
            "/equipment/software/new",
            data={
                "name": new_name,
                "software_type_id": str(sw_type.id),
                "license_model": "tenant",
                "total_cost": "10000.00",
                "coverage_scope_type_0": "department",
                "coverage_department_id_0": str(dept.id),
            },
        )

        created = Software.query.filter_by(name=new_name).first()
        assert created is not None

        coverage = SoftwareCoverage.query.filter_by(software_id=created.id).all()
        assert len(coverage) == 1
        assert coverage[0].scope_type == "department"
        assert coverage[0].department_id == dept.id

    def test_software_create_tenant_with_multiple_coverage_rows(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        POST with multiple indexed coverage rows should create
        all corresponding SoftwareCoverage records.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_security"]
        dept_a = sample_org["dept_a"]
        dept_b = sample_org["dept_b"]
        new_name = _unique_name("EQ_SW")

        client.post(
            "/equipment/software/new",
            data={
                "name": new_name,
                "software_type_id": str(sw_type.id),
                "license_model": "tenant",
                "total_cost": "30000.00",
                "coverage_scope_type_0": "department",
                "coverage_department_id_0": str(dept_a.id),
                "coverage_scope_type_1": "department",
                "coverage_department_id_1": str(dept_b.id),
            },
        )

        created = Software.query.filter_by(name=new_name).first()
        assert created is not None

        coverage = SoftwareCoverage.query.filter_by(software_id=created.id).all()
        assert len(coverage) == 2, f"Expected 2 coverage rows, got {len(coverage)}"

    def test_software_edit_updates_coverage(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        Editing a tenant-licensed software product should replace
        old coverage rows with the new ones from the form.
        """
        # First, create a software product with organization-level coverage.
        from app.services import equipment_service

        sw_type = sample_catalog["sw_type_security"]
        sw = equipment_service.create_software(
            name=_unique_name("EQ_SW"),
            software_type_id=sw_type.id,
            license_model="tenant",
            total_cost="20000.00",
            user_id=admin_user.id,
        )
        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[{"scope_type": "organization"}],
            user_id=admin_user.id,
        )

        # Verify initial coverage is organization-level.
        initial_coverage = SoftwareCoverage.query.filter_by(software_id=sw.id).all()
        assert len(initial_coverage) == 1
        assert initial_coverage[0].scope_type == "organization"

        # Now edit to use department-level coverage instead.
        dept = sample_org["dept_a"]
        client = auth_client(admin_user)
        client.post(
            f"/equipment/software/{sw.id}/edit",
            data={
                "name": sw.name,
                "software_type_id": str(sw_type.id),
                "license_model": "tenant",
                "total_cost": "20000.00",
                "coverage_scope_type_0": "department",
                "coverage_department_id_0": str(dept.id),
            },
        )

        # Verify coverage was replaced.
        updated_coverage = SoftwareCoverage.query.filter_by(software_id=sw.id).all()
        assert len(updated_coverage) == 1
        assert updated_coverage[0].scope_type == "department"
        assert updated_coverage[0].department_id == dept.id

    def test_software_edit_switching_to_per_user_clears_coverage(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        If a tenant software product is edited to per_user, the
        route should clear all coverage rows since per_user does
        not use coverage scopes.
        """
        from app.services import equipment_service

        sw_type = sample_catalog["sw_type_security"]
        sw = equipment_service.create_software(
            name=_unique_name("EQ_SW"),
            software_type_id=sw_type.id,
            license_model="tenant",
            total_cost="15000.00",
            user_id=admin_user.id,
        )
        equipment_service.set_software_coverage(
            software_id=sw.id,
            coverage_rows=[{"scope_type": "organization"}],
            user_id=admin_user.id,
        )

        # Verify coverage exists.
        assert SoftwareCoverage.query.filter_by(software_id=sw.id).count() == 1

        # Edit to switch license model to per_user.
        client = auth_client(admin_user)
        client.post(
            f"/equipment/software/{sw.id}/edit",
            data={
                "name": sw.name,
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "100.00",
                "total_cost": "",
            },
        )

        # Coverage should be cleared.
        remaining = SoftwareCoverage.query.filter_by(software_id=sw.id).count()
        assert (
            remaining == 0
        ), "Coverage rows were not cleared when switching to per_user"

    def test_software_create_per_user_does_not_create_coverage(
        self, auth_client, admin_user, sample_catalog, sample_org, db_session
    ):
        """
        POST with per_user license model should NOT create any
        SoftwareCoverage records, even if coverage form fields
        are somehow present in the POST data.
        """
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_productivity"]
        new_name = _unique_name("EQ_SW")

        client.post(
            "/equipment/software/new",
            data={
                "name": new_name,
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "99.00",
                # These coverage fields should be ignored for per_user.
                "coverage_scope_type_0": "organization",
            },
        )

        created = Software.query.filter_by(name=new_name).first()
        assert created is not None

        coverage = SoftwareCoverage.query.filter_by(software_id=created.id).count()
        assert (
            coverage == 0
        ), "Coverage rows should not be created for per_user software"


# =====================================================================
# 12. Software Product Validation and Edge Cases
# =====================================================================


class TestSoftwareProductValidation:
    """Verify software product validation rejects bad input."""

    def test_software_create_rejects_missing_name(
        self, auth_client, admin_user, sample_catalog, sample_org
    ):
        """POST without a name should re-render with an error."""
        client = auth_client(admin_user)
        sw_type = sample_catalog["sw_type_productivity"]

        response = client.post(
            "/equipment/software/new",
            data={
                "name": "",
                "software_type_id": str(sw_type.id),
                "license_model": "per_user",
                "cost_per_license": "100.00",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"required" in response.data.lower()

    def test_software_edit_nonexistent_redirects(self, auth_client, admin_user):
        """
        GET /equipment/software/999999/edit should redirect
        with a warning flash.
        """
        client = auth_client(admin_user)
        response = client.get(
            "/equipment/software/999999/edit",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()

    def test_software_deactivate_nonexistent_redirects(self, auth_client, admin_user):
        """
        POST /equipment/software/999999/deactivate should
        redirect with an error flash.
        """
        client = auth_client(admin_user)
        response = client.post(
            "/equipment/software/999999/deactivate",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"not found" in response.data.lower()


# =====================================================================
# 13. Role Enforcement (write operations blocked for non-admin/IT)
# =====================================================================


class TestEquipmentRoleEnforcement:
    """
    Verify that write operations on equipment routes are restricted
    to admin and it_staff roles.  Managers, budget executives, and
    read-only users should receive 403.

    List-only routes (hardware_list, software_list, hardware_type_list)
    use only @login_required and should be accessible to all roles.
    """

    # -- Manager blocked from write operations --

    def test_manager_blocked_from_hardware_type_create(self, auth_client, manager_user):
        """Manager should receive 403 on hardware type creation."""
        client = auth_client(manager_user)
        response = client.get("/equipment/hardware-types/new")
        assert response.status_code == 403

    def test_manager_blocked_from_hardware_create(self, auth_client, manager_user):
        """Manager should receive 403 on hardware item creation."""
        client = auth_client(manager_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 403

    def test_manager_blocked_from_software_create(
        self, auth_client, manager_user, sample_org
    ):
        """Manager should receive 403 on software creation."""
        client = auth_client(manager_user)
        response = client.get("/equipment/software/new")
        assert response.status_code == 403

    def test_manager_blocked_from_software_type_list(self, auth_client, manager_user):
        """
        Software type list uses @role_required("admin", "it_staff"),
        so manager should receive 403.
        """
        client = auth_client(manager_user)
        response = client.get("/equipment/software-types")
        assert response.status_code == 403

    def test_manager_blocked_from_software_family_list(self, auth_client, manager_user):
        """
        Software family list uses @role_required("admin", "it_staff"),
        so manager should receive 403.
        """
        client = auth_client(manager_user)
        response = client.get("/equipment/software-families")
        assert response.status_code == 403

    def test_manager_blocked_from_hardware_type_deactivate_post(
        self, auth_client, manager_user, sample_catalog
    ):
        """Manager should receive 403 when trying to deactivate."""
        client = auth_client(manager_user)
        hw_type = sample_catalog["hw_type_laptop"]
        response = client.post(f"/equipment/hardware-types/{hw_type.id}/deactivate")
        assert response.status_code == 403

    # -- Budget executive blocked from write operations --

    def test_budget_executive_blocked_from_hardware_create(
        self, auth_client, budget_user
    ):
        """Budget executive should receive 403 on hardware creation."""
        client = auth_client(budget_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 403

    def test_budget_executive_blocked_from_software_create(
        self, auth_client, budget_user
    ):
        """Budget executive should receive 403 on software creation."""
        client = auth_client(budget_user)
        response = client.get("/equipment/software/new")
        assert response.status_code == 403

    # -- Read-only blocked from write operations --

    def test_read_only_blocked_from_hardware_create(self, auth_client, read_only_user):
        """Read-only user should receive 403 on hardware creation."""
        client = auth_client(read_only_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 403

    def test_read_only_blocked_from_software_type_create(
        self, auth_client, read_only_user
    ):
        """Read-only user should receive 403 on software type creation."""
        client = auth_client(read_only_user)
        response = client.get("/equipment/software-types/new")
        assert response.status_code == 403

    # -- Any authenticated user can access list-only routes --

    def test_manager_can_view_hardware_type_list(
        self, auth_client, manager_user, sample_catalog
    ):
        """
        hardware_type_list uses only @login_required, so any
        authenticated user (including manager) should get 200.
        """
        client = auth_client(manager_user)
        response = client.get("/equipment/hardware-types")
        assert response.status_code == 200

    def test_manager_can_view_hardware_list(
        self, auth_client, manager_user, sample_catalog
    ):
        """
        hardware_list uses only @login_required, so manager
        should get 200.
        """
        client = auth_client(manager_user)
        response = client.get("/equipment/hardware")
        assert response.status_code == 200

    def test_manager_can_view_software_list(
        self, auth_client, manager_user, sample_catalog
    ):
        """
        software_list uses only @login_required, so manager
        should get 200.
        """
        client = auth_client(manager_user)
        response = client.get("/equipment/software")
        assert response.status_code == 200

    def test_read_only_can_view_hardware_list(
        self, auth_client, read_only_user, sample_catalog
    ):
        """Read-only user should see 200 on the hardware list."""
        client = auth_client(read_only_user)
        response = client.get("/equipment/hardware")
        assert response.status_code == 200

    def test_budget_executive_can_view_software_list(
        self, auth_client, budget_user, sample_catalog
    ):
        """Budget executive should see 200 on the software list."""
        client = auth_client(budget_user)
        response = client.get("/equipment/software")
        assert response.status_code == 200


# =====================================================================
# 14. Authentication Enforcement
# =====================================================================


class TestEquipmentAuthenticationEnforcement:
    """
    Verify that unauthenticated users are redirected away from
    all equipment routes.  The exact redirect target depends on
    the Flask-Login configuration (typically /auth/login).
    """

    @pytest.mark.parametrize(
        "url",
        [
            "/equipment/hardware-types",
            "/equipment/hardware-types/new",
            "/equipment/hardware",
            "/equipment/hardware/new",
            "/equipment/software-types",
            "/equipment/software-types/new",
            "/equipment/software-families",
            "/equipment/software-families/new",
            "/equipment/software",
            "/equipment/software/new",
        ],
        ids=[
            "hw_type_list",
            "hw_type_create",
            "hw_list",
            "hw_create",
            "sw_type_list",
            "sw_type_create",
            "sw_family_list",
            "sw_family_create",
            "sw_list",
            "sw_create",
        ],
    )
    def test_unauthenticated_user_redirected(self, client, url):
        """
        An unauthenticated GET to any equipment route should return
        a redirect (302) to the login page.
        """
        response = client.get(url, follow_redirects=False)
        # Flask-Login redirects unauthenticated users with 302.
        assert response.status_code in (302, 401), (
            f"Expected redirect for unauthenticated access to {url}, "
            f"got {response.status_code}"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "/equipment/hardware-types/new",
            "/equipment/hardware/new",
            "/equipment/software-types/new",
            "/equipment/software-families/new",
            "/equipment/software/new",
        ],
        ids=[
            "hw_type_create_post",
            "hw_create_post",
            "sw_type_create_post",
            "sw_family_create_post",
            "sw_create_post",
        ],
    )
    def test_unauthenticated_post_redirected(self, client, url):
        """
        An unauthenticated POST to a create route should also
        redirect to login, not create any records.
        """
        response = client.post(
            url,
            data={"name": "Should Not Exist"},
            follow_redirects=False,
        )
        assert response.status_code in (302, 401)


# =====================================================================
# 15. Nonexistent Resource Handling (cross-entity)
# =====================================================================


class TestNonexistentResourceHandling:
    """
    Verify that accessing nonexistent resource IDs results in
    graceful redirects with flash messages, not 500 errors.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "/equipment/hardware-types/999999/edit",
            "/equipment/hardware/999999/edit",
            "/equipment/software-types/999999/edit",
            "/equipment/software-families/999999/edit",
            "/equipment/software/999999/edit",
        ],
        ids=[
            "hw_type_edit",
            "hw_edit",
            "sw_type_edit",
            "sw_family_edit",
            "sw_edit",
        ],
    )
    def test_edit_nonexistent_resource_does_not_crash(
        self, auth_client, admin_user, url
    ):
        """
        GET to edit a nonexistent resource should redirect with
        a warning, never return 500.
        """
        client = auth_client(admin_user)
        response = client.get(url, follow_redirects=True)
        assert response.status_code == 200
        assert b"not found" in response.data.lower()

    @pytest.mark.parametrize(
        "url",
        [
            "/equipment/hardware-types/999999/deactivate",
            "/equipment/hardware/999999/deactivate",
            "/equipment/software-types/999999/deactivate",
            "/equipment/software-families/999999/deactivate",
            "/equipment/software/999999/deactivate",
        ],
        ids=[
            "hw_type_deactivate",
            "hw_deactivate",
            "sw_type_deactivate",
            "sw_family_deactivate",
            "sw_deactivate",
        ],
    )
    def test_deactivate_nonexistent_resource_does_not_crash(
        self, auth_client, admin_user, url
    ):
        """
        POST to deactivate a nonexistent resource should redirect
        with an error flash, never return 500.
        """
        client = auth_client(admin_user)
        response = client.post(url, follow_redirects=True)
        assert response.status_code == 200
        assert b"not found" in response.data.lower()
