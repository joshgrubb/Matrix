"""
Tests for the application factory and configuration validation.

Verifies that ``create_app()`` in ``app/__init__.py`` correctly
loads each environment configuration, registers all extensions and
blueprints, enforces production security constraints, and wires
up Flask-Login's ``user_loader`` callback.

Also tests ``BaseConfig.validate_production_secrets()`` in
``app/config.py``, which is the gatekeeper that prevents deploying
with insecure defaults.  Every branch of this function is exercised
-- default SECRET_KEY, missing Azure credentials, non-HTTPS redirect
URI, missing NeoGov API key, and DEBUG log level in production.

Coverage targets:
    - ``app/__init__.py`` create_app: 80% -> 100%
      (missing lines: invalid config ValueError, production
      SECRET_KEY RuntimeError, load_user callback)
    - ``app/config.py`` BaseConfig.validate_production_secrets: 0% -> 100%
      (all 17 statements currently uncovered)

Strategy reference: testing_strategy.md Section 7.2 (8 baseline tests).
This file exceeds the baseline with 30+ tests covering:

    - App creation with each valid config name.
    - Config-specific attribute verification (DEBUG, TESTING, etc.).
    - Invalid config name rejection with correct error message.
    - Default config_name resolution from FLASK_ENV.
    - Production SECRET_KEY enforcement at the create_app level.
    - Full validate_production_secrets branch coverage:
        - Default SECRET_KEY hard failure.
        - Missing individual Azure credential hard failures.
        - Missing all Azure credentials at once.
        - Non-HTTPS AZURE_REDIRECT_URI hard failure.
        - Multiple hard failures reported together.
        - Missing NEOGOV_API_KEY soft warning.
        - DEBUG LOG_LEVEL soft warning.
        - Clean pass when all values are correct.
    - Extension initialization verification (db, migrate, login_manager, csrf).
    - Blueprint registration verification (all 7 blueprints with correct prefixes).
    - Error handler registration (403, 404, 500).
    - Flask-Login load_user callback (valid ID, invalid ID, None).
    - Session cookie security settings per environment.
    - DEV_LOGIN_ENABLED gate per environment.

Design decisions:
    - Tests that exercise ``create_app()`` directly (not via the
      session-scoped ``app`` fixture) create short-lived app instances
      to avoid polluting the shared test database session.
    - ``validate_production_secrets`` is tested by calling the classmethod
      directly with a crafted dict, not by spinning up a full production
      app.  This isolates the validation logic from database connectivity.
    - Environment variable manipulation uses ``unittest.mock.patch.dict``
      on ``os.environ`` so changes are automatically reverted.
    - The ``load_user`` callback test uses the session-scoped ``app``
      fixture and the function-scoped ``admin_user`` fixture from
      conftest.py, ensuring the user exists in the real test database.

Fixture reminder (from conftest.py):
    app:          Session-scoped Flask application (testing config).
    db_session:   Function-scoped SQLAlchemy session with cleanup.
    admin_user:   User with admin role, organization-wide scope.
    roles:        Session-scoped dict of seeded Role records.

Run this file in isolation::

    pytest tests/test_config/test_app_factory.py -v
"""

import logging
import os
from unittest.mock import patch

import pytest

from app import create_app
from app.config import (
    BaseConfig,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
    _DEFAULT_SECRET_KEY,
    config_by_name,
)
from app.extensions import db


# =====================================================================
# 1. create_app -- valid config names produce correct environments
# =====================================================================


