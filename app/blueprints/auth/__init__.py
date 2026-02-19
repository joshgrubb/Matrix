"""
Auth blueprint — OAuth2 login/logout and Entra ID callbacks.
"""

from flask import Blueprint

bp = Blueprint(
    "auth",
    __name__,
    template_folder="templates",
)

# Import routes after blueprint creation to avoid circular imports.
from app.blueprints.auth import routes  # noqa: E402, F401
