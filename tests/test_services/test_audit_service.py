"""
Unit tests for the audit service layer.

Tests every public function in ``app.services.audit_service`` against
the real SQL Server test database.  Verifies audit log creation,
request metadata capture, JSON serialization of change values,
login/logout convenience helpers, paginated querying with filters,
date-range filtering, result ordering, and the distinct entity type
lookup.

Design decisions:
    - All tests call service functions directly (not via HTTP routes)
      to isolate service-layer behavior from route-layer error handling.
    - The ``admin_user`` fixture supplies a ``user_id`` for audit
      entries that require a non-null user reference.
    - Tests that verify request-context behavior (IP address, user
      agent) use ``app.test_request_context()`` to simulate a real
      HTTP request environment.
    - Tests that verify behavior *outside* a request context call the
      service functions directly within ``app.app_context()`` (which
      the ``app`` fixture already provides).
    - No database mocking.  The transactional rollback / cleanup
      pattern from ``conftest.py`` keeps the test database clean.
    - Every assertion is specific: we check exact field values, not
      just "record exists."

Fixture reminder (from conftest.py):
    roles dict keys: admin, it_staff, manager, budget_executive, read_only
    sample_org keys: dept_a, dept_b, div_a1, div_a2, div_b1, div_b2,
                     pos_a1_1 (auth=3), pos_a1_2 (auth=5), pos_a2_1 (auth=2),
                     pos_b1_1 (auth=4), pos_b1_2 (auth=1), pos_b2_1 (auth=6)

Run this file in isolation::

    pytest tests/test_services/test_audit_service.py -v
"""

import json
import time as _time
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.test import EnvironBuilder

from app.extensions import db
from app.models.audit import AuditLog
from app.services import audit_service


# =====================================================================
# Local helper: generate unique entity identifiers per test run
# =====================================================================

_local_counter = int(_time.time() * 10) % 9000


def _next_entity_id():
    """
    Return a unique integer suitable for use as a fake ``entity_id``.

    Prevents collisions when multiple tests create audit log entries
    in the same session with the same entity_type.
    """
    global _local_counter  # pylint: disable=global-statement
    _local_counter += 1
    return _local_counter


# =====================================================================
# 1. log_change -- core audit entry creation
# =====================================================================


