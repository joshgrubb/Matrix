"""
Branch-gap tests for the ``_add_org_scope`` helper in user_service.

Targets the private helper function ``_add_org_scope`` in
``app/services/user_service.py`` which is at 0% coverage (2 statements,
both missing).

``_add_org_scope`` creates a ``UserScope`` record with
``scope_type='organization'`` and adds it to the session WITHOUT
committing.  It is designed to be called as part of a larger
transaction (e.g., during auto-provisioning on first OAuth login).

Since the function is private and called internally, these tests
exercise it both:
    - **Directly**: Calling ``_add_org_scope`` to prove the function
      itself works correctly and gets covered.
    - **Indirectly**: Via any public code path that invokes it (if one
      exists in the current codebase).

Design decisions:
    - Tests create a real User record via the conftest fixtures,
      then call ``_add_org_scope`` directly.
    - The function does NOT commit, so tests must commit explicitly
      and verify the UserScope record exists in the database.
    - Since ``_add_org_scope`` is an internal helper, testing it
      directly is appropriate for coverage purposes even though
      callers would normally handle the surrounding transaction.

Fixture reminder (from conftest.py):
    admin_user:  Role=admin, scope=organization.
    roles:       Dict of seeded Role records.
    db_session:  SQLAlchemy session with rollback cleanup.
    create_user: Factory for custom role/scope combinations.

Run this file in isolation::

    pytest tests/test_services/test_user_service_branch_gaps.py -v
"""

import time as _time

import pytest

from app.extensions import db
from app.models.user import User, UserScope
from app.services.user_service import _add_org_scope


# =====================================================================
# Local helper fixture for unique emails
# =====================================================================

_local_counter = int(_time.time() * 10) % 9000


@pytest.fixture()
def unique_email():
    """
    Factory fixture returning a unique ``@test.local`` email each
    call.  Uses a distinct prefix to avoid collisions.
    """
    global _local_counter  # pylint: disable=global-statement

    def _make(prefix="usbg"):
        global _local_counter  # pylint: disable=global-statement
        _local_counter += 1
        return f"_tst_{prefix}_{_local_counter:04d}@test.local"

    return _make


# =====================================================================
# Helper: create a bare user with no scopes
# =====================================================================


