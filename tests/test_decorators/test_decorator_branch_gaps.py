"""
Branch-gap tests for the remaining uncovered statements in the
authorization decorators.

Targets one missing statement in each of the three decorator wrappers
in ``app/decorators.py``:

    - ``role_required.decorator.wrapper`` (7 stmts, 1 missing, 86%)
    - ``permission_required.decorator.wrapper`` (7 stmts, 1 missing, 86%)
    - ``scope_check.decorator.wrapper`` (17 stmts, 1 missing, 94%)

The existing ``test_role_required.py`` provides thorough coverage of
these decorators via both integration tests (real routes) and unit
tests (decorated dummy functions).  However, each wrapper has one
uncovered statement.  Based on the source code analysis:

    **role_required.wrapper**: The ``if not current_user.is_authenticated:
    abort(401)`` guard is the likely uncovered branch.  In production,
    ``@login_required`` runs before ``@role_required`` and redirects
    unauthenticated users to the login page, so the ``abort(401)`` inside
    ``role_required`` never fires.  The existing unauthenticated tests in
    ``test_role_required.py`` hit ``@login_required`` first, which
    redirects before ``role_required.wrapper`` executes.

    To cover this branch, we must call the decorator's wrapper function
    DIRECTLY (not through a route), simulating what would happen if
    ``@login_required`` were absent.

    **permission_required.wrapper**: Same pattern -- the
    ``if not current_user.is_authenticated: abort(401)`` guard.

    **scope_check.wrapper**: The uncovered branch is likely the
    ``entity_type == 'division'`` case or the ``entity_type == 'department'``
    fallthrough when ``has_access`` remains False.  Based on the source,
    scope_check only handles 'department' and 'position' entity types,
    so an unsupported entity type like 'division' would fall through
    with ``has_access = False`` and trigger the 403.

Test approach:
    - These tests use ``flask_login.login_user()`` and
    ``flask_login.logout_user()`` inside a test request context
    with decorated dummy functions.  This isolates the decorator
    logic from route wiring and ``@login_required``.
    - ``AnonymousUserMixin`` from Flask-Login is used to simulate
    an unauthenticated ``current_user``.

Fixture reminder (from conftest.py):
    app:            The Flask application instance.
    admin_user:     Role=admin, scope=organization.
    manager_user:   Role=manager, scope=division (div_a1).
    read_only_user: Role=read_only, scope=division (div_a1).
    sample_org:     Two departments, four divisions, six positions.

Run this file in isolation::

    pytest tests/test_decorators/test_decorator_branch_gaps.py -v
"""

import pytest
from flask_login import login_user, logout_user
from werkzeug.exceptions import BadRequest, Forbidden, Unauthorized

from app.decorators import permission_required, role_required, scope_check


# =====================================================================
# 1. role_required -- unauthenticated user hits abort(401)
# =====================================================================


class TestRoleRequiredUnauthenticatedBranch:
    """
    Cover the ``if not current_user.is_authenticated: abort(401)``
    guard inside ``role_required.decorator.wrapper``.

    In normal route usage, ``@login_required`` intercepts
    unauthenticated users before ``@role_required`` runs.  To
    exercise the guard, we call the decorated function directly
    without ``@login_required`` in front of it, and without
    logging in a user first.
    """

    def test_role_required_aborts_401_for_anonymous_user(self, app):
        """
        When ``current_user.is_authenticated`` is False (no user
        logged in), the wrapper should abort with 401.
        """

        @role_required("admin")
        def admin_only_view():
            """This should never execute for anonymous users."""
            return "admin content"

        with app.test_request_context("/test-role-anon"):
            # Do NOT call login_user -- current_user is anonymous.
            with pytest.raises(Unauthorized):
                admin_only_view()

    def test_role_required_multi_role_aborts_401_for_anonymous(self, app):
        """
        The 401 guard should fire regardless of how many roles are
        specified in the decorator arguments.
        """

        @role_required("admin", "it_staff", "manager")
        def multi_role_view():
            """Requires any of three roles."""
            return "multi content"

        with app.test_request_context("/test-multi-role-anon"):
            with pytest.raises(Unauthorized):
                multi_role_view()

    def test_role_required_allows_authenticated_correct_role(self, app, admin_user):
        """
        Sanity check: an authenticated user with the correct role
        should NOT trigger the 401 branch -- the function should
        execute normally.
        """

        @role_required("admin")
        def admin_view():
            """Admin-only view."""
            return "admin OK"

        with app.test_request_context("/test-role-ok"):
            login_user(admin_user)
            result = admin_view()
            assert result == "admin OK"

    def test_role_required_after_logout_aborts_401(self, app, admin_user):
        """
        If a user logs in, then logs out within the same request
        context, the wrapper should detect the anonymous state and
        abort with 401.

        This is an edge case but ensures the ``is_authenticated``
        check is evaluated at call time, not at decoration time.
        """

        @role_required("admin")
        def protected_view():
            """Protected view."""
            return "should not reach"

        with app.test_request_context("/test-role-logout"):
            login_user(admin_user)
            logout_user()
            with pytest.raises(Unauthorized):
                protected_view()