class TestLogChange:
    """Verify ``audit_service.log_change()`` creates correct audit entries."""

    def test_log_change_creates_entry_with_all_fields(self, app, admin_user):
        """
        Calling log_change with all parameters should persist an
        AuditLog record with every field populated correctly.
        """
        entity_id = _next_entity_id()
        previous = {"name": "Old Name", "cost": "100.00"}
        new = {"name": "New Name", "cost": "150.00"}

        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type="equip.hardware",
            entity_id=entity_id,
            previous_value=previous,
            new_value=new,
        )
        # Flush is called inside log_change; commit to make visible.
        db.session.commit()

        assert entry is not None
        assert isinstance(entry, AuditLog)
        assert entry.user_id == admin_user.id
        assert entry.action_type == "UPDATE"
        assert entry.entity_type == "equip.hardware"
        assert entry.entity_id == entity_id

        # Verify JSON serialization of change values.
        stored_prev = json.loads(entry.previous_value)
        stored_new = json.loads(entry.new_value)
        assert stored_prev == previous
        assert stored_new == new

    def test_log_change_returns_audit_log_instance(self, app, admin_user):
        """The return value should be an AuditLog model instance."""
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="org.department",
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        assert isinstance(entry, AuditLog)

    def test_log_change_assigns_id_after_flush(self, app, admin_user):
        """
        After log_change calls db.session.flush(), the entry should
        have a non-null primary key immediately, before any explicit
        commit by the caller.
        """
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="org.division",
            entity_id=_next_entity_id(),
        )
        # The entry should have an ID without needing a separate commit,
        # because log_change calls flush() internally.
        assert entry.id is not None
        assert isinstance(entry.id, int)

        # Clean up.
        db.session.commit()

    def test_log_change_persists_to_database(self, app, admin_user):
        """
        The entry created by log_change should be retrievable from
        the database via a fresh query after commit.
        """
        entity_id = _next_entity_id()

        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="equip.software_type",
            entity_id=entity_id,
        )
        db.session.commit()

        # Re-query to confirm persistence.
        found = db.session.get(AuditLog, entry.id)
        assert found is not None
        assert found.action_type == "CREATE"
        assert found.entity_type == "equip.software_type"
        assert found.entity_id == entity_id

    def test_log_change_with_none_user_id_for_system_action(self, app):
        """
        System actions (e.g., HR sync, CLI commands) pass None as
        user_id.  The entry should be created with user_id = NULL.
        """
        entry = audit_service.log_change(
            user_id=None,
            action_type="SYNC",
            entity_type="org.department",
            entity_id=_next_entity_id(),
            new_value={"synced": True},
        )
        db.session.commit()

        assert entry.user_id is None

        # Verify via fresh query.
        found = db.session.get(AuditLog, entry.id)
        assert found.user_id is None

    def test_log_change_with_none_entity_id(self, app, admin_user):
        """
        Some actions (like bulk operations) may not have a single
        entity_id.  Passing None should be accepted.
        """
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="DELETE",
            entity_type="equip.position_hardware",
            entity_id=None,
            previous_value={"bulk_delete": True, "count": 5},
        )
        db.session.commit()

        assert entry.entity_id is None

    def test_log_change_with_none_previous_and_new_values(self, app, admin_user):
        """
        When both previous_value and new_value are None (e.g., a
        simple event log), both columns should store NULL, not the
        string "null".
        """
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="auth.user",
            entity_id=_next_entity_id(),
            previous_value=None,
            new_value=None,
        )
        db.session.commit()

        found = db.session.get(AuditLog, entry.id)
        assert found.previous_value is None
        assert found.new_value is None

    def test_log_change_create_convention_null_previous(self, app, admin_user):
        """
        Per the AuditLog docstring, CREATE actions should have
        previous_value=NULL and new_value with the full record.
        Verify the service stores this convention correctly.
        """
        new_record = {
            "email": "new_user@example.com",
            "role": "manager",
            "is_active": True,
        }

        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="auth.user",
            entity_id=_next_entity_id(),
            previous_value=None,
            new_value=new_record,
        )
        db.session.commit()

        assert entry.previous_value is None
        assert json.loads(entry.new_value) == new_record

    def test_log_change_delete_convention_null_new(self, app, admin_user):
        """
        Per the AuditLog docstring, DELETE actions should have
        previous_value with the full record and new_value=NULL.
        """
        old_record = {
            "type_name": "Laptop",
            "cost_per_unit": "1200.00",
            "is_active": True,
        }

        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="DELETE",
            entity_type="equip.hardware_type",
            entity_id=_next_entity_id(),
            previous_value=old_record,
            new_value=None,
        )
        db.session.commit()

        assert json.loads(entry.previous_value) == old_record
        assert entry.new_value is None

    def test_log_change_update_convention_both_populated(self, app, admin_user):
        """
        Per the AuditLog docstring, UPDATE actions should populate
        both previous_value and new_value with only the changed fields.
        """
        previous = {"cost_per_unit": "1200.00"}
        new = {"cost_per_unit": "1350.00"}

        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type="equip.hardware_type",
            entity_id=_next_entity_id(),
            previous_value=previous,
            new_value=new,
        )
        db.session.commit()

        assert json.loads(entry.previous_value) == previous
        assert json.loads(entry.new_value) == new

    def test_log_change_stores_complex_nested_values(self, app, admin_user):
        """
        The JSON serialization should handle nested dicts, lists,
        and mixed types without data loss.
        """
        complex_value = {
            "scopes": [
                {"scope_type": "department", "department_id": 1},
                {"scope_type": "division", "division_id": 5},
            ],
            "metadata": {
                "changed_fields": ["scope_type", "department_id"],
                "count": 2,
                "verified": True,
                "notes": None,
            },
        }

        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type="auth.user_scope",
            entity_id=_next_entity_id(),
            new_value=complex_value,
        )
        db.session.commit()

        stored = json.loads(entry.new_value)
        assert stored == complex_value
        # Verify nested structure survived round-trip.
        assert len(stored["scopes"]) == 2
        assert stored["scopes"][0]["scope_type"] == "department"
        assert stored["metadata"]["notes"] is None

    def test_log_change_created_at_is_populated(self, app, admin_user):
        """
        The created_at timestamp should be set by the database
        server (via SYSUTCDATETIME default).  After commit, the
        field should be a non-null datetime.
        """
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="org.position",
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        # Re-query to get the server-set default.
        found = db.session.get(AuditLog, entry.id)
        assert found.created_at is not None
        assert isinstance(found.created_at, datetime)


