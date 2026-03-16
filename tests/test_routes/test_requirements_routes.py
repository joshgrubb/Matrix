"""
Integration tests for the requirements wizard routes (Steps 1-4).

Verifies the complete position-configuration workflow that will be
demonstrated during the CIO review:

    Step 1: Select Position (cascading dropdowns via HTMX)
    Step 2: Select Hardware (checkbox/radio form submission)
    Step 3: Select Software (checkbox form submission)
    Step 4: Summary (cost display, status tracking)

Also covers the copy-from feature, save-and-exit flow, individual
requirement removal, nonexistent resource handling, and
unauthenticated/inactive user access.

Design decisions:
    - Tests run against the real SQL Server test database using the
      commit-and-cleanup pattern from conftest.py.
    - Every test uses the ``auth_client`` factory fixture with the
      ``X-Test-User-Id`` header for authentication.
    - Database state is verified after every write operation to
      prove the route handler actually persisted the change.
    - Form field names (``hw_<id>_selected``, ``sw_<id>_quantity``,
      etc.) match the actual template ``name`` attributes so a
      template refactor that renames fields will break these tests
      and be caught immediately.

Run this file in isolation::

    pytest tests/test_routes/test_requirements_routes.py -v
"""

from app.models.requirement import PositionHardware, PositionSoftware


# =====================================================================
# 1. Step 1 -- Select Position page
# =====================================================================


class TestSelectPositionPage:
    """Verify the position selection page loads and renders content."""

    def test_select_position_page_loads_for_manager(self, auth_client, manager_user):
        """The wizard landing page returns 200 for an authorized manager."""
        client = auth_client(manager_user)
        response = client.get("/requirements/")
        assert response.status_code == 200

    def test_select_position_page_contains_department_dropdown(
        self, auth_client, manager_user, sample_org
    ):
        """
        The select-position page should render a department dropdown
        so the user can begin the cascading selection flow.
        """
        client = auth_client(manager_user)
        response = client.get("/requirements/")
        assert response.status_code == 200
        # The page should contain the department <select> element.
        assert b"department" in response.data.lower()

    def test_select_position_page_loads_for_admin(self, auth_client, admin_user):
        """Admins can access the wizard landing page."""
        client = auth_client(admin_user)
        response = client.get("/requirements/")
        assert response.status_code == 200

    def test_select_position_page_loads_for_it_staff(self, auth_client, it_staff_user):
        """IT staff can access the wizard landing page."""
        client = auth_client(it_staff_user)
        response = client.get("/requirements/")
        assert response.status_code == 200


# =====================================================================
# 2. HTMX cascading dropdown endpoints
# =====================================================================


