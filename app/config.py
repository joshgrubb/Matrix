"""
Application configuration classes (security-hardened).

Each class represents a deployment environment. The factory function
``create_app`` in ``app/__init__.py`` selects the appropriate config
based on the FLASK_ENV environment variable.

Database connection strings use the ``mssql+pyodbc`` dialect so that
SQLAlchemy communicates with SQL Server via the ODBC Driver 18.

Security hardening applied (ref: security audit 2026-02-25):
    - Finding #1: Removed internal hostname from default DB URI.
    - Finding #2: Added ``validate_production_secrets()`` class method.
    - Finding #3: HTTPS redirect URI enforced in production.
    - Finding #4: Session cookie hardening on all environments.
    - Finding #5: ``TrustServerCertificate`` removed from prod default.
    - Finding #7: ``FLASK_DEBUG`` no longer set in ``.flaskenv``.
    - Finding #8: ``DEV_LOGIN_ENABLED`` gate added.
"""

import logging
import os

# Module-level logger for startup warnings emitted by config classes.
_logger = logging.getLogger(__name__)

# =========================================================================
# Sentinel for detecting unset SECRET_KEY in production.
# Using a constant makes the check in create_app() easier to maintain.
# =========================================================================
_DEFAULT_SECRET_KEY = "dev-secret-change-me"


class BaseConfig:
    """
    Shared configuration values inherited by all environments.

    Secrets and connection strings are loaded from environment variables
    so they never appear in source control.
    """

    # -- Flask core --------------------------------------------------------
    SECRET_KEY: str = os.environ.get("SECRET_KEY", _DEFAULT_SECRET_KEY)

    # -- Session cookie hardening (Finding #4) -----------------------------
    # HttpOnly prevents JavaScript access to the session cookie.
    SESSION_COOKIE_HTTPONLY: bool = True

    # SameSite=Lax blocks cross-origin POST-based CSRF while still
    # allowing top-level navigations (e.g., following a link).
    SESSION_COOKIE_SAMESITE: str = "Lax"

    # Secure flag is False by default so http://localhost works in dev.
    # ProductionConfig overrides this to True (requires HTTPS).
    SESSION_COOKIE_SECURE: bool = False

    # Expire idle sessions after 1 hour instead of Flask's 31-day default.
    # Adjust via PERMANENT_SESSION_LIFETIME env var if needed.
    PERMANENT_SESSION_LIFETIME: int = int(
        os.environ.get("PERMANENT_SESSION_LIFETIME", "3600")
    )

    # -- SQLAlchemy --------------------------------------------------------
    # Finding #1: Default URI uses localhost instead of a real hostname.
    # Developers point at their own instance via DATABASE_URL in .env.
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL",
        (
            "mssql+pyodbc://@localhost\\SQLEXPRESS/PositionMatrixDev"
            "?driver=ODBC+Driver+18+for+SQL+Server"
            "&TrustServerCertificate=yes"
            "&Trusted_Connection=yes"
        ),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # Echo SQL statements to the log for debugging (override per env).
    SQLALCHEMY_ECHO: bool = False

    # -- Entra ID / MSAL ---------------------------------------------------
    AZURE_CLIENT_ID: str = os.environ.get("AZURE_CLIENT_ID", "")
    AZURE_CLIENT_SECRET: str = os.environ.get("AZURE_CLIENT_SECRET", "")
    AZURE_TENANT_ID: str = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_AUTHORITY: str = os.environ.get(
        "AZURE_AUTHORITY",
        (
            f"https://login.microsoftonline.com/"
            f"{os.environ.get('AZURE_TENANT_ID', 'common')}"
        ),
    )
    AZURE_REDIRECT_URI: str = os.environ.get(
        "AZURE_REDIRECT_URI", "http://localhost:5000/auth/callback"
    )

    # Scopes requested during the OAuth2 login flow.
    AZURE_SCOPES: list[str] = ["User.Read"]

    # -- NeoGov API --------------------------------------------------------
    NEOGOV_API_BASE_URL: str = os.environ.get(
        "NEOGOV_API_BASE_URL", "https://api.neogov.com/v1"
    )
    NEOGOV_API_KEY: str = os.environ.get("NEOGOV_API_KEY", "")

    # Department codes to exclude from sync (e.g., internal-only depts).
    NEOGOV_EXCLUDED_DEPARTMENTS: list[str] = [
        dept.strip()
        for dept in os.environ.get(
            "NEOGOV_EXCLUDED_DEPARTMENTS", "ADMINISTRATION"
        ).split(",")
        if dept.strip()
    ]

    # Maximum concurrent HTTP requests for NeoGov employee detail fetching.
    NEOGOV_MAX_CONCURRENT_REQUESTS: int = int(
        os.environ.get("NEOGOV_MAX_CONCURRENT_REQUESTS", "5")
    )

    # -- Dev login guard (Finding #8) --------------------------------------
    # Even when DEBUG is True, dev-login routes are disabled unless this
    # is explicitly set to "true" in the environment. This prevents an
    # accidental authentication bypass if debug mode is ever enabled on
    # a non-local machine.
    DEV_LOGIN_ENABLED: bool = (
        os.environ.get("DEV_LOGIN_ENABLED", "false").lower() == "true"
    )

    # -- Logging -----------------------------------------------------------
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # =====================================================================
    # Production validation helpers
    # =====================================================================

    @classmethod
    def validate_production_secrets(cls, app_config: dict) -> None:
        """
        Verify that all required secrets are set for production.

        Called by ``create_app()`` when ``config_name == 'production'``.
        Raises ``RuntimeError`` for hard requirements and logs warnings
        for soft requirements.

        Args:
            app_config: The ``app.config`` dict after loading the
                        config class.

        Raises:
            RuntimeError: If any critical secret is missing or still
                          set to its insecure default value.
        """
        errors: list[str] = []

        # -- SECRET_KEY (hard fail) ----------------------------------------
        if app_config.get("SECRET_KEY") == _DEFAULT_SECRET_KEY:
            errors.append(
                "SECRET_KEY is still the insecure default. "
                "Generate one with: python -c "
                '"import secrets; print(secrets.token_hex(32))"'
            )

        # -- Entra ID credentials (hard fail) ------------------------------
        # Finding #2: Without these, OAuth login silently fails or
        # redirects to the wrong tenant.
        required_azure_keys = [
            "AZURE_CLIENT_ID",
            "AZURE_CLIENT_SECRET",
            "AZURE_TENANT_ID",
        ]
        missing_azure = [key for key in required_azure_keys if not app_config.get(key)]
        if missing_azure:
            errors.append(
                "Entra ID credentials missing: "
                f"{', '.join(missing_azure)}. "
                "OAuth login will not work without these."
            )

        # -- AZURE_REDIRECT_URI must be HTTPS (hard fail) ------------------
        # Finding #3: Plain HTTP would send the authorization code in
        # the clear and Microsoft should reject non-localhost HTTP URIs.
        redirect_uri = app_config.get("AZURE_REDIRECT_URI", "")
        if redirect_uri and not redirect_uri.startswith("https://"):
            errors.append(
                f"AZURE_REDIRECT_URI ({redirect_uri}) must use HTTPS "
                "in production to protect the OAuth authorization code."
            )

        # -- Raise all hard failures at once -------------------------------
        if errors:
            # Join with newlines so the operator sees every issue in one
            # traceback instead of fixing them one at a time.
            combined = "\n  - ".join(errors)
            raise RuntimeError(f"Production configuration errors:\n  - {combined}")

        # -- NeoGov API key (soft warning) ---------------------------------
        # HR sync returns empty data without a key, but the app can
        # still run for other functionality.
        if not app_config.get("NEOGOV_API_KEY"):
            _logger.warning(
                "NEOGOV_API_KEY is not set — HR sync will return "
                "empty data. Set the key in .env for production."
            )

        # -- LOG_LEVEL sanity check (soft warning) -------------------------
        # Finding #6: DEBUG logging in production can leak secrets via
        # SQLAlchemy echo, NeoGov headers, or future debug statements.
        if app_config.get("LOG_LEVEL", "").upper() == "DEBUG":
            _logger.warning(
                "LOG_LEVEL=DEBUG is not recommended in production — "
                "sensitive data (SQL queries, API headers) may appear "
                "in logs. Consider INFO or WARNING."
            )