# =====================================================================
# 2. log_change -- request context metadata capture
# =====================================================================


class TestLogChangeRequestContext:
    """
    Verify that ``log_change`` captures IP address and user agent
    when called inside a Flask request context, and gracefully
    skips them when called outside one.
    """

    def test_log_change_captures_ip_address_in_request_context(self, app, admin_user):
        """
        Inside a request context, the entry's ip_address should
        match the request's remote_addr.
        """
        with app.test_request_context(
            "/fake-path",
            environ_base={"REMOTE_ADDR": "192.168.1.42"},
        ):
            entry = audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type="equip.hardware",
                entity_id=_next_entity_id(),
            )
            db.session.commit()

        assert entry.ip_address == "192.168.1.42"

    def test_log_change_captures_user_agent_in_request_context(self, app, admin_user):
        """
        Inside a request context, the entry's user_agent should
        contain the User-Agent header string from the request.
        """
        test_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TestBrowser/1.0"

        with app.test_request_context(
            "/fake-path",
            headers={"User-Agent": test_ua},
        ):
            entry = audit_service.log_change(
                user_id=admin_user.id,
                action_type="UPDATE",
                entity_type="equip.software",
                entity_id=_next_entity_id(),
            )
            db.session.commit()

        assert entry.user_agent is not None
        assert "TestBrowser/1.0" in entry.user_agent

    def test_log_change_truncates_long_user_agent_to_500_chars(self, app, admin_user):
        """
        The user_agent column is VARCHAR(500).  The service slices
        the user agent string to 500 characters to prevent overflow.
        """
        # Build a user agent string longer than 500 characters.
        long_ua = "X" * 600

        with app.test_request_context(
            "/fake-path",
            headers={"User-Agent": long_ua},
        ):
            entry = audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type="equip.hardware_type",
                entity_id=_next_entity_id(),
            )
            db.session.commit()

        assert entry.user_agent is not None
        assert len(entry.user_agent) <= 500

    def test_log_change_works_outside_request_context(self, app, admin_user):
        """
        When called outside a Flask request context (e.g., from a
        CLI command or background task), log_change should still
        succeed.  The ip_address and user_agent fields should be
        None because there is no HTTP request to read from.
        """
        # The app fixture provides an app_context but NOT a request
        # context, so this call exercises the RuntimeError catch.
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type="SYNC",
            entity_type="org.department",
            entity_id=_next_entity_id(),
            new_value={"synced_count": 15},
        )
        db.session.commit()

        assert entry.ip_address is None
        assert entry.user_agent is None or entry.user_agent == ""
        # The rest of the entry should still be valid.
        assert entry.action_type == "SYNC"
        assert entry.entity_type == "org.department"

    def test_log_change_captures_ipv6_address(self, app, admin_user):
        """
        The ip_address column is VARCHAR(45), which accommodates
        IPv6 addresses.  Verify an IPv6 address is stored correctly.
        """
        ipv6_addr = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"

        with app.test_request_context(
            "/fake-path",
            environ_base={"REMOTE_ADDR": ipv6_addr},
        ):
            entry = audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type="auth.user",
                entity_id=_next_entity_id(),
            )
            db.session.commit()

        assert entry.ip_address == ipv6_addr


