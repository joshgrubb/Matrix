"""
Unit tests for the user service layer.

Tests every public function in ``app.services.user_service`` against
the real SQL Server test database.  Verifies user provisioning, role
changes, scope management, deactivation/reactivation, pagination,
search filtering, role filtering, and audit trail creation.

Design decisions:
    - All tests call service functions directly (not via HTTP routes)
      to isolate service-layer behavior from route-layer error handling.
    - The ``admin_user`` fixture supplies a ``changed_by`` / ``provisioned_by``
      user ID for audit logging parameters.
    - The ``sample_org`` fixture provides departments and divisions for
      scope assignment tests.
    - Test emails use the ``@test.local`` suffix so the conftest cleanup
      fixture deletes them after each test.
    - Tests that create users via ``user_service.provision_user()`` use
      unique email addresses derived from the conftest ``_next_unique_code``
      helper (via the ``unique_email`` fixture below) to prevent unique
      constraint violations when multiple tests provision users.

Fixture reminder (from conftest.py):
    roles dict keys: admin, it_staff, manager, budget_executive, read_only
    sample_org keys: dept_a, dept_b, div_a1, div_a2, div_b1, div_b2,
                     pos_a1_1 (auth=3), pos_a1_2 (auth=5), pos_a2_1 (auth=2),
                     pos_b1_1 (auth=4), pos_b1_2 (auth=1), pos_b2_1 (auth=6)

Run this file in isolation::

    pytest tests/test_services/test_user_service.py -v
"""

import json

import pytest

from app.models.audit import AuditLog
from app.models.user import Role, User, UserScope
from app.services import user_service


# =====================================================================
# Local helper fixture for unique emails
# =====================================================================

_local_counter = 0


@pytest.fixture()
def unique_email():
    """
    Factory fixture that returns a unique ``@test.local`` email
    each time it is called within a test.

    This avoids collisions with conftest-created users and with
    other tests that provision users in the same session.
    """
    global _local_counter  # pylint: disable=global-statement

    def _make(prefix="svc"):
        global _local_counter  # pylint: disable=global-statement
        _local_counter += 1
        return f"_tst_{prefix}_{_local_counter:04d}@test.local"

    return _make


# =====================================================================
# 1. User provisioning
# =====================================================================


