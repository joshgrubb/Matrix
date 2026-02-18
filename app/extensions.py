"""
Flask extension instances.

Extensions are created here without binding to an application so that
the application factory can call ``init_app()`` on each one during
``create_app()``.  This avoids circular imports and follows the
standard Flask extension pattern.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

# -- Database ORM ----------------------------------------------------------
# The ``db`` instance is imported by models and services throughout the app.
db = SQLAlchemy()

# -- Schema migrations (Alembic via Flask-Migrate) -------------------------
migrate = Migrate()

# -- Session-based authentication ------------------------------------------
login_manager = LoginManager()
# Redirect unauthenticated users to the login page.
login_manager.login_view = "auth.login"
login_manager.login_message = "Please sign in to access this page."
login_manager.login_message_category = "warning"

# -- CSRF protection for form submissions ---------------------------------
csrf = CSRFProtect()
