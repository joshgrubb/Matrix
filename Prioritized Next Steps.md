# PositionMatrix -- Prioritized Testing Next Steps

**Date:** March 26, 2026
**Status:** Sections 1-6 of testing_strategy.md complete. CIO review postponed.
**Current Coverage:** 58% overall (4,135 statements, 1,757 missing, 8 excluded)

---

## Completed Work (Ranks 1-7)

The following items from the original prioritized next steps are done. They
are listed here for context so the remaining work can be understood relative
to what already exists.

1. CHECK constraint applied to test and production databases (2026-03-17)
2. tests/test_routes/test_reports_routes.py written (2026-03-17)
3. tests/test_services/test_organization_service.py written (2026-03-17)
4. tests/test_services/test_equipment_service.py written (2026-03-17)
5. tests/test_services/test_audit_service.py written (2026-03-23)
6. tests/test_decorators/test_role_required.py written (2026-03-23)
7. tests/test_routes/test_auth_routes.py written (2026-03-23)
8. tests/test_services/test_export_service.py written (2026-03-23)
9. tests/test_routes/test_error_handlers.py written. The strategy baseline
   called for 3 tests. The actual file contains 12 test classes covering
   404/403/500 for authenticated and unauthenticated users, multiple role
   denials, POST method denial, various exception types, custom template
   content, CSS class structure, button styling, icon presence, page title
   verification, response body size, traceback/config leak prevention, and
   db.session.rollback() verification on 500. Uses a
   disable_exception_propagation fixture to work around Flask's
   PROPAGATE_EXCEPTIONS=True in testing mode. Additional error handler
   coverage exists in test_main_routes.py (TestErrorHandlers class) and
   test_role_required.py (TestRoleRequiredResponseBehavior class).

---

## Current Coverage Snapshot -- Biggest Gaps

These numbers come from the files and functions coverage reports generated
after the Rank 7 work was completed. They drive the priority ordering below.

| File | Coverage | Missing | Notes |
|------|----------|---------|-------|
| equipment routes | 26% | 320 stmts | Largest single gap in the app |
| equipment_service | 26% | 218 stmts | CRUD, cost history, coverage mgmt |
| organization routes | 44% | 40 stmts | Page loads, detail views, HTMX |
| auth_service | 23% | 40 stmts | MSAL integration (requires mocking) |
| hr_sync_service | 8% | 286 stmts | NeoGov sync (requires mocking) |
| neogov_client | 9% | 241 stmts | API client (requires mocking) |
| cli.py | 17% | 65 stmts | db-check and hr-sync commands |
| seed_dev_*.py | 24% each | ~50 stmts each | Dev seed commands |
| logging_config | 76% | 47 stmts | Filters, redaction |
| config.py | 73% | 17 stmts | Production validation |

High-coverage files that still have meaningful branch gaps:

| File | Coverage | Missing Functions |
|------|----------|-------------------|
| requirements routes | 83% | _parse_hardware_form (45%),_parse_software_form (76%) |
| admin routes | 86% | htmx_user_divisions (0%), run_hr_sync (0%) |
| decorators | 94% | One branch each in role_required, permission_required |
| user_service | 98% | _add_org_scope (0%) |

---

## Prioritized Next Steps

Each item below is a discrete task. Priority is ordered by a combination of
coverage gap size, CIO demo risk, and production readiness impact. Level of
effort (LOE) is in hours. Level of reward (LOR) rates risk reduction on a
1 to 5 scale where 5 means "prevents a visible CIO demo failure."

---

### Rank 10: Write tests/test_routes/test_equipment_routes.py

**LOE:** 4 to 5 hours | **LOR:** 3 | **Priority tier:** P1 (elevated from P3)
**Strategy reference:** Section 7.4 (9 tests planned)
**Current coverage:** equipment routes at 26% (320 of 430 statements missing)

This is the single largest coverage gap in the application by statement
count. The strategy originally classified equipment route tests as P3
(post-review), but the coverage report reveals that 74% of this file is
untested. Equipment catalog management is likely to be shown during the CIO
review because it demonstrates the admin workflow for maintaining hardware
and software items. A broken create or edit form would be immediately
visible.

The functions coverage report shows the following are at 0%:

- `hardware_type_edit`, `hardware_type_deactivate`
- `hardware_create` (11% -- only the initial GET), `hardware_edit`,
  `hardware_deactivate`
