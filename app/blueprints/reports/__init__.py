"""
Reports blueprint — cost summaries, equipment counts, CSV/Excel export.
"""

from flask import Blueprint

bp = Blueprint(
    "reports",
    __name__,
    template_folder="templates",
)

from app.blueprints.reports import routes  # noqa: E402, F401
