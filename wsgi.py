"""
Waitress WSGI entry point for production deployment.

Usage::

    python wsgi.py

Or register as a Windows Service via NSSM::

    nssm install PositionMatrix "C:\\path\\to\\venv\\Scripts\\python.exe" "C:\\path\\to\\wsgi.py"

Waitress is a pure-Python WSGI server that runs natively on Windows
without requiring C compilation or Unix-specific dependencies.
"""

import os

from waitress import serve

from app import create_app

# Force production config when running via this entry point.
app = create_app(os.environ.get("FLASK_ENV", "production"))

if __name__ == "__main__":
    host = os.environ.get("WAITRESS_HOST", "127.0.0.1")
    port = int(os.environ.get("WAITRESS_PORT", "8080"))
    print(f"Starting Waitress on {host}:{port}")
    serve(app, host=host, port=port)