- `software_create`, `software_edit`, `software_deactivate`
- `software_type_edit`, `software_type_deactivate`
- `software_family_list`, `software_family_create`, `software_family_edit`,
  `software_family_deactivate`
- `_build_coverage_json`, `_parse_coverage_form`, `_parse_decimal`

**What each test must verify:**

Hardware type CRUD:

- `test_hardware_type_list_page_loads`: GET /equipment/hardware-types as
  admin. Assert 200. Assert page contains at least one hardware type name
  from sample_catalog.
- `test_hardware_type_create_form_loads`: GET /equipment/hardware-types/new
  as admin. Assert 200. Assert page contains a form with a name field.
- `test_hardware_type_create_saves_record`: POST to
  /equipment/hardware-types/new with valid form data (name, default cost).
  Assert redirect. Query the database and assert the new HardwareType
  exists with the correct values.
- `test_hardware_type_edit_updates_record`: Create a hardware type via
  fixture. POST to /equipment/hardware-types/<id>/edit with an updated
  name. Assert redirect. Query the database and assert the name changed.
- `test_hardware_type_edit_cost_change_creates_history`: Change the
  default_cost on a hardware type. Assert a HardwareTypeCostHistory record
  was created with the old effective_date closed and a new record opened.
- `test_hardware_type_deactivate_sets_inactive`: POST to
  /equipment/hardware-types/<id>/deactivate. Assert the record's
  is_active is False.

Hardware item CRUD:

- `test_hardware_list_page_loads`: GET /equipment/hardware as admin.
  Assert 200.
- `test_hardware_create_saves_record`: POST to /equipment/hardware/new
  with valid form data (name, hardware_type_id, unit_cost). Assert
  redirect. Assert the new Hardware record exists.
- `test_hardware_create_records_cost_history`: After creating a hardware
  item, assert a HardwareCostHistory record was created with
  effective_date set and end_date NULL.
- `test_hardware_edit_updates_record`: Edit an existing hardware item's
  name and unit_cost. Assert the database reflects both changes.
- `test_hardware_edit_cost_change_closes_old_history`: Change unit_cost.
  Assert the old HardwareCostHistory record has end_date set and a new
  record was opened.
- `test_hardware_deactivate_sets_inactive`: Deactivate a hardware item.
  Assert is_active is False.

Software CRUD (mirrors hardware pattern):

- `test_software_list_page_loads`: GET /equipment/software. Assert 200.
- `test_software_create_with_coverage`: POST to /equipment/software/new
  with form data that includes coverage scope selections. Assert the
  Software record and associated SoftwareCoverage records are created.
- `test_software_edit_updates_coverage`: Edit a software item's coverage.
  Assert old coverage records are replaced with new ones.
- `test_software_create_records_cost_history`: Assert SoftwareCostHistory
  is created on software creation.
- `test_software_edit_cost_change_closes_old_history`: Assert cost history
  rotation on price change.
- `test_software_deactivate_sets_inactive`: Deactivate software. Assert
  is_active is False.

Software type and family CRUD:

- `test_software_type_list_loads`: GET /equipment/software-types. Assert 200.
- `test_software_type_create_saves_record`: POST with valid name. Assert
  record created.
- `test_software_type_edit_updates_name`: POST with updated name. Assert
  change persisted.
- `test_software_type_deactivate_sets_inactive`: Assert deactivation works.
- `test_software_family_list_loads`: GET /equipment/software-families.
  Assert 200.
- `test_software_family_create_saves_record`: POST with valid name and
  type_id. Assert record created.
- `test_software_family_edit_updates_name`: POST with updated name.
- `test_software_family_deactivate_sets_inactive`: Assert deactivation.

Role enforcement:

- `test_manager_blocked_from_equipment_create`: Authenticate as manager.
  POST to /equipment/hardware/new. Assert 403.
- `test_read_only_blocked_from_equipment_list`: Authenticate as
  read_only. GET /equipment/hardware. Assert 403 (or appropriate
  restriction).

**Enhancement beyond the strategy baseline:**

- Add `test_hardware_create_rejects_missing_name`: POST without a name
  field. Assert the form re-renders with a validation error (not a 500).
- Add `test_hardware_create_rejects_negative_cost`: POST with a
  negative unit_cost. Assert validation error.
