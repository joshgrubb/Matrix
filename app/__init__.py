"""
Application factory for the IT Equipment & Software Tracking Application.

Usage::

    from app import create_app
    app = create_app()           # Uses FLASK_ENV to pick config.
    app = create_app("testing")  # Explicit config for tests.
"""

import logging
import os

from flask import Flask, render_template

from .config import config_by_name
from .extensions import csrf, db, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    """
    Create and configure the Flask application.

    Args:
        config_name: One of 'development', 'testing', or 'production'.
                     Defaults to the FLASK_ENV environment variable,
                     falling back to 'development'.

    Returns:
        A fully configured Flask application instance.
    """
    # Resolve the configuration class.
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")
    config_class = config_by_name.get(config_name)
    if config_class is None:
        raise ValueError(
            f"Unknown config '{config_name}'. "
            f"Valid options: {list(config_by_name.keys())}"
        )

    # Create the Flask app with the correct template/static paths.
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Safety check: refuse to run production with the default secret key.
    if (
        config_name == "production"
        and app.config["SECRET_KEY"] == "dev-secret-change-me"
    ):
        raise RuntimeError(
            "SECRET_KEY must be set to a secure random value in production."
        )

    # -- Initialize extensions ---------------------------------------------
    _register_extensions(app)

    # -- Register blueprints -----------------------------------------------
    _register_blueprints(app)

    # -- Register error handlers -------------------------------------------
    _register_error_handlers(app)

    # -- Register custom CLI commands --------------------------------------
    _register_cli_commands(app)

    # -- Configure logging -------------------------------------------------
    _configure_logging(app)

    return app


def _register_extensions(app: Flask) -> None:
    """Bind all Flask extensions to the application instance."""
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Register the Flask-Login user loader callback.
    # Imported here to avoid circular imports with models.
    from .models.user import User  # pylint: disable=import-outside-toplevel

    @login_manager.user_loader
    def load_user(user_id: str):
        """Load a user by primary key for Flask-Login session management."""
        return db.session.get(User, int(user_id))


def _register_blueprints(app: Flask) -> None:
    """
    Import and register each blueprint with its URL prefix.

    Blueprints are imported inside this function to avoid circular
    imports — models and services can safely import ``db`` from
    extensions at module level.
    """
    # pylint: disable=import-outside-toplevel

    # Main blueprint — dashboard at root URL.
    from .blueprints.main import bp as main_bp

    app.register_blueprint(main_bp)

    # Authentication — login, logout, OAuth2 callbacks.
    from .blueprints.auth import bp as auth_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")

    # Organization — departments, divisions, positions.
    from .blueprints.organization import bp as org_bp

    app.register_blueprint(org_bp, url_prefix="/org")

    # Equipment — hardware and software catalog management.
    from .blueprints.equipment import bp as equipment_bp

    app.register_blueprint(equipment_bp, url_prefix="/equipment")

    # Requirements — guided flow for position requirements.
    from .blueprints.requirements import bp as requirements_bp

    app.register_blueprint(requirements_bp, url_prefix="/requirements")

    # Reports — cost summaries, exports, reporting.
    from .blueprints.reports import bp as reports_bp

    app.register_blueprint(reports_bp, url_prefix="/reports")

    # Admin — user management, audit logs, HR sync.
    from .blueprints.admin import bp as admin_bp

    app.register_blueprint(admin_bp, url_prefix="/admin")


def _register_error_handlers(app: Flask) -> None:
    """Register custom error pages for common HTTP error codes."""

    @app.errorhandler(403)
    def forbidden(error):  # pylint: disable=unused-argument
        """Handle 403 Forbidden errors."""
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):  # pylint: disable=unused-argument
        """Handle 404 Not Found errors."""
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):  # pylint: disable=unused-argument
        """Handle 500 Internal Server Error."""
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_cli_commands(app: Flask) -> None:
    """Register custom Flask CLI commands (e.g., flask db-check)."""
    from .cli import register_commands  # pylint: disable=import-outside-toplevel
    from .seed_dev_admin import (
        register_seed_commands,
    )  # pylint: disable=import-outside-toplevel

    register_commands(app)

    register_commands(app)
    register_seed_commands(app)


def _configure_logging(app: Flask) -> None:
    """
    Set up structured JSON logging for the application.

    In production, logs are written as JSON for ingestion by log
    aggregation tools. In development, the default formatter is
    used for readability.
    """
    log_level = app.config.get("LOG_LEVEL", "INFO")
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))

    # Quiet down noisy libraries in development.
    if app.debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
