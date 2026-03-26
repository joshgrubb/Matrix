"""
Branch-gap tests for form-parsing functions in the requirements blueprint.

Targets two helper functions with significant untested branches in
``app/blueprints/requirements/routes.py``:

    - ``_parse_hardware_form`` (40 statements, 22 missing, 45% covered)
    - ``_parse_software_form`` (17 statements, 4 missing, 76% covered)

The existing tests in ``test_requirements_routes.py`` exercise the
multi-select checkbox path (Pattern 1) for hardware and the basic
happy path for software.  These tests specifically target:

    **_parse_hardware_form uncovered branches:**
    - Pattern 2: Radio-button single-select
      (``hw_type_<type_id>_selected = '<hardware_id>'``)
    - Non-numeric quantity fallback to 1
    - Non-integer hardware ID in form key (``continue`` branch)
    - Duplicate hardware ID guard (``seen_hw_ids``)
    - Notes whitespace stripping and None coercion
    - Empty radio selection (no choice made for a type)

    **_parse_software_form uncovered branches:**
    - Non-numeric quantity fallback to 1
    - Non-integer software ID in form key (``continue`` branch)
    - Notes whitespace stripping and None coercion

All tests submit real HTTP POST requests through the test client,
which exercises the full route handler including ``_parse_hardware_form``
/ ``_parse_software_form`` and the downstream
``set_position_hardware`` / ``set_position_software`` service calls.
This ensures we test the parsing AND the persistence together, not
just the parsing in isolation.

Fixture reminder (from conftest.py):
    auth_client:        Factory returning an authenticated test client.
    manager_user:       Role=manager, scope=division (div_a1).
    admin_user:         Role=admin, scope=organization.
    sample_org:         Two departments, four divisions, six positions.
    sample_catalog:     Hardware types + items, software types + items.
    create_hw_requirement: Factory creating PositionHardware records.
    create_sw_requirement: Factory creating PositionSoftware records.

    hw_type_laptop:     max_selections=1 (single-select / radio)
    hw_type_monitor:    max_selections=None (unlimited / checkboxes)
    hw_laptop_standard: belongs to hw_type_laptop, cost $1200
    hw_laptop_power:    belongs to hw_type_laptop, cost $2400
    hw_monitor_24:      belongs to hw_type_monitor, cost $350
    sw_office_e3:       per_user, $200/license
    sw_office_e5:       per_user, $400/license
    sw_antivirus:       tenant, $50000 total

Run this file in isolation::

    pytest tests/test_routes/test_requirements_branch_gaps.py -v
"""

import pytest

from app.models.requirement import PositionHardware, PositionSoftware


# =====================================================================
# 1. _parse_hardware_form -- Pattern 2: Radio-button single-select
# =====================================================================


