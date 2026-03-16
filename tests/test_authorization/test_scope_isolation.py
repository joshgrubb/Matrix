"""
Authorization scope isolation tests for PositionMatrix.

These tests verify that users can ONLY access data within their
authorized organizational scope.  A scope leak -- where a department
head sees another department's budget numbers or a read-only user
modifies data -- is the single worst bug to demonstrate during a
CIO review.

The test file is organized from the inside out:

    1. Service-layer scope helpers (the foundation everything relies on).
    2. Role-based access control on routes (decorator enforcement).
    3. Division-level scope isolation (the narrowest boundary).
    4. Department-level scope isolation.
    5. Organization-wide scope (positive: should see everything).
    6. Cross-scope operations (copy-from, HTMX partials).
    7. Report and export access control.
    8. Edge cases (deactivated users, missing scopes).

Run this file in isolation::

    pytest tests/test_authorization/test_scope_isolation.py -v
"""

import pytest

from app.services import organization_service


# =====================================================================
# 1. Service-layer scope enforcement
# =====================================================================


class TestUserCanAccessPosition:
    """Tests for ``organization_service.user_can_access_position()``."""

    def test_org_scope_user_can_access_any_position(
        self, app, db_session, admin_user, sample_org
    ):
        """A user with organization-wide scope can access every position."""
        with app.app_context():
            for key in ("pos_a1_1", "pos_a2_1", "pos_b1_1", "pos_b2_1"):
                assert organization_service.user_can_access_position(
                    admin_user, sample_org[key].id
                ), f"Admin should access {key}"

    def test_division_scope_user_can_access_own_division(
        self, app, db_session, manager_user, sample_org
    ):
        """A user scoped to div_a1 can access positions in div_a1."""
        with app.app_context():
            assert organization_service.user_can_access_position(
                manager_user, sample_org["pos_a1_1"].id
            )
            assert organization_service.user_can_access_position(
                manager_user, sample_org["pos_a1_2"].id
            )

    def test_division_scope_user_cannot_access_sibling_division(
        self, app, db_session, manager_user, sample_org
    ):
        """
        A user scoped to div_a1 cannot access positions in div_a2,
        even though div_a2 is in the same department.
        """
        with app.app_context():
            assert not organization_service.user_can_access_position(
                manager_user, sample_org["pos_a2_1"].id
            )

    def test_division_scope_user_cannot_access_other_department(
        self, app, db_session, manager_user, sample_org
    ):
        """A user scoped to div_a1 cannot access positions in dept_b."""
        with app.app_context():
            for key in ("pos_b1_1", "pos_b1_2", "pos_b2_1"):
                assert not organization_service.user_can_access_position(
                    manager_user, sample_org[key].id
                ), f"Manager scoped to div_a1 should NOT access {key}"

    def test_department_scope_user_can_access_all_divisions_in_dept(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """
        A user scoped to dept_a can access positions in ALL divisions
        within dept_a (both div_a1 and div_a2).
        """
        with app.app_context():
            for key in ("pos_a1_1", "pos_a1_2", "pos_a2_1"):
                assert organization_service.user_can_access_position(
                    manager_dept_scope_user, sample_org[key].id
                ), f"Dept-scoped manager should access {key}"

    def test_department_scope_user_cannot_access_other_department(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a cannot access positions in dept_b."""
        with app.app_context():
            for key in ("pos_b1_1", "pos_b1_2", "pos_b2_1"):
                assert not organization_service.user_can_access_position(
                    manager_dept_scope_user, sample_org[key].id
                ), f"Dept-a manager should NOT access {key}"

    def test_nonexistent_position_returns_false_for_scoped_user(
        self, app, db_session, manager_user
    ):
        """
        Requesting access to a nonexistent position returns False
        for a non-org-scope user.  (Org-scope users return True
        before the position lookup, by design.)
        """
        with app.app_context():
            assert not organization_service.user_can_access_position(
                manager_user, 999999
            )


class TestUserCanAccessDepartment:
    """Tests for ``organization_service.user_can_access_department()``."""

    def test_org_scope_can_access_any_department(
        self, app, db_session, admin_user, sample_org
    ):
        """Organization-wide scope grants access to every department."""
        with app.app_context():
            assert organization_service.user_can_access_department(
                admin_user, sample_org["dept_a"].id
            )
            assert organization_service.user_can_access_department(
                admin_user, sample_org["dept_b"].id
            )

    def test_department_scope_can_access_own_department(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """Department-scoped user can access their own department."""
        with app.app_context():
            assert organization_service.user_can_access_department(
                manager_dept_scope_user, sample_org["dept_a"].id
            )

    def test_department_scope_cannot_access_other_department(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """Department-scoped user cannot access a different department."""
        with app.app_context():
            assert not organization_service.user_can_access_department(
                manager_dept_scope_user, sample_org["dept_b"].id
            )

    def test_division_scope_can_access_parent_department(
        self, app, db_session, manager_user, sample_org
    ):
        """
        A user with division-level scope can access the parent
        department of that division.
        """
        with app.app_context():
            assert organization_service.user_can_access_department(
                manager_user, sample_org["dept_a"].id
            )

    def test_division_scope_cannot_access_unrelated_department(
        self, app, db_session, manager_user, sample_org
    ):
        """A user scoped to div_a1 cannot access dept_b."""
        with app.app_context():
            assert not organization_service.user_can_access_department(
                manager_user, sample_org["dept_b"].id
            )


class TestGetDepartmentsFiltering:
    """Tests for scope filtering in ``organization_service.get_departments()``."""

    def test_org_scope_sees_all_departments(
        self, app, db_session, admin_user, sample_org
    ):
        """A user with org scope sees both test departments."""
        with app.app_context():
            departments = organization_service.get_departments(admin_user)
            dept_ids = {d.id for d in departments}
            assert sample_org["dept_a"].id in dept_ids
            assert sample_org["dept_b"].id in dept_ids

    def test_department_scope_sees_only_scoped_department(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """A user scoped to dept_a sees dept_a but not dept_b."""
        with app.app_context():
            departments = organization_service.get_departments(manager_dept_scope_user)
            dept_ids = {d.id for d in departments}
            assert sample_org["dept_a"].id in dept_ids
            assert sample_org["dept_b"].id not in dept_ids


class TestGetPositionsFiltering:
    """Tests for scope filtering in ``organization_service.get_positions()``."""

    def test_org_scope_sees_all_positions(
        self, app, db_session, admin_user, sample_org
    ):
        """A user with org scope sees positions across all departments."""
        with app.app_context():
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
                assert sample_org[key].id in pos_ids, f"Admin should see {key}"

    def test_division_scope_sees_only_own_positions(
        self, app, db_session, manager_user, sample_org
    ):
        """A manager scoped to div_a1 sees only div_a1 positions."""
        with app.app_context():
            positions = organization_service.get_positions(manager_user)
            pos_ids = {p.id for p in positions}
            assert sample_org["pos_a1_1"].id in pos_ids
            assert sample_org["pos_a1_2"].id in pos_ids
            assert sample_org["pos_a2_1"].id not in pos_ids
            assert sample_org["pos_b1_1"].id not in pos_ids
            assert sample_org["pos_b2_1"].id not in pos_ids

    def test_department_scope_sees_all_positions_in_department(
        self, app, db_session, manager_dept_scope_user, sample_org
    ):
        """
        A manager scoped to dept_a sees positions in both div_a1
        and div_a2 but nothing from dept_b.
        """
        with app.app_context():
            positions = organization_service.get_positions(manager_dept_scope_user)
            pos_ids = {p.id for p in positions}
            assert sample_org["pos_a1_1"].id in pos_ids
            assert sample_org["pos_a1_2"].id in pos_ids
            assert sample_org["pos_a2_1"].id in pos_ids
            assert sample_org["pos_b1_1"].id not in pos_ids
            assert sample_org["pos_b2_1"].id not in pos_ids


# =====================================================================
# 2. Role-based access control on routes
# =====================================================================


class TestAdminRouteRoleEnforcement:
    """Verify that /admin/* routes are restricted to the admin role."""

    def test_admin_can_access_manage_users(self, auth_client, admin_user):
        """Admins should reach the user management page."""
        client = auth_client(admin_user)
        response = client.get("/admin/users")
        assert response.status_code == 200

    def test_manager_cannot_access_manage_users(self, auth_client, manager_user):
        """Managers should receive 403 on admin routes."""
        client = auth_client(manager_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_it_staff_cannot_access_manage_users(self, auth_client, it_staff_user):
        """IT staff can view audit logs but cannot manage users."""
        client = auth_client(it_staff_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_budget_executive_cannot_access_manage_users(
        self, auth_client, budget_user
    ):
        """Budget executives have no admin access."""
        client = auth_client(budget_user)
        response = client.get("/admin/users")
        assert response.status_code == 403

    def test_read_only_cannot_access_manage_users(self, auth_client, read_only_user):
        """Read-only users have no admin access."""
        client = auth_client(read_only_user)
        response = client.get("/admin/users")
        assert response.status_code == 403


class TestEquipmentWriteRouteRoleEnforcement:
    """
    Verify that equipment creation routes are restricted to
    admin and IT staff roles.
    """

    def test_admin_can_access_hardware_create(self, auth_client, admin_user):
        """Admins can reach the hardware creation form."""
        client = auth_client(admin_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 200

    def test_it_staff_can_access_hardware_create(self, auth_client, it_staff_user):
        """IT staff can reach the hardware creation form."""
        client = auth_client(it_staff_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 200

    def test_manager_cannot_access_hardware_create(self, auth_client, manager_user):
        """Managers cannot create hardware items."""
        client = auth_client(manager_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 403

    def test_read_only_cannot_access_hardware_create(self, auth_client, read_only_user):
        """Read-only users cannot create hardware items."""
        client = auth_client(read_only_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 403

    def test_manager_cannot_access_software_create(self, auth_client, manager_user):
        """Managers cannot create software products."""
        client = auth_client(manager_user)
        response = client.get("/equipment/software/new")
        assert response.status_code == 403

    def test_budget_executive_cannot_access_hardware_create(
        self, auth_client, budget_user
    ):
        """Budget executives cannot create hardware items."""
        client = auth_client(budget_user)
        response = client.get("/equipment/hardware/new")
        assert response.status_code == 403


class TestRequirementsWizardRoleEnforcement:
    """
    Verify that the requirements wizard is restricted to
    admin, IT staff, and manager roles.
    """

    def test_read_only_cannot_access_wizard(self, auth_client, read_only_user):
        """Read-only users cannot enter the requirements wizard."""
        client = auth_client(read_only_user)
        # The select_position route is at /requirements/ (blueprint root).
        response = client.get("/requirements/")
        assert response.status_code == 403

    def test_budget_executive_cannot_access_wizard(self, auth_client, budget_user):
        """Budget executives cannot enter the requirements wizard."""
        client = auth_client(budget_user)
        response = client.get("/requirements/")
        assert response.status_code == 403

    def test_manager_can_access_wizard(self, auth_client, manager_user):
        """Managers can enter the requirements wizard."""
        client = auth_client(manager_user)
        response = client.get("/requirements/")
        assert response.status_code == 200

    def test_admin_can_access_wizard(self, auth_client, admin_user):
        """Admins can enter the requirements wizard."""
        client = auth_client(admin_user)
        response = client.get("/requirements/")
        assert response.status_code == 200


class TestExportRouteRoleEnforcement:
    """
    Verify that export routes are restricted to admin, IT staff,
    and budget executive roles.
    """

    def test_manager_cannot_export_department_costs(self, auth_client, manager_user):
        """Managers cannot export cost data."""
        client = auth_client(manager_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 403

    def test_read_only_cannot_export_department_costs(
        self, auth_client, read_only_user
    ):
        """Read-only users cannot export cost data."""
        client = auth_client(read_only_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 403

    def test_budget_executive_can_export_department_costs(
        self, auth_client, budget_user
    ):
        """Budget executives can export cost data."""
        client = auth_client(budget_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 200

    def test_admin_can_export_department_costs(self, auth_client, admin_user):
        """Admins can export cost data."""
        client = auth_client(admin_user)
        response = client.get("/reports/export/department-costs/csv")
        assert response.status_code == 200


# =====================================================================
# 3. Division-level scope isolation on routes
# =====================================================================


class TestDivisionScopeIsolationOnRoutes:
    """
    A manager scoped to div_a1 should be blocked from accessing
    positions outside div_a1 through every wizard step.
    """

    def test_manager_can_view_own_position_summary(
        self, auth_client, manager_user, sample_org
    ):
        """A manager can view the summary for a position in their division."""
        client = auth_client(manager_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

    def test_manager_cannot_view_sibling_division_position_summary(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 is redirected away from a position
        in div_a2 (same department, different division).
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_a2_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 302

    def test_manager_cannot_view_other_department_position_summary(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 is redirected away from a position
        in dept_b.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_b1_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 302

    def test_manager_cannot_view_out_of_scope_hardware_page(
        self, auth_client, manager_user, sample_org
    ):
        """Scope check blocks the hardware selection step."""
        client = auth_client(manager_user)
        pos = sample_org["pos_b1_1"]
        response = client.get(f"/requirements/position/{pos.id}/hardware")
        assert response.status_code == 302

    def test_manager_cannot_view_out_of_scope_software_page(
        self, auth_client, manager_user, sample_org
    ):
        """Scope check blocks the software selection step."""
        client = auth_client(manager_user)
        pos = sample_org["pos_b2_1"]
        response = client.get(f"/requirements/position/{pos.id}/software")
        assert response.status_code == 302

    def test_manager_cannot_post_hardware_to_out_of_scope_position(
        self, auth_client, manager_user, sample_org
    ):
        """POST to hardware endpoint for out-of-scope position is rejected."""
        client = auth_client(manager_user)
        pos = sample_org["pos_b1_1"]
        response = client.post(
            f"/requirements/position/{pos.id}/hardware",
            data={"dummy_field": "value"},
        )
        assert response.status_code == 302

    def test_manager_cannot_post_software_to_out_of_scope_position(
        self, auth_client, manager_user, sample_org
    ):
        """POST to software endpoint for out-of-scope position is rejected."""
        client = auth_client(manager_user)
        pos = sample_org["pos_b1_2"]
        response = client.post(
            f"/requirements/position/{pos.id}/software",
            data={"dummy_field": "value"},
        )
        assert response.status_code == 302

    def test_redirect_includes_access_warning(
        self, auth_client, manager_user, sample_org
    ):
        """
        When a scope check fails, the user sees a flash warning
        about access being denied, not a silent redirect.
        """
        client = auth_client(manager_user)
        pos = sample_org["pos_b1_1"]
        response = client.get(
            f"/requirements/position/{pos.id}/summary",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"do not have access" in response.data


# =====================================================================
# 4. Department-level scope isolation on routes
# =====================================================================


class TestDepartmentScopeIsolationOnRoutes:
    """
    Department-scoped users can access any position within their
    department but not positions in other departments.
    """

    def test_dept_manager_can_view_own_division_position(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """Dept-scoped manager can view a position in div_a1."""
        client = auth_client(manager_dept_scope_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

    def test_dept_manager_can_view_other_division_in_same_dept(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """Dept-scoped manager can view a position in div_a2 (same dept)."""
        client = auth_client(manager_dept_scope_user)
        pos = sample_org["pos_a2_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

    def test_dept_manager_cannot_view_other_department_position(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """Dept-scoped manager cannot access positions in dept_b."""
        client = auth_client(manager_dept_scope_user)
        pos = sample_org["pos_b1_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 302

    def test_dept_manager_blocked_across_all_dept_b_positions(
        self, auth_client, manager_dept_scope_user, sample_org
    ):
        """Verify blocking across every dept_b position."""
        client = auth_client(manager_dept_scope_user)
        for key in ("pos_b1_1", "pos_b1_2", "pos_b2_1"):
            pos = sample_org[key]
            response = client.get(f"/requirements/position/{pos.id}/summary")
            assert (
                response.status_code == 302
            ), f"Dept-a manager should be blocked from {key}"


# =====================================================================
# 5. Organization-wide scope (positive tests)
# =====================================================================


class TestOrganizationScopeAccess:
    """Org-wide users can access positions in every department."""

    def test_admin_can_view_dept_a_position(self, auth_client, admin_user, sample_org):
        """Admin can view a dept_a position summary."""
        client = auth_client(admin_user)
        pos = sample_org["pos_a1_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

    def test_admin_can_view_dept_b_position(self, auth_client, admin_user, sample_org):
        """Admin can view a dept_b position summary."""
        client = auth_client(admin_user)
        pos = sample_org["pos_b2_1"]
        response = client.get(f"/requirements/position/{pos.id}/summary")
        assert response.status_code == 200

    def test_it_staff_can_view_any_position(
        self, auth_client, it_staff_user, sample_org
    ):
        """IT staff with org scope can view positions in every division."""
        client = auth_client(it_staff_user)
        for key in ("pos_a1_1", "pos_a2_1", "pos_b1_1", "pos_b2_1"):
            pos = sample_org[key]
            response = client.get(f"/requirements/position/{pos.id}/summary")
            assert response.status_code == 200, f"IT staff should access {key}"

    def test_admin_can_access_all_wizard_steps(
        self, auth_client, admin_user, sample_org
    ):
        """Admin can load all four wizard steps for any position."""
        client = auth_client(admin_user)
        pos = sample_org["pos_b1_2"]

        # Step 1: Select position (blueprint root).
        assert client.get("/requirements/").status_code == 200

        # Step 2: Hardware selection.
        assert (
            client.get(f"/requirements/position/{pos.id}/hardware").status_code == 200
        )

        # Step 3: Software selection.
        assert (
            client.get(f"/requirements/position/{pos.id}/software").status_code == 200
        )

        # Step 4: Summary.
        assert client.get(f"/requirements/position/{pos.id}/summary").status_code == 200


# =====================================================================
# 6. Cross-scope operations
# =====================================================================


class TestCopyFromScopeEnforcement:
    """
    The copy-from route requires the user to have scope on both
    the source and the target position.
    """

    def test_copy_within_scope_is_allowed(
        self,
        auth_client,
        manager_user,
        sample_org,
        sample_catalog,
        create_hw_requirement,
    ):
        """
        Copying from one div_a1 position to another div_a1 position
        should succeed (both are in scope).
        """
        create_hw_requirement(
            position=sample_org["pos_a1_1"],
            hardware=sample_catalog["hw_laptop_standard"],
        )

        client = auth_client(manager_user)
        target = sample_org["pos_a1_2"]
        source = sample_org["pos_a1_1"]

        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        # Should redirect to the hardware step on success.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "hardware" in location

    def test_copy_from_out_of_scope_source_is_blocked(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 cannot copy FROM a position in
        dept_b, even if the TARGET is in their scope.
        """
        client = auth_client(manager_user)
        target = sample_org["pos_a1_1"]  # In scope.
        source = sample_org["pos_b1_1"]  # Out of scope.

        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        # Should redirect to select_position, NOT to hardware step.
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "hardware" not in location

    def test_copy_to_out_of_scope_target_is_blocked(
        self, auth_client, manager_user, sample_org
    ):
        """
        A manager scoped to div_a1 cannot copy TO a position in
        dept_b, even if the SOURCE is in their scope.
        """
        client = auth_client(manager_user)
        target = sample_org["pos_b1_1"]  # Out of scope.
        source = sample_org["pos_a1_1"]  # In scope.

        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        assert response.status_code == 302
        location = response.headers.get("Location", "")
        assert "hardware" not in location

    def test_copy_both_out_of_scope_is_blocked(
        self, auth_client, manager_user, sample_org
    ):
        """Both source and target out of scope is still blocked."""
        client = auth_client(manager_user)
        target = sample_org["pos_b1_1"]
        source = sample_org["pos_b2_1"]

        response = client.post(
            f"/requirements/position/{target.id}/copy-from/{source.id}"
        )
        assert response.status_code == 302


# =====================================================================
# 7. Report and export scope isolation
# =====================================================================


class TestReportAccessControl:
    """Verify that report pages load for authorized users."""

    def test_cost_summary_loads_for_admin(self, auth_client, admin_user):
        """Admin can view the department cost summary."""
        client = auth_client(admin_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 200

    def test_cost_summary_loads_for_manager(self, auth_client, manager_user):
        """Managers can view the cost summary (scope-filtered)."""
        client = auth_client(manager_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 200

    def test_cost_summary_loads_for_read_only(self, auth_client, read_only_user):
        """Read-only users can view (but not export) reports."""
        client = auth_client(read_only_user)
        response = client.get("/reports/cost-summary")
        assert response.status_code == 200

    def test_equipment_report_loads_for_admin(self, auth_client, admin_user):
        """Admin can view the equipment detail report."""
        client = auth_client(admin_user)
        response = client.get("/reports/equipment-report")
        assert response.status_code == 200


class TestExportScopeEnforcement:
    """Verify that export endpoints enforce role restrictions."""

    def test_read_only_cannot_export_position_costs_csv(
        self, auth_client, read_only_user
    ):
        """Read-only users cannot export position cost data."""
        client = auth_client(read_only_user)
        response = client.get("/reports/export/position-costs/csv")
        assert response.status_code == 403

    def test_manager_cannot_export_position_costs_xlsx(self, auth_client, manager_user):
        """Managers cannot export position cost data."""
        client = auth_client(manager_user)
        response = client.get("/reports/export/position-costs/xlsx")
        assert response.status_code == 403

    def test_it_staff_can_export_position_costs(self, auth_client, it_staff_user):
        """IT staff can export position cost data."""
        client = auth_client(it_staff_user)
        response = client.get("/reports/export/position-costs/csv")
        assert response.status_code == 200


# =====================================================================
# 8. Admin write operations with role enforcement
# =====================================================================


class TestAdminWriteRouteEnforcement:
    """
    Verify that POST-based admin actions reject non-admin users.
    These catch bypasses where someone crafts a POST request
    directly without going through the HTML form.
    """

    def test_manager_cannot_provision_user(self, auth_client, manager_user):
        """Managers cannot create new user accounts."""
        client = auth_client(manager_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": "hacker@evil.com",
                "first_name": "Hacker",
                "last_name": "McHackface",
                "role_name": "admin",
            },
        )
        assert response.status_code == 403

    def test_read_only_cannot_change_user_role(
        self, auth_client, read_only_user, admin_user
    ):
        """Read-only users cannot escalate another user's role."""
        client = auth_client(read_only_user)
        response = client.post(
            f"/admin/users/{admin_user.id}/role",
            data={"role_name": "admin"},
        )
        assert response.status_code == 403

    def test_it_staff_cannot_deactivate_user(
        self, auth_client, it_staff_user, admin_user
    ):
        """IT staff cannot deactivate user accounts."""
        client = auth_client(it_staff_user)
        response = client.post(f"/admin/users/{admin_user.id}/deactivate")
        assert response.status_code == 403

    def test_manager_cannot_update_user_scope(
        self, auth_client, manager_user, admin_user
    ):
        """Managers cannot modify another user's scope."""
        client = auth_client(manager_user)
        response = client.post(
            f"/admin/users/{admin_user.id}/scope",
            data={"scope_type": "organization"},
        )
        assert response.status_code == 403

    def test_admin_can_provision_user(self, auth_client, admin_user):
        """Admins CAN provision users (positive control)."""
        client = auth_client(admin_user)
        response = client.post(
            "/admin/users/provision",
            data={
                "email": _next_unique_test_email(),
                "first_name": "New",
                "last_name": "User",
                "role_name": "read_only",
            },
        )
        # Successful provisioning redirects to the user list.
        assert response.status_code == 302


# Helper for generating unique emails in tests (not fixtures).
def _next_unique_test_email():
    """Generate a unique @test.local email for inline test use."""
    import time

    return f"_tst_provision_{int(time.time() * 1000)}@test.local"


# =====================================================================
# 9. Edge cases and boundary conditions
# =====================================================================


class TestScopeEdgeCases:
    """Tests for unusual but important scope scenarios."""

    def test_user_with_no_scopes_sees_nothing(
        self, auth_client, create_user, sample_org
    ):
        """
        A user with no UserScope records should not be able to
        access positions.  This can happen if an admin removes
        all scopes.
        """
        user = create_user(role_name="manager", scopes=[])

        client = auth_client(user)

        # The wizard page itself should load (role check passes).
        response = client.get("/requirements/")
        assert response.status_code == 200

        # But specific positions should be blocked.
        for key in ("pos_a1_1", "pos_b1_1"):
            pos = sample_org[key]
            response = client.get(f"/requirements/position/{pos.id}/summary")
            assert (
                response.status_code == 302
            ), f"Scopeless user should be blocked from {key}"

    def test_multi_division_scope_accesses_both_divisions(
        self, auth_client, create_user, sample_org
    ):
        """
        A user with scope on two separate divisions (div_a1 and
        div_b1) can access positions in both but not in div_a2
        or div_b2.
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
        client = auth_client(user)

        # Can access div_a1.
        response = client.get(
            f"/requirements/position/{sample_org['pos_a1_1'].id}/summary"
        )
        assert response.status_code == 200

        # Can access div_b1.
        response = client.get(
            f"/requirements/position/{sample_org['pos_b1_1'].id}/summary"
        )
        assert response.status_code == 200

        # Cannot access div_a2 (not in scope list).
        response = client.get(
            f"/requirements/position/{sample_org['pos_a2_1'].id}/summary"
        )
        assert response.status_code == 302

        # Cannot access div_b2 (not in scope list).
        response = client.get(
            f"/requirements/position/{sample_org['pos_b2_1'].id}/summary"
        )
        assert response.status_code == 302

    def test_nonexistent_position_returns_redirect_not_500(
        self, auth_client, admin_user
    ):
        """
        Accessing a position ID that does not exist should not crash.
        """
        client = auth_client(admin_user)
        response = client.get("/requirements/position/999999/summary")
        # Should be handled gracefully (redirect or 404, not 500).
        assert response.status_code in (200, 302, 404)
        assert response.status_code != 500

    def test_equipment_catalog_is_viewable_by_all_roles(
        self, auth_client, read_only_user, manager_user, budget_user
    ):
        """
        The hardware and software list pages are viewable by all
        authenticated users (only creation is restricted).
        """
        for user in (read_only_user, manager_user, budget_user):
            client = auth_client(user)

            response = client.get("/equipment/hardware")
            assert (
                response.status_code == 200
            ), f"{user.role_name} should view hardware list"

            response = client.get("/equipment/software")
            assert (
                response.status_code == 200
            ), f"{user.role_name} should view software list"

    def test_health_check_is_public(self, client):
        """
        The health check endpoint does not require authentication.
        """
        response = client.get("/health")
        assert response.status_code == 200
