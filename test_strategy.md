# PositionMatrix -- Comprehensive Testing Strategy

**Date:** March 16, 2026
**Target:** CIO Review Readiness (Next Week)
**Stack:** Flask 3.x + SQLAlchemy + SQL Server + HTMX + pytest
**Author:** Testing Strategy Document

---

## 1. Current State Assessment

### 1.1 What Exists Today

The project currently has **three test files** containing approximately **20 tests**:

| File | Tests | What It Covers |
|------|-------|----------------|
| `tests/conftest.py` | (fixtures only) | App factory, DB session with rollback, test client |
| `tests/test_services/test_db_connection.py` | ~8 | DB connectivity, schema existence, seed data verification |
| `tests/test_services/test_cost_service.py` | ~7 | `calculate_position_cost()` with hardware, software, combined, edge cases |
| `tests/test_routes/test_main_routes.py` | ~3 | Dashboard 200 response, health check endpoint |

### 1.2 What Is Good About the Existing Setup

The foundation is solid in several respects:

- The `conftest.py` uses a **session-scoped app** and **function-scoped db_session with transaction rollback**, which is the correct pattern for integration tests against a real SQL Server instance. Tests stay isolated without needing to rebuild the schema each run.
- The `TestingConfig` disables CSRF and enables dev login, which removes friction from route testing.
- The `test_cost_service.py` file is well-structured: it creates ephemeral org/equipment data per test, tests edge cases (zero requirements, nonexistent position), and validates the `authorized_count` multiplier. This is a model for the rest of the test suite.
- Pytest configuration in `pyproject.toml` is properly set up with `testpaths`, naming conventions, and verbose output.

### 1.3 Critical Gaps

The following areas have **zero test coverage** and represent the highest risk for the CIO review:

| Gap | Risk Level | Why It Matters |
|-----|------------|----------------|
| **Authorization and scope enforcement** | CRITICAL | A manager seeing another department's data is a career-ending demo bug. No tests verify that `role_required`, `permission_required`, or `scope_check` decorators actually block unauthorized access. |
| **Requirements wizard flow (Steps 1-4)** | CRITICAL | The entire core workflow (select position, select hardware, select software, view summary) has zero route-level tests. Form submissions, HTMX endpoints, and the copy-from feature are untested. |
| **Service layer (5 of 6 services)** | HIGH | Only `cost_service` has tests. `requirement_service`, `equipment_service`, `organization_service`, `user_service`, and `audit_service` are completely untested. These contain all business logic. |
| **Admin routes** | HIGH | User provisioning, role changes, scope assignment, user deactivation/reactivation have no tests. An admin accidentally breaking user management during the demo would be very visible. |
| **Report and export routes** | MEDIUM | Cost summary, equipment report, CSV/Excel exports are untested. These are likely CIO demo highlights. |
| **Auth/OAuth flow** | MEDIUM | The OAuth callback, dev-login bypass, and session management are untested. Login failures during a demo are embarrassing. |
| **HR sync service** | MEDIUM | The NeoGov integration and employee sync logic are untested, but unlikely to be demoed live. |
| **Error handlers** | LOW | 404, 403, 500 handlers exist but are not verified. |
| **CLI commands** | LOW | Seed commands and `db-check` are untested. |

### 1.4 Estimated Current Coverage

Based on the project structure, the estimated line coverage is roughly **5-8%** of the `app/` directory. The `tests/` directory covers:

- `app/services/cost_service.py` -- partial coverage (~60%)
- `app/blueprints/main/routes.py` -- partial coverage (~50%)
- `app/config.py` -- exercised by app factory in fixtures
- `app/__init__.py` -- exercised by app factory in fixtures
- Everything else -- **0%**

---

## 2. Testing Architecture

### 2.1 Test Pyramid for PositionMatrix

The testing strategy follows a layered pyramid appropriate for a Flask + SQLAlchemy + server-rendered application:

```
        /  E2E  \           Manual smoke tests before CIO review
       / --------\
      / Integration\        Route tests with test client + real DB
     / -------------\
    /   Service Unit  \     Service functions with real DB (transactional)
   / ------------------\
  /   Model / Fixture    \  Model validation, data integrity
 / -----------------------\
/   Config & Infrastructure \  App factory, DB connection, seed data (EXISTS)
\-----------------------------/
```

### 2.2 Test Organization