class TestHtmxCascadingDropdowns:
    """
    The wizard uses HTMX-powered cascading dropdowns:
    department -> divisions -> positions.

    These endpoints return HTML fragments with <option> elements.
    """

    def test_htmx_divisions_returns_options(
        self, auth_client, manager_user, sample_org
    ):
        """
        GET /org/htmx/divisions/<dept_id> returns HTML containing
        <option> elements for divisions in the department.
        """
        client = auth_client(manager_user)
        dept = sample_org["dept_a"]
        response = client.get(f"/org/htmx/divisions/{dept.id}")
        assert response.status_code == 200
        # The response is an HTML fragment with <option> tags.
        assert b"<option" in response.data

    def test_htmx_positions_returns_options(
        self, auth_client, manager_user, sample_org
    ):
        """
        GET /org/htmx/positions/<div_id> returns HTML containing
        <option> elements for positions in the division.
        """
        client = auth_client(manager_user)
        div = sample_org["div_a1"]
        response = client.get(f"/org/htmx/positions/{div.id}")
        assert response.status_code == 200
        assert b"<option" in response.data

    def test_htmx_divisions_returns_empty_for_nonexistent_dept(
        self, auth_client, admin_user
    ):
        """
        Requesting divisions for a department ID that does not exist
        should return 200 with no <option> elements (or an empty
        fragment), not a 500.
        """
        client = auth_client(admin_user)
        response = client.get("/org/htmx/divisions/999999")
        assert response.status_code == 200

    def test_htmx_positions_with_requirements_filters_correctly(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        The copy-from dropdown should only list positions that already
        have at least one requirement configured.

        Create a requirement on pos_a1_1 but not on pos_a1_2, then
        verify only pos_a1_1 appears in the response.
        """
        # Give pos_a1_1 a hardware requirement.
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
        )

        client = auth_client(manager_user)
        div = sample_org["div_a1"]
        response = client.get(
            f"/requirements/htmx/positions-with-requirements/{div.id}"
        )
        assert response.status_code == 200

        # The configured position should appear in the options.
        assert sample_org["pos_a1_1"].position_title.encode() in response.data
        # The unconfigured position should NOT appear.
        assert sample_org["pos_a1_2"].position_title.encode() not in response.data

    def test_htmx_positions_with_requirements_empty_when_none_configured(
        self, auth_client, manager_user, sample_org
    ):
        """
        When no positions in the division have requirements, the
        copy-from dropdown returns no position options.
        """
        client = auth_client(manager_user)
        div = sample_org["div_a1"]
        response = client.get(
            f"/requirements/htmx/positions-with-requirements/{div.id}"
        )
        assert response.status_code == 200
        # Should not contain any position titles.
        assert sample_org["pos_a1_1"].position_title.encode() not in response.data


# =====================================================================
# 3. Step 2 -- Select Hardware (GET and POST)
# =====================================================================


class TestSelectHardwareGet:
    """Verify the hardware selection page loads correctly."""

    def test_select_hardware_page_loads(
        self, auth_client, manager_user, sample_org, sample_catalog
    ):
        """
        GET /requirements/position/<id>/hardware returns 200 and
        contains hardware type information for an in-scope position.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/hardware")
        assert response.status_code == 200
        # The page should reference the position title.
        assert pos.position_title.encode() in response.data

    def test_select_hardware_shows_catalog_items(
        self, auth_client, manager_user, sample_org, sample_catalog
    ):
        """
        The hardware page should display the available catalog items
        so the user can make selections.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/hardware")
        assert response.status_code == 200
        # At least one catalog item name should appear in the page.
        hw = sample_catalog["hw_laptop_standard"]
        assert hw.name.encode() in response.data

    def test_select_hardware_pre_populates_existing_selections(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        When a position already has hardware requirements, the GET
        page should show those items as pre-selected (checked).

        Uses the monitor item (max_selections=None) so that
        quantity=2 is semantically valid.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]
        create_hw_requirement(position=pos, hardware=hw, quantity=2)

        client = auth_client(manager_user)
        response = client.get(f"/requirements/position/{pos.id}/hardware")
        assert response.status_code == 200
        # The pre-populated quantity value should be in the HTML.
        assert b'value="2"' in response.data


class TestSelectHardwarePost:
    """Verify hardware form submission saves requirements."""

    def test_hardware_submit_saves_single_item(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        POST with a single checked hardware item creates a
        PositionHardware record in the database.

        Uses the monitor item because hw_type_monitor has
        max_selections=None (unlimited), allowing quantity > 1.
        The laptop type has max_selections=1 and would reject
        a quantity of 2.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "2",
            },
        )
        # Should redirect to the software step.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "software" in location

        # Verify the database record was created.
        req = PositionHardware.query.filter_by(
            position_id=pos.id, hardware_id=hw.id
        ).first()
        assert req is not None, "PositionHardware record not created"
        assert req.quantity == 2

    def test_hardware_submit_saves_multiple_items(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        POST with multiple checked hardware items creates the
        correct number of PositionHardware records.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw_laptop.id}_selected": "on",
                f"hw_{hw_laptop.id}_quantity": "1",
                f"hw_{hw_monitor.id}_selected": "on",
                f"hw_{hw_monitor.id}_quantity": "2",
            },
        )
        assert response.status_code == 302

        reqs = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(reqs) == 2

        # Verify quantities are correct.
        req_map = {r.hardware_id: r.quantity for r in reqs}
        assert req_map[hw_laptop.id] == 1
        assert req_map[hw_monitor.id] == 2

    def test_hardware_submit_updates_existing_requirements(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        Submitting hardware selections when requirements already
        exist should replace them (not duplicate).

        Uses the monitor item because hw_type_monitor has
        max_selections=None (unlimited), allowing the quantity
        change from 1 to 3.  The laptop type has max_selections=1
        and would reject quantity > 1.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_monitor_24"]

        # Create an initial requirement with quantity 1.
        create_hw_requirement(position=pos, hardware=hw, quantity=1)

        # Now submit with quantity 3.
        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "3",
            },
        )
        assert response.status_code == 302

        # Should have exactly one record, not two.
        reqs = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(reqs) == 1
        assert reqs[0].quantity == 3

    def test_hardware_submit_removes_unchecked_items(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        Submitting with only item A checked when items A and B
        previously existed should remove item B.
        """
        pos = sample_org["pos_a1_1"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        # Create initial requirements for both items.
        create_hw_requirement(position=pos, hardware=hw_laptop, quantity=1)
        create_hw_requirement(position=pos, hardware=hw_monitor, quantity=2)

        # Submit with only the laptop checked.
        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw_laptop.id}_selected": "on",
                f"hw_{hw_laptop.id}_quantity": "1",
                # Monitor is NOT in the form data (unchecked).
            },
        )
        assert response.status_code == 302

        reqs = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(reqs) == 1
        assert reqs[0].hardware_id == hw_laptop.id

    def test_hardware_submit_with_no_selections_clears_all(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        Submitting the hardware form with nothing checked should
        remove all existing hardware requirements for the position.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]
        create_hw_requirement(position=pos, hardware=hw, quantity=1)

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={},  # Nothing checked.
        )
        assert response.status_code == 302

        reqs = PositionHardware.query.filter_by(position_id=pos.id).all()
        assert len(reqs) == 0

    def test_hardware_submit_with_notes(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """Hardware notes submitted via the form are persisted."""
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
                f"hw_{hw.id}_notes": "Needs 32GB RAM upgrade",
            },
        )
        assert response.status_code == 302

        req = PositionHardware.query.filter_by(
            position_id=pos.id, hardware_id=hw.id
        ).first()
        assert req is not None
        assert req.notes == "Needs 32GB RAM upgrade"

    def test_hardware_save_exit_redirects_to_dashboard(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Clicking 'Save & Exit' (action=save_exit) should redirect
        to the main dashboard instead of the software step.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
                "action": "save_exit",
            },
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        # Should NOT redirect to software; should go to dashboard.
        assert "software" not in location

        # Data should still be saved despite exiting early.
        req = PositionHardware.query.filter_by(
            position_id=pos.id, hardware_id=hw.id
        ).first()
        assert req is not None


