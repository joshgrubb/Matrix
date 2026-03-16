# PositionMatrix Dependency Update Plan

A reusable, step-by-step procedure for reviewing, updating, and deploying Python
package updates for the PositionMatrix application. Follow this plan on a regular
cadence (recommended: monthly for security checks, quarterly for feature updates)
to keep the project secure, stable, and maintainable.

---

## Table of Contents

1. [Prerequisites and Tooling](#1-prerequisites-and-tooling)
2. [Pre-Update Checklist](#2-pre-update-checklist)
3. [Phase 1: Reconnaissance](#3-phase-1-reconnaissance)
4. [Phase 2: Security Audit](#4-phase-2-security-audit)
5. [Phase 3: Update Execution](#5-phase-3-update-execution)
6. [Phase 4: Validation](#6-phase-4-validation)
7. [Phase 5: Requirements File Maintenance](#7-phase-5-requirements-file-maintenance)
8. [Phase 6: Production Deployment](#8-phase-6-production-deployment)
9. [Rollback Procedure](#9-rollback-procedure)
10. [Dependency-Specific Notes](#10-dependency-specific-notes)
11. [Recommended Cadence](#11-recommended-cadence)
12. [Future Improvements](#12-future-improvements)
13. [Quick Reference Commands](#13-quick-reference-commands)

---

## 1. Prerequisites and Tooling

### 1.1 Add Audit and Inspection Tools

Add the following packages to `requirements-dev.txt` under a new section. These
are development-only tools that should never be installed in production.

```text
# -- Dependency management ------------------------------------------------
pip-audit>=2.7,<3.0
pipdeptree>=2.23,<3.0
```

Install them into your development virtual environment:

```powershell
pip install -r requirements-dev.txt
```

### 1.2 Tool Purposes

**pip-audit** is maintained by the Python Packaging Authority (PyPA). It checks
every installed package against the OSV (Open Source Vulnerabilities) database
and reports known CVEs. It can also suggest or automatically apply the minimum
safe version.

**pipdeptree** displays the full dependency tree so you can see which packages
depend on which. This is critical before updating because changing one package
can cascade through its dependents.

**pip itself** should always be current before running updates. Newer versions
of pip include improved dependency resolution that can prevent conflicts.

### 1.3 Verify Your Environment

Before every update session, confirm you are working in the correct virtual
environment and that your tools are available:

```powershell
# Confirm the virtual environment is active.
# The prompt should show (venv) or your environment name.
python -c "import sys; print(sys.prefix)"

# Confirm pip is current.
python -m pip install --upgrade pip

# Confirm audit tools are installed.
pip-audit --version
pipdeptree --version
```

---

## 2. Pre-Update Checklist

Complete every item before making any changes. This creates a known-good
baseline you can restore if anything goes wrong.

### 2.1 Snapshot the Current State

Create a full pinned snapshot of every package currently installed. This file
records the exact versions (including transitive dependencies) so you can
reproduce the environment precisely if a rollback is needed.

```powershell
pip freeze > requirements-lock-YYYY-MM-DD.txt
```

Replace `YYYY-MM-DD` with the actual date (e.g., `requirements-lock-2026-03-16.txt`).
Keep this file until you have confirmed the update is stable in production.

### 2.2 Back Up the Requirements Files

Copy both requirements files so you have the pre-update version bounds on record:

```powershell
copy requirements.txt requirements.txt.bak
copy requirements-dev.txt requirements-dev.txt.bak
```

### 2.3 Confirm Tests Pass on the Current State

Run the full test suite against the current (pre-update) environment to make
sure you are starting from a green baseline. If tests fail before the update,
fix them first. Do not update packages on top of a broken test suite.

```powershell
pytest
pytest --cov=app/services
pylint app/
```

### 2.4 Confirm the Database Is Backed Up

Before any deployment that could involve package changes affecting SQLAlchemy,
Alembic, or pyodbc, verify that a recent backup of the PositionMatrix database
exists. This is especially important for production deployments.

### 2.5 Record the Starting Point

Save the output of `pip list` so you have a human-readable record of what was
installed before the update:

```powershell
pip list --format=columns > pip-list-before-YYYY-MM-DD.txt
```

---

## 3. Phase 1: Reconnaissance

Before changing anything, gather information about what is outdated and how
dependencies relate to each other.

### 3.1 Check for Outdated Packages

```powershell
pip list --outdated
```

This prints a table with four columns: the package name, the currently installed
version, the latest available version, and the package type (wheel or sdist).
Review the output and note which packages have updates available.

### 3.2 Inspect the Dependency Tree

```powershell
pipdeptree
```

This shows every installed package as a tree with its dependencies indented
beneath it. Look for:

- Shared dependencies (packages required by more than one of your direct
  dependencies). Updating a shared dependency could affect multiple packages.
- Version conflicts flagged with warnings at the top of the output.
- Transitive dependencies that you do not directly require but that are
  installed because one of your direct dependencies needs them.

For a condensed view showing only your direct dependencies and their immediate
children:

```powershell
pipdeptree --warn silence --depth 1
```

### 3.3 Categorize the Available Updates

Sort the outdated packages into three categories before proceeding:

**Category A: Patch updates** (e.g., 3.1.0 to 3.1.1). These typically contain
bug fixes and security patches only. They are the safest to apply and should be
applied promptly.

**Category B: Minor updates** (e.g., 3.1.x to 3.2.0). These may introduce new
features or deprecation warnings but should not contain breaking changes if the
package follows semantic versioning. Read the changelog before applying.

**Category C: Major updates** (e.g., 3.x to 4.0). These may contain breaking
API changes. Read the migration guide and changelog carefully. Plan dedicated
time for testing and potential code changes.

Your current `requirements.txt` version bounds (e.g., `>=3.1,<4.0`) already
protect you from accidental major-version jumps. Category C updates require
an intentional decision to raise the ceiling.

---

## 4. Phase 2: Security Audit

### 4.1 Run pip-audit

```powershell
pip-audit
```

This scans every installed package against the OSV vulnerability database. If
vulnerabilities are found, the output will list the package name, installed
version, vulnerability ID (CVE or PYSEC), and the version that fixes it.

### 4.2 Review Findings

For each reported vulnerability:

1. Read the vulnerability description. Not every CVE is relevant to your usage
   of the package. For example, a vulnerability in Flask's debug mode is not
   exploitable in production where debug is disabled.
2. Check whether the fix version falls within your current version bounds in
   `requirements.txt`. If it does, you can simply upgrade the package. If it
   does not, you will need to adjust the bounds.
3. Prioritize: fix critical and high severity vulnerabilities immediately.
   Medium and low severity items can be batched with the next scheduled update.

### 4.3 Preview Fixes Without Applying

To see what pip-audit would change without actually making changes:

```powershell
pip-audit --fix --dry-run
```

### 4.4 Apply Security Fixes

If the dry run looks correct and the fixes fall within your version bounds:

```powershell
pip-audit --fix
```

After applying fixes, proceed to Phase 4 (Validation) before updating any
other packages.

---

## 5. Phase 3: Update Execution

### 5.1 Update Strategy: One at a Time

**Do not update all packages at once.** Update packages individually or in small
related groups so that if something breaks, you know exactly which update caused
it. This is especially important for your project because several of your
dependencies are tightly coupled (e.g., Flask, Flask-SQLAlchemy, Flask-Migrate,
Flask-Login, and Flask-WTF all depend on Flask core; SQLAlchemy and Alembic are
tightly coupled).

### 5.2 Recommended Update Order

Update in this order to respect the dependency chain. Packages listed earlier
are depended on by packages listed later.

**Group 1: Core framework and ORM (update together, test together)**

```powershell
pip install --upgrade "SQLAlchemy>=2.0,<3.0"
pip install --upgrade "Flask>=3.1,<4.0"
```

**Group 2: Flask extensions (update after core Flask is stable)**

```powershell
pip install --upgrade "Flask-SQLAlchemy>=3.1,<4.0"
pip install --upgrade "alembic>=1.13,<2.0"
pip install --upgrade "Flask-Migrate>=4.0,<5.0"
pip install --upgrade "Flask-Login>=0.6,<1.0"
pip install --upgrade "Flask-WTF>=1.2,<2.0"
```

**Group 3: Database driver**

```powershell
pip install --upgrade "pyodbc>=5.1,<6.0"
```

**Group 4: Authentication**

```powershell
pip install --upgrade "msal>=1.31,<2.0"
```

**Group 5: Utilities (independent of each other)**

```powershell
pip install --upgrade "waitress>=3.0,<4.0"
pip install --upgrade "openpyxl>=3.1,<4.0"
pip install --upgrade "python-json-logger>=2.0,<3.0"
pip install --upgrade "python-dotenv>=1.0,<2.0"
```

**Group 6: Development dependencies**

```powershell
pip install --upgrade "pytest>=8.3,<9.0"
pip install --upgrade "pytest-flask>=1.3,<2.0"
pip install --upgrade "pytest-cov>=5.0,<6.0"
pip install --upgrade "pylint>=3.3,<4.0"
pip install --upgrade "sqlfluff>=3.2,<4.0"
pip install --upgrade "flask-debugtoolbar>=0.14,<1.0"
pip install --upgrade "pip-audit>=2.7,<3.0"
pip install --upgrade "pipdeptree>=2.23,<3.0"
```

### 5.3 Run Tests After Each Group

After updating each group, run the test suite immediately:

```powershell
pytest
```

If tests fail, you know the problem is in the group you just updated. Fix or
roll back that group before moving on to the next.

### 5.4 Handling Version Ceiling Bumps (Major Updates)

When a package releases a new major version that falls outside your current
bounds (e.g., Flask 4.0 when your ceiling is `<4.0`), follow this process:

1. Read the package's migration guide and changelog for the new major version.
2. Search your codebase for any deprecated APIs mentioned in the guide.
3. Create a branch (even though you normally commit to main, use a branch for
   major version bumps so you can abandon it cleanly if needed):

   ```powershell
   git checkout -b upgrade/flask-4
   ```

4. Update the ceiling in `requirements.txt` (e.g., change `<4.0` to `<5.0`).
5. Install the new version and run the full test suite.
6. Fix any breaking changes in your code.
7. Run pylint to catch new deprecation warnings.
8. Merge to main only after all tests pass.

---

## 6. Phase 4: Validation

After completing all updates (or after each group if you prefer incremental
validation), run this full validation sequence.

### 6.1 Automated Tests

```powershell
# Run the full test suite with verbose output.
pytest -v

# Run tests with coverage to make sure coverage has not dropped.
pytest --cov=app/services

# Run pylint to catch deprecation warnings from updated packages.
pylint app/
```

### 6.2 Dependency Conflict Check

```powershell
# Check that pip sees no broken dependencies.
pip check

# Check pipdeptree for conflicts (warnings print to stderr).
pipdeptree --warn fail
```

`pip check` verifies that every installed package's stated requirements are
satisfied. If it reports issues, a dependency conflict was introduced during
the update and must be resolved before proceeding.

### 6.3 Security Re-Audit

```powershell
pip-audit
```

Run the audit again after updates to confirm no new vulnerabilities were
introduced and all previously flagged issues are resolved.

### 6.4 Manual Smoke Test

Start the development server and manually verify critical functionality:

```powershell
flask run
```

Verify at minimum:

- Login via Microsoft Entra OAuth (or dev login in development).
- Navigate the requirements wizard through all four steps.
- View the equipment and software catalog pages.
- Export a report to Excel (openpyxl functionality).
- Check that the audit log records actions correctly.

### 6.5 Record the Final State

```powershell
pip list --format=columns > pip-list-after-YYYY-MM-DD.txt
```

---

## 7. Phase 5: Requirements File Maintenance

### 7.1 When to Update Version Bounds

Your requirements files use floor-and-ceiling bounds (e.g., `>=3.1,<4.0`).
Update these bounds in the following situations:

**Raise the floor** when the minimum version you now depend on has increased.
For example, if you start using a Flask 3.2 feature, raise the floor from
`>=3.1` to `>=3.2`. This prevents someone from installing an older version
that lacks the feature.

**Raise the ceiling** when you have tested and approved a new major version
(see Section 5.4).

**Lower the floor** is almost never needed. Only do this if you must support
an older environment that cannot run the newer version.

### 7.2 How to Update requirements.txt

After a successful update cycle, review whether any floor bumps are needed:

1. Open `requirements.txt` in VS Code.
2. For each package you updated, compare the new installed version against the
   current floor. If the floor is still accurate (the package would work at
   that version), leave it alone. If you have started using features from a
   newer minor version, raise the floor.
3. Save the file.

Example: if you updated Flask from 3.1.0 to 3.2.1 and your code now uses a
3.2 feature, change:

```text
# Before
Flask>=3.1,<4.0

# After
Flask>=3.2,<4.0
```

If the update was a patch version only (3.1.0 to 3.1.1), no change to the
bounds is needed.

### 7.3 How to Update requirements-dev.txt

Apply the same logic to `requirements-dev.txt`. Since it inherits from
`requirements.txt` via `-r requirements.txt`, you only need to update the
dev-specific packages listed there (pytest, pylint, sqlfluff, etc.).

### 7.4 Generate a Fresh Pinned Lock File

After updating the requirements files and confirming everything works, generate
a fresh lock file that records the exact installed versions for reproducibility:

```powershell
pip freeze > requirements-lock.txt
```

This file is not installed from directly. Its purpose is to serve as a snapshot
so that the exact environment can be recreated if needed. Commit it to the
repository alongside the updated requirements files.

### 7.5 Commit the Changes

```powershell
git add requirements.txt requirements-dev.txt requirements-lock.txt
git commit -m "chore: update dependencies YYYY-MM-DD

- Updated [list packages and version changes]
- pip-audit: 0 vulnerabilities
- All tests passing"
```

---

## 8. Phase 6: Production Deployment

### 8.1 Pre-Deployment

1. Confirm that all validation steps (Section 6) passed in the development
   environment.
2. Verify that a database backup exists for the production SQL Server instance.
3. Coordinate a maintenance window if the update includes major version changes
   or database migrations.

### 8.2 Deployment Steps

On the production Windows server:

```powershell
# 1. Navigate to the project directory.
cd C:\path\to\it-equipment-tracker

# 2. Pull the latest code (includes updated requirements files).
git pull origin main

# 3. Activate the production virtual environment.
.\venv\Scripts\Activate.ps1

# 4. Upgrade pip itself first.
python -m pip install --upgrade pip

# 5. Install updated dependencies.
pip install -r requirements.txt

# 6. Run any pending database migrations.
flask db upgrade

# 7. Restart the Waitress Windows service.
nssm restart PositionMatrix
```

### 8.3 Post-Deployment Verification

After the service restarts:

1. Check the application logs for startup errors.
2. Open the application in a browser and verify login works.
3. Walk through the requirements wizard for at least one position.
4. Verify that the IIS reverse proxy is forwarding requests correctly.

### 8.4 Production-Only Packages

Never install development dependencies on the production server. The production
server should only run:

```powershell
pip install -r requirements.txt
```

Not `requirements-dev.txt`. This keeps the production attack surface minimal.

---

## 9. Rollback Procedure

If an update causes problems that cannot be resolved quickly, restore the
previous working state.

### 9.1 Rollback in Development

```powershell
# Restore the pre-update requirements files.
copy requirements.txt.bak requirements.txt
copy requirements-dev.txt.bak requirements-dev.txt

# Recreate the virtual environment from the pinned snapshot.
deactivate
Remove-Item -Recurse -Force venv
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-lock-YYYY-MM-DD.txt
```

Use the lock file you created in Section 2.1. This restores every package
(including transitive dependencies) to the exact version that was installed
before the update.

### 9.2 Rollback in Production

```powershell
# 1. Stop the service.
nssm stop PositionMatrix

# 2. Revert the code to the previous commit.
git revert HEAD --no-edit
# Or, if multiple commits were involved:
git reset --hard <commit-hash-before-update>

# 3. Reinstall from the reverted requirements.
pip install -r requirements.txt

# 4. Roll back database migrations if any were applied.
flask db downgrade <previous-revision>

# 5. Restart the service.
nssm start PositionMatrix
```

### 9.3 When to Rollback vs. Fix Forward

**Roll back** if the problem is blocking users from working and you cannot
identify the root cause within 30 minutes.

**Fix forward** if the problem is minor (e.g., a deprecation warning in logs,
a cosmetic issue) and you can resolve it with a small code change.

---

## 10. Dependency-Specific Notes

This section documents known considerations for each direct dependency. Update
it as you learn new things during update cycles.

### 10.1 Flask Ecosystem

Flask, Flask-SQLAlchemy, Flask-Migrate, Flask-Login, and Flask-WTF are all
maintained by the Pallets project or close ecosystem contributors. They tend
to release coordinated updates. When Flask releases a new minor version, wait
a week or two for the extensions to catch up before updating the whole group
together.

### 10.2 SQLAlchemy and Alembic

SQLAlchemy and Alembic are tightly coupled. Flask-Migrate is a thin wrapper
around Alembic. Always update SQLAlchemy and Alembic together, then update
Flask-Migrate, and then test database migrations immediately:

```powershell
flask db upgrade
flask db downgrade -1
flask db upgrade
```

This round-trip test confirms that migrations still work in both directions.

### 10.3 pyodbc

pyodbc is a C extension that links against the ODBC Driver. After updating
pyodbc, verify that the installed ODBC Driver version on the machine is still
compatible. Check the pyodbc release notes for any changes to driver
compatibility. On Windows, you can verify the driver version in:

```
Control Panel > Administrative Tools > ODBC Data Sources > Drivers tab
```

### 10.4 msal

The Microsoft Authentication Library (msal) is updated frequently by Microsoft.
Updates often add support for new Entra ID features or fix token caching issues.
After updating, test the full OAuth2 login flow (not just dev login).

### 10.5 waitress

Waitress is the production WSGI server. After updating, restart the Windows
service and monitor logs for any changes in behavior, especially around
connection handling, timeouts, or thread pool configuration.

### 10.6 openpyxl

openpyxl handles Excel export. After updating, generate a test export and open
it in Excel to verify that formatting, formulas, and data are intact.

### 10.7 pylint

Pylint frequently adds new rules and reclassifies existing ones. After
updating, run pylint against the full codebase. New warnings may appear that
were not flagged before. Review them and either fix the code or add specific
disables to `pyproject.toml` if the new rules conflict with your project
conventions.

### 10.8 sqlfluff

SQLFluff adds and refines rules across minor versions. After updating, run
SQLFluff against your SQL files to check for new findings. Update the
`[tool.sqlfluff]` configuration in `pyproject.toml` if needed.

---

## 11. Recommended Cadence

### Monthly: Security Check

Run `pip-audit` and apply any security patches. This should take less than an
hour if no vulnerabilities are found.

```powershell
pip-audit
pip list --outdated
```

If vulnerabilities are found, apply fixes immediately following the process in
Sections 4 and 5.

### Quarterly: Full Update Cycle

Run the complete plan from Section 2 through Section 8. Budget half a day for
this, including testing and production deployment.

### On-Demand: Critical Vulnerability

If a critical CVE is published for one of your direct dependencies (especially
Flask, SQLAlchemy, or msal), apply the fix immediately without waiting for the
next scheduled check. Subscribe to security advisories for your key packages:

- Flask/Pallets: <https://github.com/pallets/flask/security/advisories>
- SQLAlchemy: <https://github.com/sqlalchemy/sqlalchemy/security/advisories>
- msal: <https://github.com/AzureAD/microsoft-authentication-library-for-python/releases>

### Annually: Python Version Upgrade

Your project targets Python 3.12+ with 3.14 planned for production. When
upgrading the Python version itself:

1. Create a new virtual environment with the new Python version.
2. Install all dependencies from `requirements-dev.txt`.
3. Run the full test suite and validation sequence.
4. Update the `requires-python` field in `pyproject.toml`.
5. Update the production server's Python installation.
6. Rebuild the production virtual environment.

---

## 12. Future Improvements

These are enhancements to consider as the project matures and CI/CD is adopted.

### 12.1 Add pip-audit to CI Pipeline

When you add GitHub Actions or another CI system, include a step that runs
`pip-audit` on every push or pull request. This ensures that no commit
introduces a known vulnerability.

### 12.2 Consider pip-compile for Lock Files

The `pip-tools` package provides `pip-compile`, which generates a fully pinned
`requirements.txt` from a `requirements.in` file (your current
`requirements.txt` would become `requirements.in`). This gives you deterministic
builds without changing your workflow significantly. Evaluate this when CI/CD is
in place.

### 12.3 Adopt Git Branching for Updates

When the team grows or the project becomes mission-critical, consider creating
a `dependency-update/YYYY-MM-DD` branch for each update cycle. This allows you
to test the full update in isolation before merging to main.

### 12.4 Automated Dependency PRs

Tools like Dependabot (GitHub) or Renovate can automatically open pull requests
when new versions of your dependencies are published. These integrate naturally
with CI pipelines that run tests automatically.

---

## 13. Quick Reference Commands

Copy and paste these during an update session. Replace `YYYY-MM-DD` with the
current date.

```powershell
# === PRE-UPDATE ===
python -m pip install --upgrade pip
pip freeze > requirements-lock-YYYY-MM-DD.txt
copy requirements.txt requirements.txt.bak
copy requirements-dev.txt requirements-dev.txt.bak
pip list --format=columns > pip-list-before-YYYY-MM-DD.txt
pytest

# === RECONNAISSANCE ===
pip list --outdated
pipdeptree
pip-audit

# === UPDATE (repeat per group, test between groups) ===
pip install --upgrade "PackageName>=floor,<ceiling"
pytest

# === VALIDATION ===
pytest -v
pytest --cov=app/services
pylint app/
pip check
pipdeptree --warn fail
pip-audit
pip list --format=columns > pip-list-after-YYYY-MM-DD.txt

# === REQUIREMENTS MAINTENANCE ===
# Edit requirements.txt and requirements-dev.txt as needed.
pip freeze > requirements-lock.txt
git add requirements.txt requirements-dev.txt requirements-lock.txt
git commit -m "chore: update dependencies YYYY-MM-DD"

# === PRODUCTION DEPLOYMENT ===
git pull origin main
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
flask db upgrade
nssm restart PositionMatrix

# === ROLLBACK (if needed) ===
copy requirements.txt.bak requirements.txt
copy requirements-dev.txt.bak requirements-dev.txt
deactivate
Remove-Item -Recurse -Force venv
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements-lock-YYYY-MM-DD.txt
```