```
tests/
    conftest.py                         # Shared fixtures (EXISTS, needs expansion)
    test_config/
        test_app_factory.py             # App creation, config loading
    test_models/
        test_user_model.py              # User model methods (has_permission, scopes)
        test_organization_model.py      # Org model relationships
        test_equipment_model.py         # Equipment model defaults
    test_services/
        test_db_connection.py           # (EXISTS)
        test_cost_service.py            # (EXISTS)
        test_requirement_service.py     # CRUD, copy, bulk, validation
        test_equipment_service.py       # Catalog CRUD, coverage
        test_organization_service.py    # Scope filtering, access checks
        test_user_service.py            # Provisioning, role changes, scopes
        test_audit_service.py           # Audit logging
        test_hr_sync_service.py         # NeoGov sync (mocked API)
        test_export_service.py          # CSV/Excel generation
        test_auth_service.py            # OAuth helpers (mocked MSAL)
    test_routes/
        test_main_routes.py             # (EXISTS, needs expansion)
        test_auth_routes.py             # Login, callback, dev-login
        test_admin_routes.py            # User management
        test_equipment_routes.py        # Catalog management
        test_requirements_routes.py     # The wizard flow (Steps 1-4)
        test_reports_routes.py          # Reports and exports
        test_organization_routes.py     # Org HTMX endpoints
    test_decorators/
        test_role_required.py           # Role decorator enforcement
        test_permission_required.py     # Permission decorator enforcement
        test_scope_check.py             # Scope decorator enforcement
    test_authorization/
        test_scope_isolation.py         # Cross-cutting scope leak tests
```

### 2.3 Fixture Strategy

The existing `conftest.py` needs expansion. Here is the complete fixture architecture:

**Root `tests/conftest.py` -- shared across all tests:**

- `app` (session scope) -- EXISTS, keep as-is
- `database` (session scope) -- EXISTS, keep as-is
- `db_session` (function scope) -- EXISTS, keep as-is
- `client` (function scope) -- EXISTS, needs authenticated variant
- `authenticated_client` (function scope) -- NEW: logs in as a configurable user via dev-login
- `admin_user` (function scope) -- NEW: creates/fetches an admin user for the test
- `manager_user` (function scope) -- NEW: creates/fetches a manager user with division scope
- `it_staff_user` (function scope) -- NEW: creates/fetches an IT staff user
- `read_only_user` (function scope) -- NEW: creates/fetches a read-only user
- `budget_user` (function scope) -- NEW: creates/fetches a budget executive user
- `sample_org_structure` (function scope) -- NEW: creates dept/div/position hierarchy
- `sample_equipment_catalog` (function scope) -- NEW: creates hw types, hw items, sw types, sw items

**Key design decisions for fixtures:**

1. **Use the real SQL Server test database**, not SQLite. The project uses SQL Server-specific features (schemas, `SYSUTCDATETIME()`, `sys.schemas` queries). SQLite would miss real bugs.
2. **Use transactional rollback** (already implemented) so tests stay fast and isolated.
3. **Authenticate via dev-login route** for route tests, since the app already supports `DEV_LOGIN_ENABLED` in testing config. This avoids mocking Flask-Login internals.
4. **Build minimal data per test** rather than loading a large shared dataset. The `test_cost_service.py` pattern of creating ephemeral records in an `autouse` fixture is the right model.

---

## 3. Prioritized Implementation Plan

Given the CIO review is next week, every test must be written in priority order. If time runs out, the P0 tests alone will catch the most embarrassing failures.

### Priority Legend

- **P0 (Days 1-2):** Tests that prevent demo-ending failures. Write these first, no exceptions.
- **P1 (Days 3-4):** Tests that prevent visible bugs during a walkthrough.
- **P2 (Day 5):** Tests that demonstrate thoroughness and production readiness.
- **P3 (Post-review):** Tests for completeness and long-term maintenance.

---

## 4. P0 -- Demo-Ending Failure Prevention (Days 1-2)

These tests catch bugs that would visibly break during a live demo or raise immediate security concerns.

### 4.1 Expanded Fixtures in conftest.py

**Priority:** P0 -- everything else depends on these.

```python
# tests/conftest.py additions needed:

# 1. Authenticated client factory fixture
#    - Accepts a user object
#    - Uses the dev-login route to authenticate
#    - Returns a test client with an active session
#
# 2. Role-specific user fixtures (admin, manager, it_staff, etc.)
#    - Each creates a User record with the correct role
#    - Manager gets division-level scope
#    - Admin gets org-wide scope
#    - Read-only gets division-level scope
#
# 3. Sample org structure fixture
#    - Two departments (dept_a, dept_b)
#    - Two divisions per department (div_a1, div_a2, div_b1, div_b2)
#    - Two positions per division (8 total)
#    - Sets authorized_count to known values for cost assertions
#
# 4. Sample equipment catalog fixture
#    - Two hardware types with two items each
#    - Two software types with two items each (one per_user, one tenant)
#    - Known costs for deterministic assertions
```

**Implementation notes:**

- The `authenticated_client` fixture should call `client.post("/auth/dev-login", data={"user_id": user.id})` and verify the response redirects successfully.
- User fixtures must create corresponding `UserScope` records so scope-based filtering works.
- All fixtures use `db_session` so they roll back after each test.

### 4.2 Authorization and Scope Isolation Tests

**File:** `tests/test_authorization/test_scope_isolation.py`
**Priority:** P0 -- a scope leak is the single worst bug to show a CIO.

