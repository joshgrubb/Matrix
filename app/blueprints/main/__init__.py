"""
Main blueprint — dashboard and landing page.
"""

from flask import Blueprint

bp = Blueprint(
    "main",
    __name__,
    template_folder="templates",
)

# Import routes after blueprint creation to avoid circular imports.
from app.blueprints.main import routes  # noqa: E402, F401