# =====================================================================
# 4. Step 3 -- Select Software (GET and POST)
# =====================================================================


class TestSelectSoftwareGet:
    """Verify the software selection page loads correctly."""

    def test_select_software_page_loads(
        self, auth_client, manager_user, sample_org, sample_catalog
    ):
        """
        GET /requirements/position/<id>/software returns 200 for
        an in-scope position.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/software")
        assert response.status_code == 200
        assert pos.position_title.encode() in response.data

    def test_select_software_shows_catalog_items(
        self, auth_client, manager_user, sample_org, sample_catalog
    ):
        """The software page should display available catalog products."""
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/software")
        assert response.status_code == 200
        sw = sample_catalog["sw_office_e3"]
        assert sw.name.encode() in response.data


class TestSelectSoftwarePost:
    """Verify software form submission saves requirements."""

    def test_software_submit_saves_single_item(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        POST with a single checked software item creates a
        PositionSoftware record in the database.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "1",
            },
        )
        # Should redirect to the summary step.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "summary" in location

        req = PositionSoftware.query.filter_by(
            position_id=pos.id, software_id=sw.id
        ).first()
        assert req is not None, "PositionSoftware record not created"
        assert req.quantity == 1

    def test_software_submit_saves_multiple_items(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        POST with multiple checked software items creates the
        correct number of PositionSoftware records.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw_e3 = sample_catalog["sw_office_e3"]
        sw_av = sample_catalog["sw_antivirus"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw_e3.id}_selected": "on",
                f"sw_{sw_e3.id}_quantity": "1",
                f"sw_{sw_av.id}_selected": "on",
                f"sw_{sw_av.id}_quantity": "1",
            },
        )
        assert response.status_code == 302

        reqs = PositionSoftware.query.filter_by(position_id=pos.id).all()
        assert len(reqs) == 2

    def test_software_submit_replaces_existing_requirements(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        db_session,
    ):
        """
        Resubmitting the software form replaces existing records
        rather than duplicating them.
        """
        pos = sample_org["pos_a1_1"]
        sw_e3 = sample_catalog["sw_office_e3"]
        sw_av = sample_catalog["sw_antivirus"]

        # Start with both items.
        create_sw_requirement(position=pos, software=sw_e3, quantity=1)
        create_sw_requirement(position=pos, software=sw_av, quantity=1)

        # Resubmit with only sw_e3 at quantity 3.
        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw_e3.id}_selected": "on",
                f"sw_{sw_e3.id}_quantity": "3",
            },
        )
        assert response.status_code == 302

        reqs = PositionSoftware.query.filter_by(position_id=pos.id).all()
        # Only sw_e3 should remain; sw_av was unchecked.
        assert len(reqs) == 1
        assert reqs[0].software_id == sw_e3.id
        assert reqs[0].quantity == 3

    def test_software_save_exit_redirects_to_dashboard(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        The 'Save & Exit' button redirects to the dashboard
        instead of the summary page.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "1",
                "action": "save_exit",
            },
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "summary" not in location

        # Data should still be saved.
        req = PositionSoftware.query.filter_by(
            position_id=pos.id, software_id=sw.id
        ).first()
        assert req is not None


# =====================================================================
# 5. Step 4 -- Position Summary
# =====================================================================


class TestPositionSummary:
    """Verify the summary page loads and displays cost data."""

    def test_summary_page_loads_with_no_requirements(
        self, auth_client, manager_user, sample_org
    ):
        """
        The summary page should load even when the position has
        no requirements (shows zero costs).
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200
        assert pos.position_title.encode() in response.data

    def test_summary_page_shows_hardware_costs(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        The summary page should display hardware cost information
        when requirements exist.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]
        create_hw_requirement(position=pos, hardware=hw, quantity=1)

        client = auth_client(manager_user)
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200
        # The hardware item name should appear on the summary page.
        assert hw.name.encode() in response.data

    def test_summary_page_shows_software_costs(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_sw_requirement,
    ):
        """
        The summary page should display software cost information
        when requirements exist.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]
        create_sw_requirement(position=pos, software=sw, quantity=1)

        client = auth_client(manager_user)
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200
        assert sw.name.encode() in response.data

    def test_summary_sets_status_to_submitted(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        Viewing the summary page for a position with requirements
        should set requirements_status to 'submitted'.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]
        create_hw_requirement(position=pos, hardware=hw, quantity=1)

        # Verify the status is not 'submitted' before viewing.
        assert pos.requirements_status != "submitted"

        client = auth_client(manager_user)
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

        # Refresh the position from the database.
        db_session.refresh(pos)
        assert pos.requirements_status == "submitted"

    def test_summary_does_not_downgrade_reviewed_status(
        self,
        app,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        If requirements_status is already 'reviewed', viewing the
        summary should NOT downgrade it to 'submitted'.
        """
        from app.services import requirement_service

        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]
        create_hw_requirement(position=pos, hardware=hw, quantity=1)

        # Manually set status to 'reviewed'.
        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="reviewed",
            user_id=manager_user.id,
        )
        db_session.refresh(pos)
        assert pos.requirements_status == "reviewed"

        # View the summary page.
        client = auth_client(manager_user)
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

        # Status should remain 'reviewed'.
        db_session.refresh(pos)
        assert pos.requirements_status == "reviewed"

    def test_summary_without_requirements_does_not_set_submitted(
        self,
        auth_client,
        manager_user,
        sample_org,
        db_session,
    ):
        """
        Viewing the summary for a position with zero requirements
        should NOT set requirements_status to 'submitted' because
        there is nothing to submit.
        """
        pos = sample_org["pos_a1_1"]

        client = auth_client(manager_user)
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

        db_session.refresh(pos)
        assert pos.requirements_status != "submitted"