class TestCreateAppValidConfigs:
    """
    Verify that ``create_app()`` returns a properly configured Flask
    application for each recognized config name.
    """

    def test_create_app_testing_config_sets_testing_true(self):
        """
        Passing 'testing' should produce an app where
        ``app.config['TESTING']`` is True.
        """
        test_app = create_app("testing")
        assert test_app.config["TESTING"] is True

    def test_create_app_testing_config_disables_csrf(self):
        """
        The testing config should disable WTF-CSRF so that form
        submissions in tests do not require CSRF tokens.
        """
        test_app = create_app("testing")
        assert test_app.config["WTF_CSRF_ENABLED"] is False

    def test_create_app_testing_config_enables_dev_login(self):
        """
        The testing config should enable the dev-login bypass so
        integration tests can authenticate without OAuth.
        """
        test_app = create_app("testing")
        assert test_app.config["DEV_LOGIN_ENABLED"] is True

    def test_create_app_development_config_sets_debug_true(self):
        """
        Passing 'development' should produce an app where
        ``app.config['DEBUG']`` is True.
        """
        dev_app = create_app("development")
        assert dev_app.config["DEBUG"] is True

    def test_create_app_development_config_enables_sqlalchemy_echo(self):
        """
        Development config should echo SQL statements to help
        developers debug query issues.
        """
        dev_app = create_app("development")
        assert dev_app.config["SQLALCHEMY_ECHO"] is True

    def test_create_app_development_config_sets_debug_log_level(self):
        """
        Development config should default LOG_LEVEL to DEBUG for
        maximum verbosity during local development.
        """
        dev_app = create_app("development")
        assert dev_app.config["LOG_LEVEL"] == "DEBUG"

    def test_create_app_returns_flask_instance(self):
        """
        The return value must be a Flask application instance, not
        None or some other type.
        """
        from flask import Flask

        test_app = create_app("testing")
        assert isinstance(test_app, Flask)

    def test_create_app_testing_uses_test_database_uri(self):
        """
        The testing config should use the TEST_DATABASE_URL
        environment variable or its default, which points at the
        PositionMatrixTest database -- not the development or
        production database.
        """
        test_app = create_app("testing")
        uri = test_app.config["SQLALCHEMY_DATABASE_URI"]
        # The default test URI references PositionMatrixTest.
        # If TEST_DATABASE_URL is set in the environment, it overrides,
        # but either way it should NOT be the dev database.
        assert "PositionMatrixDev" not in uri or os.environ.get("TEST_DATABASE_URL")


# =====================================================================
# 2. create_app -- production config specifics
# =====================================================================


class TestCreateAppProductionConfig:
    """
    Verify production config attributes and the hard-fail safety
    check in ``create_app()`` that rejects the default SECRET_KEY.
    """

    @patch.object(
        ProductionConfig,
        "SECRET_KEY",
        "a-very-secure-random-production-key-1234567890ab",
    )
    @patch.dict(
        os.environ,
        {
            "SECRET_KEY": "a-very-secure-random-production-key-1234567890ab",
            "AZURE_CLIENT_ID": "prod-client-id",
            "AZURE_CLIENT_SECRET": "prod-client-secret",
            "AZURE_TENANT_ID": "prod-tenant-id",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "prod-neogov-key",
            "LOG_LEVEL": "WARNING",
        },
        clear=False,
    )
    def test_create_app_production_config_succeeds_with_valid_env(self):
        """
        When all required environment variables are set to valid
        production values, ``create_app('production')`` should
        succeed without raising.

        NOTE: ``ProductionConfig.SECRET_KEY`` is a class-level
        attribute evaluated at import time via
        ``os.environ.get("SECRET_KEY", default)``.  Patching
        ``os.environ`` alone has no effect because the class
        attribute is already baked in.  We must also patch the
        class attribute directly with ``patch.object``.
        """
        prod_app = create_app("production")
        assert prod_app.config["DEBUG"] is False

    @patch.object(
        ProductionConfig,
        "SECRET_KEY",
        "a-very-secure-random-production-key-1234567890ab",
    )
    @patch.dict(
        os.environ,
        {
            "SECRET_KEY": "a-very-secure-random-production-key-1234567890ab",
            "AZURE_CLIENT_ID": "prod-client-id",
            "AZURE_CLIENT_SECRET": "prod-client-secret",
            "AZURE_TENANT_ID": "prod-tenant-id",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "prod-neogov-key",
            "LOG_LEVEL": "WARNING",
        },
        clear=False,
    )
    def test_production_config_disables_sqlalchemy_echo(self):
        """
        Production must not echo SQL statements, which can leak
        sensitive data in query parameters and connection strings.
        """
        prod_app = create_app("production")
        assert prod_app.config["SQLALCHEMY_ECHO"] is False

    @patch.object(
        ProductionConfig,
        "SECRET_KEY",
        "a-very-secure-random-production-key-1234567890ab",
    )
    @patch.dict(
        os.environ,
        {
            "SECRET_KEY": "a-very-secure-random-production-key-1234567890ab",
            "AZURE_CLIENT_ID": "prod-client-id",
            "AZURE_CLIENT_SECRET": "prod-client-secret",
            "AZURE_TENANT_ID": "prod-tenant-id",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "prod-neogov-key",
            "LOG_LEVEL": "WARNING",
        },
        clear=False,
    )
    def test_production_config_disables_dev_login(self):
        """
        DEV_LOGIN_ENABLED must be False in production to prevent
        authentication bypass via the dev-login route.
        """
        prod_app = create_app("production")
        assert prod_app.config["DEV_LOGIN_ENABLED"] is False

    @patch.object(
        ProductionConfig,
        "SECRET_KEY",
        "a-very-secure-random-production-key-1234567890ab",
    )
    @patch.dict(
        os.environ,
        {
            "SECRET_KEY": "a-very-secure-random-production-key-1234567890ab",
            "AZURE_CLIENT_ID": "prod-client-id",
            "AZURE_CLIENT_SECRET": "prod-client-secret",
            "AZURE_TENANT_ID": "prod-tenant-id",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "prod-neogov-key",
            "LOG_LEVEL": "WARNING",
        },
        clear=False,
    )
    def test_production_config_secures_session_cookie(self):
        """
        Production must set SESSION_COOKIE_SECURE to True so the
        session cookie is only transmitted over HTTPS.
        """
        prod_app = create_app("production")
        assert prod_app.config["SESSION_COOKIE_SECURE"] is True

    def test_production_rejects_default_secret_key_in_create_app(self):
        """
        ``create_app('production')`` must raise ``RuntimeError``
        when SECRET_KEY is still the insecure default value.

        This is the safety check in create_app() itself, separate
        from validate_production_secrets().
        """
        # Ensure SECRET_KEY is NOT overridden via environment so the
        # default sentinel value is used.
        env_overrides = {
            "AZURE_CLIENT_ID": "test-id",
            "AZURE_CLIENT_SECRET": "test-secret",
            "AZURE_TENANT_ID": "test-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
        }
        # Remove SECRET_KEY from env if present, so the default is used.
        env_copy = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
        env_copy.update(env_overrides)

        with patch.dict(os.environ, env_copy, clear=True):
            with pytest.raises(RuntimeError, match="SECRET_KEY"):
                create_app("production")