- Add `test_software_coverage_form_round_trip`: Create software with
  department-level coverage. Edit it. Reload the edit form and assert the
  previously selected coverage scopes are pre-checked. This is the
  classic "coverage gets lost on edit" bug.
- Add `test_parse_decimal_handles_comma_formatted_input`: If the form
  accepts user-typed costs like "1,200.00", verify `_parse_decimal`
  handles it correctly (or rejects it cleanly).

---

### Rank 11: Write tests/test_routes/test_organization_routes.py

**LOE:** 2 to 3 hours | **LOR:** 2 | **Priority tier:** P2
**Strategy reference:** Section 2.2 (listed in test organization, no
detailed test list in sections 3-7)
**Current coverage:** organization routes at 44% (40 of 72 statements missing)

The organization routes serve the department listing, department detail,
division detail, positions listing, employees listing, and HTMX endpoints
for dynamic dropdowns. The HTMX endpoints (htmx_divisions, htmx_positions)
are at 100% because the requirements wizard tests call them. But the six
page-rendering functions are all at 0%.

**What each test must verify:**

- `test_departments_page_loads`: GET /organization/departments as admin.
  Assert 200. Assert response contains department names from sample_org.
- `test_department_detail_shows_divisions`: GET
  /organization/departments/<id> for a department with divisions. Assert
  200. Assert the division names appear in the response.
- `test_department_detail_scope_check`: Authenticate as a manager scoped
  to div_a1. GET /organization/departments/<dept_b_id>. Assert 403 (if
  scope_check is applied) or assert that the page loads but shows only
  scoped data.
- `test_division_detail_shows_positions`: GET
  /organization/divisions/<id>. Assert 200. Assert position titles appear.
- `test_all_divisions_page_loads`: GET /organization/divisions. Assert 200.
- `test_all_positions_page_loads`: GET /organization/positions. Assert 200.
  Assert at least one position title from sample_org.
- `test_all_employees_page_loads`: GET /organization/employees. Assert 200.
- `test_all_employees_excludes_inactive_by_default`: If the route filters
  inactive employees by default, assert that an inactive employee fixture
  does not appear in the response.

**Enhancement beyond the strategy baseline:**

- Add `test_department_detail_shows_position_count`: Assert the department
  detail page displays the total authorized count and filled count for its
  divisions.
- Add `test_organization_pages_blocked_for_unauthenticated`: GET each
  organization route without authentication. Assert redirect to login.

---

### Rank 12: Write tests/test_services/test_auth_service.py

**LOE:** 2 hours | **LOR:** 2 | **Priority tier:** P2
**Strategy reference:** Section 6.3 (6 tests planned)
**Current coverage:** auth_service.py at 23% (40 of 52 statements missing)

The auth routes tests (Rank 7) mock auth_service at the route boundary,
so the route layer is covered. But the service functions themselves --
`initiate_auth_flow`, `complete_auth_flow`, and `process_login` -- have
0% coverage. These contain the MSAL integration logic that handles token
exchange, user provisioning on first login, and claim extraction. A bug
in `process_login` (e.g., wrong claim key for email) would only surface
during a real Microsoft Entra login, not during dev-login testing.

**What each test must verify:**

All tests must mock `_build_msal_app` to return a mock
`ConfidentialClientApplication`. Do not call Microsoft Entra ID.

- `test_initiate_auth_flow_returns_auth_uri_and_stores_state`: Call
  `initiate_auth_flow()` with the mock MSAL app configured to return
  a known auth_uri. Assert the returned dict contains `auth_uri`. Assert
  the flow state is stored in `session["auth_flow"]`.
- `test_initiate_auth_flow_passes_correct_scopes`: Assert that
  `initiate_authorization_code_flow` is called with the expected scopes
  (e.g., `["User.Read"]` or whatever the app configures).
- `test_complete_auth_flow_returns_token_result`: Configure the mock
  to return a successful token result from
  `acquire_token_by_authorization_code_flow`. Call `complete_auth_flow`
  with valid request args. Assert the returned dict contains
  `id_token_claims`.
- `test_complete_auth_flow_raises_on_missing_session_state`: Call
  `complete_auth_flow` without setting `session["auth_flow"]`. Assert
  `ValueError` is raised with a message about missing session state.