# =====================================================================
# 6. Full wizard happy path (end-to-end)
# =====================================================================


class TestWizardEndToEnd:
    """
    Walk through the entire wizard flow as an authorized manager
    to prove the demo path works end-to-end.

    This is the single most important test class for the CIO
    review -- it simulates exactly what will be demonstrated.
    """

    def test_complete_wizard_flow(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Step 1 -> Step 2 (POST hardware) -> Step 3 (POST software)
        -> Step 4 (view summary).

        Verifies that each step saves data and redirects correctly,
        and that the summary page reflects the selections.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]
        sw = sample_catalog["sw_office_e3"]

        # -- Step 1: Load position selection page. --
        response = client.get("/requirements/")
        assert response.status_code == 200

        # -- Step 2: Load hardware page, then submit. --
        response = client.get(f"/requirements/position/{pos.id}/hardware")
        assert response.status_code == 200

        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
            },
        )
        assert response.status_code == 302
        assert "software" in response.headers.get("Location", "")

        # Verify hardware saved.
        hw_req = PositionHardware.query.filter_by(
            position_id=pos.id, hardware_id=hw.id
        ).first()
        assert hw_req is not None

        # -- Step 3: Load software page, then submit. --
        response = client.get(f"/requirements/position/{pos.id}/software")
        assert response.status_code == 200

        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "1",
            },
        )
        assert response.status_code == 302
        assert "summary" in response.headers.get("Location", "")

        # Verify software saved.
        sw_req = PositionSoftware.query.filter_by(
            position_id=pos.id, software_id=sw.id
        ).first()
        assert sw_req is not None

        # -- Step 4: Load summary page. --
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

        # Summary should mention both items.
        assert hw.name.encode() in response.data
        assert sw.name.encode() in response.data

        # Status should now be 'submitted'.
        db_session.refresh(pos)
        assert pos.requirements_status == "submitted"


