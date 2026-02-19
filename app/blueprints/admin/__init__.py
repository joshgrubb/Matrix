"""
Admin blueprint — user management, audit logs, HR sync, system config.
"""

from flask import Blueprint

bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
)

from app.blueprints.admin import routes  # noqa: E402, F401