class TestHardwareFormRadioButtonSingleSelect:
    """
    Hardware types with ``max_selections=1`` use radio buttons.
    The form field pattern is:

        hw_type_<type_id>_selected = '<hardware_id>'
        hw_<hardware_id>_quantity = '1'
        hw_<hardware_id>_notes = 'Optional note'

    The existing tests only exercise Pattern 1 (checkboxes).  These
    tests ensure the radio-button code path is fully covered.
    """

    def test_radio_button_selection_creates_requirement(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Submitting a single-select radio for hw_type_laptop should
        create a PositionHardware record for the selected item.

        hw_type_laptop has max_selections=1, so the form uses radio
        buttons with name='hw_type_<id>_selected' and value='<hw_id>'.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]
        hw_standard = sample_catalog["hw_laptop_standard"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                # Radio-button pattern: type-level key, hardware_id as value.
                f"hw_type_{hw_type.id}_selected": str(hw_standard.id),
                f"hw_{hw_standard.id}_quantity": "1",
            },
        )

        # Should redirect to software step.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "software" in location

        # Verify the database record.
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_standard.id,
        ).first()
        assert (
            req is not None
        ), "Radio-button selection did not create a PositionHardware record"
        assert req.quantity == 1

    def test_radio_button_with_notes_persists_notes(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Notes submitted alongside a radio-button selection should
        be persisted to the database.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]
        hw_power = sample_catalog["hw_laptop_power"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_type_{hw_type.id}_selected": str(hw_power.id),
                f"hw_{hw_power.id}_quantity": "1",
                f"hw_{hw_power.id}_notes": "Needs GPU for modeling",
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_power.id,
        ).first()
        assert req is not None
        assert req.notes == "Needs GPU for modeling"

    def test_radio_button_switching_selection_updates_record(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        If a position previously had hw_laptop_standard selected
        and the user switches to hw_laptop_power via the radio
        button, the old record should be removed and a new one
        created (because set_position_hardware replaces all).
        """
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]
        hw_standard = sample_catalog["hw_laptop_standard"]
        hw_power = sample_catalog["hw_laptop_power"]

        # Pre-existing requirement for the standard laptop.
        create_hw_requirement(position=pos, hardware=hw_standard, quantity=1)

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                # Now select the power laptop via radio button.
                f"hw_type_{hw_type.id}_selected": str(hw_power.id),
                f"hw_{hw_power.id}_quantity": "1",
            },
        )

        assert response.status_code == 302

        # Old selection should be gone.
        old_req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_standard.id,
        ).first()
        assert old_req is None, "Old radio selection was not removed"

        # New selection should be present.
        new_req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_power.id,
        ).first()
        assert new_req is not None, "New radio selection was not created"

    def test_empty_radio_selection_skipped(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        If a radio button group exists in the form but no option
        is selected (empty string value), the parser should skip
        it gracefully.  This exercises the ``if not hw_id_str:
        continue`` branch.
        """
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                # Empty selection for the radio group.
                f"hw_type_{hw_type.id}_selected": "",
            },
        )

        assert response.status_code == 302

        # No hardware should be saved.
        reqs = PositionHardware.query.filter_by(
            position_id=pos.id,
        ).all()
        assert len(reqs) == 0


# =====================================================================
# 2. _parse_hardware_form -- combined radio + checkbox in one POST
# =====================================================================


class TestHardwareFormMixedSelections:
    """
    A real form submission may contain BOTH radio-button selections
    (for single-select types like laptop) AND checkbox selections
    (for unlimited types like monitors) in the same POST.  Both
    patterns must be parsed correctly in a single request.
    """

    def test_radio_and_checkbox_combined_in_single_post(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Submit a radio-button laptop selection AND a checkbox
        monitor selection in the same POST.  Both should be saved.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_type_laptop = sample_catalog["hw_type_laptop"]
        hw_standard = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                # Pattern 2: radio button for laptop type.
                f"hw_type_{hw_type_laptop.id}_selected": str(hw_standard.id),
                f"hw_{hw_standard.id}_quantity": "1",
                # Pattern 1: checkbox for monitor.
                f"hw_{hw_monitor.id}_selected": "on",
                f"hw_{hw_monitor.id}_quantity": "2",
            },
        )

        assert response.status_code == 302

        # Verify both records exist.
        laptop_req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_standard.id,
        ).first()
        monitor_req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_monitor.id,
        ).first()

        assert laptop_req is not None, "Radio-selected laptop not saved"
        assert laptop_req.quantity == 1
        assert monitor_req is not None, "Checkbox-selected monitor not saved"
        assert monitor_req.quantity == 2


# =====================================================================
# 3. _parse_hardware_form -- non-numeric quantity defaults to 1
# =====================================================================