These tests verify that users cannot see or modify data outside their authorized scope. Each test logs in as a specific role/scope combination and attempts to access resources they should not reach.

**Test cases (minimum 15 tests):**

```
test_manager_cannot_access_other_departments_positions
    Login as manager scoped to dept_a.
    GET /requirements/position/<dept_b_position_id>/summary.
    Assert 403 or redirect with warning flash.

test_manager_cannot_access_other_divisions_positions
    Login as manager scoped to div_a1.
    GET /requirements/position/<div_a2_position_id>/summary.
    Assert 403 or redirect with warning flash.

test_manager_cannot_copy_from_out_of_scope_position
    Login as manager scoped to div_a1.
    POST /requirements/position/<div_a1_pos>/copy-from/<div_b1_pos>.
    Assert 403 or redirect with warning flash.

test_read_only_cannot_access_admin_routes
    Login as read_only user.
    GET /admin/users.
    Assert 403.

test_read_only_cannot_provision_users
    Login as read_only user.
    POST /admin/users/provision with form data.
    Assert 403.

test_manager_cannot_access_admin_routes
    Login as manager.
    GET /admin/users.
    Assert 403.

test_manager_cannot_create_hardware
    Login as manager.
    POST /equipment/hardware/new with form data.
    Assert 403.

test_budget_executive_cannot_modify_equipment
    Login as budget_executive.
    POST /equipment/hardware/new.
    Assert 403.

test_it_staff_cannot_manage_users
    Login as it_staff.
    GET /admin/users.
    Assert 403 (admin-only route).

test_admin_can_access_all_positions
    Login as admin with org-wide scope.
    GET /requirements/position/<any_position_id>/summary.
    Assert 200.

test_org_scope_user_sees_all_departments
    Login as admin with org-wide scope.
    Verify organization_service.get_departments returns all depts.

test_division_scope_user_sees_only_scoped_positions
    Login as manager with div_a1 scope.
    Verify organization_service.get_positions returns only div_a1 positions.

test_department_scope_user_sees_all_divisions_in_dept
    Login as manager with dept_a scope.
    Verify get_positions returns positions from div_a1 AND div_a2.

test_unauthenticated_user_redirected_to_login
    Use unauthenticated client.
    GET /. Assert redirect to login.
    GET /admin/users. Assert redirect to login.
    GET /requirements/position/1/summary. Assert redirect to login.

test_deactivated_user_cannot_login
    Deactivate a user record.
    Attempt dev-login as that user.
    Assert login fails or is rejected.
```

**What these catch:** Any scenario where a department head sees another department's budget numbers, a read-only user modifies data, or a manager escalates to admin functions. These are the bugs that make executives lose confidence in a system.

### 4.3 Requirements Wizard Flow Tests

**File:** `tests/test_routes/test_requirements_routes.py`
**Priority:** P0 -- this is the core user workflow that will be demoed.

**Test cases (minimum 20 tests):**

```
Step 1: Select Position
    test_select_position_page_loads
        GET /requirements/select-position. Assert 200.
        Assert page contains department dropdown.

    test_htmx_divisions_returns_options
        GET /org/htmx/divisions/<dept_id>.
        Assert 200. Assert response contains <option> elements.

    test_htmx_positions_returns_options
        GET /org/htmx/positions/<div_id>.
        Assert 200. Assert response contains <option> elements.

    test_htmx_positions_with_requirements_filters_correctly
        Create a position with requirements and one without.
        GET /requirements/htmx/positions-with-requirements/<div_id>.
        Assert only the configured position appears.

Step 2: Select Hardware
    test_select_hardware_page_loads
        GET /requirements/position/<id>/hardware. Assert 200.
        Assert page contains hardware type accordions.

    test_select_hardware_submit_saves_requirements
        POST /requirements/position/<id>/hardware with form data.
        Assert redirect to software step.
        Verify PositionHardware records created in DB.

    test_select_hardware_respects_max_selections
        Submit more items than max_selections allows for a type.
        Assert validation error or items capped.

    test_select_hardware_updates_existing_requirements
        Create initial requirements. POST with changed quantities.
        Assert records updated, not duplicated.

    test_select_hardware_removes_unchecked_items
        Create requirements for items A and B.
        POST with only item A checked.
        Assert item B requirement removed.

Step 3: Select Software
    test_select_software_page_loads
        GET /requirements/position/<id>/software. Assert 200.

    test_select_software_submit_saves_requirements
        POST with software selections.
        Assert redirect to summary. Verify PositionSoftware records.

    test_select_software_shows_usage_counts
        Create requirements across multiple positions.
        Assert "Used by N positions" badges appear.

    test_select_software_shows_common_items
        Create positions with overlapping software in same division.
        Assert common items have "Suggested" badges.

Step 4: Summary
    test_position_summary_page_loads
        GET /requirements/position/<id>/summary. Assert 200.

    test_position_summary_shows_correct_costs
        Create known requirements (hw + sw with known costs).
        Assert displayed costs match expected calculations.

    test_position_summary_sets_submitted_status
        View summary for position with requirements.
        Assert requirements_status updated to "submitted".

    test_position_summary_does_not_downgrade_reviewed_status
        Set status to "reviewed". View summary.
        Assert status remains "reviewed" (not downgraded).

Copy From Feature
    test_copy_requirements_creates_duplicate_records
        Create requirements on source position.
        POST /requirements/position/<target>/copy-from/<source>.
        Assert target now has matching requirements.

    test_copy_requirements_redirects_to_hardware_step
        Assert redirect goes to select_hardware for the target.

    test_copy_requirements_requires_scope_on_both_positions
        Attempt copy where user lacks scope on source.
        Assert 403 or warning redirect.
```