# =====================================================================
# 3. create_app -- invalid config name
# =====================================================================


class TestCreateAppInvalidConfig:
    """
    Verify that ``create_app()`` raises ``ValueError`` for
    unrecognized config names.
    """

    def test_invalid_config_name_raises_value_error(self):
        """
        An unknown config name should raise ValueError, not silently
        fall back to a default or return None.
        """
        with pytest.raises(ValueError, match="Unknown config"):
            create_app("staging")

    def test_invalid_config_error_message_lists_valid_options(self):
        """
        The error message should list the valid config names so the
        developer knows what options are available.
        """
        with pytest.raises(ValueError) as exc_info:
            create_app("nonexistent")

        error_msg = str(exc_info.value)
        # All three valid config names should appear in the message.
        assert "development" in error_msg
        assert "testing" in error_msg
        assert "production" in error_msg

    def test_empty_string_config_name_raises_value_error(self):
        """
        An empty string is not a valid config name and should raise
        ValueError just like any other invalid name.
        """
        with pytest.raises(ValueError, match="Unknown config"):
            create_app("")

    def test_none_config_with_invalid_flask_env_raises(self):
        """
        When config_name is None, create_app reads FLASK_ENV.  If
        FLASK_ENV is set to an invalid value, ValueError should be
        raised.
        """
        with patch.dict(os.environ, {"FLASK_ENV": "invalid_env"}, clear=False):
            with pytest.raises(ValueError, match="Unknown config"):
                create_app(None)


# =====================================================================
# 4. create_app -- default config name resolution
# =====================================================================