# =====================================================================
# 2. permission_required -- unauthenticated user hits abort(401)
# =====================================================================


class TestPermissionRequiredUnauthenticatedBranch:
    """
    Cover the ``if not current_user.is_authenticated: abort(401)``
    guard inside ``permission_required.decorator.wrapper``.

    Same approach as the role_required tests: call the decorated
    function directly without a logged-in user.
    """

    def test_permission_required_aborts_401_for_anonymous_user(self, app):
        """
        When no user is logged in, ``permission_required`` should
        abort with 401 before checking any permissions.
        """

        @permission_required("equipment.create")
        def create_equipment_view():
            """Requires equipment.create permission."""
            return "created"

        with app.test_request_context("/test-perm-anon"):
            with pytest.raises(Unauthorized):
                create_equipment_view()

    def test_permission_required_allows_authenticated_with_permission(
        self, app, admin_user
    ):
        """
        Sanity check: an authenticated user whose role grants the
        required permission should execute the function normally.
        """

        @permission_required("equipment.create")
        def create_view():
            """Requires equipment.create."""
            return "created OK"

        with app.test_request_context("/test-perm-ok"):
            login_user(admin_user)
            # Admin role should have equipment.create permission.
            result = create_view()
            assert result == "created OK"

    def test_permission_required_after_logout_aborts_401(self, app, admin_user):
        """
        Logging out mid-context should cause the 401 guard to fire.
        """

        @permission_required("equipment.create")
        def protected_view():
            """Protected by permission."""
            return "nope"

        with app.test_request_context("/test-perm-logout"):
            login_user(admin_user)
            logout_user()
            with pytest.raises(Unauthorized):
                protected_view()


# =====================================================================
# 3. scope_check -- unsupported entity_type falls through
# =====================================================================


class TestScopeCheckUnsupportedEntityType:
    """
    The ``scope_check`` decorator only handles ``entity_type`` values
    of ``'department'`` and ``'position'``.  If an unsupported entity
    type (like ``'division'``) is used, the ``has_access`` variable
    remains False and the decorator should abort with 403.

    This exercises the fallthrough branch where neither the
    ``if entity_type == 'department'`` nor the
    ``elif entity_type == 'position'`` conditions match.
    """

    def test_unsupported_entity_type_denies_non_org_user(
        self, app, manager_user, sample_org
    ):
        """
        A manager (non-org-scope) accessing a route with an
        unsupported entity_type should be denied because
        ``has_access`` never becomes True.
        """
        div_a1_id = sample_org["div_a1"].id

        @scope_check("division", "id")
        def division_view(id):  # pylint: disable=redefined-builtin
            """View using an unsupported entity type for scope_check."""
            return f"div-{id}"

        with app.test_request_context(f"/test-scope-div/{div_a1_id}"):
            login_user(manager_user)
            with pytest.raises(Forbidden):
                division_view(id=div_a1_id)

    def test_org_scope_user_bypasses_unsupported_entity_type(
        self, app, admin_user, sample_org
    ):
        """
        An org-scope user should bypass the entity_type check
        entirely (the ``has_org_scope()`` early return fires first),
        so even an unsupported entity type works for admins.
        """
        div_a1_id = sample_org["div_a1"].id

        @scope_check("division", "id")
        def division_view(id):  # pylint: disable=redefined-builtin
            """View using an unsupported entity type."""
            return f"div-{id}"

        with app.test_request_context(f"/test-scope-div-admin/{div_a1_id}"):
            login_user(admin_user)
            result = division_view(id=div_a1_id)
            assert result == f"div-{div_a1_id}"