- `test_complete_auth_flow_raises_on_error_response`: Configure the mock
  to return `{"error": "invalid_grant"}`. Assert `ValueError` is raised.
- `test_process_login_existing_user_returns_user`: Create a user in the
  database with a known entra_object_id. Call `process_login` with a
  token result whose `oid` claim matches. Assert the returned user is
  the existing user. Assert `record_login` was called.
- `test_process_login_new_user_auto_provisions`: Call `process_login`
  with a token result whose `oid` does not match any existing user.
  Assert a new User record was created in the database with the correct
  email, first_name, and last_name extracted from claims.
- `test_process_login_missing_oid_claim_raises`: Call `process_login`
  with a token result that has no `oid` key. Assert `ValueError` is
  raised.

**Enhancement beyond the strategy baseline:**

- Add `test_process_login_updates_last_login_timestamp`: After a
  successful login, assert the user's last_login_at field was updated.
- Add `test_process_login_handles_name_claim_variants`: Microsoft Entra
  tokens may use `name` or `given_name`/`family_name`. Test both formats
  to ensure the parsing logic handles whichever claim set is present.
- Add `test_clear_session_removes_auth_flow_key`: Call `clear_session`.
  Assert `session["auth_flow"]` is gone and `_user_id` is gone.

---

### Rank 13: Expand tests on uncovered branches in high-coverage files

**LOE:** 2 to 3 hours total | **LOR:** 3 | **Priority tier:** P1
**Current impact:** These are small branch gaps in otherwise well-tested
files, but they represent real untested code paths.

**13a: admin routes -- htmx_user_divisions (0%) and run_hr_sync (0%)**

The admin routes file is at 86%, but two functions are completely untested.

- `test_htmx_user_divisions_returns_options`: Authenticate as admin. GET
  /admin/htmx/divisions?department_id=<valid_id>. Assert 200. Assert the
  response contains division names as HTMX option fragments.
- `test_htmx_user_divisions_empty_department_returns_empty`: Pass a
  department_id with no divisions. Assert 200 with empty or "no divisions"
  content.
- `test_run_hr_sync_requires_admin`: Authenticate as manager. POST
  /admin/hr-sync/run. Assert 403.
- `test_run_hr_sync_as_admin_triggers_sync`: Authenticate as admin. POST
  /admin/hr-sync/run. This will need to mock the actual sync function
  to avoid calling the NeoGov API. Assert redirect or success response.

**13b: requirements routes -- _parse_hardware_form (45%)**

The hardware form parsing function has significant untested branches.
Review the function for edge cases:

- `test_hardware_form_with_zero_quantity_skips_item`: Submit a hardware
  form where an item is checked but quantity is 0. Verify the item is
  not saved (or is saved with quantity 0 depending on business rules).
- `test_hardware_form_with_non_numeric_quantity_handled`: Submit a form
  with quantity set to "abc". Assert the form does not crash with a 500.
- `test_hardware_form_with_no_selections_clears_requirements`: Submit
  the hardware form with no items checked. Assert all existing
  PositionHardware records for that position are removed.

**13c: user_service -- _add_org_scope (0%)**

This is a small helper function but it is at 0%.

- `test_provision_user_with_admin_role_gets_org_scope`: Provision a user
  with the admin role. Assert a UserScope record with scope_type
  "organization" is created automatically via `_add_org_scope`.

**13d: decorators -- remaining branches**

Each decorator has one untested branch (86-94% coverage). Check the
functions coverage for which branch is missed:

- `test_role_required_wrapper_handles_unauthenticated_user`: This likely
  tests the `current_user.is_authenticated` check. If the test for
  unauthenticated users in test_role_required.py covers this, mark it
  as covered. If not, add a test that hits a role_required route without
  logging in and asserts the redirect.

---

### Rank 14: Write tests/test_services/test_hr_sync_service.py

**LOE:** 3 to 4 hours | **LOR:** 1 | **Priority tier:** P2
**Strategy reference:** Section 6.4 (8 tests planned)
**Current coverage:** hr_sync_service at 8%, neogov_client at 9%

This is a large coverage gap by statement count (527 missing lines between
the two files), but the HR sync is unlikely to be demonstrated during the
CIO review and it requires extensive mocking. It is ranked here because
of its size, but it should be deprioritized if time is limited.

