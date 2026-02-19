"""
Equipment blueprint — CRUD for hardware/software catalog and types.
"""

from flask import Blueprint

bp = Blueprint(
    "equipment",
    __name__,
    template_folder="templates",
)

from app.blueprints.equipment import routes  # noqa: E402, F401