class TestHardwareFormNonNumericQuantity:
    """
    When a user submits a non-numeric quantity (e.g., 'abc'), the
    parser should fall back to quantity=1 rather than crashing with
    a ValueError.  This exercises the ``except ValueError: quantity = 1``
    branch inside the parsing function.
    """

    def test_non_numeric_quantity_defaults_to_one(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Submit 'abc' as the quantity for a checkbox item.  The parser
        should default to 1, not crash with a 500 error.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "abc",
            },
        )

        # Should succeed (redirect), not 500.
        assert response.status_code == 302

        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw.id,
        ).first()
        assert req is not None
        # Quantity should default to 1 when parsing fails.
        assert req.quantity == 1

    def test_zero_quantity_becomes_one(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        A quantity of 0 should be coerced to 1 by the
        ``max(1, int(quantity))`` expression in the parser.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "0",
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw.id,
        ).first()
        assert req is not None
        assert (
            req.quantity == 1
        ), "A quantity of 0 should be coerced to 1 by max(1, int(qty))"

    def test_negative_quantity_becomes_one(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        A negative quantity should be coerced to 1 by
        ``max(1, int(quantity))``.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "-5",
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw.id,
        ).first()
        assert req is not None
        assert req.quantity == 1

    def test_non_numeric_quantity_on_radio_defaults_to_one(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Non-numeric quantity in a radio-button (Pattern 2) submission
        should also default to 1.  This tests the same fallback branch
        but triggered via the radio-button code path.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]
        hw_standard = sample_catalog["hw_laptop_standard"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_type_{hw_type.id}_selected": str(hw_standard.id),
                f"hw_{hw_standard.id}_quantity": "not_a_number",
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_standard.id,
        ).first()
        assert req is not None
        assert req.quantity == 1


# =====================================================================
# 4. _parse_hardware_form -- notes whitespace and None coercion
# =====================================================================


class TestHardwareFormNotesEdgeCases:
    """
    The parser strips whitespace from notes and coerces empty
    strings to None (``notes.strip() or None``).  These tests
    verify that edge cases are handled.
    """

    def test_whitespace_only_notes_become_none(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Notes consisting of only whitespace should be stored as
        NULL (None), not as an empty or whitespace-only string.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
                f"hw_{hw.id}_notes": "   ",
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw.id,
        ).first()
        assert req is not None
        assert req.notes is None, "Whitespace-only notes should be coerced to None"

    def test_notes_with_leading_trailing_whitespace_are_stripped(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Notes with leading/trailing whitespace should be stripped
        before storage.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
                f"hw_{hw.id}_notes": "  Dual-arm mount  ",
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw.id,
        ).first()
        assert req is not None
        assert req.notes == "Dual-arm mount"

    def test_missing_notes_field_becomes_none(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        When the notes field is not submitted at all, the parser
        should produce None (not crash from a missing key).
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
                # Intentionally no notes field submitted.
            },
        )

        assert response.status_code == 302
        req = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw.id,
        ).first()
        assert req is not None
        assert req.notes is None


# =====================================================================
# 5. _parse_hardware_form -- duplicate hardware ID deduplication
# =====================================================================


class TestHardwareFormDuplicateDedup:
    """
    If the same hardware_id appears in both the checkbox pattern
    AND the radio-button pattern (due to a malformed form or
    crafted request), the ``seen_hw_ids`` set should prevent
    duplicate items from being created.
    """

    def test_duplicate_hw_id_produces_only_one_record(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Submit the same hardware_id via both checkbox and radio
        patterns.  Only one PositionHardware record should be
        created (the first one parsed wins).

        NOTE: hw_type_laptop has max_selections=1, so the quantity
        must be 1 to pass the service-layer validation.  The test
        verifies dedup (one record, not two), not quantity handling.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]
        hw_standard = sample_catalog["hw_laptop_standard"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                # Pattern 1: checkbox for the same item.
                f"hw_{hw_standard.id}_selected": "on",
                f"hw_{hw_standard.id}_quantity": "1",
                # Pattern 2: radio for the same item's type.
                f"hw_type_{hw_type.id}_selected": str(hw_standard.id),
            },
        )

        assert response.status_code == 302

        reqs = PositionHardware.query.filter_by(
            position_id=pos.id,
            hardware_id=hw_standard.id,
        ).all()
        # The dedup guard should ensure only one record, not two.
        assert (
            len(reqs) == 1
        ), f"Expected 1 record but found {len(reqs)} -- dedup failed"