All tests must mock `NeoGovApiClient.fetch_all_organization_data()` to
return controlled test data. Do not make real API calls.

**What each test must verify:**

- `test_full_sync_creates_new_departments`: Provide mock data with a new
  department. Assert a Department record is created.
- `test_full_sync_updates_changed_departments`: Provide mock data where
  a department name has changed. Assert the existing record is updated.
- `test_full_sync_deactivates_removed_departments`: Provide mock data
  that omits an existing department. Assert its is_active is set to False.
- `test_full_sync_creates_positions_with_correct_fks`: Provide mock data
  with positions. Assert Position records are created with correct
  division_id foreign keys.
- `test_full_sync_provisions_users_for_new_employees`: Provide mock data
  with a new employee. Assert a User record is provisioned with the
  correct email and default role.
- `test_full_sync_deactivates_users_for_removed_employees`: Provide mock
  data that omits an existing employee. Assert the associated User is
  deactivated.
- `test_full_sync_records_sync_log`: After a successful sync, assert an
  HRSyncLog record was created with status "completed" and correct
  statistics.
- `test_full_sync_handles_api_failure_gracefully`: Make the mock raise
  an exception. Assert the sync log is created with status "failed" and
  that the exception does not propagate to the caller.

**Enhancement beyond the strategy baseline:**

- Add `test_full_sync_recalculates_filled_counts`: After syncing employees,
  assert that Position.filled_count is updated to reflect the number of
  active employees in each position.
- Add `test_full_sync_is_idempotent`: Run the sync twice with the same
  data. Assert no duplicate records are created.

---

### Rank 15: Write tests/test_models/ (model tests)

**LOE:** 2 hours | **LOR:** 1 | **Priority tier:** P3
**Strategy reference:** Section 7.1 (9 tests planned)
**Current coverage:** Model files are at 92-94% from being exercised by
service and route tests. The missing lines are `__repr__` methods (0% each).

These tests verify model properties and methods in isolation. Most model
behavior is already tested through the service layer, so the incremental
value is small but the tests are fast to write.

**Files to create:**

tests/test_models/test_user_model.py:

- `test_user_full_name_property`: Assert `user.full_name` returns
  "First Last".
- `test_user_role_name_property`: Assert `user.role_name` returns the
  role's name string.
- `test_user_has_role_returns_true_for_matching_role`: Assert
  `user.has_role("admin")` returns True for an admin user.
- `test_user_has_role_returns_false_for_wrong_role`: Assert
  `user.has_role("admin")` returns False for a manager user.
- `test_user_has_permission_checks_role_permissions`: Assert
  `user.has_permission("manage_users")` returns True for admin and
  False for manager.
- `test_user_has_org_scope_returns_true_for_org_scope`: Assert
  `user.has_org_scope` returns True for an admin with organization
  scope.
- `test_user_scoped_department_ids_returns_correct_set`: Create a user
  with department-level scopes. Assert `scoped_department_ids` returns
  only those department IDs.
- `test_user_scoped_division_ids_returns_correct_set`: Create a user
  with division-level scopes. Assert `scoped_division_ids` returns only
  those division IDs.

tests/test_models/test_organization_model.py:

- `test_position_division_relationship`: Assert `position.division`
  returns the correct Division object.
- `test_division_department_relationship`: Assert `division.department`
  returns the correct Department object.

tests/test_models/test_equipment_model.py:

- `test_software_coverage_scope_types`: Create SoftwareCoverage records
  with different scope_type values. Assert the relationships resolve
  correctly.

---

### Rank 16: Write tests/test_config/test_app_factory.py

**LOE:** 1.5 hours | **LOR:** 1 | **Priority tier:** P3
**Strategy reference:** Section 7.2 (8 tests planned)
**Current coverage:** **init**.py at 94%, config.py at 73%

The config.py gap is entirely in `validate_production_secrets` (0%), which
is the function that rejects weak secret keys and missing Azure credentials
in production mode. This function is important for production security but
will not affect the CIO demo.

- `test_create_app_testing_config`: Assert `create_app("testing")` returns
  an app with TESTING=True.
- `test_create_app_development_config`: Assert `create_app("development")`
  returns an app with DEBUG=True.
- `test_create_app_production_config`: Assert `create_app("production")`
  with valid environment variables returns an app.