def _create_bare_user(db_session, roles, unique_email, role_name="read_only"):
    """
    Create a User record with NO scopes attached.

    This is different from the conftest helpers which always assign
    at least one scope.  A bare user allows us to test that
    ``_add_org_scope`` is the sole source of the org scope.

    Args:
        db_session: The test database session.
        roles: The roles dict fixture.
        unique_email: The unique_email factory fixture.
        role_name: The role to assign.

    Returns:
        A committed User with zero UserScope records.
    """
    email = unique_email(f"bare_{role_name}")
    user = User(
        email=email,
        first_name="Bare",
        last_name="User",
        role_id=roles[role_name].id,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Confirm the user has no scopes before the test runs.
    existing = UserScope.query.filter_by(user_id=user.id).all()
    assert len(existing) == 0, "Bare user fixture should have zero scopes"

    return user


# =====================================================================
# 1. _add_org_scope -- creates an organization-scope UserScope record
# =====================================================================


class TestAddOrgScopeBasic:
    """
    Verify that ``_add_org_scope`` creates a UserScope record with
    the correct attributes.
    """

    def test_creates_org_scope_record(self, app, db_session, roles, unique_email):
        """
        Calling ``_add_org_scope`` on a bare user should add a
        UserScope with ``scope_type='organization'`` to the session.
        After commit, the record should be persisted.
        """
        user = _create_bare_user(db_session, roles, unique_email)

        # Call the function under test.
        _add_org_scope(user)

        # The function does not commit, so we must commit.
        db_session.commit()

        # Verify the scope was created.
        scopes = UserScope.query.filter_by(user_id=user.id).all()
        assert len(scopes) == 1
        assert scopes[0].scope_type == "organization"
        assert scopes[0].department_id is None
        assert scopes[0].division_id is None

    def test_scope_references_correct_user(self, app, db_session, roles, unique_email):
        """
        The created UserScope should have its ``user_id`` set to
        the given user's ID.
        """
        user = _create_bare_user(db_session, roles, unique_email)

        _add_org_scope(user)
        db_session.commit()

        scope = UserScope.query.filter_by(
            user_id=user.id,
            scope_type="organization",
        ).first()
        assert scope is not None
        assert scope.user_id == user.id


# =====================================================================
# 2. _add_org_scope -- does not commit (caller responsibility)
# =====================================================================


class TestAddOrgScopeNoCommit:
    """
    Verify that ``_add_org_scope`` adds to the session but does
    NOT call ``db.session.commit()``.  This is important because
    the function is designed to be part of a larger transaction.
    """

    def test_scope_not_visible_before_commit(
        self, app, db_session, roles, unique_email
    ):
        """
        After calling ``_add_org_scope`` but before committing,
        a fresh query should still show the scope (because it's in
        the same session), but if we rollback, it should disappear.
        """
        user = _create_bare_user(db_session, roles, unique_email)

        _add_org_scope(user)

        # The scope IS in the session (flushed or pending).
        # Rollback should remove it.
        db_session.rollback()

        scopes = UserScope.query.filter_by(user_id=user.id).all()
        # After rollback, no scopes should exist because _add_org_scope
        # does not commit.  The user itself might also be rolled back
        # depending on session state, but the key point is that the
        # scope is not persisted.
        assert len(scopes) == 0, (
            "_add_org_scope should not auto-commit; rollback should "
            "remove the pending scope"
        )


# =====================================================================
# 3. _add_org_scope -- user model integration
# =====================================================================


class TestAddOrgScopeModelIntegration:
    """
    After adding an org scope via ``_add_org_scope``, the User
    model's ``has_org_scope()`` helper should return True.
    """

    def test_has_org_scope_returns_true_after_add(
        self, app, db_session, roles, unique_email
    ):
        """
        A bare user with no scopes should return False from
        ``has_org_scope()``.  After ``_add_org_scope`` and commit,
        it should return True.
        """
        user = _create_bare_user(db_session, roles, unique_email)
        assert user.has_org_scope() is False

        _add_org_scope(user)
        db_session.commit()

        # Refresh the user to ensure the relationship is loaded.
        db_session.refresh(user)
        assert user.has_org_scope() is True


# =====================================================================
# 4. _add_org_scope -- multiple calls are additive
# =====================================================================


class TestAddOrgScopeIdempotency:
    """
    Calling ``_add_org_scope`` twice on the same user should create
    TWO scope records (the function does not check for duplicates).
    This verifies the function's behavior and documents that
    callers are responsible for deduplication.
    """

    def test_double_call_creates_two_scope_records(
        self, app, db_session, roles, unique_email
    ):
        """
        Two calls to ``_add_org_scope`` should produce two UserScope
        records.  The function is simple and does not deduplicate.
        """
        user = _create_bare_user(db_session, roles, unique_email)

        _add_org_scope(user)
        _add_org_scope(user)
        db_session.commit()

        scopes = UserScope.query.filter_by(
            user_id=user.id,
            scope_type="organization",
        ).all()
        assert len(scopes) == 2, (
            "_add_org_scope does not deduplicate; two calls should "
            "produce two records"
        )


# =====================================================================
# 5. _add_org_scope -- works for any role
# =====================================================================


class TestAddOrgScopeAnyRole:
    """
    ``_add_org_scope`` should work regardless of the user's role.
    The function does not inspect role; it blindly adds an org scope.
    """

    @pytest.mark.parametrize(
        "role_name",
        ["admin", "it_staff", "manager", "budget_executive", "read_only"],
    )
    def test_org_scope_created_for_each_role(
        self, app, db_session, roles, unique_email, role_name
    ):
        """
        Calling ``_add_org_scope`` should succeed for a user with
        any role, creating an organization-type scope.
        """
        user = _create_bare_user(db_session, roles, unique_email, role_name=role_name)

        _add_org_scope(user)
        db_session.commit()

        scope = UserScope.query.filter_by(
            user_id=user.id,
            scope_type="organization",
        ).first()
        assert scope is not None, f"_add_org_scope should work for role '{role_name}'"
