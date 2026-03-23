Prioritized Next Steps
Each item below is atomic (one file or one task). Priority ranks by risk-to-the-CIO-demo first, then by production readiness. Level of effort (LOE) is estimated in hours. Level of reward (LOR) rates how much each item reduces risk on a 1 to 5 scale, where 5 means "prevents a visible CIO demo failure."

* [X] ~~*Rank 1: Apply the CHECK constraint to test and production databases*~~ [2026-03-17]
LOE: 15 minutes. LOR: 5. This is a SQL DDL change, not a test file. Without it, the existing test_admin_routes.py deactivate/reactivate tests will throw IntegrityError and your test suite will show red. Run the ALTER TABLE statement from the test_admin_routes.py header comment against both PositionMatrix_Test and PositionMatrix.
* [X] ~~*Rank 2: Write tests/test_routes/test_reports_routes.py*~~ [2026-03-17]
LOE: 3 to 4 hours. LOR: 5. The strategy lists 14 tests covering cost summary pages, equipment reports, CSV/Excel exports (content type and file response), scope-filtered data, and export role enforcement. Reports are the most likely CIO demo endpoint that currently has zero behavioral test coverage. The export role enforcement is partially covered in test_scope_isolation.py, but the page-loads-and-shows-data tests are not.
* [X] ~~*Rank 3: Write tests/test_services/test_organization_service.py*~~ [2026-03-17]
LOE: 2 to 3 hours. LOR: 4. The strategy lists 13 tests. While test_scope_isolation.py exercises user_can_access_position and get_positions through the route layer, it does not directly test get_departments with different scope levels, user_can_access_department, employee filtering, or inactive employee exclusion. These are the queries that power every dropdown and listing page.
* [X] ~~*Rank 4: Write tests/test_services/test_equipment_service.py*~~ [2026-03-17]
LOE: 2 to 3 hours. LOR: 3. The strategy lists 13 tests for hardware/software CRUD, cost history tracking, and coverage management. Equipment catalog integrity directly affects cost calculations. If a cost history record fails to close properly when a price changes, the CIO could see stale numbers.
* [ ] Rank 5: Write tests/test_services/test_audit_service.py
LOE: 1.5 to 2 hours. LOR: 3. The strategy lists 9 tests. Audit logging is exercised indirectly by the admin route tests (the test_audit_log_page_shows_entries test proves a CREATE entry appears), but the audit service itself (IP capture, previous/new value serialization, pagination, filtering) is untested. If the CIO asks "how do we know who changed what," having audit service tests backing the answer is valuable.
* [ ] Rank 6: Write tests/test_decorators/test_role_required.py
LOE: 1 to 1.5 hours. LOR: 2. The strategy lists 10 isolated decorator tests. This is substantially mitigated by test_scope_isolation.py, which tests the decorators at the route integration level. The incremental value is catching decorator-internal edge cases (like a decorator returning 500 instead of 403 on an unexpected input). If time is tight, this can be deferred.
* [ ] Rank 7: Write tests/test_routes/test_auth_routes.py
LOE: 2 hours. LOR: 2. The strategy lists 9 tests. Login/logout failures during a demo are embarrassing but unlikely if dev-login is used. The highest-value subset is test_dev_login_works_in_testing_mode and test_logout_clears_session, which you could write in 30 minutes as a stopgap.
* [ ] Rank 8: Write tests/test_services/test_export_service.py
LOE: 1.5 hours. LOR: 2. The strategy lists 6 tests. If you write test_reports_routes.py (Rank 2), you get route-level export coverage. These service-level tests add column/value verification on the actual CSV/Excel output, which is useful but lower marginal value.
* [ ] Rank 9: Write tests/test_routes/test_error_handlers.py
LOE: 30 minutes. LOR: 1. Three tests: hit a nonexistent URL and assert a custom 404 page, trigger a 403 and assert the custom page, and (if possible) trigger a 500 and assert the custom page. Quick win, low risk reduction.
* [ ] Rank 10: Run pytest --cov=app --cov-report=html and review the coverage report
LOE: 15 minutes. LOR: 3. You should do this before the review regardless. The HTML report gives you a concrete number to cite ("62% overall, 85% on services") and may reveal untested branches in files you thought were covered. Save the report for the CIO slide deck.
* [ ] Rank 11 (post-review): Write remaining P2 and P3 files
test_auth_service.py, test_hr_sync_service.py, model tests, app factory tests, equipment route tests, and CLI command tests. These are all important for long-term maintainability but will not affect the CIO demo.