**What these catch:** Broken form submissions, lost data during the wizard flow, incorrect cost displays, and scope violations in the copy feature. Any of these failing during a demo would require awkward explanations.

### 4.4 Cost Service Edge Cases

**File:** `tests/test_services/test_cost_service.py` (expand existing)
**Priority:** P0 -- cost numbers will be scrutinized.

**Additional test cases to add:**

```
test_tenant_software_cost_calculation
    Create a tenant-licensed software with total_cost and coverage.
    Assert cost is correctly allocated: total_cost / covered_headcount * authorized_count.

test_tenant_software_no_double_counting_headcount
    Create coverage rows that overlap (org-wide + specific dept).
    Assert covered_headcount counts each position only once.

test_department_cost_breakdown_matches_position_totals
    Calculate costs for all positions in a department.
    Assert department total equals sum of position totals.

test_zero_authorized_count_position
    Create position with authorized_count = 0.
    Assert grand_total is $0.00, no division-by-zero errors.

test_cost_with_inactive_equipment
    Assign an inactive hardware item to a position.
    Verify behavior (should it be excluded or included?).

test_department_average_cost_per_person
    Create multiple positions with known costs.
    Assert average, min, max calculations are correct.

test_organization_cost_summary_aggregation
    Create data across multiple departments.
    Assert org-level totals match sum of department totals.

test_cost_decimal_precision
    Use costs that produce repeating decimals (e.g., $100 / 3 positions).
    Assert results are rounded to 2 decimal places via ROUND_HALF_UP.
```

---

## 5. P1 -- Visible Bug Prevention (Days 3-4)

These tests catch bugs that would be noticeable during a walkthrough but are less likely to halt the demo entirely.

### 5.1 Service Layer Tests

#### 5.1.1 Requirement Service

**File:** `tests/test_services/test_requirement_service.py`

```
test_add_hardware_requirement_creates_record
test_add_hardware_requirement_updates_existing_duplicate
test_update_hardware_requirement_changes_quantity
test_remove_hardware_requirement_deletes_record
test_remove_hardware_requirement_nonexistent_raises
test_add_software_requirement_creates_record
test_add_software_requirement_updates_existing_duplicate
test_update_software_requirement_changes_quantity
test_remove_software_requirement_deletes_record
test_copy_position_requirements_copies_hardware_and_software
test_copy_position_requirements_does_not_duplicate_existing
test_copy_position_requirements_invalid_source_raises
test_bulk_save_hardware_replaces_all_requirements
test_bulk_save_hardware_validates_max_selections
test_get_hardware_usage_counts_returns_correct_counts
test_get_software_usage_counts_returns_correct_counts
test_get_division_common_hardware_respects_threshold
test_get_division_common_software_respects_threshold
test_get_division_common_returns_empty_for_no_data
test_update_requirements_status_valid_transitions
test_get_requirements_status_returns_current_value
test_requirement_changes_create_audit_entries
test_requirement_changes_create_history_records
```

#### 5.1.2 Organization Service

**File:** `tests/test_services/test_organization_service.py`

```
test_get_departments_org_scope_returns_all
test_get_departments_dept_scope_returns_only_scoped
test_get_departments_div_scope_returns_parent_departments
test_get_positions_filtered_by_department
test_get_positions_filtered_by_division
test_get_positions_scope_filtered_for_manager
test_user_can_access_department_with_org_scope
test_user_can_access_department_with_dept_scope
test_user_cannot_access_department_without_scope
test_user_can_access_position_with_division_scope
test_user_cannot_access_position_outside_scope
test_get_employees_filtered_by_scope
test_get_employees_respects_inactive_filter
```

#### 5.1.3 Equipment Service

**File:** `tests/test_services/test_equipment_service.py`

```
test_create_hardware_type_records_cost_history
test_update_hardware_type_cost_closes_old_history
test_create_hardware_item_with_cost
test_update_hardware_item_cost_records_history
test_deactivate_hardware_sets_is_active_false
test_create_software_product_per_user
test_create_software_product_tenant
test_update_software_cost_records_history
test_get_software_products_excludes_inactive
test_get_software_products_filters_by_type
test_set_software_coverage_replaces_existing
test_get_coverage_summary_returns_correct_data
test_get_universal_software_ids_returns_flagged_items
```