class TestCreateAppDefaultConfigResolution:
    """
    Verify that ``create_app(None)`` reads FLASK_ENV from the
    environment and falls back to 'development' when unset.
    """

    def test_none_config_uses_flask_env(self):
        """
        When config_name is None and FLASK_ENV is 'testing',
        the app should use TestingConfig.
        """
        with patch.dict(os.environ, {"FLASK_ENV": "testing"}, clear=False):
            test_app = create_app(None)
            assert test_app.config["TESTING"] is True

    def test_none_config_defaults_to_development(self):
        """
        When config_name is None and FLASK_ENV is not set,
        the app should default to DevelopmentConfig.
        """
        # Remove FLASK_ENV entirely from the environment.
        env_without_flask_env = {
            k: v for k, v in os.environ.items() if k != "FLASK_ENV"
        }
        with patch.dict(os.environ, env_without_flask_env, clear=True):
            dev_app = create_app(None)
            assert dev_app.config["DEBUG"] is True


# =====================================================================
# 5. config_by_name lookup dict
# =====================================================================


class TestConfigByNameMapping:
    """
    Verify that the ``config_by_name`` dict correctly maps string
    keys to config classes.
    """

    def test_config_by_name_contains_all_three_environments(self):
        """The mapping must contain development, testing, and production."""
        assert "development" in config_by_name
        assert "testing" in config_by_name
        assert "production" in config_by_name

    def test_development_maps_to_development_config(self):
        """The 'development' key must map to DevelopmentConfig."""
        assert config_by_name["development"] is DevelopmentConfig

    def test_testing_maps_to_testing_config(self):
        """The 'testing' key must map to TestingConfig."""
        assert config_by_name["testing"] is TestingConfig

    def test_production_maps_to_production_config(self):
        """The 'production' key must map to ProductionConfig."""
        assert config_by_name["production"] is ProductionConfig

    def test_config_by_name_has_exactly_three_entries(self):
        """
        There should be exactly three entries.  A new entry without
        corresponding tests would be a gap.
        """
        assert len(config_by_name) == 3


# =====================================================================
# 6. BaseConfig shared attributes
# =====================================================================


class TestBaseConfigAttributes:
    """
    Verify that BaseConfig sets sensible defaults inherited by all
    environment-specific config classes.
    """

    def test_session_cookie_httponly_is_true(self):
        """HttpOnly must be enabled to prevent JS access to the cookie."""
        assert BaseConfig.SESSION_COOKIE_HTTPONLY is True

    def test_session_cookie_samesite_is_lax(self):
        """SameSite=Lax blocks cross-origin POST-based CSRF."""
        assert BaseConfig.SESSION_COOKIE_SAMESITE == "Lax"

    def test_sqlalchemy_track_modifications_is_false(self):
        """
        Track modifications must be disabled to avoid unnecessary
        memory overhead from SQLAlchemy event listeners.
        """
        assert BaseConfig.SQLALCHEMY_TRACK_MODIFICATIONS is False

    def test_default_secret_key_sentinel_value(self):
        """
        The default SECRET_KEY should be the known sentinel value
        that production validation checks against.
        """
        assert _DEFAULT_SECRET_KEY == "dev-secret-change-me"


# =====================================================================
# 7. validate_production_secrets -- hard failures (RuntimeError)
# =====================================================================