class DevelopmentConfig(BaseConfig):
    """
    Development environment: verbose logging, SQL echo enabled.

    Dev login is enabled by default in this config. Override
    ``DEV_LOGIN_ENABLED`` in ``.env`` if you want to disable it.
    """

    DEBUG: bool = True
    SQLALCHEMY_ECHO: bool = True
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "DEBUG")

    # Enable dev login bypass in development by default.
    DEV_LOGIN_ENABLED: bool = (
        os.environ.get("DEV_LOGIN_ENABLED", "true").lower() == "true"
    )


class TestingConfig(BaseConfig):
    """
    Testing environment: uses a separate test database.

    WTF_CSRF_ENABLED is disabled so form submissions in tests don't
    need CSRF tokens. Dev login is enabled for test convenience.
    """

    TESTING: bool = True
    WTF_CSRF_ENABLED: bool = False

    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "TEST_DATABASE_URL",
        (
            "mssql+pyodbc://@localhost\\SQLEXPRESS/PositionMatrixTest"
            "?driver=ODBC+Driver+18+for+SQL+Server"
            "&TrustServerCertificate=yes"
            "&Trusted_Connection=yes"
        ),
    )
    LOG_LEVEL: str = "DEBUG"

    # Enable dev login bypass for integration tests.
    DEV_LOGIN_ENABLED: bool = True


class ProductionConfig(BaseConfig):
    """
    Production environment: strict settings, no debug output.

    All secrets must be set via environment variables. The application
    factory calls ``validate_production_secrets()`` at startup and will
    refuse to launch if critical values are missing.

    Finding #4: Session cookie is marked Secure so it is only sent
    over HTTPS (IIS terminates TLS in front of Waitress).

    Finding #5: The default DATABASE_URL omits TrustServerCertificate
    to enforce TLS certificate validation against SQL Server.
    """

    DEBUG: bool = False
    SQLALCHEMY_ECHO: bool = False
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "WARNING")

    # -- Session cookie: require HTTPS in production -----------------------
    SESSION_COOKIE_SECURE: bool = True

    # -- Database: enforce TLS cert validation (Finding #5) ----------------
    # Production servers should present a certificate from a trusted CA
    # (or install the self-signed cert into the Windows trust store).
    # Override via DATABASE_URL in .env if you still need the bypass.
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL",
        (
            "mssql+pyodbc://@localhost\\SQLEXPRESS/PositionMatrix"
            "?driver=ODBC+Driver+18+for+SQL+Server"
            "&Encrypt=yes"
            "&Trusted_Connection=yes"
        ),
    )

    # -- Dev login: always disabled in production --------------------------
    DEV_LOGIN_ENABLED: bool = False


# Lookup dict used by the application factory.
config_by_name: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