# =====================================================================
# 3. log_change -- all valid action types
# =====================================================================


class TestLogChangeActionTypes:
    """
    Verify that log_change accepts every action_type defined in the
    CHECK constraint on the audit_log table.  Each valid action_type
    should be persisted without a database error.
    """

    @pytest.mark.parametrize(
        "action_type",
        ["CREATE", "UPDATE", "DELETE", "LOGIN", "LOGOUT", "SYNC", "COPY"],
    )
    def test_log_change_accepts_valid_action_type(self, app, admin_user, action_type):
        """
        Each action_type in the CHECK constraint should be accepted
        by the database without an IntegrityError.
        """
        entry = audit_service.log_change(
            user_id=admin_user.id,
            action_type=action_type,
            entity_type="test.action_type_check",
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        found = db.session.get(AuditLog, entry.id)
        assert found is not None
        assert found.action_type == action_type


# =====================================================================
# 4. log_login and log_logout convenience helpers
# =====================================================================


class TestLogLogin:
    """Verify ``audit_service.log_login()`` records login events."""

    def test_log_login_creates_login_entry(self, app, admin_user):
        """
        log_login should create an AuditLog with action_type='LOGIN'
        and entity_type='auth.user'.
        """
        entry = audit_service.log_login(user_id=admin_user.id)
        db.session.commit()

        assert entry is not None
        assert entry.action_type == "LOGIN"
        assert entry.entity_type == "auth.user"

    def test_log_login_sets_entity_id_to_user_id(self, app, admin_user):
        """
        For login events, the entity_id should match the user_id
        because the entity being acted upon is the user themselves.
        """
        entry = audit_service.log_login(user_id=admin_user.id)
        db.session.commit()

        assert entry.entity_id == admin_user.id

    def test_log_login_sets_user_id(self, app, admin_user):
        """The user_id on the audit entry should match the logged-in user."""
        entry = audit_service.log_login(user_id=admin_user.id)
        db.session.commit()

        assert entry.user_id == admin_user.id

    def test_log_login_has_no_previous_or_new_value(self, app, admin_user):
        """
        Login events are simple markers; they should not carry any
        previous_value or new_value payloads.
        """
        entry = audit_service.log_login(user_id=admin_user.id)
        db.session.commit()

        assert entry.previous_value is None
        assert entry.new_value is None

    def test_log_login_returns_audit_log_instance(self, app, admin_user):
        """The return value should be a proper AuditLog model instance."""
        entry = audit_service.log_login(user_id=admin_user.id)
        db.session.commit()

        assert isinstance(entry, AuditLog)
        assert entry.id is not None

    def test_log_login_persists_to_database(self, app, admin_user):
        """The login entry should be retrievable via a fresh query."""
        entry = audit_service.log_login(user_id=admin_user.id)
        db.session.commit()

        found = db.session.get(AuditLog, entry.id)
        assert found is not None
        assert found.action_type == "LOGIN"
        assert found.user_id == admin_user.id


class TestLogLogout:
    """Verify ``audit_service.log_logout()`` records logout events."""

    def test_log_logout_creates_logout_entry(self, app, admin_user):
        """
        log_logout should create an AuditLog with action_type='LOGOUT'
        and entity_type='auth.user'.
        """
        entry = audit_service.log_logout(user_id=admin_user.id)
        db.session.commit()

        assert entry is not None
        assert entry.action_type == "LOGOUT"
        assert entry.entity_type == "auth.user"

    def test_log_logout_sets_entity_id_to_user_id(self, app, admin_user):
        """The entity_id should match the user_id for logout events."""
        entry = audit_service.log_logout(user_id=admin_user.id)
        db.session.commit()

        assert entry.entity_id == admin_user.id

    def test_log_logout_sets_user_id(self, app, admin_user):
        """The user_id on the audit entry should match the logged-out user."""
        entry = audit_service.log_logout(user_id=admin_user.id)
        db.session.commit()

        assert entry.user_id == admin_user.id

    def test_log_logout_has_no_previous_or_new_value(self, app, admin_user):
        """Logout events should not carry change payloads."""
        entry = audit_service.log_logout(user_id=admin_user.id)
        db.session.commit()

        assert entry.previous_value is None
        assert entry.new_value is None

    def test_log_logout_persists_to_database(self, app, admin_user):
        """The logout entry should be retrievable via a fresh query."""
        entry = audit_service.log_logout(user_id=admin_user.id)
        db.session.commit()

        found = db.session.get(AuditLog, entry.id)
        assert found is not None
        assert found.action_type == "LOGOUT"


# =====================================================================
# 5. get_audit_logs -- pagination
# =====================================================================


class TestGetAuditLogsPagination:
    """
    Verify ``audit_service.get_audit_logs()`` returns properly
    paginated results with correct metadata.
    """

    def test_get_audit_logs_returns_pagination_object(self, app, admin_user):
        """
        The return value should be a SQLAlchemy pagination object
        with .items, .total, .pages, .page, and .per_page attributes.
        """
        # Create at least one entry so the query has data.
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="test.pagination",
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        result = audit_service.get_audit_logs(page=1, per_page=10)

        assert hasattr(result, "items")
        assert hasattr(result, "total")
        assert hasattr(result, "pages")
        assert hasattr(result, "page")
        assert hasattr(result, "per_page")

    def test_get_audit_logs_respects_per_page_limit(self, app, admin_user):
        """
        When more entries exist than per_page allows, the returned
        items list should be capped at per_page.
        """
        # Create 5 entries with a unique entity_type for filtering.
        unique_type = f"test.perpage_{_next_entity_id()}"
        for _ in range(5):
            audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type=unique_type,
                entity_id=_next_entity_id(),
            )
        db.session.commit()

        result = audit_service.get_audit_logs(
            page=1, per_page=3, entity_type=unique_type
        )

        assert len(result.items) == 3
        assert result.total == 5
        assert result.pages == 2

    def test_get_audit_logs_page_two_returns_remaining(self, app, admin_user):
        """
        Requesting page 2 with per_page=3 and 5 total entries
        should return the remaining 2 entries.
        """
        unique_type = f"test.page2_{_next_entity_id()}"
        for _ in range(5):
            audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type=unique_type,
                entity_id=_next_entity_id(),
            )
        db.session.commit()

        result = audit_service.get_audit_logs(
            page=2, per_page=3, entity_type=unique_type
        )

        assert len(result.items) == 2
        assert result.page == 2

    def test_get_audit_logs_out_of_range_page_returns_empty(self, app, admin_user):
        """
        Requesting a page beyond the last page should return an
        empty items list (error_out=False behavior).
        """
        unique_type = f"test.outofrange_{_next_entity_id()}"
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        result = audit_service.get_audit_logs(
            page=999, per_page=10, entity_type=unique_type
        )

        assert len(result.items) == 0

    def test_get_audit_logs_default_per_page_is_50(self, app, admin_user):
        """
        When per_page is not specified, it should default to 50
        per the function signature.
        """
        result = audit_service.get_audit_logs(page=1)
        assert result.per_page == 50