# =====================================================================
# 6. _parse_hardware_form -- invalid (non-integer) hardware ID
# =====================================================================


class TestHardwareFormInvalidHardwareId:
    """
    If a crafted request includes a form key like
    ``hw_notanumber_selected``, the parser should skip it via the
    ``except ValueError: continue`` branch and not crash.
    """

    def test_non_integer_hw_id_in_checkbox_is_skipped(
        self,
        auth_client,
        manager_user,
        sample_org,
        db_session,
    ):
        """
        A malformed checkbox key with a non-integer ID should be
        silently skipped without causing a 500 error.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                "hw_notanumber_selected": "on",
                "hw_notanumber_quantity": "1",
            },
        )

        # Should succeed (redirect), not crash.
        assert response.status_code == 302

        # No requirements should have been created.
        reqs = PositionHardware.query.filter_by(
            position_id=pos.id,
        ).all()
        assert len(reqs) == 0

    def test_non_integer_hw_id_in_radio_is_skipped(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        A radio-button value that is not a valid integer should be
        silently skipped.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_type = sample_catalog["hw_type_laptop"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_type_{hw_type.id}_selected": "not_an_int",
            },
        )

        assert response.status_code == 302
        reqs = PositionHardware.query.filter_by(
            position_id=pos.id,
        ).all()
        assert len(reqs) == 0


# =====================================================================
# 7. _parse_software_form -- non-numeric quantity defaults to 1
# =====================================================================


class TestSoftwareFormNonNumericQuantity:
    """
    Same as the hardware non-numeric tests, but for the software
    form parser.  Exercises the ``except ValueError: quantity = 1``
    branch in ``_parse_software_form``.
    """

    def test_non_numeric_quantity_defaults_to_one(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        A non-numeric quantity for software should default to 1.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "xyz",
            },
        )

        assert response.status_code == 302
        req = PositionSoftware.query.filter_by(
            position_id=pos.id,
            software_id=sw.id,
        ).first()
        assert req is not None
        assert req.quantity == 1

    def test_zero_quantity_becomes_one(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """A software quantity of 0 should be coerced to 1."""
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "0",
            },
        )

        assert response.status_code == 302
        req = PositionSoftware.query.filter_by(
            position_id=pos.id,
            software_id=sw.id,
        ).first()
        assert req is not None
        assert req.quantity == 1


# =====================================================================
# 8. _parse_software_form -- invalid (non-integer) software ID
# =====================================================================


class TestSoftwareFormInvalidSoftwareId:
    """
    If a crafted request includes ``sw_badvalue_selected``, the
    parser should skip it.
    """

    def test_non_integer_sw_id_is_skipped(
        self,
        auth_client,
        manager_user,
        sample_org,
        db_session,
    ):
        """
        A malformed software key with a non-integer ID should be
        silently skipped without causing a 500 error.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                "sw_badvalue_selected": "on",
                "sw_badvalue_quantity": "1",
            },
        )

        assert response.status_code == 302
        reqs = PositionSoftware.query.filter_by(
            position_id=pos.id,
        ).all()
        assert len(reqs) == 0


# =====================================================================
# 9. _parse_software_form -- notes whitespace and None coercion
# =====================================================================


class TestSoftwareFormNotesEdgeCases:
    """
    Verify the software parser handles notes edge cases the same
    way as the hardware parser.
    """

    def test_whitespace_only_notes_become_none(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """Software notes of only whitespace should be stored as None."""
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "1",
                f"sw_{sw.id}_notes": "   ",
            },
        )

        assert response.status_code == 302
        req = PositionSoftware.query.filter_by(
            position_id=pos.id,
            software_id=sw.id,
        ).first()
        assert req is not None
        assert req.notes is None

    def test_missing_notes_field_becomes_none(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """Software with no notes field submitted should store None."""
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e5"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "1",
                # No notes key at all.
            },
        )

        assert response.status_code == 302
        req = PositionSoftware.query.filter_by(
            position_id=pos.id,
            software_id=sw.id,
        ).first()
        assert req is not None
        assert req.notes is None
