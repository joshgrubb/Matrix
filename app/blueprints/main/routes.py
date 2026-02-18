"""
Routes for the main blueprint — dashboard and landing page.
"""

from flask import render_template

from app.blueprints.main import bp


@bp.route("/")
def dashboard():
    """
    Render the application dashboard.

    The dashboard will show cost summaries by department/division once
    the cost service is built in Sprint 5. For now it serves as a
    landing page confirming the application is running.
    """
    return render_template("main/dashboard.html")


@bp.route("/health")
def health_check():
    """
    Simple health check endpoint for monitoring.

    Returns a plain-text 'OK' response. Useful for IIS Application
    Request Routing health probes and uptime monitoring.
    """
    return "OK", 200