# =====================================================================
# 6. get_audit_logs -- ordering
# =====================================================================


class TestGetAuditLogsOrdering:
    """Verify that audit log results are ordered newest-first."""

    def test_get_audit_logs_ordered_by_created_at_descending(self, app, admin_user):
        """
        The first item in the results should have a created_at that
        is greater than or equal to the last item's created_at.
        Entries are ordered by created_at DESC.
        """
        unique_type = f"test.ordering_{_next_entity_id()}"
        for i in range(3):
            audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type=unique_type,
                entity_id=_next_entity_id(),
                new_value={"sequence": i},
            )
        db.session.commit()

        result = audit_service.get_audit_logs(
            page=1, per_page=10, entity_type=unique_type
        )

        items = result.items
        assert len(items) == 3
        # Verify descending order: each item's created_at should be
        # >= the next item's created_at.
        for j in range(len(items) - 1):
            assert items[j].created_at >= items[j + 1].created_at


# =====================================================================
# 7. get_audit_logs -- filtering by user_id
# =====================================================================


class TestGetAuditLogsFilterByUser:
    """Verify user_id filtering returns only the target user's entries."""

    def test_get_audit_logs_filtered_by_user_returns_only_that_user(
        self, app, admin_user, it_staff_user
    ):
        """
        Create entries for two different users, then filter by one.
        Only that user's entries should appear in the results.
        """
        unique_type = f"test.userfilter_{_next_entity_id()}"

        # Admin creates an entry.
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        # IT staff creates an entry.
        audit_service.log_change(
            user_id=it_staff_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        # Filter for admin only.
        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            user_id=admin_user.id,
            entity_type=unique_type,
        )

        assert result.total == 1
        assert all(item.user_id == admin_user.id for item in result.items)

    def test_get_audit_logs_user_filter_with_no_matches_returns_empty(self, app):
        """
        Filtering by a user_id that has no entries should return
        an empty result set, not an error.
        """
        result = audit_service.get_audit_logs(page=1, per_page=10, user_id=999999)

        assert result.total == 0
        assert len(result.items) == 0