#### 5.1.4 User Service

**File:** `tests/test_services/test_user_service.py`

```
test_provision_user_creates_record_with_role
test_provision_user_duplicate_email_raises
test_change_user_role_updates_role
test_change_user_role_invalid_role_raises
test_set_user_scopes_replaces_existing
test_set_user_scopes_organization_wide
test_set_user_scopes_department_level
test_set_user_scopes_division_level
test_deactivate_user_sets_inactive
test_reactivate_user_sets_active
test_get_all_users_pagination
test_get_all_users_search_filter
test_get_all_users_role_filter
test_get_all_users_excludes_inactive_by_default
```

### 5.2 Admin Route Tests

**File:** `tests/test_routes/test_admin_routes.py`

```
test_manage_users_page_loads_for_admin
test_manage_users_page_blocked_for_non_admin
test_edit_user_page_loads
test_edit_user_shows_current_scope
test_provision_user_creates_account
test_provision_user_requires_all_fields
test_provision_user_rejects_duplicate_email
test_change_user_role_succeeds
test_update_user_scope_to_department
test_update_user_scope_to_division
test_update_user_scope_to_organization
test_deactivate_user_succeeds
test_reactivate_user_succeeds
test_audit_log_page_loads
test_hr_sync_trigger_requires_admin
```

### 5.3 Report and Export Route Tests

**File:** `tests/test_routes/test_reports_routes.py`

```
test_cost_summary_page_loads
test_cost_summary_shows_department_totals
test_cost_summary_respects_user_scope
test_equipment_report_page_loads
test_equipment_report_filters_by_department
test_equipment_report_filters_by_division
test_export_department_costs_csv_returns_file
test_export_department_costs_csv_content_type
test_export_department_costs_xlsx_returns_file
test_export_department_costs_xlsx_content_type
test_export_position_costs_csv_returns_file
test_export_position_costs_xlsx_returns_file
test_export_requires_authorized_role
test_export_blocked_for_read_only_user
```

### 5.4 Decorator Unit Tests

**File:** `tests/test_decorators/test_role_required.py`

```
test_role_required_allows_matching_role
test_role_required_allows_any_of_multiple_roles
test_role_required_blocks_non_matching_role
test_role_required_returns_403_not_500
test_permission_required_allows_user_with_permission
test_permission_required_blocks_user_without_permission
test_scope_check_allows_org_wide_user
test_scope_check_allows_user_with_matching_scope
test_scope_check_blocks_user_without_scope
test_scope_check_returns_403_not_500
```

---

## 6. P2 -- Production Readiness (Day 5)

### 6.1 Audit Service Tests

**File:** `tests/test_services/test_audit_service.py`

```
test_log_change_creates_entry
test_log_change_captures_ip_and_user_agent
test_log_change_works_outside_request_context
test_log_login_records_login_event
test_log_logout_records_logout_event
test_get_audit_logs_paginated
test_get_audit_logs_filtered_by_entity_type
test_get_audit_logs_filtered_by_user
test_audit_entry_stores_previous_and_new_values
```

### 6.2 Auth Route Tests

**File:** `tests/test_routes/test_auth_routes.py`

```
test_login_page_renders
test_login_redirects_authenticated_user_to_dashboard
test_callback_rejects_invalid_state
test_callback_handles_error_from_microsoft
test_callback_handles_missing_auth_code
test_logout_clears_session
test_dev_login_works_in_testing_mode
test_dev_login_picker_shows_all_dev_users
test_dev_login_as_specific_user_sets_session
```

### 6.3 Auth Service Tests (Mocked MSAL)

**File:** `tests/test_services/test_auth_service.py`

```
test_get_auth_url_returns_valid_url
test_acquire_token_success_returns_claims
test_acquire_token_error_raises_value_error
test_process_login_existing_user_returns_user
test_process_login_new_user_auto_provisions
test_process_login_missing_claims_raises
```

These tests must mock `msal.ConfidentialClientApplication` since we cannot call Microsoft Entra ID during tests. Use `unittest.mock.patch` to mock `_build_msal_app`.

### 6.4 HR Sync Service Tests (Mocked API)

**File:** `tests/test_services/test_hr_sync_service.py`

```
test_full_sync_creates_new_departments
test_full_sync_updates_changed_departments
test_full_sync_deactivates_removed_departments
test_full_sync_creates_positions_with_correct_fks
test_full_sync_provisions_users_for_new_employees
test_full_sync_deactivates_users_for_removed_employees
test_full_sync_records_sync_log
test_full_sync_handles_api_failure_gracefully
```

Mock `NeoGovApiClient.fetch_all_organization_data()` to return controlled test data.

### 6.5 Export Service Tests

**File:** `tests/test_services/test_export_service.py`