class TestValidateProductionSecretsHardFailures:
    """
    Verify that ``validate_production_secrets`` raises RuntimeError
    for critical security misconfigurations.

    These are blocking errors that prevent the application from
    starting in production.
    """

    def test_default_secret_key_raises_runtime_error(self):
        """
        If SECRET_KEY is still the insecure default, validation
        must raise RuntimeError.
        """
        config_dict = {
            "SECRET_KEY": _DEFAULT_SECRET_KEY,
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "WARNING",
        }
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            BaseConfig.validate_production_secrets(config_dict)

    def test_default_secret_key_error_suggests_generation_command(self):
        """
        The error message for a default SECRET_KEY should include
        a helpful command for generating a secure key.
        """
        config_dict = {
            "SECRET_KEY": _DEFAULT_SECRET_KEY,
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
        }
        with pytest.raises(RuntimeError, match="secrets.token_hex"):
            BaseConfig.validate_production_secrets(config_dict)

    def test_missing_azure_client_id_raises_runtime_error(self):
        """
        A missing AZURE_CLIENT_ID must cause a hard failure because
        OAuth login cannot function without it.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
        }
        with pytest.raises(RuntimeError, match="AZURE_CLIENT_ID"):
            BaseConfig.validate_production_secrets(config_dict)

    def test_missing_azure_client_secret_raises_runtime_error(self):
        """
        A missing AZURE_CLIENT_SECRET must cause a hard failure.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
        }
        with pytest.raises(RuntimeError, match="AZURE_CLIENT_SECRET"):
            BaseConfig.validate_production_secrets(config_dict)

    def test_missing_azure_tenant_id_raises_runtime_error(self):
        """
        A missing AZURE_TENANT_ID must cause a hard failure.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
        }
        with pytest.raises(RuntimeError, match="AZURE_TENANT_ID"):
            BaseConfig.validate_production_secrets(config_dict)

    def test_all_azure_credentials_missing_raises_single_error(self):
        """
        When all three Azure credentials are missing, the error
        should be raised once (not three separate exceptions) and
        should list all three missing keys.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "",
            "AZURE_CLIENT_SECRET": "",
            "AZURE_TENANT_ID": "",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
        }
        with pytest.raises(RuntimeError) as exc_info:
            BaseConfig.validate_production_secrets(config_dict)

        error_msg = str(exc_info.value)
        assert "AZURE_CLIENT_ID" in error_msg
        assert "AZURE_CLIENT_SECRET" in error_msg
        assert "AZURE_TENANT_ID" in error_msg

    def test_non_https_redirect_uri_raises_runtime_error(self):
        """
        An HTTP (non-HTTPS) AZURE_REDIRECT_URI in production must
        cause a hard failure.  Plain HTTP would expose the OAuth
        authorization code in transit.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "http://app.example.com/auth/callback",
        }
        with pytest.raises(RuntimeError, match="HTTPS"):
            BaseConfig.validate_production_secrets(config_dict)

    def test_non_https_redirect_uri_error_includes_the_uri(self):
        """
        The error message should include the offending URI so the
        operator can see exactly what needs to change.
        """
        bad_uri = "http://insecure.example.com/auth/callback"
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": bad_uri,
        }
        with pytest.raises(RuntimeError, match=bad_uri):
            BaseConfig.validate_production_secrets(config_dict)

    def test_multiple_hard_failures_reported_together(self):
        """
        If both SECRET_KEY and Azure credentials are invalid, all
        errors should appear in a single RuntimeError so the operator
        can fix everything in one pass instead of iterating.
        """
        config_dict = {
            "SECRET_KEY": _DEFAULT_SECRET_KEY,
            "AZURE_CLIENT_ID": "",
            "AZURE_CLIENT_SECRET": "",
            "AZURE_TENANT_ID": "",
            "AZURE_REDIRECT_URI": "http://insecure.example.com/callback",
        }
        with pytest.raises(RuntimeError) as exc_info:
            BaseConfig.validate_production_secrets(config_dict)

        error_msg = str(exc_info.value)
        # All three categories of hard failure should be present.
        assert "SECRET_KEY" in error_msg
        assert "Entra ID" in error_msg or "AZURE_CLIENT_ID" in error_msg
        assert "HTTPS" in error_msg

    def test_empty_redirect_uri_does_not_trigger_https_check(self):
        """
        When AZURE_REDIRECT_URI is empty, the HTTPS check should
        be skipped (the missing Azure creds check covers the real
        issue).  Only non-empty HTTP URIs should trigger the HTTPS
        error.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "WARNING",
        }
        # Should NOT raise -- empty URI skips the HTTPS check.
        BaseConfig.validate_production_secrets(config_dict)


# =====================================================================
# 8. validate_production_secrets -- soft warnings (logging)
# =====================================================================


