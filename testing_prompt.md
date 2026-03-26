# Test creation prompt

Review my testing_strategy.md and prioritized_testing_next_steps.md to understand my strategy to be ready for a strict CIO review and go live in production. Then thoroughly review my Matrix project with a particular focus on my existing tests. Write the tests for Rank 15: Write tests/test_models/ (model tests). Make the tests real, robust, and accurate. There can be no mistakes or shortcuts. The examples in the testing_strategy.md and prioritized_testing_next_steps.md are the baseline but the actual written tests should be more robust and exacting than the plan calls for. The tests should done correctly and according to best practice. Don't let me get embarrassed during CIO review.


pytest tests/test_decorators/test_decorator_branch_gaps.py -v --tb=long 2>&1 | Out-File -FilePath test_output.txt

pytest tests/test_services/test_hr_sync_service.py -v --tb=long 2>&1 | Out-File -FilePath test_output.txt

pytest tests/test_routes/test_requirements_branch_gaps.py -v --tb=long 2>&1 | Out-File -FilePath test_output.txt

pytest tests/test_routes/test_admin_branch_gaps.py -v --tb=long 2>&1 | Out-File -FilePath test_output.txt

pytest tests/test_models/test_organization_model.py -v --tb=long 2>&1 | Out-File -FilePath test_output.txt
