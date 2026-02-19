"""
Organization blueprint — view departments, divisions, and positions.

All org data is read-only in the UI (sourced from NeoGov sync).
"""

from flask import Blueprint

bp = Blueprint(
    "organization",
    __name__,
    template_folder="templates",
)

from app.blueprints.organization import routes  # noqa: E402, F401
