# PositionMatrix — IT Equipment & Software Tracking

Track IT equipment and software requirements for every authorized position in
the organization. Calculate and report budgetary impact per position, division,
and department.

## Prerequisites

- **Python 3.12+** (3.14 targeted for production)
- **SQL Server 2022** (Express for development, Full for production)
- **ODBC Driver 18 for SQL Server**
  ([Download](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server))
- **Git**
- **VS Code** (recommended)

## Quick Start (Windows Development)

### 1. Clone and Enter the Repository

```powershell
git clone https://github.com/YOUR_ORG/it-equipment-tracker.git
cd it-equipment-tracker
```

### 2. Create and Activate a Virtual Environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```powershell
pip install -r requirements-dev.txt
```

### 4. Configure Environment Variables

```powershell
copy .env.example .env
```

Edit `.env` with your SQL Server connection string and other settings. The
default assumes Windows Authentication to a local SQL Express instance:

```
DATABASE_URL=mssql+pyodbc://@localhost\SQLEXPRESS/PositionMatrix?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes&Trusted_Connection=yes
```

### 5. Create the Database

Run the `database_creation.sql` script against your SQL Server instance using
SSMS or `sqlcmd`. The script creates the `PositionMatrix` database, all schemas,
tables, indexes, constraints, and seed data.

### 6. Initialize Flask-Migrate

Since the database was created from a DDL script (not from Alembic), stamp the
current state so Flask-Migrate knows the schema is up to date:

```powershell
flask db init
flask db migrate -m "Initial schema - match existing DDL"
flask db stamp head
```

Future schema changes should be made via `flask db migrate` / `flask db upgrade`.

### 7. Run the Development Server

```powershell
flask run
```

The app will be available at `http://localhost:5000`.

### 8. Run Tests

Create a `PositionMatrix_Test` database by running the DDL script again with
the database name changed, then:

```powershell
pytest
pytest --cov=app/services
```

## Project Structure

```
it-equipment-tracker/
├── app/
│   ├── __init__.py            # Application factory
│   ├── config.py              # Dev / Test / Prod configuration
│   ├── extensions.py          # SQLAlchemy, Migrate, LoginManager
│   ├── models/                # SQLAlchemy models (one file per schema)
│   ├── services/              # Business logic layer
│   ├── blueprints/            # Route handlers grouped by feature
│   ├── templates/             # Shared Jinja2 templates
│   └── static/                # CSS, JS, images
├── tests/                     # pytest test suite
├── migrations/                # Alembic version directory
├── requirements.txt           # Production dependencies
├── requirements-dev.txt       # Dev/test dependencies
├── wsgi.py                    # Waitress entry point (production)
├── pyproject.toml             # Pylint, pytest, SQLFluff config
└── .env.example               # Environment variable template
```

## Architecture

```
Route (HTTP request)
  → Service (business logic, scope filtering, audit logging)
    → Model (database via SQLAlchemy)
```

Routes never access models directly. Services enforce authorization scopes
and record audit entries for all data changes.

## Database Schemas

| Schema   | Purpose                                      | Phase |
|----------|----------------------------------------------|-------|
| `org`    | Departments, divisions, positions, employees | MVP   |
| `equip`  | Hardware types, software catalog, requirements | MVP |
| `auth`   | Users, roles, permissions, scopes            | MVP   |
| `audit`  | Audit log, HR sync log                       | MVP   |
| `budget` | Cost history, requirement history, snapshots | MVP   |
| `asset`  | Physical IT assets and assignments           | 2     |
| `itsm`   | Tickets, incidents, change requests          | 4     |

## Production Deployment

See the project plan for full deployment architecture. Summary:

1. IIS as reverse proxy (SSL termination).
2. Waitress as WSGI server on port 8080.
3. Register as a Windows Service via NSSM.
4. Deploy via `git pull` + `pip install` + `flask db upgrade` + service restart.
