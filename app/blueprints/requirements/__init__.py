"""
Requirements blueprint — guided flow for setting position requirements.
"""

from flask import Blueprint

bp = Blueprint(
    "requirements",
    __name__,
    template_folder="templates",
)

from app.blueprints.requirements import routes  # noqa: E402, F401