# =====================================================================
# 8. get_audit_logs -- filtering by action_type
# =====================================================================


class TestGetAuditLogsFilterByAction:
    """Verify action_type filtering."""

    def test_get_audit_logs_filtered_by_action_type(self, app, admin_user):
        """
        Create entries with different action_types, then filter for
        one.  Only matching entries should appear.
        """
        unique_type = f"test.actionfilter_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="DELETE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            action_type="UPDATE",
            entity_type=unique_type,
        )

        assert result.total == 1
        assert result.items[0].action_type == "UPDATE"


# =====================================================================
# 9. get_audit_logs -- filtering by entity_type
# =====================================================================


class TestGetAuditLogsFilterByEntityType:
    """Verify entity_type filtering."""

    def test_get_audit_logs_filtered_by_entity_type(self, app, admin_user):
        """
        Create entries with different entity_types, then filter by
        one.  Only matching entries should appear.
        """
        type_a = f"test.entityfilter_a_{_next_entity_id()}"
        type_b = f"test.entityfilter_b_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=type_a,
            entity_id=_next_entity_id(),
        )
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=type_b,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        result = audit_service.get_audit_logs(page=1, per_page=50, entity_type=type_a)

        assert result.total == 1
        assert result.items[0].entity_type == type_a

    def test_get_audit_logs_entity_type_no_matches_returns_empty(self, app):
        """
        Filtering by a nonexistent entity_type should return zero
        results without raising an error.
        """
        result = audit_service.get_audit_logs(
            page=1,
            per_page=10,
            entity_type="nonexistent.entity.type.xyz",
        )

        assert result.total == 0
        assert len(result.items) == 0


# =====================================================================
# 10. get_audit_logs -- filtering by date range
# =====================================================================