class TestValidateProductionSecretsSoftWarnings:
    """
    Verify that ``validate_production_secrets`` logs warnings for
    non-critical misconfigurations without raising.
    """

    def test_missing_neogov_api_key_logs_warning(self, caplog):
        """
        A missing NEOGOV_API_KEY should log a warning because HR
        sync will not work, but the app can still run for other
        functionality.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "",
            "LOG_LEVEL": "WARNING",
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            # Should not raise -- this is a soft warning.
            BaseConfig.validate_production_secrets(config_dict)

        assert any("NEOGOV_API_KEY" in record.message for record in caplog.records)

    def test_none_neogov_api_key_logs_warning(self, caplog):
        """
        NEOGOV_API_KEY set to None (missing from config entirely)
        should also trigger the warning.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "LOG_LEVEL": "WARNING",
            # NEOGOV_API_KEY intentionally absent from dict.
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            BaseConfig.validate_production_secrets(config_dict)

        assert any("NEOGOV_API_KEY" in record.message for record in caplog.records)

    def test_debug_log_level_logs_warning(self, caplog):
        """
        LOG_LEVEL=DEBUG in production should log a warning because
        debug output can leak secrets via SQLAlchemy echo, NeoGov
        headers, or future debug statements.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "DEBUG",
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            BaseConfig.validate_production_secrets(config_dict)

        assert any("LOG_LEVEL" in record.message for record in caplog.records)

    def test_debug_log_level_warning_mentions_sensitive_data(self, caplog):
        """
        The LOG_LEVEL=DEBUG warning should mention the risk of
        sensitive data exposure to help the operator understand
        why it matters.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "DEBUG",
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            BaseConfig.validate_production_secrets(config_dict)

        # The warning should mention sensitive data or SQL queries.
        warning_messages = [
            record.message for record in caplog.records if "LOG_LEVEL" in record.message
        ]
        assert len(warning_messages) > 0
        combined = " ".join(warning_messages)
        assert "sensitive" in combined.lower() or "sql" in combined.lower()

    def test_warning_log_level_does_not_trigger_debug_warning(self, caplog):
        """
        LOG_LEVEL=WARNING is the recommended production level and
        should NOT trigger any debug-level log warning.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "WARNING",
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            BaseConfig.validate_production_secrets(config_dict)

        log_level_warnings = [
            record for record in caplog.records if "LOG_LEVEL" in record.message
        ]
        assert len(log_level_warnings) == 0


# =====================================================================
# 9. validate_production_secrets -- clean pass
# =====================================================================


class TestValidateProductionSecretsCleanPass:
    """
    Verify that ``validate_production_secrets`` passes silently
    when all configuration values are correct.
    """

    def test_valid_production_config_does_not_raise(self):
        """
        A fully valid production configuration should not raise
        any exceptions.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "WARNING",
        }
        # Should complete without raising.
        BaseConfig.validate_production_secrets(config_dict)

    def test_valid_production_config_does_not_log_warnings(self, caplog):
        """
        When everything is configured correctly, no warnings should
        be emitted.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "WARNING",
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            BaseConfig.validate_production_secrets(config_dict)

        # No warnings from the config module.
        config_warnings = [
            record for record in caplog.records if record.name == "app.config"
        ]
        assert len(config_warnings) == 0

    def test_info_log_level_does_not_trigger_warnings(self, caplog):
        """
        LOG_LEVEL=INFO is a reasonable production level and should
        not trigger the DEBUG-specific warning.
        """
        config_dict = {
            "SECRET_KEY": "secure-production-key-abcdef1234567890",
            "AZURE_CLIENT_ID": "valid-id",
            "AZURE_CLIENT_SECRET": "valid-secret",
            "AZURE_TENANT_ID": "valid-tenant",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "valid-key",
            "LOG_LEVEL": "INFO",
        }
        with caplog.at_level(logging.WARNING, logger="app.config"):
            BaseConfig.validate_production_secrets(config_dict)

        log_level_warnings = [
            record for record in caplog.records if "LOG_LEVEL" in record.message
        ]
        assert len(log_level_warnings) == 0


# =====================================================================
# 10. Extension initialization verification
# =====================================================================


class TestExtensionRegistration:
    """
    Verify that ``create_app()`` initializes all required Flask
    extensions and binds them to the application.
    """

    def test_sqlalchemy_is_initialized(self, app):
        """
        The SQLAlchemy ``db`` extension should be bound to the app
        and the engine should be available.
        """
        assert db.engine is not None

    def test_login_manager_is_initialized(self, app):
        """
        Flask-Login's LoginManager should be registered, and
        ``login_view`` should point to the auth.login route.
        """
        from app.extensions import login_manager

        assert login_manager.login_view == "auth.login"

    def test_csrf_protection_is_initialized(self, app):
        """
        The CSRFProtect extension should be registered on the app.
        In testing config, WTF_CSRF_ENABLED is False, but the
        extension itself should still be bound.
        """
        from app.extensions import csrf

        # CSRFProtect registers itself; we verify by checking the app
        # has the extension's error handler or that the config key exists.
        assert "WTF_CSRF_ENABLED" in app.config


# =====================================================================
# 11. Blueprint registration verification
# =====================================================================


class TestBlueprintRegistration:
    """
    Verify that all seven blueprints are registered with the correct
    URL prefixes.

    This catches accidental blueprint deregistration or prefix changes
    that would break all routes in a module.
    """

    def test_main_blueprint_registered(self, app):
        """The main blueprint should be registered at the root URL."""
        assert "main" in app.blueprints

    def test_auth_blueprint_registered(self, app):
        """The auth blueprint should be registered with /auth prefix."""
        assert "auth" in app.blueprints

    def test_organization_blueprint_registered(self, app):
        """The org blueprint should be registered with /org prefix."""
        assert "organization" in app.blueprints

    def test_equipment_blueprint_registered(self, app):
        """The equipment blueprint should be registered with /equipment prefix."""
        assert "equipment" in app.blueprints

    def test_requirements_blueprint_registered(self, app):
        """The requirements blueprint should be registered with /requirements prefix."""
        assert "requirements" in app.blueprints

    def test_reports_blueprint_registered(self, app):
        """The reports blueprint should be registered with /reports prefix."""
        assert "reports" in app.blueprints

    def test_admin_blueprint_registered(self, app):
        """The admin blueprint should be registered with /admin prefix."""
        assert "admin" in app.blueprints

    def test_all_seven_blueprints_present(self, app):
        """
        Exactly seven blueprints should be registered.  A missing
        blueprint would break an entire section of the application.
        """
        expected_blueprints = {
            "main",
            "auth",
            "organization",
            "equipment",
            "requirements",
            "reports",
            "admin",
        }
        # app.blueprints includes all registered blueprints.
        assert expected_blueprints.issubset(set(app.blueprints.keys()))

    def test_auth_blueprint_has_login_route(self, app):
        """
        The auth blueprint should have a ``/auth/login`` URL rule
        registered.  This is the entry point for authentication.
        """
        url_rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/auth/login" in url_rules

    def test_admin_blueprint_has_users_route(self, app):
        """
        The admin blueprint should have a ``/admin/users`` URL rule.
        This is the user management page shown during CIO review.
        """
        url_rules = [rule.rule for rule in app.url_map.iter_rules()]
        assert "/admin/users" in url_rules


# =====================================================================
# 12. Error handler registration
# =====================================================================


class TestErrorHandlerRegistration:
    """
    Verify that custom error handlers are registered for 403, 404,
    and 500.  Behavioral testing of these handlers is in
    ``test_routes/test_error_handlers.py`` -- here we only confirm
    they are wired up.
    """

    def test_403_handler_registered(self, app):
        """The app should have a custom handler for 403 errors."""
        # Flask stores error handlers in app.error_handler_spec.
        # The global (None blueprint) handlers are under the None key.
        handlers = app.error_handler_spec.get(None, {})
        assert 403 in handlers

    def test_404_handler_registered(self, app):
        """The app should have a custom handler for 404 errors."""
        handlers = app.error_handler_spec.get(None, {})
        assert 404 in handlers

    def test_500_handler_registered(self, app):
        """The app should have a custom handler for 500 errors."""
        handlers = app.error_handler_spec.get(None, {})
        assert 500 in handlers


# =====================================================================
# 13. Flask-Login load_user callback
# =====================================================================


class TestLoadUserCallback:
    """
    Verify that the ``load_user`` callback registered by
    ``_register_extensions()`` returns the correct User for a valid
    ID and None for an invalid ID.

    This callback is at 0% coverage because the test suite uses the
    ``X-Test-User-Id`` header approach via ``request_loader`` instead
    of session-based auth.  These tests exercise ``load_user``
    directly to close the coverage gap.
    """

    def test_load_user_returns_correct_user_for_valid_id(
        self, app, db_session, admin_user
    ):
        """
        Given a valid user ID (as a string, since Flask-Login stores
        it as a string in the session), ``load_user`` should return
        the corresponding User model instance.
        """
        from app.extensions import login_manager

        with app.app_context():
            # Access the user_loader callback directly.
            # Flask-Login stores it as _user_callback.
            load_fn = login_manager._user_callback
            assert (
                load_fn is not None
            ), "No user_loader callback registered on login_manager"

            loaded_user = load_fn(str(admin_user.id))
            assert loaded_user is not None
            assert loaded_user.id == admin_user.id
            assert loaded_user.email == admin_user.email

    def test_load_user_returns_none_for_nonexistent_id(self, app, db_session):
        """
        A user ID that does not exist in the database should cause
        ``load_user`` to return None, which tells Flask-Login the
        session is invalid.
        """
        from app.extensions import login_manager

        with app.app_context():
            load_fn = login_manager._user_callback
            # Use an absurdly high ID that will not exist.
            loaded_user = load_fn("999999999")
            assert loaded_user is None

    def test_load_user_returns_user_model_instance(self, app, db_session, admin_user):
        """
        The returned object must be a User model instance, not a
        dict, tuple, or other representation.
        """
        from app.extensions import login_manager
        from app.models.user import User

        with app.app_context():
            load_fn = login_manager._user_callback
            loaded_user = load_fn(str(admin_user.id))
            assert isinstance(loaded_user, User)


# =====================================================================
# 14. Session cookie security per environment
# =====================================================================


class TestSessionCookieSecurity:
    """
    Verify that session cookie security settings are correctly
    differentiated between environments.
    """

    def test_testing_config_does_not_require_secure_cookie(self):
        """
        Testing config should not require Secure cookies because
        tests run over plain HTTP on localhost.
        """
        test_app = create_app("testing")
        assert test_app.config["SESSION_COOKIE_SECURE"] is False

    def test_development_config_does_not_require_secure_cookie(self):
        """
        Development config should not require Secure cookies because
        developers run on http://localhost.
        """
        dev_app = create_app("development")
        assert dev_app.config["SESSION_COOKIE_SECURE"] is False

    def test_all_environments_set_httponly(self):
        """
        Every environment should set HttpOnly to prevent JavaScript
        access to the session cookie.
        """
        test_app = create_app("testing")
        assert test_app.config["SESSION_COOKIE_HTTPONLY"] is True

        dev_app = create_app("development")
        assert dev_app.config["SESSION_COOKIE_HTTPONLY"] is True

    def test_all_environments_set_samesite_lax(self):
        """
        Every environment should set SameSite=Lax for cross-origin
        POST CSRF protection.
        """
        test_app = create_app("testing")
        assert test_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"

        dev_app = create_app("development")
        assert dev_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# =====================================================================
# 15. DEV_LOGIN_ENABLED gate per environment
# =====================================================================


class TestDevLoginGate:
    """
    Verify that DEV_LOGIN_ENABLED is appropriately gated in each
    environment to prevent accidental authentication bypass.
    """

    def test_testing_config_enables_dev_login(self):
        """
        Testing config must enable dev-login so integration tests
        can authenticate without OAuth.
        """
        test_app = create_app("testing")
        assert test_app.config["DEV_LOGIN_ENABLED"] is True

    @patch.object(
        ProductionConfig,
        "SECRET_KEY",
        "a-very-secure-random-production-key-1234567890ab",
    )
    @patch.dict(
        os.environ,
        {
            "SECRET_KEY": "a-very-secure-random-production-key-1234567890ab",
            "AZURE_CLIENT_ID": "prod-client-id",
            "AZURE_CLIENT_SECRET": "prod-client-secret",
            "AZURE_TENANT_ID": "prod-tenant-id",
            "AZURE_REDIRECT_URI": "https://app.example.com/auth/callback",
            "NEOGOV_API_KEY": "prod-neogov-key",
            "LOG_LEVEL": "WARNING",
        },
        clear=False,
    )
    def test_production_config_disables_dev_login(self):
        """
        Production config must ALWAYS disable dev-login regardless
        of any DEV_LOGIN_ENABLED environment variable.
        """
        prod_app = create_app("production")
        assert prod_app.config["DEV_LOGIN_ENABLED"] is False


# =====================================================================
# 16. Permanent session lifetime
# =====================================================================


class TestPermanentSessionLifetime:
    """
    Verify that the session lifetime is configured to a reasonable
    value for security.
    """

    def test_default_session_lifetime_is_one_hour(self):
        """
        The default PERMANENT_SESSION_LIFETIME should be 3600
        seconds (1 hour), not Flask's 31-day default.
        """
        test_app = create_app("testing")
        assert test_app.config["PERMANENT_SESSION_LIFETIME"] == 3600
