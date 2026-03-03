"""
Centralized logging configuration for the PositionMatrix application.

This module replaces the minimal ``_configure_logging()`` in
``app/__init__.py`` with a production-grade logging pipeline that
provides:

    - **Structured JSON output** (production) via ``python-json-logger``.
    - **Colored human-readable output** (development) for terminal use.
    - **Request correlation IDs** — a UUID-per-request stored in
      ``flask.g`` and injected into every log record automatically.
    - **User context injection** — ``user_id``, ``user_email``, and
      ``user_role`` from ``flask_login.current_user`` appear in every
      log record emitted during a request.
    - **Sensitive data redaction** — passwords, tokens, API keys, and
      secrets are masked before they reach any handler.
    - **Request lifecycle logging** — ``before_request`` /
      ``after_request`` hooks log method, path, status, duration,
      and client IP for every HTTP request.
    - **Rotating file handlers** — ``RotatingFileHandler`` for
      production file output with configurable size and backup count.
    - **Waitress / WSGI integration** — Waitress's own logger is
      captured into the same pipeline when running in production.
    - **SQLAlchemy noise control** — query echo is suppressed in
      production; set to WARNING in development unless explicitly
      overridden.

Usage in ``app/__init__.py``::

    from .logging_config import configure_logging

    def create_app(config_name=None):
        ...
        configure_logging(app)
        return app

Environment variables (all optional, with sane defaults):

    LOG_LEVEL           — Root log level (default: INFO in prod, DEBUG in dev).
    LOG_DIR             — Directory for log files (default: ./logs).
    LOG_FILE            — Log filename (default: positionmatrix.log).
    LOG_MAX_BYTES       — Max size per log file before rotation (default: 10 MB).
    LOG_BACKUP_COUNT    — Number of rotated files to keep (default: 5).
    LOG_FORMAT          — Force 'json' or 'text' (auto-detected from DEBUG).

Author: PositionMatrix Team
"""

import logging
import logging.handlers
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import Flask, g, has_request_context, request
from flask_login import current_user
from pythonjsonlogger import jsonlogger


# =========================================================================
# Constants
# =========================================================================

# Patterns that indicate a value should be redacted.  Applied to both
# dictionary keys (case-insensitive) and URL query parameter names.
_SENSITIVE_KEY_PATTERNS: re.Pattern = re.compile(
    r"(password|passwd|secret|token|api_key|apikey|authorization"
    r"|access_token|refresh_token|client_secret|cookie|session"
    r"|credit_card|ssn|social_security)",
    re.IGNORECASE,
)

# Replacement string for redacted values.
_REDACTED = "***REDACTED***"

# Default log directory (relative to the working directory / project root).
_DEFAULT_LOG_DIR = "logs"

# Default maximum bytes per log file (10 MB).
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024

# Default number of rotated backup files to retain.
_DEFAULT_BACKUP_COUNT = 5

# Paths to exclude from request lifecycle logging (noisy health checks, etc.).
_EXCLUDED_PATHS: set[str] = {
    "/health",
    "/favicon.ico",
    "/static",
}


# =========================================================================
# Correlation ID helpers
# =========================================================================


def get_correlation_id() -> str:
    """
    Return the current request's correlation ID.

    If a client sends an ``X-Request-ID`` header, that value is reused
    so that the caller can correlate front-end and back-end logs.
    Otherwise a new UUID4 is generated and stored on ``flask.g``.

    Outside of a request context (e.g., CLI commands, background tasks)
    this returns ``"no-request-context"``.

    Returns:
        A string suitable for log injection and response headers.
    """
    if not has_request_context():
        return "no-request-context"

    # Return cached value if we've already computed it this request.
    correlation_id = getattr(g, "correlation_id", None)
    if correlation_id is not None:
        return correlation_id

    # Honour an incoming header if present; generate otherwise.
    correlation_id = request.headers.get(
        "X-Request-ID",
        str(uuid.uuid4()),
    )
    g.correlation_id = correlation_id
    return correlation_id