class TestGetAuditLogsFilterByDate:
    """Verify start_date and end_date filtering."""

    def test_get_audit_logs_filtered_by_start_date(self, app, admin_user):
        """
        Entries created before start_date should be excluded.
        Since we cannot control SYSUTCDATETIME() precisely, we
        verify that filtering with a past date returns our entries
        and filtering with a future date excludes them.
        """
        unique_type = f"test.startdate_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        # A start_date well in the past should include our entry.
        past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            entity_type=unique_type,
            start_date=past_date,
        )
        assert result.total >= 1

        # A start_date in the future should exclude our entry.
        future_date = datetime(2099, 12, 31, tzinfo=timezone.utc)
        result_future = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            entity_type=unique_type,
            start_date=future_date,
        )
        assert result_future.total == 0

    def test_get_audit_logs_filtered_by_end_date(self, app, admin_user):
        """
        Entries created after end_date should be excluded.  An
        end_date well in the future should include our entries;
        one in the past should exclude them.
        """
        unique_type = f"test.enddate_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        # End date in the future: entry should be included.
        future_date = datetime(2099, 12, 31, tzinfo=timezone.utc)
        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            entity_type=unique_type,
            end_date=future_date,
        )
        assert result.total >= 1

        # End date in the past: entry should be excluded.
        past_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        result_past = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            entity_type=unique_type,
            end_date=past_date,
        )
        assert result_past.total == 0

    def test_get_audit_logs_filtered_by_date_range(self, app, admin_user):
        """
        Combining start_date and end_date should return only entries
        within the inclusive range.
        """
        unique_type = f"test.daterange_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        # A range that spans the present should include our entry.
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2099, 12, 31, tzinfo=timezone.utc)
        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            entity_type=unique_type,
            start_date=start,
            end_date=end,
        )
        assert result.total >= 1

        # A range entirely in the past should exclude it.
        old_start = datetime(2019, 1, 1, tzinfo=timezone.utc)
        old_end = datetime(2019, 12, 31, tzinfo=timezone.utc)
        result_old = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            entity_type=unique_type,
            start_date=old_start,
            end_date=old_end,
        )
        assert result_old.total == 0


# =====================================================================
# 11. get_audit_logs -- combined filters
# =====================================================================


class TestGetAuditLogsCombinedFilters:
    """
    Verify that multiple filters can be applied simultaneously and
    each one narrows the result set independently.
    """

    def test_get_audit_logs_combined_user_and_action_filters(
        self, app, admin_user, it_staff_user
    ):
        """
        Filter by both user_id and action_type at the same time.
        Only entries matching BOTH criteria should be returned.
        """
        unique_type = f"test.combined_{_next_entity_id()}"

        # Admin CREATE.
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        # Admin UPDATE.
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        # IT staff CREATE.
        audit_service.log_change(
            user_id=it_staff_user.id,
            action_type="CREATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        # Filter: admin + CREATE only.
        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=unique_type,
        )

        assert result.total == 1
        assert result.items[0].user_id == admin_user.id
        assert result.items[0].action_type == "CREATE"

    def test_get_audit_logs_all_filters_applied_simultaneously(self, app, admin_user):
        """
        Apply user_id, action_type, entity_type, start_date, and
        end_date all at once.  The result should satisfy every
        filter criterion.
        """
        unique_type = f"test.allfilters_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type=unique_type,
            entity_id=_next_entity_id(),
            previous_value={"old": True},
            new_value={"new": True},
        )
        db.session.commit()

        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            user_id=admin_user.id,
            action_type="UPDATE",
            entity_type=unique_type,
            start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2099, 12, 31, tzinfo=timezone.utc),
        )

        assert result.total == 1
        item = result.items[0]
        assert item.user_id == admin_user.id
        assert item.action_type == "UPDATE"
        assert item.entity_type == unique_type


# =====================================================================
# 12. get_distinct_entity_types
# =====================================================================


