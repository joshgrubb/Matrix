"""
Routes for the main blueprint — dashboard and health check.
"""

from flask import render_template
from flask_login import current_user, login_required
from sqlalchemy import text

from app.blueprints.main import bp
from app.extensions import db


@bp.route("/")
@login_required
def dashboard():
    """
    Main dashboard displaying summary statistics.

    Shows department count, position count, and a quick cost overview.
    Redirected to after login.
    """
    return render_template("main/dashboard.html")


@bp.route("/health")
def health_check():
    """
    Health check endpoint for monitoring and load balancers.

    Returns 200 if the app is running and can reach the database.
    """
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}, 200
    except Exception as exc:  # pylint: disable=broad-except
        return {"status": "unhealthy", "database": str(exc)}, 503