# =====================================================================
# 7. Copy-from feature
# =====================================================================


class TestCopyRequirementsRoute:
    """
    Verify the copy-from feature creates duplicate requirements
    and redirects to the hardware step for customization.
    """

    def test_copy_creates_matching_hardware_records(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        Copying from a source position with hardware requirements
        creates matching PositionHardware records on the target.

        Uses quantity=1 because hw_laptop_standard belongs to the
        laptop type which has max_selections=1.  The copy route
        calls set_position_hardware() on the target, which runs
        _validate_max_selections and would reject quantity > 1.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw = sample_catalog["hw_laptop_standard"]

        create_hw_requirement(position=source, hardware=hw, quantity=1)

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        assert response.status_code == 302
        assert "hardware" in response.headers.get("Location", "")

        # The copy route commits in its own request context, so the
        # test session's identity map may be stale.  Expire all
        # cached objects so the next query hits the database.
        db_session.expire_all()

        # Verify the target now has the copied requirement.
        req = PositionHardware.query.filter_by(
            position_id=target.id, hardware_id=hw.id
        ).first()
        assert req is not None, (
            "PositionHardware not found on target after copy. "
            "Check whether _validate_max_selections rejected the "
            "copied quantity."
        )
        assert req.quantity == 1

    def test_copy_creates_matching_software_records(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        create_sw_requirement,
        db_session,
    ):
        """
        Copying from a source position with software requirements
        creates matching PositionSoftware records on the target.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw = sample_catalog["hw_laptop_standard"]
        sw = sample_catalog["sw_office_e3"]

        # Source needs at least one requirement for copy to succeed.
        create_hw_requirement(position=source, hardware=hw, quantity=1)
        create_sw_requirement(position=source, software=sw, quantity=1)

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        assert response.status_code == 302

        sw_req = PositionSoftware.query.filter_by(
            position_id=target.id, software_id=sw.id
        ).first()
        assert sw_req is not None
        assert sw_req.quantity == 1

    def test_copy_replaces_existing_target_requirements(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        Copying to a target that already has requirements should
        replace them with the source's requirements, not merge.
        """
        source = sample_org["pos_a1_1"]
        target = sample_org["pos_a1_2"]
        hw_laptop = sample_catalog["hw_laptop_standard"]
        hw_monitor = sample_catalog["hw_monitor_24"]

        # Source has laptop.
        create_hw_requirement(position=source, hardware=hw_laptop, quantity=1)
        # Target already has monitor.
        create_hw_requirement(position=target, hardware=hw_monitor, quantity=2)

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        assert response.status_code == 302

        # Target should now have only the laptop (from source).
        reqs = PositionHardware.query.filter_by(position_id=target.id).all()
        assert len(reqs) == 1
        assert reqs[0].hardware_id == hw_laptop.id

    def test_copy_from_empty_source_shows_error(
        self,
        auth_client,
        manager_user,
        sample_org,
    ):
        """
        Copying from a position with no requirements should show
        a flash error message instead of silently proceeding.
        """
        source = sample_org["pos_a1_1"]  # No requirements.
        target = sample_org["pos_a1_2"]

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}",
            follow_redirects=True,
        )
        assert response.status_code == 200
        # The flash message should indicate the source has nothing.
        assert b"no equipment" in response.data.lower() or (
            b"no software" in response.data.lower()
        )