class TestGetDistinctEntityTypes:
    """Verify ``audit_service.get_distinct_entity_types()``."""

    def test_get_distinct_entity_types_returns_list(self, app, admin_user):
        """The return value should be a list of strings."""
        # Create at least one entry to guarantee non-empty results.
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="test.distinct_check",
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        result = audit_service.get_distinct_entity_types()

        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(item, str) for item in result)

    def test_get_distinct_entity_types_includes_created_types(self, app, admin_user):
        """
        After creating entries with specific entity_types, those
        types should appear in the distinct list.
        """
        type_x = f"test.distinct_x_{_next_entity_id()}"
        type_y = f"test.distinct_y_{_next_entity_id()}"

        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=type_x,
            entity_id=_next_entity_id(),
        )
        audit_service.log_change(
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type=type_y,
            entity_id=_next_entity_id(),
        )
        db.session.commit()

        result = audit_service.get_distinct_entity_types()

        assert type_x in result
        assert type_y in result

    def test_get_distinct_entity_types_is_sorted_alphabetically(self, app, admin_user):
        """The returned list should be sorted in ascending alphabetical order."""
        result = audit_service.get_distinct_entity_types()

        assert result == sorted(result)

    def test_get_distinct_entity_types_does_not_duplicate(self, app, admin_user):
        """
        Multiple entries with the same entity_type should produce
        only one occurrence in the distinct list.
        """
        unique_type = f"test.no_dupe_{_next_entity_id()}"

        # Create three entries with the same entity_type.
        for _ in range(3):
            audit_service.log_change(
                user_id=admin_user.id,
                action_type="CREATE",
                entity_type=unique_type,
                entity_id=_next_entity_id(),
            )
        db.session.commit()

        result = audit_service.get_distinct_entity_types()

        # Count occurrences; should be exactly 1.
        assert result.count(unique_type) == 1


# =====================================================================
# 13. Integration: audit entries created by other services
# =====================================================================


class TestAuditIntegrationWithUserService:
    """
    Verify that audit entries created indirectly through other
    services (e.g., user_service.provision_user) are correctly
    retrievable via audit_service.get_audit_logs.

    This is an integration-level sanity check that proves the audit
    trail works end-to-end, not just when log_change is called
    directly.
    """

    def test_user_provisioning_creates_queryable_audit_entry(self, app, admin_user):
        """
        After provisioning a user via user_service, a CREATE audit
        entry for 'auth.user' should be retrievable by filtering
        on the admin's user_id.
        """
        # Import here to avoid circular import issues at module level.
        from app.services import user_service

        # Use the unique email pattern from the project.
        email = f"_tst_audit_integ_{_next_entity_id()}@test.local"

        user = user_service.provision_user(
            email=email,
            first_name="AuditInteg",
            last_name="Test",
            provisioned_by=admin_user.id,
        )

        # The audit entry should exist and be findable.
        result = audit_service.get_audit_logs(
            page=1,
            per_page=50,
            user_id=admin_user.id,
            action_type="CREATE",
            entity_type="auth.user",
        )

        # Find the entry for our specific user.
        matching = [item for item in result.items if item.entity_id == user.id]
        assert len(matching) >= 1

        entry = matching[0]
        new_val = json.loads(entry.new_value)
        assert new_val["email"] == email

    def test_audit_entry_stores_previous_and_new_values_for_role_change(
        self, app, admin_user
    ):
        """
        When user_service.change_user_role is called, the audit entry
        should store the old role in previous_value and the new role
        in new_value, demonstrating the full round-trip of the audit
        trail through a real service call.
        """
        from app.services import user_service

        email = f"_tst_audit_role_{_next_entity_id()}@test.local"

        user = user_service.provision_user(
            email=email,
            first_name="RoleAudit",
            last_name="Integ",
            role_name="read_only",
            provisioned_by=admin_user.id,
        )

        user_service.change_user_role(
            user_id=user.id,
            new_role_name="manager",
            changed_by=admin_user.id,
        )

        # Find the UPDATE audit entry for this user's role change.
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
        assert new["role"] == "manager"