class TestProvisionUser:
    """Verify ``user_service.provision_user()`` creates users correctly."""

    def test_provision_user_creates_record_with_correct_fields(
        self, app, admin_user, unique_email
    ):
        """
        A newly provisioned user should have the correct email,
        names, role, active status, and provisioned_by reference.
        """
        email = unique_email("prov")

        user = user_service.provision_user(
            email=email,
            first_name="Jane",
            last_name="Doe",
            role_name="manager",
            provisioned_by=admin_user.id,
        )

        assert user.id is not None
        assert user.email == email
        assert user.first_name == "Jane"
        assert user.last_name == "Doe"
        assert user.role_name == "manager"
        assert user.is_active is True
        assert user.provisioned_by == admin_user.id
        assert user.provisioned_at is not None

    def test_provision_user_defaults_to_read_only_role(
        self, app, admin_user, unique_email
    ):
        """
        When no role_name is specified, the user should be created
        with the default ``read_only`` role.
        """
        email = unique_email("default_role")

        user = user_service.provision_user(
            email=email,
            first_name="Default",
            last_name="User",
            provisioned_by=admin_user.id,
        )

        assert user.role_name == "read_only"

    def test_provision_user_with_entra_object_id(self, app, admin_user, unique_email):
        """
        When an Entra object ID is provided, it should be stored
        on the user record for future OAuth lookups.
        """
        email = unique_email("entra")
        entra_oid = "00000000-aaaa-bbbb-cccc-111111111111"

        user = user_service.provision_user(
            email=email,
            first_name="Entra",
            last_name="User",
            role_name="read_only",
            provisioned_by=admin_user.id,
            entra_object_id=entra_oid,
        )

        assert user.entra_object_id == entra_oid

    def test_provision_user_invalid_role_raises_value_error(
        self, app, admin_user, unique_email
    ):
        """
        Providing a role_name that does not exist in the database
        should raise ValueError, not a database FK error.
        """
        email = unique_email("bad_role")

        with pytest.raises(ValueError, match="not found"):
            user_service.provision_user(
                email=email,
                first_name="Bad",
                last_name="Role",
                role_name="nonexistent_role_xyz",
                provisioned_by=admin_user.id,
            )

    def test_provision_user_duplicate_email_raises_integrity_error(
        self, app, admin_user, unique_email
    ):
        """
        Provisioning a second user with the same email address
        should fail because email has a unique constraint.

        The route layer checks for duplicates via get_user_by_email
        before calling provision_user, but the service layer must
        not silently swallow a constraint violation.
        """
        email = unique_email("dupe")

        # First provision succeeds.
        user_service.provision_user(
            email=email,
            first_name="First",
            last_name="User",
            provisioned_by=admin_user.id,
        )

        # Second provision with the same email should raise.
        with pytest.raises(Exception):
            user_service.provision_user(
                email=email,
                first_name="Second",
                last_name="User",
                provisioned_by=admin_user.id,
            )

    def test_provision_user_creates_audit_entry(self, app, admin_user, unique_email):
        """
        Provisioning a user should write a CREATE audit log entry
        with the user's email and role in the new_value field.
        """
        email = unique_email("audit")

        user = user_service.provision_user(
            email=email,
            first_name="Audit",
            last_name="Test",
            role_name="it_staff",
            provisioned_by=admin_user.id,
        )

        # Find the audit entry for this user.
        entry = (
            AuditLog.query.filter_by(
                action_type="CREATE",
                entity_type="auth.user",
                entity_id=user.id,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

        assert entry is not None
        assert entry.user_id == admin_user.id

        new_value = json.loads(entry.new_value)
        assert new_value["email"] == email
        assert new_value["role"] == "it_staff"

    def test_provision_user_persists_to_database(self, app, admin_user, unique_email):
        """
        After provisioning, the user should be retrievable from the
        database by both ID and email lookups.
        """
        email = unique_email("persist")

        user = user_service.provision_user(
            email=email,
            first_name="Persist",
            last_name="Check",
            provisioned_by=admin_user.id,
        )

        # Lookup by ID.
        found_by_id = user_service.get_user_by_id(user.id)
        assert found_by_id is not None
        assert found_by_id.email == email

        # Lookup by email.
        found_by_email = user_service.get_user_by_email(email)
        assert found_by_email is not None
        assert found_by_email.id == user.id

    def test_get_user_by_email_is_case_insensitive(self, app, admin_user, unique_email):
        """
        Email lookups should be case-insensitive to match how
        Microsoft Entra ID handles email addresses.
        """
        email = unique_email("case")

        user_service.provision_user(
            email=email,
            first_name="Case",
            last_name="Test",
            provisioned_by=admin_user.id,
        )

        # Query with different casing.
        found = user_service.get_user_by_email(email.upper())
        assert found is not None
        assert found.email == email


# =====================================================================
# 2. Role changes
# =====================================================================


class TestChangeUserRole:
    """Verify ``user_service.change_user_role()`` updates roles correctly."""

    def test_change_role_updates_user_record(self, app, admin_user, unique_email):
        """
        After changing a user's role from read_only to manager,
        the user's role_name property should reflect the new role.
        """
        email = unique_email("role_chg")
        user = user_service.provision_user(
            email=email,
            first_name="Role",
            last_name="Change",
            role_name="read_only",
            provisioned_by=admin_user.id,
        )
        assert user.role_name == "read_only"

        updated = user_service.change_user_role(
            user_id=user.id,
            new_role_name="manager",
            changed_by=admin_user.id,
        )

        assert updated.role_name == "manager"
        assert updated.id == user.id

    def test_change_role_to_every_valid_role(
        self, app, admin_user, roles, unique_email
    ):
        """
        Cycle through all five seeded roles and verify each
        assignment succeeds.  This catches FK or constraint issues
        with any specific role record.
        """
        email = unique_email("all_roles")
        user = user_service.provision_user(
            email=email,
            first_name="Multi",
            last_name="Role",
            role_name="read_only",
            provisioned_by=admin_user.id,
        )

        for role_name in [
            "admin",
            "it_staff",
            "manager",
            "budget_executive",
            "read_only",
        ]:
            updated = user_service.change_user_role(
                user_id=user.id,
                new_role_name=role_name,
                changed_by=admin_user.id,
            )
            assert updated.role_name == role_name

    def test_change_role_invalid_role_raises_value_error(
        self, app, admin_user, unique_email
    ):
        """
        Specifying a nonexistent role name should raise ValueError
        and leave the user's current role unchanged.
        """
        email = unique_email("bad_role_chg")
        user = user_service.provision_user(
            email=email,
            first_name="Bad",
            last_name="RoleChange",
            role_name="read_only",
            provisioned_by=admin_user.id,
        )

        with pytest.raises(ValueError, match="not found"):
            user_service.change_user_role(
                user_id=user.id,
                new_role_name="supreme_overlord",
                changed_by=admin_user.id,
            )

        # Verify role was not changed.
        refreshed = user_service.get_user_by_id(user.id)
        assert refreshed.role_name == "read_only"

    def test_change_role_nonexistent_user_raises_value_error(self, app, admin_user):
        """Attempting to change the role of a missing user should raise."""
        with pytest.raises(ValueError, match="not found"):
            user_service.change_user_role(
                user_id=999999,
                new_role_name="admin",
                changed_by=admin_user.id,
            )

    def test_change_role_creates_audit_entry(self, app, admin_user, unique_email):
        """
        A role change should write an UPDATE audit log entry
        with both the previous and new role names.
        """
        email = unique_email("role_audit")
        user = user_service.provision_user(
            email=email,
            first_name="Role",
            last_name="Audit",
            role_name="read_only",
            provisioned_by=admin_user.id,
        )

        user_service.change_user_role(
            user_id=user.id,
            new_role_name="it_staff",
            changed_by=admin_user.id,
        )

        entry = (
            AuditLog.query.filter_by(
                action_type="UPDATE",
                entity_type="auth.user",
                entity_id=user.id,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

        assert entry is not None
        prev = json.loads(entry.previous_value)
        new = json.loads(entry.new_value)
        assert prev["role"] == "read_only"
        assert new["role"] == "it_staff"


# =====================================================================
# 3. Deactivation and reactivation
# =====================================================================


class TestDeactivateUser:
    """Verify ``user_service.deactivate_user()`` soft-deletes users."""

    def test_deactivate_user_sets_is_active_false(self, app, admin_user, unique_email):
        """After deactivation, the user's is_active flag should be False."""
        email = unique_email("deact")
        user = user_service.provision_user(
            email=email,
            first_name="Deact",
            last_name="User",
            provisioned_by=admin_user.id,
        )
        assert user.is_active is True

        result = user_service.deactivate_user(
            user_id=user.id,
            changed_by=admin_user.id,
        )

        assert result.is_active is False

        # Verify it persisted.
        refreshed = user_service.get_user_by_id(user.id)
        assert refreshed.is_active is False

    def test_deactivate_already_inactive_user_raises_value_error(
        self, app, admin_user, unique_email
    ):
        """
        Attempting to deactivate a user who is already inactive
        should raise ValueError to prevent double-deactivation.
        """
        email = unique_email("double_deact")
        user = user_service.provision_user(
            email=email,
            first_name="Double",
            last_name="Deact",
            provisioned_by=admin_user.id,
        )

        # First deactivation succeeds.
        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)

        # Second deactivation should raise.
        with pytest.raises(ValueError, match="already inactive"):
            user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)

    def test_deactivate_nonexistent_user_raises_value_error(self, app, admin_user):
        """Deactivating a missing user should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            user_service.deactivate_user(user_id=999999, changed_by=admin_user.id)

    def test_deactivate_user_creates_audit_entry(self, app, admin_user, unique_email):
        """Deactivation should write a DEACTIVATE audit log entry."""
        email = unique_email("deact_audit")
        user = user_service.provision_user(
            email=email,
            first_name="Deact",
            last_name="Audit",
            provisioned_by=admin_user.id,
        )

        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)

        entry = (
            AuditLog.query.filter_by(
                action_type="DEACTIVATE",
                entity_type="auth.user",
                entity_id=user.id,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

        assert entry is not None
        assert entry.user_id == admin_user.id
        prev = json.loads(entry.previous_value)
        new = json.loads(entry.new_value)
        assert prev["is_active"] is True
        assert new["is_active"] is False


class TestReactivateUser:
    """Verify ``user_service.reactivate_user()`` re-enables users."""

    def test_reactivate_user_sets_is_active_true(self, app, admin_user, unique_email):
        """After reactivation, the user's is_active flag should be True."""
        email = unique_email("react")
        user = user_service.provision_user(
            email=email,
            first_name="React",
            last_name="User",
            provisioned_by=admin_user.id,
        )

        # Deactivate first.
        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)
        assert user.is_active is False

        # Reactivate.
        result = user_service.reactivate_user(user_id=user.id, changed_by=admin_user.id)

        assert result.is_active is True

        # Verify it persisted.
        refreshed = user_service.get_user_by_id(user.id)
        assert refreshed.is_active is True

    def test_reactivate_already_active_user_raises_value_error(
        self, app, admin_user, unique_email
    ):
        """
        Attempting to reactivate a user who is already active
        should raise ValueError.
        """
        email = unique_email("double_react")
        user = user_service.provision_user(
            email=email,
            first_name="Double",
            last_name="React",
            provisioned_by=admin_user.id,
        )

        # User is already active from provisioning.
        with pytest.raises(ValueError, match="already active"):
            user_service.reactivate_user(user_id=user.id, changed_by=admin_user.id)

    def test_reactivate_nonexistent_user_raises_value_error(self, app, admin_user):
        """Reactivating a missing user should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            user_service.reactivate_user(user_id=999999, changed_by=admin_user.id)

    def test_reactivate_user_creates_audit_entry(self, app, admin_user, unique_email):
        """Reactivation should write a REACTIVATE audit log entry."""
        email = unique_email("react_audit")
        user = user_service.provision_user(
            email=email,
            first_name="React",
            last_name="Audit",
            provisioned_by=admin_user.id,
        )

        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)
        user_service.reactivate_user(user_id=user.id, changed_by=admin_user.id)

        entry = (
            AuditLog.query.filter_by(
                action_type="REACTIVATE",
                entity_type="auth.user",
                entity_id=user.id,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

        assert entry is not None
        prev = json.loads(entry.previous_value)
        new = json.loads(entry.new_value)
        assert prev["is_active"] is False
        assert new["is_active"] is True

    def test_full_deactivate_reactivate_cycle(self, app, admin_user, unique_email):
        """
        A user should survive a full active -> inactive -> active
        cycle with all state intact (role, email, name unchanged).
        """
        email = unique_email("cycle")
        user = user_service.provision_user(
            email=email,
            first_name="Cycle",
            last_name="Test",
            role_name="manager",
            provisioned_by=admin_user.id,
        )

        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)
        user_service.reactivate_user(user_id=user.id, changed_by=admin_user.id)

        refreshed = user_service.get_user_by_id(user.id)
        assert refreshed.is_active is True
        assert refreshed.email == email
        assert refreshed.first_name == "Cycle"
        assert refreshed.last_name == "Test"
        assert refreshed.role_name == "manager"


# =====================================================================
# 4. Scope management
# =====================================================================


class TestSetUserScopes:
    """
    Verify ``user_service.set_user_scopes()`` replaces all existing
    scopes with the provided list.
    """

    def test_set_organization_scope(self, app, admin_user, unique_email):
        """
        Setting organization scope should create a single UserScope
        record with scope_type='organization' and no FK references.
        """
        email = unique_email("org_scope")
        user = user_service.provision_user(
            email=email,
            first_name="Org",
            last_name="Scope",
            provisioned_by=admin_user.id,
        )

        scopes = user_service.set_user_scopes(
            user_id=user.id,
            scopes=[{"scope_type": "organization"}],
            changed_by=admin_user.id,
        )

        assert len(scopes) == 1
        assert scopes[0].scope_type == "organization"
        assert scopes[0].department_id is None
        assert scopes[0].division_id is None

    def test_set_department_scope(self, app, admin_user, sample_org, unique_email):
        """
        Setting department scope should create a UserScope record
        tied to the specified department.
        """
        email = unique_email("dept_scope")
        user = user_service.provision_user(
            email=email,
            first_name="Dept",
            last_name="Scope",
            provisioned_by=admin_user.id,
        )

        dept_a = sample_org["dept_a"]
        scopes = user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {
                    "scope_type": "department",
                    "department_id": dept_a.id,
                }
            ],
            changed_by=admin_user.id,
        )

        assert len(scopes) == 1
        assert scopes[0].scope_type == "department"
        assert scopes[0].department_id == dept_a.id

    def test_set_division_scope(self, app, admin_user, sample_org, unique_email):
        """
        Setting division scope should create a UserScope record
        tied to the specified division.
        """
        email = unique_email("div_scope")
        user = user_service.provision_user(
            email=email,
            first_name="Div",
            last_name="Scope",
            provisioned_by=admin_user.id,
        )

        div_a1 = sample_org["div_a1"]
        scopes = user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": div_a1.id,
                }
            ],
            changed_by=admin_user.id,
        )

        assert len(scopes) == 1
        assert scopes[0].scope_type == "division"
        assert scopes[0].division_id == div_a1.id

    def test_set_multiple_department_scopes(
        self, app, admin_user, sample_org, unique_email
    ):
        """
        A user can have scopes to multiple departments simultaneously.
        """
        email = unique_email("multi_dept")
        user = user_service.provision_user(
            email=email,
            first_name="Multi",
            last_name="Dept",
            provisioned_by=admin_user.id,
        )

        dept_a = sample_org["dept_a"]
        dept_b = sample_org["dept_b"]

        scopes = user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {"scope_type": "department", "department_id": dept_a.id},
                {"scope_type": "department", "department_id": dept_b.id},
            ],
            changed_by=admin_user.id,
        )

        assert len(scopes) == 2
        scope_dept_ids = {s.department_id for s in scopes}
        assert dept_a.id in scope_dept_ids
        assert dept_b.id in scope_dept_ids

    def test_set_multiple_division_scopes(
        self, app, admin_user, sample_org, unique_email
    ):
        """
        A user can have scopes to multiple divisions simultaneously.
        """
        email = unique_email("multi_div")
        user = user_service.provision_user(
            email=email,
            first_name="Multi",
            last_name="Div",
            provisioned_by=admin_user.id,
        )

        div_a1 = sample_org["div_a1"]
        div_b1 = sample_org["div_b1"]

        scopes = user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {"scope_type": "division", "division_id": div_a1.id},
                {"scope_type": "division", "division_id": div_b1.id},
            ],
            changed_by=admin_user.id,
        )

        assert len(scopes) == 2
        scope_div_ids = {s.division_id for s in scopes}
        assert div_a1.id in scope_div_ids
        assert div_b1.id in scope_div_ids

    def test_set_scopes_replaces_existing_scopes(
        self, app, admin_user, sample_org, unique_email
    ):
        """
        Calling set_user_scopes should DELETE all previous scopes
        and replace them with the new list.  No leftover scopes
        from the previous configuration should remain.
        """
        email = unique_email("replace")
        user = user_service.provision_user(
            email=email,
            first_name="Replace",
            last_name="Scope",
            provisioned_by=admin_user.id,
        )

        # First: assign org-wide scope.
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[{"scope_type": "organization"}],
            changed_by=admin_user.id,
        )

        # Verify org scope exists.
        existing = UserScope.query.filter_by(user_id=user.id).all()
        assert len(existing) == 1
        assert existing[0].scope_type == "organization"

        # Second: replace with division scope.
        div_a1 = sample_org["div_a1"]
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": div_a1.id,
                }
            ],
            changed_by=admin_user.id,
        )

        # Verify org scope is gone and division scope is present.
        remaining = UserScope.query.filter_by(user_id=user.id).all()
        assert len(remaining) == 1
        assert remaining[0].scope_type == "division"
        assert remaining[0].division_id == div_a1.id

    def test_set_scopes_nonexistent_user_raises_value_error(self, app, admin_user):
        """Setting scopes for a missing user should raise ValueError."""
        with pytest.raises(ValueError, match="not found"):
            user_service.set_user_scopes(
                user_id=999999,
                scopes=[{"scope_type": "organization"}],
                changed_by=admin_user.id,
            )

    def test_set_scopes_creates_audit_entry(
        self, app, admin_user, sample_org, unique_email
    ):
        """
        Scope changes should write an UPDATE audit log entry with
        both the previous and new scope configurations.
        """
        email = unique_email("scope_audit")
        user = user_service.provision_user(
            email=email,
            first_name="Scope",
            last_name="Audit",
            provisioned_by=admin_user.id,
        )

        # Set initial org scope.
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[{"scope_type": "organization"}],
            changed_by=admin_user.id,
        )

        # Change to department scope.
        dept_a = sample_org["dept_a"]
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {
                    "scope_type": "department",
                    "department_id": dept_a.id,
                }
            ],
            changed_by=admin_user.id,
        )

        # Find the most recent scope audit entry.
        entry = (
            AuditLog.query.filter_by(
                action_type="UPDATE",
                entity_type="auth.user_scope",
                entity_id=user.id,
            )
            .order_by(AuditLog.id.desc())
            .first()
        )

        assert entry is not None

        prev = json.loads(entry.previous_value)
        new = json.loads(entry.new_value)

        # Previous should contain the org scope.
        assert len(prev["scopes"]) == 1
        assert prev["scopes"][0]["scope_type"] == "organization"

        # New should contain the department scope.
        assert len(new["scopes"]) == 1
        assert new["scopes"][0]["scope_type"] == "department"
        assert new["scopes"][0]["department_id"] == dept_a.id

    def test_set_scopes_reflects_on_user_model_helpers(
        self, app, admin_user, sample_org, unique_email, db_session
    ):
        """
        After setting scopes, the User model's scope helper methods
        (has_org_scope, scoped_department_ids, scoped_division_ids)
        should return the correct values.
        """
        email = unique_email("model_helpers")
        user = user_service.provision_user(
            email=email,
            first_name="Model",
            last_name="Helpers",
            provisioned_by=admin_user.id,
        )

        # Set org scope and verify.
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[{"scope_type": "organization"}],
            changed_by=admin_user.id,
        )
        db_session.refresh(user)
        assert user.has_org_scope() is True
        assert user.scoped_department_ids() == []
        assert user.scoped_division_ids() == []

        # Switch to department scope.
        dept_a = sample_org["dept_a"]
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {
                    "scope_type": "department",
                    "department_id": dept_a.id,
                }
            ],
            changed_by=admin_user.id,
        )
        db_session.refresh(user)
        assert user.has_org_scope() is False
        assert dept_a.id in user.scoped_department_ids()
        assert user.scoped_division_ids() == []

        # Switch to division scope.
        div_b1 = sample_org["div_b1"]
        user_service.set_user_scopes(
            user_id=user.id,
            scopes=[
                {
                    "scope_type": "division",
                    "division_id": div_b1.id,
                }
            ],
            changed_by=admin_user.id,
        )
        db_session.refresh(user)
        assert user.has_org_scope() is False
        assert user.scoped_department_ids() == []
        assert div_b1.id in user.scoped_division_ids()


# =====================================================================
# 5. get_all_users pagination, search, and filtering
# =====================================================================


class TestGetAllUsers:
    """
    Verify ``user_service.get_all_users()`` pagination, text search,
    role filtering, and the include_inactive flag.
    """

    def test_get_all_users_returns_paginated_result(
        self, app, admin_user, unique_email
    ):
        """
        The return value should be a SQLAlchemy pagination object
        with .items, .total, .pages, and .page attributes.
        """
        # Create a couple of users to ensure the result is non-empty.
        for i in range(3):
            user_service.provision_user(
                email=unique_email(f"page_{i}"),
                first_name=f"Page{i}",
                last_name="User",
                provisioned_by=admin_user.id,
            )

        result = user_service.get_all_users(page=1, per_page=10)

        assert hasattr(result, "items")
        assert hasattr(result, "total")
        assert hasattr(result, "pages")
        assert hasattr(result, "page")
        assert result.page == 1
        assert len(result.items) > 0

    def test_get_all_users_respects_per_page(self, app, admin_user, unique_email):
        """
        Requesting per_page=2 should return at most 2 items per page
        even when more users exist.
        """
        for i in range(5):
            user_service.provision_user(
                email=unique_email(f"pp_{i}"),
                first_name=f"PerPage{i}",
                last_name="Test",
                provisioned_by=admin_user.id,
            )

        result = user_service.get_all_users(page=1, per_page=2)
        assert len(result.items) <= 2

    def test_get_all_users_excludes_inactive_by_default(
        self, app, admin_user, unique_email
    ):
        """
        By default, inactive users should NOT appear in the results.
        """
        email = unique_email("inactive_filter")
        user = user_service.provision_user(
            email=email,
            first_name="Inactive",
            last_name="FilterTest",
            provisioned_by=admin_user.id,
        )

        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)

        # Search specifically for this user.
        result = user_service.get_all_users(search="Inactive FilterTest", per_page=100)

        user_ids = {u.id for u in result.items}
        assert user.id not in user_ids

    def test_get_all_users_includes_inactive_when_requested(
        self, app, admin_user, unique_email
    ):
        """
        With include_inactive=True, deactivated users should appear.
        """
        email = unique_email("show_inactive")
        user = user_service.provision_user(
            email=email,
            first_name="Show",
            last_name="InactiveTest",
            provisioned_by=admin_user.id,
        )

        user_service.deactivate_user(user_id=user.id, changed_by=admin_user.id)

        result = user_service.get_all_users(
            include_inactive=True,
            search="Show InactiveTest",
            per_page=100,
        )

        user_ids = {u.id for u in result.items}
        assert user.id in user_ids

    def test_get_all_users_search_by_first_name(self, app, admin_user, unique_email):
        """Text search should match against first_name."""
        email = unique_email("srch_fn")
        user_service.provision_user(
            email=email,
            first_name="Xylophone",
            last_name="SearchFN",
            provisioned_by=admin_user.id,
        )

        result = user_service.get_all_users(search="Xylophone", per_page=100)
        emails = {u.email for u in result.items}
        assert email in emails

    def test_get_all_users_search_by_last_name(self, app, admin_user, unique_email):
        """Text search should match against last_name."""
        email = unique_email("srch_ln")
        user_service.provision_user(
            email=email,
            first_name="Search",
            last_name="Quetzalcoatl",
            provisioned_by=admin_user.id,
        )

        result = user_service.get_all_users(search="Quetzalcoatl", per_page=100)
        emails = {u.email for u in result.items}
        assert email in emails

    def test_get_all_users_search_by_email(self, app, admin_user, unique_email):
        """Text search should match against email address."""
        email = unique_email("srch_email")
        user_service.provision_user(
            email=email,
            first_name="Email",
            last_name="Search",
            provisioned_by=admin_user.id,
        )

        result = user_service.get_all_users(search=email, per_page=100)
        emails = {u.email for u in result.items}
        assert email in emails

    def test_get_all_users_search_by_full_name(self, app, admin_user, unique_email):
        """
        Text search should match against the concatenated full name
        so queries like 'Jane Doe' work.
        """
        email = unique_email("srch_full")
        user_service.provision_user(
            email=email,
            first_name="Zebediah",
            last_name="Pumpernickel",
            provisioned_by=admin_user.id,
        )

        result = user_service.get_all_users(
            search="Zebediah Pumpernickel", per_page=100
        )
        emails = {u.email for u in result.items}
        assert email in emails

    def test_get_all_users_filter_by_role(self, app, admin_user, unique_email):
        """
        The role_name filter should return only users with that role.
        """
        email_mgr = unique_email("role_filter_mgr")
        email_ro = unique_email("role_filter_ro")

        user_service.provision_user(
            email=email_mgr,
            first_name="RoleFilter",
            last_name="Manager",
            role_name="manager",
            provisioned_by=admin_user.id,
        )
        user_service.provision_user(
            email=email_ro,
            first_name="RoleFilter",
            last_name="ReadOnly",
            role_name="read_only",
            provisioned_by=admin_user.id,
        )

        # Filter for managers only, searching for our test prefix.
        result = user_service.get_all_users(
            search="RoleFilter",
            role_name="manager",
            per_page=100,
        )

        emails = {u.email for u in result.items}
        assert email_mgr in emails
        assert email_ro not in emails

    def test_get_all_users_search_is_case_insensitive(
        self, app, admin_user, unique_email
    ):
        """
        Search should be case-insensitive (ILIKE), matching
        regardless of the casing used in the query.
        """
        email = unique_email("case_srch")
        user_service.provision_user(
            email=email,
            first_name="CaseSensitive",
            last_name="Test",
            provisioned_by=admin_user.id,
        )

        result = user_service.get_all_users(search="casesensitive", per_page=100)
        emails = {u.email for u in result.items}
        assert email in emails


# =====================================================================
# 6. Role and user lookup helpers
# =====================================================================


class TestUserLookupHelpers:
    """Verify the simple lookup functions in user_service."""

    def test_get_user_by_id_returns_user(self, app, admin_user, unique_email):
        """get_user_by_id should return the correct user."""
        email = unique_email("lookup_id")
        user = user_service.provision_user(
            email=email,
            first_name="Lookup",
            last_name="ById",
            provisioned_by=admin_user.id,
        )

        found = user_service.get_user_by_id(user.id)
        assert found is not None
        assert found.email == email

    def test_get_user_by_id_returns_none_for_missing(self, app):
        """get_user_by_id should return None for a nonexistent ID."""
        found = user_service.get_user_by_id(999999)
        assert found is None

    def test_get_user_by_email_returns_user(self, app, admin_user, unique_email):
        """get_user_by_email should find users by their email."""
        email = unique_email("lookup_email")
        user_service.provision_user(
            email=email,
            first_name="Lookup",
            last_name="ByEmail",
            provisioned_by=admin_user.id,
        )

        found = user_service.get_user_by_email(email)
        assert found is not None
        assert found.first_name == "Lookup"

    def test_get_user_by_email_returns_none_for_missing(self, app):
        """get_user_by_email should return None for unknown emails."""
        found = user_service.get_user_by_email("nobody@nowhere.test")
        assert found is None

    def test_get_user_by_entra_id_returns_user(self, app, admin_user, unique_email):
        """get_user_by_entra_id should find users by their Azure AD OID."""
        email = unique_email("lookup_entra")
        entra_oid = "aaaa-bbbb-cccc-dddd-eeee-0001"

        user_service.provision_user(
            email=email,
            first_name="Lookup",
            last_name="ByEntra",
            entra_object_id=entra_oid,
            provisioned_by=admin_user.id,
        )

        found = user_service.get_user_by_entra_id(entra_oid)
        assert found is not None
        assert found.email == email

    def test_get_user_by_entra_id_returns_none_for_missing(self, app):
        """get_user_by_entra_id should return None for unknown OIDs."""
        found = user_service.get_user_by_entra_id("zzzz-nonexistent-oid")
        assert found is None

    def test_get_all_roles_returns_seeded_roles(self, app):
        """
        get_all_roles should return the five seeded roles:
        admin, budget_executive, it_staff, manager, read_only.
        """
        all_roles = user_service.get_all_roles()

        role_names = {r.role_name for r in all_roles}
        assert "admin" in role_names
        assert "it_staff" in role_names
        assert "manager" in role_names
        assert "budget_executive" in role_names
        assert "read_only" in role_names

    def test_get_all_roles_are_ordered_by_name(self, app):
        """get_all_roles results should be alphabetically ordered."""
        all_roles = user_service.get_all_roles()
        names = [r.role_name for r in all_roles]
        assert names == sorted(names)


# =====================================================================
# 7. Record login
# =====================================================================


class TestRecordLogin:
    """Verify ``user_service.record_login()`` updates timestamps."""

    def test_record_login_sets_last_login(self, app, admin_user, unique_email):
        """After record_login, last_login should be non-null."""
        email = unique_email("login_ts")
        user = user_service.provision_user(
            email=email,
            first_name="Login",
            last_name="Timestamp",
            provisioned_by=admin_user.id,
        )

        assert user.last_login is None

        user_service.record_login(user)

        assert user.last_login is not None

    def test_record_login_sets_first_login_once(self, app, admin_user, unique_email):
        """
        first_login_at should be set on the first login and never
        overwritten by subsequent logins.
        """
        email = unique_email("first_login")
        user = user_service.provision_user(
            email=email,
            first_name="First",
            last_name="Login",
            provisioned_by=admin_user.id,
        )

        assert user.first_login_at is None

        # First login.
        user_service.record_login(user)
        first_login_time = user.first_login_at
        assert first_login_time is not None

        # Second login.
        user_service.record_login(user)
        assert user.first_login_at == first_login_time

    def test_record_login_updates_last_login_on_subsequent_calls(
        self, app, admin_user, unique_email
    ):
        """
        last_login should be updated on every call, not just the first.
        """
        email = unique_email("multi_login")
        user = user_service.provision_user(
            email=email,
            first_name="Multi",
            last_name="Login",
            provisioned_by=admin_user.id,
        )

        user_service.record_login(user)
        first_last_login = user.last_login

        # Call again; last_login should change (or at least not be None).
        user_service.record_login(user)
        # The timestamps may be identical if the test runs fast enough,
        # but last_login should still be non-null and >= first.
        assert user.last_login is not None
        assert user.last_login >= first_last_login
