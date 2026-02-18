"""
Smoke tests for the main blueprint routes.

These verify that the application starts up correctly and the
dashboard and health check endpoints respond.
"""


class TestDashboard:
    """Tests for the dashboard landing page."""

    def test_dashboard_returns_200(self, client):
        """The dashboard should return HTTP 200."""
        response = client.get("/")
        assert response.status_code == 200

    def test_dashboard_contains_app_name(self, client):
        """The dashboard should display the application name."""
        response = client.get("/")
        assert b"PositionMatrix" in response.data


class TestHealthCheck:
    """Tests for the health check endpoint."""

    def test_health_check_returns_200(self, client):
        """The health check should return HTTP 200 with 'OK'."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.data == b"OK"