- `test_production_rejects_default_secret_key`: Set SECRET_KEY to the
  default value. Assert `validate_production_secrets` raises or logs a
  warning.
- `test_production_validates_azure_credentials`: Omit AZURE_CLIENT_ID.
  Assert validation fails.
- `test_load_user_callback`: Assert that the `load_user` callback
  registered with Flask-Login returns the correct User when given a
  valid user ID, and returns None for an invalid ID. This covers the
  `_register_extensions.load_user` function at 0%.

---

### Rank 17: Write tests/test_cli.py (CLI command tests)

**LOE:** 1.5 hours | **LOR:** 1 | **Priority tier:** P3
**Strategy reference:** Section 7.3 (4 tests planned)
**Current coverage:** cli.py at 17% (65 of 78 missing -- db_check_command
and hr_sync_command are both at 0%)

Use Flask's `CliRunner` to invoke CLI commands:

- `test_db_check_command_succeeds`: Invoke `db-check` via the test runner.
  Assert exit code 0. Assert output contains success indicators.
- `test_db_check_command_reports_schema_issues`: This may require mocking
  the database to simulate a missing schema. If not feasible, skip.
- `test_hr_sync_command_triggers_sync`: Mock the hr_sync_service. Invoke
  `hr-sync` via the test runner. Assert the mock was called.

The seed commands (seed_dev_*.py at 24% each) are lower priority. They
create dev users and are only run manually. Testing them is a nice-to-have
for completeness but adds minimal production value.

---

### Rank 18: Run coverage report and prepare CIO artifacts

**LOE:** 30 minutes | **LOR:** 3 | **Priority tier:** Do this after each
batch of tests, and once more immediately before the review.

```powershell
pytest --cov=app --cov-report=html --cov-report=term-missing -v
```

Open `htmlcov/index.html` in a browser. Save it for the CIO slide deck.
Prepare a summary table showing:

- Overall coverage percentage
- Service layer coverage percentage
- Route layer coverage percentage
- Number of tests
- Number of test files
- Key areas covered (authorization, costs, workflow, reports, exports, admin)

Target numbers to cite: "65%+ overall, 90%+ on services, 85%+ on critical
routes, 150+ tests across 15+ test files."

---

## Execution Order Summary

If working sequentially, this is the recommended order:

| Order | Task | LOE | Cumulative Tests Added |
|-------|------|-----|------------------------|
| 1 | Rank 10: test_equipment_routes.py | 4-5 hrs | ~25 new tests |
| 2 | Rank 11: test_organization_routes.py | 2-3 hrs | ~10 new tests |
| 3 | Rank 12: test_auth_service.py | 2 hrs | ~10 new tests |
| 4 | Rank 13: Branch gap cleanup | 2-3 hrs | ~10 new tests |
| 5 | Rank 14: test_hr_sync_service.py | 3-4 hrs | ~10 new tests |
| 6 | Rank 15: Model tests | 2 hrs | ~11 new tests |
| 7 | Rank 16: App factory tests | 1.5 hrs | ~6 new tests |
| 8 | Rank 17: CLI tests | 1.5 hrs | ~3 new tests |
| 9 | Rank 18: Coverage report | 0.5 hrs | -- |

Total estimated LOE: 19 to 24 hours
Total new tests: ~85

After completing through order 4 (Ranks 10 through 13), the projected
coverage should be approximately 70-75% overall with the equipment routes
gap closed and all critical-path code verified. That is a strong position
for CIO review.

---

## What to Tell the CIO (Updated)

After completing the above work through at least order 4, you can say:

1. "We have 200+ automated tests covering authorization, cost calculations,
   the core workflow, admin operations, reporting, equipment catalog
   management, and data exports."
2. "Every role and scope boundary is tested to verify users only see data
   within their authorized scope."
3. "Cost calculations are tested at both the service layer and the export
   layer, verified to the penny with decimal precision checks."
4. "The complete position configuration workflow is tested end-to-end."
5. "All equipment catalog CRUD operations (create, edit, deactivate) are
   tested for both hardware and software, including cost history tracking."
6. "We run tests against a real SQL Server instance, not an in-memory
   substitute, so our tests catch database-specific issues."
7. "Test coverage across the application is 70%+ and growing. The service
   layer is at 90%+ and critical routes are at 85%+."