```
test_export_department_costs_csv_correct_columns
test_export_department_costs_csv_correct_values
test_export_department_costs_excel_creates_valid_file
test_export_position_costs_csv_correct_columns
test_export_position_costs_excel_creates_valid_file
test_export_empty_data_produces_headers_only
```

### 6.6 Error Handler Tests

**File:** `tests/test_routes/test_error_handlers.py`

```
test_404_returns_custom_error_page
test_403_returns_custom_error_page
test_500_returns_custom_error_page
```

---

## 7. P3 -- Post-Review Completeness

These tests should be written after the CIO review for long-term maintainability.

### 7.1 Model Tests

```
test_user_has_permission_checks_role_permissions
test_user_has_org_scope_returns_true_for_org_scope
test_user_scoped_department_ids_returns_correct_set
test_user_scoped_division_ids_returns_correct_set
test_user_full_name_property
test_user_role_name_property
test_position_division_relationship
test_division_department_relationship
test_software_coverage_scope_types
```

### 7.2 App Factory Tests

```
test_create_app_development_config
test_create_app_testing_config
test_create_app_production_config
test_create_app_invalid_config_raises
test_production_rejects_default_secret_key
test_production_validates_azure_credentials
test_production_warns_missing_neogov_key
test_production_warns_debug_log_level
```

### 7.3 CLI Command Tests

```
test_db_check_command_succeeds
test_seed_dev_admin_creates_user
test_seed_dev_manager_creates_user
test_seed_dev_scope_assigns_scopes
```

### 7.4 Equipment Route Tests

```
test_hardware_list_page_loads
test_hardware_create_form_loads
test_hardware_create_saves_record
test_hardware_edit_updates_record
test_software_list_page_loads
test_software_create_with_coverage
test_software_edit_updates_coverage
test_software_type_crud
test_hardware_type_crud
```

---

## 8. Implementation Specifications

### 8.1 Expanded conftest.py Blueprint

The following is the specification for the expanded `conftest.py`. This is the foundation that all other tests depend on. **Write this first.**

```python
"""
Pytest configuration and shared fixtures.

Provides a test application, database session, test client,
authenticated clients for each role, and reusable data fixtures
for organizational structure and equipment catalog.
"""

import pytest
from app import create_app
from app.extensions import db as _db
from app.models.user import Role, User, UserScope
from app.models.organization import Department, Division, Position
from app.models.equipment import (
    HardwareType, Hardware, SoftwareType, Software,
)


# -- App and DB fixtures (session scope) --
# Keep existing app, database fixtures unchanged.


# -- Authenticated client helper --
# This is the key fixture that enables all route testing.

@pytest.fixture(scope="function")
def auth_client(app, client):
    """
    Factory fixture that returns a function to log in as any user.

    Usage in tests:
        def test_something(auth_client, admin_user):
            c = auth_client(admin_user)
            response = c.get("/admin/users")
            assert response.status_code == 200
    """
    def _login_as(user):
        # Use the dev-login route that already exists in TestingConfig.
        with client.session_transaction() as sess:
            # Directly set Flask-Login's user_id in the session.
            # This avoids hitting the dev-login route and is more
            # reliable for testing.
            sess["_user_id"] = str(user.id)
        return client
    return _login_as


# -- Role-specific user fixtures --

@pytest.fixture(scope="function")
def admin_user(db_session):
    """Create an admin user with organization-wide scope."""
    role = Role.query.filter_by(role_name="admin").first()
    user = User(
        email="test_admin@localhost",
        first_name="Test",
        last_name="Admin",
        role_id=role.id,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    scope = UserScope(
        user_id=user.id,
        scope_type="organization",
    )
    db_session.add(scope)
    db_session.flush()
    return user


# Pattern repeats for: manager_user (division scope),
# it_staff_user (org scope), read_only_user (division scope),
# budget_user (org scope).
# Manager specifically gets scope to dept_a / div_a1 only.


# -- Organizational structure fixture --

@pytest.fixture(scope="function")
def sample_org(db_session):
    """
    Create a two-department org structure for scope testing.

    Returns a dict with keys: dept_a, dept_b, div_a1, div_a2,
    div_b1, div_b2, and positions pos_a1_1, pos_a1_2, etc.
    """
    # Create departments
    dept_a = Department(
        department_code="DEPT_A",
        department_name="Department A",
    )
    dept_b = Department(
        department_code="DEPT_B",
        department_name="Department B",
    )
    db_session.add_all([dept_a, dept_b])
    db_session.flush()

    # Create divisions (2 per department)
    # Create positions (2 per division, with known authorized_counts)
    # ... (full implementation in actual code)

    return {
        "dept_a": dept_a, "dept_b": dept_b,
        "div_a1": div_a1, "div_a2": div_a2,
        "div_b1": div_b1, "div_b2": div_b2,
        # ... positions ...
    }


# -- Equipment catalog fixture --

@pytest.fixture(scope="function")
def sample_catalog(db_session):
    """
    Create a minimal equipment catalog with known costs.

    Returns a dict with hardware types, items, software types,
    and items for deterministic cost testing.
    """
    # ... creates hw_type_laptop, hw_laptop_standard ($1200),
    # sw_type_productivity, sw_office ($200/user), etc.
    pass
```

