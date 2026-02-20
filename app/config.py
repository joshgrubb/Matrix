"""
Application configuration classes.

Each class represents a deployment environment. The factory function
``create_app`` in ``app/__init__.py`` selects the appropriate config
based on the FLASK_ENV environment variable.

Database connection strings use the ``mssql+pyodbc`` dialect so that
SQLAlchemy communicates with SQL Server via the ODBC Driver 18.
"""

import os


class BaseConfig:
    """
    Shared configuration values inherited by all environments.

    Secrets and connection strings are loaded from environment variables
    so they never appear in source control.
    """

    # -- Flask core --------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # -- SQLAlchemy --------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        (
            "mssql+pyodbc://@toc-pbi-svr-01\\SQLEXPRESS/PositionMatrix"
            "?driver=ODBC+Driver+18+for+SQL+Server"
            "&TrustServerCertificate=yes"
            "&Trusted_Connection=yes"
        ),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Echo SQL statements to the log for debugging (override per env).
    SQLALCHEMY_ECHO = False

    # -- Entra ID / MSAL ---------------------------------------------------
    AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
    AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_AUTHORITY = os.environ.get(
        "AZURE_AUTHORITY",
        f"https://login.microsoftonline.com/"
        f"{os.environ.get('AZURE_TENANT_ID', 'common')}",
    )
    AZURE_REDIRECT_URI = os.environ.get(
        "AZURE_REDIRECT_URI", "http://localhost:5000/auth/callback"
    )
    # Scopes requested during OAuth2 login.
    AZURE_SCOPES = ["User.Read"]

    # -- NeoGov API --------------------------------------------------------
    NEOGOV_API_BASE_URL = os.environ.get(
        "NEOGOV_API_BASE_URL", "https://api.neogov.com/v1"
    )
    NEOGOV_API_KEY = os.environ.get("NEOGOV_API_KEY", "")
    # Department codes to exclude from sync (e.g., internal-only departments).
    NEOGOV_EXCLUDED_DEPARTMENTS: list[str] = [
        dept.strip()
        for dept in os.environ.get(
            "NEOGOV_EXCLUDED_DEPARTMENTS", "ADMINISTRATION"
        ).split(",")
        if dept.strip()
    ]

    # -- Logging -----------------------------------------------------------
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


class DevelopmentConfig(BaseConfig):
    """Development environment: verbose logging, SQL echo enabled."""

    DEBUG = True
    SQLALCHEMY_ECHO = True
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG")


class TestingConfig(BaseConfig):
    """
    Testing environment: uses a separate test database.

    WTF_CSRF_ENABLED is disabled so form submissions in tests don't
    need CSRF tokens.
    """

    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL",
        (
            "mssql+pyodbc://@localhost\\SQLEXPRESS/PositionMatrix"
            "?driver=ODBC+Driver+18+for+SQL+Server"
            "&TrustServerCertificate=yes"
            "&Trusted_Connection=yes"
        ),
    )
    LOG_LEVEL = "DEBUG"


class ProductionConfig(BaseConfig):
    """
    Production environment: strict settings, no debug output.

    SECRET_KEY must be set via environment variable in production.
    The application factory will raise an error if it's still the default.
    """

    DEBUG = False
    SQLALCHEMY_ECHO = False
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "WARNING")


# Lookup dict used by the application factory.
config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