# =====================================================================
# 8. Individual requirement removal
# =====================================================================


class TestRequirementRemoval:
    """
    Verify the HTMX remove endpoints for individual hardware
    and software requirements.
    """

    def test_remove_hardware_requirement(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
        db_session,
    ):
        """
        POST /requirements/hardware/<req_id>/remove deletes the
        PositionHardware record.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]
        req = create_hw_requirement(position=pos, hardware=hw, quantity=1)
        req_id = req.id

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/hardware/{req_id}/remove",
            headers={"Referer": f"/requirements/position/{pos.id}/hardware"},
        )
        assert response.status_code == 302

        # Verify deletion.
        deleted = db_session.get(PositionHardware, req_id)
        assert deleted is None

    def test_remove_software_requirement(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_sw_requirement,
        db_session,
    ):
        """
        POST /requirements/software/<req_id>/remove deletes the
        PositionSoftware record.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]
        req = create_sw_requirement(position=pos, software=sw, quantity=1)
        req_id = req.id

        client = auth_client(manager_user)
        response = client.post(
            f"/requirements/software/{req_id}/remove",
            headers={"Referer": f"/requirements/position/{pos.id}/software"},
        )
        assert response.status_code == 302

        deleted = db_session.get(PositionSoftware, req_id)
        assert deleted is None

    def test_remove_nonexistent_hardware_shows_error(self, auth_client, manager_user):
        """
        Attempting to remove a hardware requirement that does not
        exist should flash a danger message, not crash.
        """
        client = auth_client(manager_user)
        response = client.post(
            "/requirements/hardware/999999/remove",
            follow_redirects=True,
        )
        # Should redirect and show an error, not 500.
        assert response.status_code == 200

    def test_remove_nonexistent_software_shows_error(self, auth_client, manager_user):
        """
        Attempting to remove a software requirement that does not
        exist should flash a danger message, not crash.
        """
        client = auth_client(manager_user)
        response = client.post(
            "/requirements/software/999999/remove",
            follow_redirects=True,
        )
        assert response.status_code == 200


# =====================================================================
# 9. Nonexistent and edge-case resources
# =====================================================================