### 8.2 Mocking Strategy

For components that depend on external services, use `unittest.mock.patch`:

| Component | What to Mock | How |
|-----------|-------------|-----|
| `auth_service` | `msal.ConfidentialClientApplication` | `@patch("app.services.auth_service._build_msal_app")` |
| `hr_sync_service` | `NeoGovApiClient.fetch_all_organization_data` | `@patch("app.services.hr_sync_service.NeoGovApiClient")` |
| `export_service` | Nothing -- test actual CSV/Excel output | Use `io.BytesIO` to read and verify content |
| Flask-Login | Session manipulation | Use `session_transaction()` to set `_user_id` |

Do **not** mock the database. The transactional rollback pattern gives real DB integration coverage without cleanup overhead. Mocking SQLAlchemy would miss SQL Server-specific bugs (schema references, date functions, join behavior).

### 8.3 Test Naming Convention

All test names follow this pattern to maximize readability and traceability:

```
test_<unit_under_test>_<scenario>_<expected_outcome>
```

Examples:

- `test_manager_accessing_other_department_returns_403`
- `test_copy_requirements_with_valid_scope_creates_records`
- `test_tenant_software_cost_divides_by_covered_headcount`

### 8.4 Assertion Patterns

**Route tests:** Assert status codes, redirects, flash messages, and rendered content.

```python
# Pattern for testing protected routes
def test_admin_route_blocked_for_manager(auth_client, manager_user):
    """Managers should receive 403 when accessing admin routes."""
    c = auth_client(manager_user)
    response = c.get("/admin/users")
    assert response.status_code == 403
```

```python
# Pattern for testing form submissions
def test_hardware_submit_saves_requirements(
    auth_client, manager_user, sample_org, sample_catalog
):
    """Submitting hardware selections should create PositionHardware records."""
    c = auth_client(manager_user)
    pos = sample_org["pos_a1_1"]
    hw = sample_catalog["hw_laptop_standard"]

    response = c.post(
        f"/requirements/position/{pos.id}/hardware",
        data={
            f"hw_{hw.id}_selected": "on",
            f"hw_{hw.id}_quantity": "2",
        },
        follow_redirects=False,
    )
    # Should redirect to software step.
    assert response.status_code == 302
    assert "software" in response.location

    # Verify database record created.
    from app.models.requirement import PositionHardware
    req = PositionHardware.query.filter_by(
        position_id=pos.id, hardware_id=hw.id
    ).first()
    assert req is not None
    assert req.quantity == 2
```

**Service tests:** Assert return values, database state, and exceptions.

```python
# Pattern for service tests with known data
def test_tenant_software_cost_calculation(self):
    """Tenant software cost should be total_cost / headcount * authorized."""
    # Create tenant software with $1000 total cost covering 10 people.
    # Position has authorized_count = 3.
    # Expected: ($1000 / 10) * 3 = $300.00
    result = cost_service.calculate_position_cost(self.position.id)
    assert result.software_total == Decimal("300.00")
```

---

## 9. Running the Tests

### 9.1 Commands

```powershell
# Run all tests with verbose output
pytest

# Run only P0 tests (authorization + wizard + costs)
pytest tests/test_authorization/ tests/test_routes/test_requirements_routes.py tests/test_services/test_cost_service.py -v

# Run with coverage report
pytest --cov=app --cov-report=term-missing

# Run with coverage limited to services (most critical layer)
pytest --cov=app/services --cov-report=html

# Run a specific test file
pytest tests/test_authorization/test_scope_isolation.py -v

# Run tests matching a keyword
pytest -k "scope" -v

# Run tests and stop on first failure (useful during dev)
pytest -x
```

### 9.2 Coverage Targets

| Layer | Target | Rationale |
|-------|--------|-----------|
| `app/services/cost_service.py` | 90%+ | Money calculations must be correct |
| `app/services/requirement_service.py` | 80%+ | Core wizard data operations |
| `app/services/organization_service.py` | 80%+ | Scope filtering is security-critical |
| `app/services/user_service.py` | 75%+ | Admin operations must work |
| `app/decorators.py` | 90%+ | Authorization enforcement |
| `app/blueprints/requirements/routes.py` | 75%+ | Core workflow |
| `app/blueprints/admin/routes.py` | 70%+ | Admin panel reliability |
| `app/blueprints/reports/routes.py` | 70%+ | CIO demo feature |
| Overall `app/` | 60%+ | Reasonable for a 5-day sprint |

### 9.3 Pre-CIO Review Checklist

Before the CIO review, run these commands and verify all pass:

```powershell
# 1. Full test suite passes
pytest -v

# 2. No authorization leaks
pytest tests/test_authorization/ -v

# 3. Cost calculations correct
pytest tests/test_services/test_cost_service.py -v

# 4. Wizard flow works end-to-end
pytest tests/test_routes/test_requirements_routes.py -v

# 5. Reports load correctly
pytest tests/test_routes/test_reports_routes.py -v

# 6. Coverage report (save for CIO slide deck)
pytest --cov=app --cov-report=html
# Open htmlcov/index.html in browser
```

---

## 10. Day-by-Day Execution Plan

### Day 1 (Tuesday): Foundation + Authorization

| Time Block | Task | Output |
|-----------|------|--------|
| Morning | Expand `conftest.py` with all shared fixtures | Working fixtures for roles, org, catalog |
| Morning | Write `test_scope_isolation.py` (15 tests) | All authorization boundaries verified |
| Afternoon | Write `test_role_required.py` and `test_permission_required.py` | Decorator enforcement verified |
| Afternoon | Run suite, fix any fixture issues | Green test suite |

**End of Day 1 deliverable:** ~25 new tests. All authorization paths verified. This alone prevents the worst possible demo failure.

### Day 2 (Wednesday): Core Workflow + Costs

| Time Block | Task | Output |
|-----------|------|--------|
| Morning | Write `test_requirements_routes.py` Steps 1-4 (20 tests) | Wizard flow verified end-to-end |
| Afternoon | Expand `test_cost_service.py` (8 new tests) | Tenant costs, edge cases, aggregations verified |
| Afternoon | Write `test_organization_service.py` (13 tests) | Scope filtering logic verified at service layer |

**End of Day 2 deliverable:** ~65 total tests. Core workflow and cost calculations verified. The system's primary value proposition is now tested.

### Day 3 (Thursday): Services + Admin

| Time Block | Task | Output |
|-----------|------|--------|
| Morning | Write `test_requirement_service.py` (22 tests) | All CRUD, copy, bulk operations verified |
| Afternoon | Write `test_user_service.py` (14 tests) | User management verified |
| Afternoon | Write `test_admin_routes.py` (15 tests) | Admin panel verified |

**End of Day 3 deliverable:** ~115 total tests. All P0 and most P1 tests complete.

### Day 4 (Friday): Reports + Auth + Polish

| Time Block | Task | Output |
|-----------|------|--------|
| Morning | Write `test_reports_routes.py` (14 tests) | Reports and exports verified |
| Morning | Write `test_equipment_service.py` (13 tests) | Catalog management verified |
| Afternoon | Write `test_auth_routes.py` (9 tests) | Login flow verified |
| Afternoon | Run full suite, fix failures, generate coverage report | Clean green suite |

**End of Day 4 deliverable:** ~150 total tests. All P0 + P1 + partial P2 complete.

### Day 5 (Monday morning before review): Smoke Test + Report

| Time Block | Task | Output |
|-----------|------|--------|
| Morning | Manual smoke test of key demo flows | Confidence check |
| Morning | Generate coverage report, review any gaps | Coverage HTML ready for CIO if asked |
| Morning | Write `test_error_handlers.py` (3 tests) | Error pages verified |

**Final deliverable:** ~155+ tests, 60%+ coverage, authorization verified, costs verified, workflow verified, reports verified.

---

## 11. Key Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Test DB not set up identically to dev DB | Medium | Run DDL script against PositionMatrix_Test before starting. Verify with existing `test_db_connection.py`. |
| Dev-login bypass does not work in test config | Low | The `TestingConfig` already sets `DEV_LOGIN_ENABLED = True`. If issues arise, use `session_transaction()` to set `_user_id` directly. |
| Form field names in POST data do not match templates | Medium | Inspect actual templates before writing route tests. Use browser dev tools to check `name` attributes. |
| HTMX endpoints return fragments, not full pages | Expected | Assert on fragment content (specific HTML elements), not full page structure. |
| Cost calculation tests depend on specific seed data | Low | All cost tests create their own ephemeral data via fixtures. They do not depend on DDL seed data. |
| Time runs out before all tests written | Medium | The priority system ensures the most impactful tests are written first. P0 alone prevents the worst failures. |

---

## 12. What to Tell the CIO

When asked about testing during the review, you can honestly say:

1. "We have **150+ automated tests** covering authorization, cost calculations, the core workflow, admin operations, and reporting."
2. "Every role/scope boundary is tested to verify users only see data within their authorized scope."
3. "Cost calculations are tested with known inputs to verify accuracy down to the penny."
4. "The complete position configuration workflow (select position, select hardware, select software, review summary) is tested end-to-end."
5. "We run tests against a real SQL Server instance, not an in-memory substitute, so our tests catch database-specific issues."
6. "Test coverage across the application is 60%+ and growing. The service layer (business logic) is at 80%+ coverage."

This is an honest and defensible position. The tests are real, the coverage is meaningful, and the highest-risk areas are thoroughly verified.