# =====================================================================
# 4. scope_check -- unauthenticated user hits abort(401)
# =====================================================================


class TestScopeCheckUnauthenticatedBranch:
    """
    The ``scope_check`` wrapper also has an
    ``if not current_user.is_authenticated: abort(401)`` guard.
    This may or may not be the uncovered branch (the wrapper has
    17 statements with 1 missing), but testing it ensures full
    coverage of the authentication check.
    """

    def test_scope_check_aborts_401_for_anonymous_user(self, app):
        """
        When no user is logged in, ``scope_check`` should abort
        with 401 before evaluating any scope logic.
        """

        @scope_check("department", "id")
        def dept_view(id):  # pylint: disable=redefined-builtin
            """View protected by scope_check."""
            return f"dept-{id}"

        with app.test_request_context("/test-scope-anon/1"):
            with pytest.raises(Unauthorized):
                dept_view(id=1)


# =====================================================================
# 5. Combined: decorator chain behavior with all three decorators
# =====================================================================


class TestDecoratorChainEdgeCases:
    """
    Exercise edge cases that arise when decorators are stacked
    together, as they are in the real application routes.

    These tests document the interaction between ``role_required``
    and ``scope_check`` when both are applied to the same function.
    """

    def test_role_check_fires_before_scope_check(self, app, read_only_user, sample_org):
        """
        When both ``@role_required`` and ``@scope_check`` are applied,
        the role check should fire first (outermost decorator).
        A read_only user accessing an admin+scope-checked route
        should get 403 from the role check, not from the scope check.

        This verifies that decorator ordering is correct.
        """
        pos_id = sample_org["pos_a1_1"].id

        # Stack decorators in the same order as production routes:
        # @role_required runs first (outermost), then @scope_check.
        @role_required("admin")
        @scope_check("position", "id")
        def admin_scoped_view(id):  # pylint: disable=redefined-builtin
            """Admin-only, scope-checked view."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-chain/{pos_id}"):
            login_user(read_only_user)
            with pytest.raises(Forbidden):
                admin_scoped_view(id=pos_id)

    def test_correct_role_but_wrong_scope_is_blocked(
        self, app, manager_user, sample_org
    ):
        """
        A manager has the correct role for a manager-accessible
        route, but if scope_check denies them (wrong position),
        they should still get 403.
        """
        pos_b1_id = sample_org["pos_b1_1"].id  # Outside manager's div_a1 scope.

        @role_required("admin", "manager")
        @scope_check("position", "id")
        def manager_scoped_view(id):  # pylint: disable=redefined-builtin
            """Manager-accessible, scope-checked view."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-chain-scope/{pos_b1_id}"):
            login_user(manager_user)
            with pytest.raises(Forbidden):
                manager_scoped_view(id=pos_b1_id)

    def test_correct_role_and_correct_scope_succeeds(
        self, app, manager_user, sample_org
    ):
        """
        A manager with both the correct role and correct scope
        should pass through both decorators and execute the view.
        """
        pos_a1_id = sample_org["pos_a1_1"].id  # Inside manager's div_a1 scope.

        @role_required("admin", "manager")
        @scope_check("position", "id")
        def manager_scoped_view(id):  # pylint: disable=redefined-builtin
            """Manager-accessible, scope-checked view."""
            return f"pos-{id}"

        with app.test_request_context(f"/test-chain-ok/{pos_a1_id}"):
            login_user(manager_user)
            result = manager_scoped_view(id=pos_a1_id)
            assert result == f"pos-{pos_a1_id}"