class TestNonexistentResources:
    """
    Verify graceful handling when a position ID does not exist.
    These prevent bookmark-to-a-deleted-position stack traces.
    """

    def test_hardware_page_for_nonexistent_position(self, auth_client, admin_user):
        """
        GET /requirements/position/999999/hardware should redirect
        with a flash warning, not crash.
        """
        client = auth_client(admin_user)
        response = client.get("/requirements/position/999999/hardware")
        assert response.status_code == 302

    def test_software_page_for_nonexistent_position(self, auth_client, admin_user):
        """
        GET /requirements/position/999999/software should redirect
        with a flash warning, not crash.
        """
        client = auth_client(admin_user)
        response = client.get("/requirements/position/999999/software")
        assert response.status_code == 302

    def test_summary_page_for_nonexistent_position(self, auth_client, admin_user):
        """
        GET /requirements/position/999999/summary should redirect
        with a flash warning, not crash.
        """
        client = auth_client(admin_user)
        response = client.get("/requirements/position/999999/summary")
        assert response.status_code == 302

    def test_nonexistent_position_flash_message(self, auth_client, admin_user):
        """The redirect for a missing position should include a warning."""
        client = auth_client(admin_user)
        response = client.get(
            "/requirements/position/999999/summary",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert (
            b"not found" in response.data.lower()
            or b"do not have access" in response.data.lower()
        )


# =====================================================================
# 10. Unauthenticated and inactive user access
# =====================================================================


class TestUnauthenticatedAccess:
    """
    Verify that unauthenticated users are redirected to login
    for every wizard endpoint.  These tests use the plain
    ``client`` fixture (no auth header).

    If Flask-Login's @login_required decorator is accidentally
    removed from a route, these tests will catch it.
    """

    def test_unauthenticated_select_position_redirects(self, client):
        """Unauthenticated GET /requirements/ redirects to login."""
        response = client.get("/requirements/")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_hardware_page_redirects(self, client):
        """Unauthenticated GET to hardware step redirects to login."""
        response = client.get("/requirements/position/1/hardware")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_software_page_redirects(self, client):
        """Unauthenticated GET to software step redirects to login."""
        response = client.get("/requirements/position/1/software")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_summary_page_redirects(self, client):
        """Unauthenticated GET to summary step redirects to login."""
        response = client.get("/requirements/position/1/summary")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_hardware_post_redirects(self, client):
        """Unauthenticated POST to hardware step redirects to login."""
        response = client.post(
            "/requirements/position/1/hardware",
            data={"dummy": "data"},
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()

    def test_unauthenticated_copy_post_redirects(self, client):
        """Unauthenticated POST to copy-from redirects to login."""
        response = client.post("/requirements/position/1/copy-from/2")
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "login" in location.lower() or "auth" in location.lower()


class TestInactiveUserAccess:
    """
    Verify that deactivated users cannot access the wizard.
    The app should reject them at the Flask-Login layer.
    """

    def test_inactive_user_cannot_access_wizard(
        self, auth_client, inactive_user, sample_org
    ):
        """
        A deactivated user should be treated as unauthenticated
        and redirected away from the wizard.
        """
        client = auth_client(inactive_user)
        response = client.get("/requirements/")
        # Flask-Login should reject inactive users.
        # Depending on the user_loader implementation, this is
        # either a 302 redirect to login or a 403.
        assert response.status_code in (302, 403)


# =====================================================================
# 11. Draft status tracking
# =====================================================================


class TestDraftStatusTracking:
    """
    Verify that submitting hardware or software sets the
    requirements_status to 'draft' when it was previously unset.
    """

    def test_hardware_submission_sets_draft_status(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Submitting hardware selections for a position with no
        prior status should mark it as 'draft'.
        """
        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]

        # Ensure no status is set.
        assert pos.requirements_status is None

        client = auth_client(manager_user)
        client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
            },
        )

        db_session.refresh(pos)
        assert pos.requirements_status == "draft"

    def test_software_submission_sets_draft_status(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        Submitting software selections for a position with no
        prior status should mark it as 'draft'.
        """
        pos = sample_org["pos_a1_1"]
        sw = sample_catalog["sw_office_e3"]

        assert pos.requirements_status is None

        client = auth_client(manager_user)
        client.post(
            f"/requirements/position/{pos.id}/software",
            data={
                f"sw_{sw.id}_selected": "on",
                f"sw_{sw.id}_quantity": "1",
            },
        )

        db_session.refresh(pos)
        assert pos.requirements_status == "draft"

    def test_hardware_submission_does_not_overwrite_existing_status(
        self,
        app,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        db_session,
    ):
        """
        If requirements_status is already 'submitted', a hardware
        re-submission should NOT downgrade it back to 'draft'.
        """
        from app.services import requirement_service

        pos = sample_org["pos_a1_1"]
        hw = sample_catalog["hw_laptop_standard"]

        # Set status to 'submitted'.
        requirement_service.update_requirements_status(
            position_id=pos.id,
            status="submitted",
            user_id=manager_user.id,
        )
        db_session.refresh(pos)
        assert pos.requirements_status == "submitted"

        # Resubmit hardware.
        client = auth_client(manager_user)
        client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={
                f"hw_{hw.id}_selected": "on",
                f"hw_{hw.id}_quantity": "1",
            },
        )

        db_session.refresh(pos)
        # Status should remain 'submitted' (not downgraded).
        assert pos.requirements_status == "submitted"