# =========================================================================
# Sensitive data redaction
# =========================================================================


def _redact_value(key: str, value: Any) -> Any:
    """
    Return ``_REDACTED`` if *key* matches a sensitive pattern.

    Args:
        key:   The dictionary key or parameter name to inspect.
        value: The original value.

    Returns:
        The original *value* unchanged, or ``_REDACTED``.
    """
    if _SENSITIVE_KEY_PATTERNS.search(str(key)):
        return _REDACTED
    return value


def redact_dict(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Recursively redact sensitive values in a dictionary.

    Useful for sanitising request headers, form data, and JSON
    payloads before they are written to any log handler.

    Args:
        data: A dict (or None) to sanitise.

    Returns:
        A shallow copy with sensitive leaf values replaced, or None.
    """
    if data is None:
        return None

    sanitised: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            sanitised[key] = redact_dict(value)
        else:
            sanitised[key] = _redact_value(key, value)
    return sanitised


# =========================================================================
# Custom logging filter — injects request context into every record
# =========================================================================


class RequestContextFilter(logging.Filter):
    """
    Logging filter that enriches every ``LogRecord`` with request and
    user context so that formatters can include them automatically.

    Injected attributes:

        correlation_id  — UUID for the current request.
        user_id         — Current user's primary key (or ``-``).
        user_email      — Current user's email (or ``anonymous``).
        user_role       — Current user's role name (or ``-``).
        remote_addr     — Client IP address (or ``-``).
        request_method  — HTTP method (or ``-``).
        request_path    — URL path (or ``-``).
    """

    # pylint: disable=too-few-public-methods

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Attach context attributes to *record* and allow it through.

        Args:
            record: The log record being processed.

        Returns:
            Always ``True`` (never suppresses records).
        """
        # -- Correlation ID ------------------------------------------------
        record.correlation_id = get_correlation_id()  # type: ignore[attr-defined]

        # -- User context --------------------------------------------------
        if has_request_context():
            try:
                if current_user and current_user.is_authenticated:
                    record.user_id = current_user.id  # type: ignore[attr-defined]
                    record.user_email = current_user.email  # type: ignore[attr-defined]
                    record.user_role = (  # type: ignore[attr-defined]
                        current_user.role.role_name if current_user.role else "-"
                    )
                else:
                    record.user_id = "-"  # type: ignore[attr-defined]
                    record.user_email = "anonymous"  # type: ignore[attr-defined]
                    record.user_role = "-"  # type: ignore[attr-defined]
            except Exception:  # pylint: disable=broad-except
                # Flask-Login may raise if the app context is torn down.
                record.user_id = "-"  # type: ignore[attr-defined]
                record.user_email = "anonymous"  # type: ignore[attr-defined]
                record.user_role = "-"  # type: ignore[attr-defined]

            record.remote_addr = request.remote_addr or "-"  # type: ignore[attr-defined]
            record.request_method = request.method  # type: ignore[attr-defined]
            record.request_path = request.path  # type: ignore[attr-defined]
        else:
            record.user_id = "-"  # type: ignore[attr-defined]
            record.user_email = "system"  # type: ignore[attr-defined]
            record.user_role = "-"  # type: ignore[attr-defined]
            record.remote_addr = "-"  # type: ignore[attr-defined]
            record.request_method = "-"  # type: ignore[attr-defined]
            record.request_path = "-"  # type: ignore[attr-defined]

        return True


# =========================================================================
# Custom logging filter — redacts sensitive data in log messages
# =========================================================================


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that scrubs sensitive values from ``LogRecord``
    args and message text before they reach any handler.

    This is a defence-in-depth measure.  Individual callers *should*
    avoid logging secrets, but this filter catches accidental leaks
    (e.g., SQLAlchemy echoing connection strings, or exception
    tracebacks containing credentials).
    """

    # pylint: disable=too-few-public-methods

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Scrub sensitive patterns from the record message.

        Args:
            record: The log record being processed.

        Returns:
            Always ``True``.
        """
        # Scrub the formatted message for common secret patterns.
        if record.args and isinstance(record.args, dict):
            record.args = redact_dict(record.args)

        # Scrub raw message string for inline secrets.
        if isinstance(record.msg, str):
            record.msg = _SENSITIVE_KEY_PATTERNS.sub(
                lambda m: m.group(0) + "=" + _REDACTED,
                record.msg,
            )

        return True


# =========================================================================
# Formatters
# =========================================================================


class DevelopmentFormatter(logging.Formatter):
    """
    Human-readable formatter with ANSI colour codes for terminal use.

    Format::

        [HH:MM:SS] LEVEL    module          [correlation_id] user@email — Message

    Colour key:

        DEBUG    → grey
        INFO     → green
        WARNING  → yellow
        ERROR    → red
        CRITICAL → bold red
    """

    # ANSI escape sequences.
    _COLOURS = {
        logging.DEBUG: "\033[90m",  # grey
        logging.INFO: "\033[92m",  # green
        logging.WARNING: "\033[93m",  # yellow
        logging.ERROR: "\033[91m",  # red
        logging.CRITICAL: "\033[1;91m",  # bold red
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """
        Format *record* with colour and context fields.

        Args:
            record: The log record to format.

        Returns:
            The formatted, colour-coded string.
        """
        colour = self._COLOURS.get(record.levelno, "")
        reset = self._RESET

        # Extract injected attributes (set by RequestContextFilter).
        correlation_id = getattr(record, "correlation_id", "-")
        user_email = getattr(record, "user_email", "system")
        user_id = getattr(record, "user_id", "-")

        # Truncate correlation ID for readability in the terminal.
        short_id = correlation_id[:8] if correlation_id != "-" else "-"

        # Build the formatted line.
        timestamp = self.formatTime(record, "%H:%M:%S")
        level = record.levelname.ljust(8)
        module = record.name[-30:].ljust(30)  # Right-align, truncate long names.

        # Core message.
        message = record.getMessage()

        formatted = (
            f"{colour}[{timestamp}] {level}{reset} "
            f"{module} [{short_id}] "
            f"u:{user_id:<4} "
            f"— {message}"
        )

        # Append exception info if present.
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            formatted += f"\n{record.exc_text}"

        return formatted


class ProductionJsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter for production log aggregation.

    Emits one JSON object per line with a stable set of fields
    suitable for ingestion by ELK, Splunk, Azure Monitor, or any
    JSON-aware log aggregation tool.

    Standard fields::

        timestamp, level, logger, correlation_id,
        user_id, user_email, user_role,
        remote_addr, method, path,
        message, module, funcName, lineno
    """

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """
        Inject application-specific fields into the JSON output.

        Args:
            log_record:   The dict that will be serialised to JSON.
            record:       The original ``LogRecord``.
            message_dict: Any extra dict data from the log call.
        """
        super().add_fields(log_record, record, message_dict)

        # Standardise the timestamp field name and format.
        # NOTE: We use ``datetime.fromtimestamp`` instead of
        # ``self.formatTime`` because ``time.strftime`` (which
        # ``formatTime`` delegates to) does not support the ``%f``
        # microsecond directive — only ``datetime.strftime`` does.
        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        # Inject context from RequestContextFilter.
        log_record["correlation_id"] = getattr(record, "correlation_id", "-")
        log_record["user_id"] = getattr(record, "user_id", "-")
        log_record["user_email"] = getattr(record, "user_email", "system")
        log_record["user_role"] = getattr(record, "user_role", "-")
        log_record["remote_addr"] = getattr(record, "remote_addr", "-")
        log_record["method"] = getattr(record, "request_method", "-")
        log_record["path"] = getattr(record, "request_path", "-")

        # Source location (useful for debugging production issues).
        log_record["module"] = record.module
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno


# =========================================================================
# Handler factory
# =========================================================================


def _build_console_handler(
    is_production: bool,
    log_level: int,
) -> logging.StreamHandler:
    """
    Build a console (stderr) handler with the appropriate formatter.

    Args:
        is_production: ``True`` for JSON output, ``False`` for coloured text.
        log_level:     The minimum level for this handler.

    Returns:
        A configured ``StreamHandler``.
    """
    handler = logging.StreamHandler()
    handler.setLevel(log_level)

    if is_production:
        handler.setFormatter(
            ProductionJsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
        )
    else:
        handler.setFormatter(DevelopmentFormatter())

    return handler


def _build_file_handler(
    log_dir: str,
    log_file: str,
    max_bytes: int,
    backup_count: int,
    log_level: int,
) -> logging.handlers.RotatingFileHandler:
    """
    Build a rotating file handler that always outputs JSON.

    File logs are *always* JSON regardless of environment so that
    automated tooling can parse them reliably.

    Args:
        log_dir:      Directory for log files.
        log_file:     Log filename.
        max_bytes:    Maximum bytes per file before rotation.
        backup_count: Number of backup files to retain.
        log_level:    The minimum level for this handler.

    Returns:
        A configured ``RotatingFileHandler``.
    """
    os.makedirs(log_dir, exist_ok=True)
    filepath = os.path.join(log_dir, log_file)

    handler = logging.handlers.RotatingFileHandler(
        filename=filepath,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(log_level)

    # File logs are always structured JSON for machine parsing.
    handler.setFormatter(
        ProductionJsonFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
    )
    return handler


# =========================================================================
# Request lifecycle hooks
# =========================================================================


def _register_request_hooks(app: Flask) -> None:
    """
    Register ``before_request`` and ``after_request`` hooks on *app*
    for automatic request lifecycle logging.

    Logged fields per request:

        - Correlation ID (UUID or forwarded ``X-Request-ID``).
        - HTTP method and path.
        - Client IP (``REMOTE_ADDR``).
        - Authenticated user (ID, email, role).
        - Response status code.
        - Request duration in milliseconds.

    Args:
        app: The Flask application instance.
    """
    request_logger = logging.getLogger("app.request")

    @app.before_request
    def _log_request_start():
        """Capture the request start time and log the incoming request."""
        # Store start time for duration calculation in after_request.
        g.request_start_time = time.monotonic()

        # Ensure the correlation ID is generated early.
        get_correlation_id()

        # Skip noisy endpoints.
        if any(request.path.startswith(p) for p in _EXCLUDED_PATHS):
            g.skip_request_log = True
            return

        g.skip_request_log = False
        request_logger.info(
            "Request started: %s %s from %s",
            request.method,
            request.full_path.rstrip("?"),
            request.remote_addr,
        )

    @app.after_request
    def _log_request_end(response):
        """Log the completed request with status and duration."""
        # Always inject the correlation ID into the response headers
        # so that callers (and browser dev tools) can trace requests.
        correlation_id = getattr(g, "correlation_id", None)
        if correlation_id:
            response.headers["X-Request-ID"] = correlation_id

        # Skip logging for excluded paths.
        if getattr(g, "skip_request_log", False):
            return response

        # Calculate request duration.
        start_time = getattr(g, "request_start_time", None)
        duration_ms = (
            round((time.monotonic() - start_time) * 1000, 2)
            if start_time is not None
            else -1
        )

        # Choose log level based on status code.
        status_code = response.status_code
        if status_code >= 500:
            log_method = request_logger.error
        elif status_code >= 400:
            log_method = request_logger.warning
        else:
            log_method = request_logger.info

        log_method(
            "Request completed: %s %s → %d (%s ms)",
            request.method,
            request.full_path.rstrip("?"),
            status_code,
            duration_ms,
        )

        return response


# =========================================================================
# Third-party logger tuning
# =========================================================================


def _configure_library_loggers(is_production: bool) -> None:
    """
    Adjust log levels for noisy third-party libraries.

    In production, most libraries are set to WARNING to reduce volume.
    In development, they are slightly more verbose but still tamed.

    Args:
        is_production: ``True`` to aggressively silence libraries.
    """
    # SQLAlchemy engine logs every SQL query at INFO — too noisy.
    sqlalchemy_level = logging.WARNING if is_production else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sqlalchemy_level)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

    # Alembic migration logger.
    logging.getLogger("alembic").setLevel(logging.INFO)

    # Waitress request logger (duplicates our lifecycle logging).
    logging.getLogger("waitress").setLevel(
        logging.WARNING if is_production else logging.INFO
    )

    # MSAL (Entra ID) is chatty at DEBUG.
    logging.getLogger("msal").setLevel(logging.WARNING)

    # urllib3 connection pool logging.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Werkzeug's built-in request logger (dev server only).
    if is_production:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)


# =========================================================================
# Public entry point
# =========================================================================


def configure_logging(app: Flask) -> None:
    """
    Configure the full logging pipeline for the Flask application.

    This is the single entry point called from ``create_app()``.  It
    replaces the previous ``_configure_logging()`` function entirely.

    Steps performed:

        1. Determine environment (production vs. development).
        2. Resolve log level from config / environment.
        3. Clear any pre-existing handlers on the root logger.
        4. Attach the ``RequestContextFilter`` and
           ``SensitiveDataFilter`` to the root logger.
        5. Build and attach console and file handlers.
        6. Register Flask ``before_request`` / ``after_request`` hooks.
        7. Tune third-party library log levels.

    Args:
        app: The Flask application instance (must have config loaded).
    """
    # -- Resolve settings --------------------------------------------------
    is_production = not app.debug and not app.testing
    log_level_name = app.config.get("LOG_LEVEL", "INFO" if is_production else "DEBUG")
    log_level = getattr(logging, log_level_name.upper(), logging.INFO)

    log_dir = os.environ.get("LOG_DIR", _DEFAULT_LOG_DIR)
    log_file = os.environ.get("LOG_FILE", "positionmatrix.log")
    max_bytes = int(os.environ.get("LOG_MAX_BYTES", str(_DEFAULT_MAX_BYTES)))
    backup_count = int(os.environ.get("LOG_BACKUP_COUNT", str(_DEFAULT_BACKUP_COUNT)))

    # Allow forcing a format via env var (useful for containers).
    force_format = os.environ.get("LOG_FORMAT", "").lower()
    if force_format == "json":
        use_json = True
    elif force_format == "text":
        use_json = False
    else:
        use_json = is_production

    # -- Configure root logger ---------------------------------------------
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any handlers that Flask or basicConfig may have attached
    # to prevent duplicate output.
    root_logger.handlers.clear()

    # -- Attach filters (apply to root so all children inherit) ------------
    context_filter = RequestContextFilter()
    sensitive_filter = SensitiveDataFilter()
    root_logger.addFilter(context_filter)
    root_logger.addFilter(sensitive_filter)

    # -- Console handler ---------------------------------------------------
    console_handler = _build_console_handler(
        is_production=use_json,
        log_level=log_level,
    )
    root_logger.addHandler(console_handler)

    # -- File handler (always enabled; rotates automatically) --------------
    try:
        file_handler = _build_file_handler(
            log_dir=log_dir,
            log_file=log_file,
            max_bytes=max_bytes,
            backup_count=backup_count,
            log_level=log_level,
        )
        root_logger.addHandler(file_handler)
    except OSError as exc:
        # If we can't write to the log directory (e.g., permissions),
        # log a warning to console but don't crash the application.
        root_logger.warning(
            "Could not create file log handler at %s/%s: %s. "
            "Continuing with console logging only.",
            log_dir,
            log_file,
            exc,
        )

    # -- Register request lifecycle hooks ----------------------------------
    _register_request_hooks(app)

    # -- Tune third-party loggers ------------------------------------------
    _configure_library_loggers(is_production)

    # -- Startup banner (logged once at application boot) ------------------
    startup_logger = logging.getLogger("app.startup")
    startup_logger.info(
        "Logging configured — level=%s, format=%s, file=%s/%s, " "production=%s",
        log_level_name.upper(),
        "json" if use_json else "text",
        log_dir,
        log_file,
        is_production,
    )
